"""Facts about one web page and its site, measured: what a visitor's browser and a crawler that doesn't run JavaScript each get.

`gather(chrome, url)` loads the page in Monday's Chrome (the rendered page, every request's size, paint and layout timings), fetches
the same address over plain HTTP (what a crawler without JavaScript reads), reads robots.txt and the sitemap, follows the other
spellings of the address, asks for a page that shouldn't exist and requests a sample of the page's own links. It judges nothing: no
checklist, no score, no report. `brief` makes the facts compact for a prompt. They go to whichever AI Jev chose for the task, so any
written analysis of a site (an SEO report, an accessibility review, a performance write-up, a comparison) is that AI's work on
measured ground: not Monday's guess, and not fixed rules.
Self-check: python -m core.webfacts [--live]   ·   python -m core.webfacts example.com   prints the facts Monday would hand an AI
"""
import asyncio
import json
import re
import secrets
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser

import httpx

from .probes import tls as _tls

HTTP_TIMEOUT_S = 10.0
GATHER_TIMEOUT_S = 75.0       # the whole gathering, Chrome's load included
MAX_HOPS = 6                  # redirects followed per address
MAX_HTML_CHARS = 3_000_000
SAMPLE_LINKS = 12             # the page's own links that are requested
SAMPLE_SITEMAP = 6            # sitemap addresses that are requested
BRIEF_CHARS = 9000            # what `brief` keeps for a prompt
UA = "Mozilla/5.0 (compatible; MondayWebFacts/1.0)"
_GENERIC_ANCHOR = re.compile(r"^(?:click here|here|read more|learn more|more|link|this|details)$", re.I)
_URL = re.compile(r"(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?::\d+)?(?:/[^\s\"'<>]*)?", re.I)
_SEARCH_ENGINES = ("google.", "bing.com", "duckduckgo.com", "search.yahoo.", "yandex.", "baidu.")


# -- addresses -------------------------------------------------------------------------------------------------------------
def find_url(text):
    """The first address or domain inside free words ("the site garbacircle.in", "https://x.com/a?b=1"), else None."""
    m = _URL.search(text or "")
    return m.group(0).rstrip(".,;:)") if m else None


def normalize(url):
    """'garbacircle.in' -> 'https://garbacircle.in/'. Raises ValueError when there is no host."""
    u = (url or "").strip().strip("\"'<>")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "https://" + u.lstrip("/")
    p = urllib.parse.urlsplit(u)
    if not p.hostname or "." not in p.hostname:
        raise ValueError(f"“{url}” isn't a web address I can look at.")
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def site_of(host):
    """The host without a leading www., so a.example.com and www.example.com are one site."""
    return re.sub(r"^www\.", "", (host or "").lower())


def same_site(host, other):
    a, b = site_of(host), site_of(other)
    return bool(a) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def same_page(a, b):
    """Two addresses for the same page: scheme, host, path and query agree; a trailing slash and a fragment don't matter."""
    pa, pb = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
    return (pa.scheme, pa.netloc.lower(), pa.path.rstrip("/"), pa.query) == (pb.scheme, pb.netloc.lower(), pb.path.rstrip("/"), pb.query)


def is_search_page(url):
    h = host_of(url)
    return any(e in h for e in _SEARCH_ENGINES)


# -- markup read without running it (what a crawler that doesn't execute JavaScript gets) --------------------------------------
class _Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = self.lang = self.charset = None
        self.doctype = False
        self.meta, self.og, self.tw = {}, {}, {}
        self.canonical, self.alternates, self.icons = [], [], []
        self.headings, self.anchors, self.images, self.ld = [], [], [], []
        self.words, self._skip, self._body = 0, 0, False
        self._title, self._head, self._a, self._ld = None, None, None, None

    def handle_decl(self, decl):
        self.doctype = self.doctype or decl.lower().startswith("doctype html")

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang") or self.lang
        elif tag == "body":
            self._body = True
        elif tag == "title" and self.title is None:
            self._title = []
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if a.get("charset"):
                self.charset = a["charset"]
            if name and "content" in a:
                if name.startswith("og:"):
                    self.og.setdefault(name[3:], a["content"])
                elif name.startswith("twitter:"):
                    self.tw.setdefault(name[8:], a["content"])
                else:
                    self.meta.setdefault(name, a["content"])
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel and a.get("href"):
                self.canonical.append(a["href"])
            elif "alternate" in rel and a.get("hreflang"):
                self.alternates.append({"hreflang": a["hreflang"], "href": a.get("href", "")})
            elif "icon" in rel or "apple-touch-icon" in rel:
                self.icons.append(a.get("href", ""))
        elif re.fullmatch(r"h[1-6]", tag):
            self._head = (int(tag[1]), [])
        elif tag == "img":
            self.images.append(a)
        elif tag == "a" and "href" in a:
            self._a = (a, [])
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._ld = []
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title" and self._title is not None:
            self.title, self._title = " ".join("".join(self._title).split()), None
        elif self._head and tag == f"h{self._head[0]}":
            self.headings.append({"level": self._head[0], "text": " ".join("".join(self._head[1]).split())[:90]})
            self._head = None
        elif tag == "a" and self._a:
            self.anchors.append((self._a[0], " ".join("".join(self._a[1]).split())[:60]))
            self._a = None
        elif tag == "script" and self._ld is not None:
            self.ld.append("".join(self._ld))
            self._ld = None

    def handle_data(self, data):
        if self._title is not None:
            self._title.append(data)
        if self._ld is not None:
            self._ld.append(data)
        if self._head:
            self._head[1].append(data)
        if self._a:
            self._a[1].append(data)
        if self._body and not self._skip:
            self.words += len(data.split())

    def facts(self, base):
        host = host_of(base)
        imgs = self.images
        links = {"internal": 0, "external": 0, "nofollow": 0, "empty": 0, "generic": 0, "hrefs": []}
        for a, text in self.anchors:
            href = a.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:")):
                links["empty"] += 1
                continue
            if href.startswith(("mailto:", "tel:")):
                continue
            full = urllib.parse.urljoin(base, href)
            if not full.startswith(("http://", "https://")):
                continue
            if "nofollow" in a.get("rel", "").lower():
                links["nofollow"] += 1
            if same_site(host_of(full), host):
                links["internal"] += 1
                links["hrefs"].append(full.split("#")[0])
            else:
                links["external"] += 1
            if _GENERIC_ANCHOR.match(text):
                links["generic"] += 1
        return {"title": self.title, "lang": self.lang, "charset": self.charset, "doctype": self.doctype, "meta": self.meta, "og": self.og,
                "twitter": self.tw, "canonical": [urllib.parse.urljoin(base, c) for c in self.canonical], "alternates": self.alternates,
                "icons": self.icons, "headings": self.headings, "links": links, "words": self.words,
                "images": {"total": len(imgs), "missing_alt": sum("alt" not in i for i in imgs), "empty_alt": sum(i.get("alt") == "" for i in imgs),
                           "lazy": sum(i.get("loading", "").lower() == "lazy" for i in imgs),
                           "unsized": sum(not (i.get("width") and i.get("height")) for i in imgs),
                           "modern": sum(bool(re.search(r"\.(?:webp|avif|svg)(?:\?|$)", i.get("src", ""), re.I)) for i in imgs)},
                "jsonld": [_ld(t) for t in self.ld]}


def _ld(text):
    """One JSON-LD block: its @type names, and whether it parses."""
    try:
        data = json.loads(text)
    except ValueError:
        return {"types": [], "ok": False}
    types, stack = [], [data]
    while stack:
        x = stack.pop(0)  # in document order, so the types read the way the page lists them
        if isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, dict):
            t = x.get("@type")
            types += [t] if isinstance(t, str) else [s for s in (t or []) if isinstance(s, str)]
            stack.extend(x.get("@graph") or [])
    return {"types": types, "ok": True}


def parse_html(html, base):
    p = _Page()
    try:
        p.feed(html[:MAX_HTML_CHARS])
        p.close()
    except Exception:  # malformed markup: what was read so far is still a fact
        pass
    return p.facts(base)


# -- fetching ------------------------------------------------------------------------------------------------------------------
async def fetch(client, url, method="GET"):
    """Follow redirects by hand, so the chain is known. -> (chain [{url, status, location}], last response or None, error)."""
    chain, r = [], None
    for _ in range(MAX_HOPS + 1):
        try:
            r = await client.request(method, url)
        except httpx.HTTPError as e:
            return chain, None, f"{type(e).__name__}: {str(e)[:120]}"
        chain.append({"url": url, "status": r.status_code, "location": r.headers.get("location")})
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = urllib.parse.urljoin(url, r.headers["location"])
            continue
        break
    return chain, r, None


def parse_robots(text):
    """{"blocks_all", "sitemaps", "rules", "agents": {name: "allowed" | "blocked" | "partly"}} from a robots.txt body: the crawlers it names."""
    groups, cur, sitemaps = [], None, []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if cur is None or cur["rules"]:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
            cur["agents"].append(v)
        elif k in ("allow", "disallow") and cur is not None:
            cur["rules"].append((k, v))
        elif k == "sitemap" and v:
            sitemaps.append(v)

    def verdict(name):
        g = next((g for g in groups if name.lower() in (a.lower() for a in g["agents"])), None) or next((g for g in groups if "*" in g["agents"]), None)
        if g is None:
            return "allowed"
        blocked = [v for k, v in g["rules"] if k == "disallow" and v]
        if "/" in blocked:
            return "blocked" if not any(k == "allow" and v == "/" for k, v in g["rules"]) else "allowed"
        return "partly" if blocked else "allowed"
    named = [a for g in groups for a in g["agents"] if a != "*"]
    return {"blocks_all": verdict("*") == "blocked", "sitemaps": sitemaps, "rules": sum(len(g["rules"]) for g in groups),
            "agents": {a: verdict(a) for a in dict.fromkeys(named)}}


def parse_sitemap(text):
    """(kind "urlset" | "index", [loc…], lastmod count) from sitemap XML; ValueError when it isn't one."""
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError as e:
        raise ValueError(f"not valid XML ({e})")
    kind = root.tag.rsplit("}", 1)[-1]
    if kind not in ("urlset", "sitemapindex"):
        raise ValueError(f"the root element is <{kind}>, not <urlset>")
    locs = [(e.text or "").strip() for e in root.findall(".//{*}loc") if (e.text or "").strip()]  # (iter() ignores the {*} wildcard)
    return ("index" if kind == "sitemapindex" else "urlset"), locs, len(root.findall(".//{*}lastmod"))


async def _sitemap(client, robots, origin):
    """Find and read the sitemap: the one robots.txt names, else /sitemap.xml. An index reads its first two children."""
    urls = (robots or {}).get("sitemaps") or [origin + "/sitemap.xml"]
    chain, r, err = await fetch(client, urls[0])
    out = {"url": urls[0], "status": r.status_code if r is not None else None, "error": err, "urls": [], "lastmod": 0, "index": False,
           "named_in_robots": bool((robots or {}).get("sitemaps")), "sample": []}
    if r is None or r.status_code != 200:
        return out
    try:
        kind, locs, lastmod = parse_sitemap(r.text)
    except ValueError as e:
        return {**out, "error": str(e)}
    out.update(index=kind == "index", lastmod=lastmod)
    if kind == "index":
        out["children"] = len(locs)
        for child in locs[:2]:
            _, cr, _ = await fetch(client, child)
            if cr is not None and cr.status_code == 200:
                try:
                    _, cl, lm = parse_sitemap(cr.text)
                    out["lastmod"] += lm
                    out["urls"] += cl
                except ValueError:
                    pass
    else:
        out["urls"] = locs
    return out


async def _status_of(client, url):
    """HEAD, else GET when the server doesn't answer HEAD: {"url", "status", "location"} without following the redirect."""
    try:
        r = await client.head(url)
        if r.status_code in (403, 405, 501):
            r = await client.get(url)
    except httpx.HTTPError as e:
        return {"url": url, "status": None, "error": type(e).__name__}
    return {"url": url, "status": r.status_code, "location": r.headers.get("location")}


async def _gather_limited(coros, limit=6):
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            return await c
    return await asyncio.gather(*(run(c) for c in coros))


def summarize_requests(rows, host):
    """Raw request rows from Chrome -> the page's weight: count, bytes, requests to other sites, the heaviest file, the Cache-Control
    values of the site's own static files. None when nothing was measured."""
    if not rows:
        return None
    third = [r for r in rows if not same_site(host_of(r["url"]), host)]
    static = [r for r in rows if r.get("type") in ("script", "stylesheet", "image", "font", "media") and same_site(host_of(r["url"]), host)]
    top = max(rows, key=lambda r: r["bytes"])
    name = urllib.parse.urlsplit(top["url"]).path.rsplit("/", 1)[-1][:40] or top["url"][:40]
    return {"count": len(rows), "bytes": sum(r["bytes"] for r in rows), "third_party": len(third),
            "third_hosts": list(dict.fromkeys(host_of(r["url"]) for r in third))[:8], "own_static_files": len(static),
            "own_static_cache_control": dict(Counter((r.get("cache") or "none") for r in static).most_common(4)),
            "encodings": dict(Counter((r.get("encoding") or "none") for r in rows if r.get("type") in ("document", "script", "stylesheet")).most_common(4)),
            "heaviest": f"{name} ({top['bytes'] / 1000:.0f} kB)"}


# -- gathering -------------------------------------------------------------------------------------------------------------------
async def gather(chrome, url, search=None, progress=None):
    """Load `url` and everything around it. -> {"ok", "text", "facts"}. `chrome`: a core.chrome.Chrome (or anything with a
    `load_measured(url)` coroutine); `progress(text)` says what phase it is in."""
    say = progress or (lambda t: None)
    try:
        target = normalize(url)
    except ValueError as e:
        return {"ok": False, "text": str(e), "verified": False}
    try:
        return await asyncio.wait_for(_gather(chrome, target, search, say), GATHER_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "text": f"Looking at {host_of(target)} took longer than {GATHER_TIMEOUT_S:.0f} s, so I stopped.", "verified": False}


async def _gather(chrome, target, search, say):
    host = host_of(target)
    say(f"Loading {host} in Monday's Chrome")
    page = await chrome.load_measured(target)
    dom, perf = page.get("dom"), page.get("perf")
    final = page.get("final_url") or target
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(final))
    alt = host[4:] if host.startswith("www.") else "www." + host
    variants = [f"{s}://{h}/" for s in ("http", "https") for h in (host, alt)]
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Encoding": "gzip, deflate"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, headers=headers, follow_redirects=False, verify=_tls()) as client:
        say("Reading the page as a crawler without JavaScript does, robots.txt, and the other spellings of the address")
        raw_c, robots_c, soft_c, *var_c = await asyncio.gather(
            fetch(client, final), fetch(client, origin + "/robots.txt"), fetch(client, f"{origin}/monday-probe-{secrets.token_hex(4)}"),
            *(fetch(client, v) for v in variants))
        raw_chain, raw_r, raw_err = raw_c
        if raw_r is None and not dom:
            return {"ok": False, "verified": False, "text": f"I couldn't reach {host}: {page.get('text') or raw_err}"}
        rr = robots_c[1]
        if rr is not None and rr.status_code == 200 and "html" not in rr.headers.get("content-type", "").lower():
            robots = {**parse_robots(rr.text), "status": 200, "text": rr.text[:1500]}
        else:
            robots = {"status": rr.status_code if rr is not None else None, "sitemaps": [], "agents": {}, "blocks_all": False, "text": ""}
        say("Reading the sitemap and a sample of its addresses and of the page's own links")
        sitemap = await _sitemap(client, robots if robots.get("status") == 200 else None, origin)
        raw_facts = parse_html(raw_r.text, final) if raw_r is not None and "html" in raw_r.headers.get("content-type", "html").lower() else None
        page_links = ((dom or raw_facts or {}).get("links") or {}).get("hrefs") or []
        sample_links = list(dict.fromkeys(l for l in page_links if not same_page(l, final)))[:SAMPLE_LINKS]
        sample_map = list(dict.fromkeys(sitemap["urls"]))[:SAMPLE_SITEMAP]
        checked = await _gather_limited([_status_of(client, u) for u in sample_links + sample_map])
    sitemap["sample"] = checked[len(sample_links):]
    f = {"asked": target, "final_url": final, "host": host_of(final), "status": page.get("status") or (raw_r.status_code if raw_r is not None else None),
         "chain": page.get("chain") or raw_chain, "headers": page.get("headers") or (dict(raw_r.headers) if raw_r is not None else {}),
         "ttfb_ms": (perf or {}).get("ttfb"), "dom": dom, "raw": raw_facts, "perf": perf,
         "requests": summarize_requests(page.get("requests"), host_of(final)), "protocol": page.get("protocol"), "console": page.get("console") or [],
         "render_error": None if dom else page.get("text"), "robots": robots, "sitemap": sitemap, "search": search,
         "variants": [{"url": v, "chain": c[0], "error": c[2]} for v, c in zip(variants, var_c)],
         "made_up_address": {"first_status": soft_c[0][0]["status"] if soft_c[0] else None, "final_status": soft_c[1].status_code if soft_c[1] is not None else None,
                             "location": soft_c[0][0].get("location") if soft_c[0] else None},
         "links": checked[:len(sample_links)], "html_bytes": len(raw_r.content) if raw_r is not None else None, "when": time.time()}
    return {"ok": True, "verified": True, "url": final, "title": (dom or raw_facts or {}).get("title"), "facts": f, "text": f"Measured {host_of(final)}."}


# -- the facts, compact, for a prompt ------------------------------------------------------------------------------------------------
def _view(p, cap):
    """The facts of one reading of the page (rendered, or without JavaScript), trimmed."""
    if not p:
        return None
    lk, im = p.get("links") or {}, p.get("images") or {}
    return {"title": p.get("title"), "meta_description": (p.get("meta") or {}).get("description"), "robots_meta": (p.get("meta") or {}).get("robots"),
            "canonical": p.get("canonical"), "lang": p.get("lang"), "viewport": (p.get("meta") or {}).get("viewport"),
            "headings": [[h["level"], h["text"]] for h in (p.get("headings") or [])[:cap]], "words": p.get("words"),
            "images": {k: v for k, v in im.items() if v}, "links": {k: v for k, v in lk.items() if k != "hrefs" and v},
            "structured_data_types": [t for b in p.get("jsonld") or [] for t in b.get("types", [])] or ([f"{len(p['jsonld'])} block(s), unparsed"] if p.get("jsonld") else []),
            "structured_data_errors": sum(not b.get("ok") for b in p.get("jsonld") or []) or None,
            "open_graph": sorted((p.get("og") or {}).keys()), "twitter_card": (p.get("twitter") or {}).get("card"), "favicon": bool(p.get("icons")),
            "hreflang": [a["hreflang"] for a in p.get("alternates") or []][:8] or None}


def differences(f):
    """Neutral side-by-side facts a reader would otherwise have to dig out of the JSON: what a crawler without JavaScript gets against the
    rendered page, where the canonical tag points against the address served, which hosts the sitemap lists against the host that
    answers, how the sampled addresses answered, whether the site is among the search results Monday read. Equalities and counts only."""
    dom, raw, out = f.get("dom"), f.get("raw"), {}
    final = f.get("final_url") or ""
    if dom and raw:
        n = lambda p, k: len([h for h in p.get("headings") or [] if k is None or h["level"] == k])  # noqa: E731
        lk = lambda p: (p.get("links") or {}).get("internal", 0) + (p.get("links") or {}).get("external", 0)  # noqa: E731
        out["without_javascript_then_rendered"] = {
            "words": [raw.get("words"), dom.get("words")], "headings": [n(raw, None), n(dom, None)], "h1_headings": [n(raw, 1), n(dom, 1)],
            "links": [lk(raw), lk(dom)], "images": [(raw.get("images") or {}).get("total"), (dom.get("images") or {}).get("total")],
            "has_title": [bool(raw.get("title")), bool(dom.get("title"))], "has_meta_description": [bool((raw.get("meta") or {}).get("description")), bool((dom.get("meta") or {}).get("description"))],
            "structured_data_blocks": [len(raw.get("jsonld") or []), len(dom.get("jsonld") or [])]}
    canon = (dom or raw or {}).get("canonical") or []
    out["canonical_tags"] = {"values": canon, "each_equals_the_address_served": [same_page(c, final) for c in canon]}
    sm = f.get("sitemap") or {}
    if sm.get("urls"):
        counts = Counter(str(x.get("status")) for x in sm.get("sample") or [])
        out["sitemap"] = {"hosts_listed": sorted({host_of(u) for u in sm["urls"][:200]}), "host_serving_the_page": f.get("host"), "sampled_answers_by_status": dict(counts)}
    lk_counts = Counter(str(x.get("status")) for x in f.get("links") or [])
    if lk_counts:
        out["sampled_internal_links_answers_by_status"] = dict(lk_counts)
    sr = f.get("search") or {}
    if sr.get("results"):
        rank = next((i for i, r in enumerate(sr["results"], 1) if same_site(host_of(r.get("url", "")), f.get("host"))), None)
        out["site_among_search_results_monday_read"] = {"query": sr.get("query"), "results_read": len(sr["results"]), "this_site_is_among_them": rank is not None,
                                                        "its_position_among_them": rank}
    return out


def brief(f, limit=BRIEF_CHARS):
    """The facts as compact JSON for a prompt: no judgments, just what was measured and where it came from."""
    for cap in (1.0, 0.5, 0.25):
        n = lambda k: max(3, int(k * cap))  # noqa: E731
        sm, rb, h = f.get("sitemap") or {}, f.get("robots") or {}, f.get("headers") or {}
        pv = f.get("perf") or {}
        out = {
            "address": {"asked": f.get("asked"), "final": f.get("final_url"), "status": f.get("status"), "redirect_chain": [[x.get("url"), x.get("status")] for x in f.get("chain") or []],
                        "other_spellings": {v["url"]: ("error: " + v["error"]) if v.get("error") else f"ends at {(v['chain'][-1].get('location') or v['chain'][-1]['url'])} after "
                                                                                                     f"{len(v['chain']) - 1} redirect(s), statuses {[x['status'] for x in v['chain']]}"
                                            for v in f.get("variants") or [] if v.get("chain") or v.get("error")}},
            "response_headers": {k: h[k] for k in ("content-type", "content-encoding", "cache-control", "x-robots-tag", "strict-transport-security", "server", "link") if k in h},
            "protocol": f.get("protocol"), "html_bytes_without_javascript": f.get("html_bytes"),
            "differences_at_a_glance": differences(f),
            "rendered_page": _view(f.get("dom"), n(24)), "without_javascript": _view(f.get("raw"), n(12)),
            "timings_ms_one_lab_load": {k: v for k, v in {"time_to_first_byte": pv.get("ttfb"), "first_contentful_paint": pv.get("fcp"), "largest_contentful_paint": pv.get("lcp"),
                                                          "dom_content_loaded": pv.get("dcl"), "load": pv.get("load")}.items() if v is not None} | (
                {"cumulative_layout_shift": pv["cls"]} if pv.get("cls") is not None else {}),
            "requests": f.get("requests"),
            "robots_txt": {"status": rb.get("status"), "text": (rb.get("text") or "")[:int(1200 * cap)], "crawlers_named": rb.get("agents") or None},
            "sitemap": {"url": sm.get("url"), "status": sm.get("status"), "error": sm.get("error"), "named_in_robots_txt": sm.get("named_in_robots"), "is_index": sm.get("index"),
                        "address_count": len(sm.get("urls") or []), "lastmod_entries": sm.get("lastmod"), "first_addresses": (sm.get("urls") or [])[:n(6)],
                        "sampled_statuses": [[x["url"], x.get("status"), x.get("location")] for x in sm.get("sample") or []]},
            "made_up_address_probe": f.get("made_up_address"),
            "sampled_internal_links": [[x["url"], x.get("status"), x.get("location")] for x in f.get("links") or []][:n(12)],
            "console_errors": [e["text"][:120] for e in f.get("console") or [] if e.get("kind") in ("error", "exception")][:5],
            "page_text_excerpt": ((f.get("dom") or {}).get("text_excerpt") or "")[:int(1500 * cap)] or None,
            "not_measured": "backlinks, keyword rankings, search-console data, real-user performance: nothing here covers them",
        }
        if f.get("search"):
            out["search_monday_ran"] = {"query": f["search"].get("query"), "engine": f["search"].get("engine"), "results_read": [[r.get("title"), r.get("url")] for r in f["search"].get("results", [])][:10]}
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(text) <= limit:
            return text
    return text[:limit]


# -- python -m core.webfacts -------------------------------------------------------------------------------------------------------
def _selfcheck():
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Handmade garba circle costumes for Navratri | Garba Circle</title>
    <meta name="description" content="Rent or buy handmade garba costumes for Navratri with free alterations and delivery across India. See the new collection.">
    <meta name="viewport" content="width=device-width, initial-scale=1"><link rel="canonical" href="https://example.com/">
    <link rel="icon" href="/f.ico"><meta property="og:title" content="t"><meta property="og:description" content="d"><meta property="og:image" content="i">
    <meta name="twitter:card" content="summary"><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization"},{"@type":"WebSite"}]}</script>
    <script>var x = "<h1>not a heading</h1>";</script></head><body><h1>Garba costumes</h1><h2>Rent</h2><h3>Kids</h3>
    <img src="a.webp" alt="a" width="1" height="1"><img src="b.jpg"><a href="/rent">Rent a costume</a><a href="/buy">Buy</a><a href="https://other.org/x" rel="nofollow">x</a>
    <a href="#">click here</a><noscript>hidden words here</noscript><p>""" + "word " * 320 + """</p></body></html>"""
    p = parse_html(page, "https://example.com/")
    assert p["title"].startswith("Handmade") and p["lang"] == "en" and p["doctype"] and p["canonical"] == ["https://example.com/"]
    assert [h["level"] for h in p["headings"]] == [1, 2, 3], "a heading inside a script string isn't a heading"
    assert p["images"] == {"total": 2, "missing_alt": 1, "empty_alt": 0, "lazy": 0, "unsized": 1, "modern": 1}, p["images"]
    assert p["links"]["internal"] == 2 and p["links"]["external"] == 1 and p["links"]["nofollow"] == 1 and p["links"]["empty"] == 1 and p["links"]["generic"] == 0
    assert p["jsonld"] == [{"types": ["Organization", "WebSite"], "ok": True}] and p["words"] == 331, p["words"]  # 320 + the visible words; not <noscript>'s or <script>'s
    assert p["og"]["image"] == "i" and p["twitter"]["card"] == "summary" and p["icons"] == ["/f.ico"]
    rb = parse_robots("User-agent: *\nDisallow: /private\n\nUser-agent: GPTBot\nDisallow: /\n\nSitemap: https://e.com/s.xml")
    assert rb["sitemaps"] == ["https://e.com/s.xml"] and rb["agents"] == {"GPTBot": "blocked"} and not rb["blocks_all"]
    assert parse_robots("User-agent: *\nDisallow: /")["blocks_all"] and not parse_robots("User-agent: *\nAllow: /")["blocks_all"]
    kind, locs, lm = parse_sitemap('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://e.com/</loc><lastmod>2026-01-01</lastmod></url></urlset>')
    assert (kind, locs, lm) == ("urlset", ["https://e.com/"], 1)
    try:
        parse_sitemap("<html></html>")
        raise AssertionError("an HTML page is not a sitemap")
    except ValueError:
        pass
    assert normalize("garbacircle.in") == "https://garbacircle.in/" and find_url("the site garbacircle.in and more") == "garbacircle.in"
    assert find_url("https://x.com/a?b=1.") == "https://x.com/a?b=1" and find_url("no address here") is None
    assert same_page("https://a.com/x/", "https://a.com/x") and not same_page("http://a.com/", "https://a.com/") and same_site("www.a.com", "a.com")
    assert is_search_page("https://www.bing.com/search?q=x") and is_search_page("https://www.google.co.in/search?q=x") and not is_search_page("https://garbacircle.in/")
    rows = [{"url": "https://e.com/a.js", "type": "script", "bytes": 5000, "cache": "public, max-age=31536000, immutable", "encoding": "br"},
            {"url": "https://fonts.gstatic.com/f.woff2", "type": "font", "bytes": 20000, "cache": None, "encoding": None}]
    s = summarize_requests(rows, "e.com")
    assert s["count"] == 2 and s["third_party"] == 1 and s["own_static_files"] == 1 and s["heaviest"].startswith("f.woff2") and s["own_static_cache_control"] == {"public, max-age=31536000, immutable": 1}
    facts = {"asked": "https://e.com/", "final_url": "https://www.e.com/", "host": "www.e.com", "status": 200, "chain": [{"url": "https://e.com/", "status": 308}, {"url": "https://www.e.com/", "status": 200}],
             "headers": {"content-type": "text/html", "server": "x"}, "dom": p, "raw": {**p, "words": 0, "title": None, "headings": []}, "perf": {"ttfb": 200, "lcp": 1500, "cls": 0.01},
             "robots": {"status": 200, "text": "User-agent: *\nAllow: /", "agents": {}}, "sitemap": {"url": "https://e.com/sitemap.xml", "status": 200, "urls": ["https://e.com/"], "lastmod": 1, "sample": []},
             "variants": [{"url": "http://e.com/", "chain": [{"url": "http://e.com/", "status": 301, "location": "https://www.e.com/"}, {"url": "https://www.e.com/", "status": 200}]}],
             "links": [{"url": "https://e.com/rent", "status": 200}], "requests": s, "made_up_address": {"first_status": 404, "final_status": 404}}
    b = brief(facts)
    assert len(b) <= BRIEF_CHARS and json.loads(b)["rendered_page"]["title"].startswith("Handmade") and json.loads(b)["without_javascript"]["words"] == 0
    assert json.loads(b)["address"]["other_spellings"]["http://e.com/"].startswith("ends at https://www.e.com/ after 1 redirect")
    d = json.loads(b)["differences_at_a_glance"]
    assert d["without_javascript_then_rendered"]["words"] == [0, 331] and d["without_javascript_then_rendered"]["has_title"] == [False, True], d
    assert d["canonical_tags"]["each_equals_the_address_served"] == [False] and d["sitemap"]["hosts_listed"] == ["e.com"] and d["sitemap"]["host_serving_the_page"] == "www.e.com", d
    assert len(brief({**facts, "dom": {**p, "headings": [{"level": 2, "text": "x" * 80}] * 500}}, limit=3000)) <= 3000, "a huge page is still cut to the limit"
    print(f"webfacts self-check: OK (brief {len(b)} chars)")


async def _live():
    """A local site with a good page and a bad one, looked at through a real headless Chrome. Needs Playwright and Chrome."""
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from .chrome import Chrome

    base = {"port": 0}
    body = lambda t: t.replace("{P}", f"http://127.0.0.1:{base['port']}")  # noqa: E731
    pages = {
        "/": ("text/html", """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Garba costumes for Navratri, rent or buy | Test</title>
            <meta name="description" content="Handmade garba costumes for Navratri with free alterations and delivery across India. Rent or buy online today.">
            <meta name="viewport" content="width=device-width, initial-scale=1"><link rel="canonical" href="{P}/"><link rel="icon" href="/f.ico">
            <script type="application/ld+json">{"@type":"Organization","name":"t"}</script></head><body><h1>Garba costumes</h1><a href="/about">About</a>
            <a href="/gone">Gone</a><a href="/rent">Rent</a><p>""" + "word " * 350 + "</p></body></html>"),
        "/about": ("text/html", "<!doctype html><title>About</title><h1>About</h1>"), "/rent": ("text/html", "<!doctype html><title>Rent</title><h1>Rent</h1>"),
        "/bad": ("text/html", "<!doctype html><html><head><meta name='robots' content='noindex'></head><body><div id=app></div><script>document.getElementById('app').innerHTML='<h1>Hi</h1>'</script></body></html>"),
        "/robots.txt": ("text/plain", "User-agent: *\nAllow: /\nSitemap: {P}/sitemap.xml\n"),
        "/sitemap.xml": ("application/xml", '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{P}/</loc></url><url><loc>{P}/about</loc></url></urlset>')}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            ctype, text = pages.get(self.path.split("?")[0], (None, None))
            self.send_response(200 if ctype else 404)
            self.send_header("Content-Type", ctype or "text/html")
            data = body(text or "<h1>not found</h1>").encode()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command == "GET":
                self.wfile.write(data)
        do_HEAD = do_GET

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    base["port"] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    chrome = Chrome(headless=True, profile=tempfile.mkdtemp())
    try:
        good = await gather(chrome, f"http://127.0.0.1:{base['port']}/")
        bad = await gather(chrome, f"http://127.0.0.1:{base['port']}/bad")
    finally:
        await chrome.close()
        srv.shutdown()
    assert good["ok"] and bad["ok"], (good.get("text"), bad.get("text"))
    g, b = good["facts"], bad["facts"]
    assert g["status"] == 200 and g["dom"]["title"].startswith("Garba costumes") and g["raw"]["words"] >= 350 and g["robots"]["status"] == 200
    assert g["sitemap"]["status"] == 200 and len(g["sitemap"]["urls"]) == 2, g["sitemap"]
    assert g["made_up_address"]["final_status"] == 404 and g["perf"]["ttfb"] is not None, (g["made_up_address"], g["perf"])  # (a hidden headless tab may record no paint timings)
    assert any(l["status"] == 404 and l["url"].endswith("/gone") for l in g["links"]), g["links"]
    assert g["requests"]["count"] >= 1 and g["ttfb_ms"] is not None
    assert b["dom"]["headings"] == [{"level": 1, "text": "Hi"}] and b["raw"]["headings"] == [], "the h1 exists only once JavaScript has run"
    assert b["dom"]["meta"].get("robots") == "noindex" and not b["raw"]["title"]
    print(f"webfacts live check: OK (good page {len(brief(g))} chars of facts; lab {g['perf']})")


async def _cli(url):
    import tempfile

    from .chrome import Chrome
    chrome = Chrome(headless=True, profile=tempfile.mkdtemp())
    try:
        r = await gather(chrome, url, progress=lambda t: print(f"… {t}", file=sys.stderr))
    finally:
        await chrome.close()
    print(brief(r["facts"], 20000) if r["ok"] else f"FAILED: {r['text']}")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        asyncio.run(_cli(args[0]))
    else:
        _selfcheck()
        if "--live" in sys.argv:
            asyncio.run(_live())
