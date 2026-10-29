"""Web research over plain HTTP (2026-10-16): what a research answer is written from when no AI with its own web search answers.

Why: Monday's own Chrome searched for research until 2026-10-16, and engines treat an automated Chrome as a robot. Measured that day,
"the latest news about the GTA 6 release date": Google asked for a captcha and Bing answered with news homepages (Times of India,
Hindustan Times), so the answer said "no information". The same search over plain HTTP: DuckDuckGo's and Bing's pages listed Rockstar,
Xbox and Wikipedia in under a second. The owner's brief also says research launches no local Chromium. The Cloudflare route the brief
names is Browser Run (its REST `/markdown`: a page rendered in a browser of Cloudflare's, read as Markdown; the owner, 2026-10-20: "use
Cloudflare's agentic browser, the browser will be multiple like multi agent"): when CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are in
Monday\\.env (Integrations), each source page is read in its own remote browser, several at once (`cloud_browser`, `cf_markdown`); a page it
can't read, or no keys, falls back to the plain GET below. Nothing of it runs on this PC or touches the owner's desktop.

Since 2026-10-24 the search goes to Ollama's web search first, through the owner's own Ollama (signed in to ollama.com for its cloud
models): its results come with their pages' text. Measured that day: five searches at once answered in 2 s, each on topic, while
DuckDuckGo refused the third search in a row (HTTP 202, "anomaly") and Bing listed gift shops for "GIFT City AI data centre policy".
The engines below are what's left when it can't answer (Ollama down or signed out).

- `ollama_search(client, query)`: Ollama's web search, each result with its page's text.
- `results(client, query)`: DuckDuckGo's HTML search (real addresses behind its /l/?uddg= links), else Bing's results page.
- `headlines(client, query)`: Google News' RSS for the query, with each source and date ("the latest" needs dates).
- `page_text(html)`: what a reader sees of a page (its article or main part when it has one): no script, style, navigation, forms.
- `gather(question)`: the search, then the top results' own pages fetched together, numbered for the answer to cite.
- `gather_many(queries)`: several searches for one long request (Monday.search_text plans them), as one numbered list.
- `search_plan(text)`: a model's planned searches, one per line, as clean queries.
- `evidence(found)`: the numbered text the answer is written from.
Self-check: python -m core.research (offline, fixtures) · python -m core.research --live "question" (the real web: the evidence)
"""
import asyncio
import base64
import contextlib
import email.utils
import html
import re
import sys
import time
import urllib.parse
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

import httpx

from . import probes

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
SEARCH_S = 8.0          # one search engine's answer
FETCH_S = 8.0           # one source page
PAGE_MAX = 2_000_000    # bytes of a page that are read
SOURCES = 3             # pages read, from the top results
TRY = 6                 # results tried to find them (a video, a login wall, a PDF is skipped)
SOURCE_CHARS = 3200     # of a page's text, for the answer
SOURCE_MIN_CHARS = 300  # less is no source (a consent wall, an app shell)
HEADLINES = 4
OLLAMA_SEARCH = "/api/experimental/web_search"  # on the Ollama here (0.40); it searches as the account it is signed in to
OLLAMA_SEARCH_S = 20.0  # one search there, its pages' text included
QUERIES_MAX = 5         # searches planned for one request (gather_many)
PLAN_SOURCES = 2        # pages kept from each of them
NEWS_MANY = 8           # headlines kept across them
SKIP_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com")  # no text to read
# Words that ask what is happening now: the dated headlines join the pages. A year alone doesn't ("vector databases in 2026": headlines
# about AI stocks came with it, 2026-10-16)
RECENT = re.compile(r"\b(?:latest|news|today|tonight|yesterday|this\s+(?:week|month)|recent(?:ly)?|current(?:ly)?|update[sd]?|"
                    r"announce[sd]?|release\s+date|right\s+now|happening)\b", re.I)
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "nav", "header", "footer", "aside", "form", "button", "select", "iframe",
              "figure", "menu"}
_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section", "article", "main", "blockquote", "pre",
               "dd", "dt", "table", "ul", "ol"}


def result_url(href):
    """A search result's own address. Bing links its results through /ck/a?u=a1<base64> (a page that redirects by script), Google
    through /url?q=, DuckDuckGo through /l/?uddg=. Anything else as it is."""
    try:
        if (href or "").startswith("//"):
            href = "https:" + href
        p = urllib.parse.urlsplit(href or "")
        q, host = urllib.parse.parse_qs(p.query), (p.hostname or "").lower()
        if host.endswith("bing.com") and p.path.startswith("/ck/") and (q.get("u") or [""])[0].startswith("a1"):
            s = q["u"][0][2:]
            out = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()
        elif "google." in host and p.path == "/url":
            out = (q.get("q") or q.get("url") or [""])[0]
        elif host.endswith("duckduckgo.com") and p.path.startswith("/l/"):
            out = (q.get("uddg") or [""])[0]
        else:
            return href
        return out if out.startswith(("http://", "https://")) else href
    except (ValueError, UnicodeDecodeError):
        return href


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_ddg(body):
    """DuckDuckGo's HTML results page -> [{title, url, snippet}], its ads left out."""
    out = []
    for chunk in re.split(r'<div class="result ', body or "")[1:]:
        if "result--ad" in chunk[:200]:
            continue
        a = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S) or \
            re.search(r'href="([^"]+)"[^>]*class="result__a"[^>]*>(.*?)</a>', chunk, re.S)
        if not a:
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', chunk, re.S)
        url = result_url(html.unescape(a.group(1)))
        if url.startswith("http") and "duckduckgo.com/y.js" not in url:
            out.append({"title": _clean(a.group(2)), "url": url, "snippet": _clean(sn.group(1)) if sn else ""})
    return out


def parse_bing(body):
    """Bing's results page -> [{title, url, snippet}] (its organic results, li.b_algo)."""
    out = []
    for chunk in re.split(r'<li class="b_algo', body or "")[1:]:
        a = re.search(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", chunk, re.S)
        if not a:
            continue
        sn = re.search(r"<p[^>]*>(.*?)</p>", chunk, re.S)
        url = result_url(html.unescape(a.group(1)))
        if url.startswith("http"):
            out.append({"title": _clean(a.group(2)), "url": url, "snippet": _clean(sn.group(1)) if sn else ""})
    return out


def parse_news(xml_text):
    """Google News' RSS -> [{title, source, date, url}], newest first."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            when = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        if title and link:
            items.append({"title": title, "source": src, "date": when.strftime("%Y-%m-%d") if when else None, "url": link,
                          "ts": when.timestamp() if when else 0})
    return sorted(items, key=lambda x: -x["ts"])


class _Text(HTMLParser):
    """A page's visible text, in reading order: skipped parts left out, block elements on their own lines; an <article>'s or <main>'s
    text kept apart, since that is the page's own content when there is one."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.main, self.all, self.inner = 0, 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in ("article", "main"):
            self.main += 1
        if tag in _BLOCK_TAGS:
            self._put("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in ("article", "main") and self.main:
            self.main -= 1
        if tag in _BLOCK_TAGS:
            self._put("\n")

    def handle_data(self, data):
        if not self.skip and data.strip():
            self._put(data)

    def _put(self, s):
        self.all.append(s)
        if self.main:
            self.inner.append(s)


def page_text(markup):
    p = _Text()
    try:
        p.feed(markup or "")
        p.close()
    except Exception:  # broken markup: what was read so far
        pass

    def tidy(parts):
        lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in "".join(parts).split("\n"))
        return "\n".join(ln for ln in lines if len(ln) > 1)
    inner = tidy(p.inner)
    return inner if len(inner) >= SOURCE_MIN_CHARS else tidy(p.all)


def client():
    return httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, follow_redirects=True,
                             verify=probes.tls(), timeout=httpx.Timeout(SEARCH_S, connect=4))


async def results(c, query):
    """-> (engine, [{title, url, snippet}], why): DuckDuckGo first, else Bing; why says what failed when neither listed anything."""
    why = []
    for engine, url, parse in (("DuckDuckGo", "https://html.duckduckgo.com/html/?q={}", parse_ddg),
                               ("Bing", "https://www.bing.com/search?q={}&setlang=en", parse_bing)):
        try:
            r = await c.get(url.format(urllib.parse.quote_plus(query)))
            found = parse(r.text) if r.status_code == 200 else []
            if found:
                return engine, found, None
            why.append(f"{engine}: " + (f"HTTP {r.status_code}" if r.status_code != 200 else "no results it would show (a robot check?)"))
        except httpx.HTTPError as e:
            why.append(f"{engine}: {type(e).__name__}")
    return None, [], "; ".join(why)


async def ollama_search(c, query, n=TRY):
    """Ollama's web search through the Ollama here: -> [{title, url, text}] (the text is the page's, already read), [] when it can't
    answer (Ollama down, signed out of ollama.com, a version without it)."""
    try:
        r = await c.post(probes.OLLAMA + OLLAMA_SEARCH, json={"query": query, "max_results": n}, timeout=OLLAMA_SEARCH_S)
        rows = (r.json() or {}).get("results") or [] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    return [{"title": x.get("title") or x["url"], "url": x["url"], "text": (x.get("content") or "").strip()} for x in rows if x.get("url")]


async def headlines(c, query):
    try:
        r = await c.get(f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en")
        return parse_news(r.text)[:HEADLINES] if r.status_code == 200 else []
    except httpx.HTTPError:
        return []


CF_API = "https://api.cloudflare.com/client/v4/accounts/{}/browser-rendering/markdown"
CF_S = 20.0  # one page in a Cloudflare browser: it renders the page (what scripts draw included), so slower than a GET


def cloud_browser():
    """Cloudflare Browser Run: (account id, API token) from the environment or Monday\\.env, or None when either is missing (then research
    says so: pages read over plain HTTP). Keys are read here, never logged or returned to a page."""
    from . import jev
    vals = jev.read_env_file()
    acc, tok = jev.setting("CLOUDFLARE_ACCOUNT_ID", vals), jev.setting("CLOUDFLARE_API_TOKEN", vals)
    return (acc, tok) if acc and tok else None


async def cf_markdown(c, url, cloud):
    """A page as one of Cloudflare's browsers read it (Browser Run's /markdown: the rendered page as Markdown), or "" when it couldn't
    (no success, an HTTP error, a timeout). Each call is its own remote browser: a search's pages are read in several at once."""
    try:
        r = await c.post(CF_API.format(cloud[0]), json={"url": url}, headers={"Authorization": f"Bearer {cloud[1]}"}, timeout=CF_S)
        d = r.json() if r.status_code == 200 else {}
        return (d.get("result") or "") if isinstance(d, dict) and d.get("success") and isinstance(d.get("result"), str) else ""
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
        return ""


async def fetch_text(c, url, cloud=None):
    """A page's text, or "" (not HTML, too slow, an error page, nothing to read). With Cloudflare's browsers configured (`cloud`), the page as
    one of them rendered it, the plain GET only when that gave nothing."""
    if cloud:
        md = await cf_markdown(c, url, cloud)
        if len(md) >= SOURCE_MIN_CHARS:
            return md[:PAGE_MAX], url
    try:
        async with c.stream("GET", url, timeout=FETCH_S) as r:
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                return "", str(r.url)
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > PAGE_MAX:
                    break
            return page_text(body.decode(r.encoding or "utf-8", "replace")), str(r.url)
    except (httpx.HTTPError, asyncio.TimeoutError):
        return "", url


async def gather(query, recent=None, n=SOURCES, gate=None):
    """The search for `query`, its top results' pages read together, and for anything current the dated headlines. Ollama's web search
    first (its results come read); else an engine's results page, then those pages. gate: one engine search at a time (gather_many).
    -> {"query", "engine", "sources": [{n, title, url, text}], "news": [{n, title, source, date, url}], "results", "why"}"""
    recent = bool(RECENT.search(query)) if recent is None else recent
    cloud = cloud_browser()
    skip = lambda u: any(h in (urllib.parse.urlsplit(u).hostname or "") for h in SKIP_HOSTS)  # noqa: E731
    async with client() as c:
        hits, news = await asyncio.gather(ollama_search(c, query), headlines(c, query) if recent else asyncio.sleep(0, []))
        picks = [h for h in hits if not skip(h["url"])][:TRY]
        if picks:
            engine, why, how = "Ollama web search", None, "Ollama's web search"
            found = [{"title": h["title"], "url": h["url"], "snippet": h["text"][:200]} for h in picks]
            texts = [(h["text"], None) for h in picks]
        else:
            async with gate or contextlib.nullcontext():
                engine, found, why = await results(c, query)
            picks, how = [r for r in found if not skip(r["url"])][:TRY], "Cloudflare's browsers" if cloud else "plain HTTP"
            texts = await asyncio.gather(*(fetch_text(c, r["url"], cloud) for r in picks))
    sources = []
    for r, (text, landed) in zip(picks, texts):
        if len(text) >= SOURCE_MIN_CHARS and len(sources) < n:
            sources.append({"n": len(sources) + 1, "title": r["title"][:120], "url": landed or r["url"], "text": text[:SOURCE_CHARS]})
    news = [{**h, "n": len(sources) + i + 1} for i, h in enumerate(news)]
    if not sources and not news:
        why = why or "none of the results could be read"
    return {"query": query, "engine": engine, "sources": sources, "news": news, "results": found[:8], "why": why if not sources and not news else None,
            "browsers": how}


def search_plan(text, most=QUERIES_MAX):
    """A model's planned searches, one per line -> distinct clean queries (its numbering, bullets, quotes and thinking taken off)."""
    out = []
    for ln in re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).splitlines():
        q = re.sub(r"^\s*(?:[-*•]|\d+[.):])\s*", "", ln).strip(" \t\"'`“”*")
        if q and len(q) <= 150 and q.lower() not in (x.lower() for x in out):
            out.append(q)
    return out[:most]


def merge(founds):
    """Several searches' finds as one: each page once (the first search that read it) and numbered again, headlines once
    (NEWS_MANY at most), numbered after the pages."""
    sources, news = [], []
    for s in (s for f in founds for s in f["sources"]):
        if s["url"] not in {x["url"] for x in sources}:
            sources.append({**s, "n": len(sources) + 1})
    for h in (h for f in founds for h in f["news"]):
        if h["title"] not in {x["title"] for x in news}:
            news.append(h)
    news = [{**h, "n": len(sources) + i + 1} for i, h in enumerate(news[:NEWS_MANY])]
    one = lambda k: " / ".join(dict.fromkeys(f[k] for f in founds if f.get(k))) or None  # noqa: E731
    return {"query": " · ".join(f["query"] for f in founds), "engine": one("engine"), "sources": sources, "news": news,
            "results": [r for f in founds for r in f["results"]][:8], "browsers": one("browsers"),
            "why": None if sources or news else "; ".join(dict.fromkeys(f["why"] for f in founds if f["why"])) or "none of the results could be read"}


async def gather_many(queries, recent=None, n=PLAN_SOURCES):
    """Several searches for one request, run together, as one numbered list (merge). One query is plain gather. The engines' pages
    refuse searches at once (DuckDuckGo from the third in a row, 2026-10-24), so when Ollama's search can't answer they go one by one."""
    if len(queries) < 2:
        return await gather(queries[0], recent)
    gate = asyncio.Lock()
    return merge(await asyncio.gather(*(gather(q, recent, n, gate) for q in queries)))


def evidence(found):
    """The numbered text an answer is written from: each page read, then the headlines with their dates."""
    parts = [f"Web search for “{found['query']}” ({found['engine'] or 'no engine answered'}; pages read over {found.get('browsers') or 'plain HTTP'}), "
             f"read {time.strftime('%Y-%m-%d')}."]
    for s in found["sources"]:
        parts.append(f"[{s['n']}] {s['title']}\n{s['url']}\n{s['text']}")
    if found["news"]:
        parts.append("Latest headlines (Google News):\n" + "\n".join(
            f"[{h['n']}] {h['date'] or 'undated'} · {h['source'] or 'unknown source'}: {h['title']}" for h in found["news"]))
    others = [r for r in found["results"] if r["url"] not in {s["url"] for s in found["sources"]}][:4]
    if others:
        parts.append("Other results (titles and snippets only, not read; don't cite them):\n" + "\n".join(
            f"- {r['title']}: {r['snippet'][:200]}" for r in others))
    return "\n\n".join(parts)


def cited(found):
    """Every numbered source as the owner sees it under the answer: [n] Title: address."""
    rows = [(s["n"], s["title"], s["url"], "page") for s in found["sources"]] + \
        [(h["n"], f"{h['source']}, {h['date']}: {h['title']}" if h["date"] else f"{h['source']}: {h['title']}", h["url"], "headline")
         for h in found["news"]]
    return [{"n": n, "title": t, "url": u, "kind": k} for n, t, u, k in rows]


if __name__ == "__main__":
    if "--live" in sys.argv:  # python -m core.research --live "question": the real web
        q = sys.argv[sys.argv.index("--live") + 1]
        t0 = time.monotonic()
        f = asyncio.run(gather(q))
        print(evidence(f)[:6000])
        print(f"\n{len(f['sources'])} pages, {len(f['news'])} headlines via {f['engine']} in {time.monotonic() - t0:.1f}s; why: {f['why']}")
        sys.exit(0)
    b64 = base64.urlsafe_b64encode(b"https://www.rockstargames.com/VI").decode().rstrip("=")
    assert result_url(f"https://www.bing.com/ck/a?!&&p=abc&u=a1{b64}&ntb=1") == "https://www.rockstargames.com/VI"
    assert result_url("https://www.google.com/url?q=https://example.org/a%3Fb&sa=U") == "https://example.org/a?b"
    assert result_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FX&rut=1") == "https://en.wikipedia.org/wiki/X"
    assert result_url("https://www.bing.com/ck/a?u=a1bm90IGEgdXJs") == "https://www.bing.com/ck/a?u=a1bm90IGEgdXJs", "decodes to no address"
    ddg = ('<div class="result results_links result--ad "><a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad">Buy GTA</a></div>'
           '<div class="result results_links results_links_deep web-result "><h2 class="result__title"><a rel="nofollow" class="result__a" '
           'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.rockstargames.com%2FVI&amp;rut=x">Grand Theft Auto VI &amp; you</a></h2>'
           '<a class="result__snippet" href="x">Coming <b>November 19, 2026</b>.</a></div>')
    assert parse_ddg(ddg) == [{"title": "Grand Theft Auto VI & you", "url": "https://www.rockstargames.com/VI", "snippet": "Coming November 19, 2026 ."}], \
        parse_ddg(ddg)
    bing = (f'<li class="b_algo" data-x="1"><h2><a href="https://www.bing.com/ck/a?!&amp;&amp;p=1&amp;u=a1{b64}&amp;ntb=1">GTA VI</a></h2>'
            '<div class="b_caption"><p>Launches <strong>Nov 19</strong></p></div></li>')
    assert parse_bing(bing) == [{"title": "GTA VI", "url": "https://www.rockstargames.com/VI", "snippet": "Launches Nov 19"}], parse_bing(bing)
    rss = ('<rss><channel><item><title>Old one - Forbes</title><link>https://news.google.com/a</link><pubDate>Mon, 12 Oct 2026 10:00:00 GMT'
           '</pubDate><source url="https://forbes.com">Forbes</source></item><item><title>New one - IGN</title><link>https://news.google.com/b'
           '</link><pubDate>Thu, 15 Oct 2026 09:00:00 GMT</pubDate><source url="https://ign.com">IGN</source></item></channel></rss>')
    assert [(x["title"], x["source"], x["date"]) for x in parse_news(rss)] == [("New one", "IGN", "2026-10-15"), ("Old one", "Forbes", "2026-10-12")]
    page = ("<html><head><style>x{}</style><script>var a=1</script></head><body><nav>Home | News</nav><header>Site</header>"
            "<article><h1>GTA 6 date</h1><p>" + "Rockstar says November 19, 2026. " * 12 + "</p><form>Sign up</form></article>"
            "<footer>(c) site</footer></body></html>")
    t = page_text(page)
    assert t.startswith("GTA 6 date\nRockstar says") and "var a" not in t and "Home | News" not in t and "Sign up" not in t and "(c)" not in t, t
    assert page_text("<body><p>" + "words " * 80 + "</p><nav>menu</nav></body>").startswith("words words"), "no article: the body, without its menu"
    assert RECENT.search("the latest news about GTA 6") and RECENT.search("GTA 6 release date") and not RECENT.search("what is a monad")
    assert not RECENT.search("compare the strongest open source vector databases in 2026") and not RECENT.search("new york pizza")
    f = {"query": "gta 6 release date", "engine": "DuckDuckGo", "results": [{"title": "Other", "url": "https://o.example", "snippet": "s"}],
         "sources": [{"n": 1, "title": "Rockstar", "url": "https://www.rockstargames.com/VI", "text": "November 19, 2026"}],
         "news": [{"n": 2, "title": "Trailer out", "source": "IGN", "date": "2026-10-15", "url": "https://news.google.com/b"}]}
    ev = evidence(f)
    assert "[1] Rockstar\nhttps://www.rockstargames.com/VI\nNovember 19, 2026" in ev and "[2] 2026-10-15 · IGN: Trailer out" in ev and "- Other: s" in ev
    assert cited(f) == [{"n": 1, "title": "Rockstar", "url": "https://www.rockstargames.com/VI", "kind": "page"},
                        {"n": 2, "title": "IGN, 2026-10-15: Trailer out", "url": "https://news.google.com/b", "kind": "headline"}]

    # Cloudflare's browsers (offline: a stand-in client): a page read as Markdown, nothing on a failed call, the plain GET without keys
    class _R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    class _C:
        def __init__(self, *answers):
            self.answers, self.calls = list(answers), []

        async def post(self, url, json=None, headers=None, timeout=None):
            self.calls.append((url, json, headers))
            return self.answers.pop(0)
    cl = _C(_R(200, {"success": True, "result": "# Rockstar\n\nNovember 19, 2026. " * 20}), _R(200, {"success": False, "errors": [{"code": 2}]}),
            _R(429, None))
    assert asyncio.run(cf_markdown(cl, "https://www.rockstargames.com/VI", ("acc1", "tok1"))).startswith("# Rockstar") and \
        cl.calls[0][0] == CF_API.format("acc1") and cl.calls[0][1] == {"url": "https://www.rockstargames.com/VI"} and \
        cl.calls[0][2] == {"Authorization": "Bearer tok1"}, cl.calls
    assert asyncio.run(cf_markdown(cl, "https://x.example", ("acc1", "tok1"))) == "" and asyncio.run(cf_markdown(cl, "https://x.example", ("a", "t"))) == ""
    assert cloud_browser() is None or len(cloud_browser()) == 2, "keys from .env, never made up"

    # Ollama's web search (a stand-in client): its results with their pages' text; nothing when it refuses or isn't there
    cl = _C(_R(200, {"results": [{"title": "BharatGen", "url": "https://bharatgen.com/", "content": " Param-1, 2.9B. "}, {"title": "no address"}]}),
            _R(404, None), _R(401, {"error": "unauthorized"}))
    assert asyncio.run(ollama_search(cl, "BharatGen")) == [{"title": "BharatGen", "url": "https://bharatgen.com/", "text": "Param-1, 2.9B."}]
    assert cl.calls[0][0] == probes.OLLAMA + OLLAMA_SEARCH and cl.calls[0][1] == {"query": "BharatGen", "max_results": TRY}, cl.calls
    assert asyncio.run(ollama_search(cl, "x")) == [] and asyncio.run(ollama_search(cl, "x")) == []
    # a long request's searches, as a model planned them, and their finds as one numbered list
    assert search_plan("<think>hmm</think>\n1. BharatGen Param-1 launch\n- IndiaAI Mission GPUs 2026\n\n\"GIFT City AI data centre\"\n"
                       "2) bharatgen param-1 launch\n* " + "x" * 200) == ["BharatGen Param-1 launch", "IndiaAI Mission GPUs 2026", "GIFT City AI data centre"]
    assert search_plan(None) == [] and len(search_plan("\n".join(f"q{i}" for i in range(9)))) == QUERIES_MAX
    a = {"query": "a", "engine": "Ollama web search", "browsers": "Ollama's web search", "why": None, "results": [{"title": "A", "url": "u1", "snippet": ""}],
         "sources": [{"n": 1, "title": "A", "url": "u1", "text": "ta"}, {"n": 2, "title": "B", "url": "u2", "text": "tb"}],
         "news": [{"n": 3, "title": "H1", "source": "S", "date": "2026-10-24", "url": "n1"}]}
    b = {**a, "query": "b", "results": [], "sources": [{"n": 1, "title": "B", "url": "u2", "text": "tb"}, {"n": 2, "title": "C", "url": "u3", "text": "tc"}],
         "news": [{"n": 3, "title": "H1", "source": "S", "date": "2026-10-24", "url": "n1"}, {"n": 4, "title": "H2", "source": "S", "date": None, "url": "n2"}]}
    m = merge([a, b])
    assert [(s["n"], s["url"]) for s in m["sources"]] == [(1, "u1"), (2, "u2"), (3, "u3")] and [(h["n"], h["title"]) for h in m["news"]] == [(4, "H1"), (5, "H2")]
    assert m["query"] == "a · b" and m["engine"] == "Ollama web search" and m["why"] is None and "[3] C\nu3\ntc" in evidence(m), m
    m = merge([{**a, "sources": [], "news": [], "why": "DuckDuckGo: HTTP 202"}, {**b, "sources": [], "news": [], "why": "DuckDuckGo: HTTP 202"}])
    assert m["why"] == "DuckDuckGo: HTTP 202" and not m["sources"], "nothing read: why, once"
    print("research self-check: OK")
