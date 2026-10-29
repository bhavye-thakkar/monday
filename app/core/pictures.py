"""Pictures for a document Monday writes (the owner, 2026-10-26: "search about gandhiji and prepare a doc for him … with images"): openly
licensed photos of its subject, found through Openverse (a search of Creative Commons and public-domain pictures), never from Wikipedia or
Wikimedia Commons (the owner's choice that day: Monday sends Wikipedia no contact, so it takes no pictures from it). Jev decides which of
the photos found show the subject. Each is kept with what it shows and whose it is: the caption under it credits it, as its licence asks.
Read only; the files land in a folder of Monday's own (data/tmp/pictures) and go once the document holds them.

    python -m core.pictures            self-check (a stand-in Openverse and Jev)
    python -m core.pictures "subject"  what the live search finds for a subject (prints, downloads nothing)
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import sys
import uuid
from pathlib import Path

API = "https://api.openverse.org/v1/images/"
HEADERS = {"User-Agent": "Monday/0.2 (a personal assistant on its owner's PC, making a document for them)"}
NOT_FROM = re.compile(r"wiki", re.I)  # a source or address of Wikipedia's or Wikimedia's: never (the owner's choice)
SEARCH_S, TRIES = 40, 2        # Openverse is slow at times (28.7 s, and a 500 after 52 s, 2026-10-26): asked twice, then no pictures
FETCH_S, MAX_BYTES = 20, 8_000_000
MIN_W, MIN_H = 400, 280        # a photo smaller than this isn't worth a place on a page
SHOWS_P = 0.6                  # Jev at least this sure that a photo shows the subject
JUDGE_S = 10.0                 # Jev's turn at judging the photos found (asked while the document is being written)
CAPTION_CHARS, CREDIT_CHARS = 110, 50
LICENCES = {"by": "CC BY", "by-sa": "CC BY-SA", "by-nd": "CC BY-ND", "by-nc": "CC BY-NC", "by-nc-sa": "CC BY-NC-SA", "by-nc-nd": "CC BY-NC-ND",
            "cc0": "CC0", "pdm": "Public Domain Mark"}  # (a photo under any other terms isn't taken)
FOLDER = Path(__file__).resolve().parent.parent / "data" / "tmp" / "pictures"


async def _get(url, params=None, timeout=SEARCH_S):
    import httpx
    async with httpx.AsyncClient(headers=HEADERS, timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.content


def _plain(text, most):
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip(" .;:")
    return t if len(t) <= most else t[:most - 1].rsplit(" ", 1)[0] + "…"


async def find(subject, get=_get):
    """Openverse's photos of `subject` in its order of relevance, none of Wikimedia's, doubles, small ones and unknown terms left out ->
    ([{"url", "title", "tags", "credit", "page", "licence_url"}], why none) ."""
    why = "Openverse didn't answer"
    for _ in range(TRIES):
        try:
            found = json.loads(await get(API, {"q": subject, "page_size": 20, "excluded_source": "wikimedia"}))
            break
        except Exception as e:  # (offline, slow, a server error: asked once more, then no pictures, said by the caller)
            why = f"Openverse didn't answer ({type(e).__name__})"
    else:
        return [], why
    out, seen = [], set()
    for x in found.get("results") or []:
        title = _plain(x.get("title"), CAPTION_CHARS) or "Untitled"
        if NOT_FROM.search(" ".join(str(x.get(k) or "") for k in ("source", "provider", "url", "foreign_landing_url"))) or x.get("mature") \
                or x.get("unstable__sensitivity") or (x.get("width") or 0) < MIN_W or (x.get("height") or 0) < MIN_H or not x.get("url") \
                or x.get("license") not in LICENCES or title.lower() in seen:
            continue
        seen.add(title.lower())
        licence = f"{LICENCES[x['license']]} {x.get('license_version') or ''}".strip()
        where = (x.get("source") or x.get("provider") or "Openverse").replace("_", " ").title()
        out.append({"url": x["url"], "title": title, "tags": [t.get("name") for t in x.get("tags") or [] if t.get("name")][:8],
                    "credit": f"Photo: {_plain(x.get('creator'), CREDIT_CHARS) or 'unknown'}, {licence}, via {where}",
                    "page": x.get("foreign_landing_url") or "", "licence_url": x.get("license_url") or ""})
    return out, None if out else f"Openverse has no photos of {subject} that could be used"


async def pick(subject, found, jev, n):
    """The photos of `found` that show `subject`, by Jev's reading of their titles and tags: the surest first, at most `n` ->
    (photos, why none)."""
    from typesafe_sdk import Noul
    state = {"subject": subject} | {f"photo_{i}": {"title": p["title"], "tags": p["tags"]} for i, p in enumerate(found, 1)}
    r = await jev.ask(state, {f"p{i}": Noul(instructions=f"Is `photo_{i}`, by its title and tags, a photo of `subject` or of something of its "
                                                         "own, fit to illustrate a document about `subject`?") for i in range(1, len(found) + 1)},
                      budget=JUDGE_S)
    if r.get("status") != "ok":
        return [], f"Jev couldn't judge the photos found ({r.get('error') or r.get('status')})"
    sure = [(((r["answers"].get(f"p{i}") or {}).get("noul") or 0.0), i) for i in range(1, len(found) + 1)]
    chosen = [found[i - 1] for p, i in sorted(sure, key=lambda x: (-x[0], x[1])) if p >= SHOWS_P][:n]
    return chosen, None if chosen else f"Jev judged none of the {len(found)} photos found to show {subject}"


async def fetch(pics, folder, get=_get):
    """The photos downloaded into `folder` -> those that came, each with its "path" (a JPEG or a PNG; an error page or a huge file isn't)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    async def one(p):
        try:
            data = await get(p["url"], timeout=FETCH_S)  # ponytail: read whole, then checked; stream it if a source starts sending huge files
        except Exception:
            return None
        if not data or len(data) > MAX_BYTES or data[:3] not in (b"\xff\xd8\xff", b"\x89PN"):
            return None
        path = folder / f"{uuid.uuid4().hex[:12]}{'.png' if data[:3] == b'\x89PN' else '.jpg'}"
        path.write_bytes(data)
        return {**p, "path": path}
    return [x for x in await asyncio.gather(*map(one, pics)) if x]


async def gather(subject, n, jev, folder, get=_get):
    """Up to `n` photos of `subject` for a document: found (Openverse), chosen (Jev), downloaded into `folder` -> (photos, why there are
    none), the photos as fetch() gives them."""
    found, why = await find(subject, get)
    if not found:
        return [], why
    chosen, why = await pick(subject, found, jev, n + 2)  # (two spare, for a download that doesn't come)
    if not chosen:
        return [], why
    got = (await fetch(chosen, folder, get))[:n]
    return got, None if got else "none of the photos chosen could be downloaded"


def _check():
    import tempfile
    jpeg = b"\xff\xd8\xff" + b"\x00" * 64
    asked = []

    def result(i, title, **k):
        return {"id": str(i), "title": title, "url": f"https://live.staticflickr.com/{i}.jpg", "creator": "js42", "license": "by-nd",
                "license_version": "2.0", "license_url": "https://creativecommons.org/licenses/by-nd/2.0/", "source": "flickr", "provider": "flickr",
                "foreign_landing_url": f"https://www.flickr.com/photos/x/{i}", "width": 1024, "height": 681, "mature": False,
                "tags": [{"name": "gandhi"}, {"name": "india"}]} | k

    async def get(url, params=None, timeout=None):
        if url == API:
            asked.append(params)
            return json.dumps({"results": [
                result(1, "Plaque at the Mahatma Gandhi memorial"), result(2, "Mahatma Gandhi statue", creator="<b>Vinoth</b> &amp; Co"),
                result(3, "Gandhi on Commons", source="wikimedia"), result(4, "A copy", url="https://upload.wikimedia.org/x.jpg"),
                result(5, "Tiny", width=240, height=179), result(6, "Odd terms", license="sampling+"), result(7, "plaque at the mahatma gandhi memorial"),
                result(8, "IMG_2345"), result(9, "Gandhi Mandapam", license="cc0", license_version="1.0"), result(10, "Broken link")]}).encode()
        if url.endswith("/10.jpg"):
            return b"<html>gone</html>"
        return jpeg

    found, why = asyncio.run(find("Mahatma Gandhi", get))
    assert asked[0]["excluded_source"] == "wikimedia" and why is None, asked
    assert [p["title"] for p in found] == ["Plaque at the Mahatma Gandhi memorial", "Mahatma Gandhi statue", "IMG_2345", "Gandhi Mandapam", "Broken link"], \
        "Wikimedia's never (by its source or its address), nor small ones, unknown terms or a double"
    assert found[0]["credit"] == "Photo: js42, CC BY-ND 2.0, via Flickr" and found[1]["credit"].startswith("Photo: Vinoth & Co,"), found[1]
    assert found[3]["credit"] == "Photo: js42, CC0 1.0, via Flickr" and found[0]["page"].startswith("https://www.flickr.com/")

    class Jev:  # its numbers for each photo: the plaque 0.9, the statue 0.95, IMG_2345 0.2, the mandapam 0.7, the broken one 0.8
        def __init__(self, status="ok"):
            self.status, self.state = status, None

        async def ask(self, state, questions, budget=None):
            self.state = state
            p = {"p1": 0.9, "p2": 0.95, "p3": 0.2, "p4": 0.7, "p5": 0.8}
            return {"status": self.status, "answers": {k: {"noul": v} for k, v in p.items()}, "error": "Jev is offline"}
    j = Jev()
    chosen, why = asyncio.run(pick("Mahatma Gandhi", found, j, 3))
    assert [p["title"] for p in chosen] == ["Mahatma Gandhi statue", "Plaque at the Mahatma Gandhi memorial", "Broken link"], chosen
    assert j.state["photo_3"] == {"title": "IMG_2345", "tags": ["gandhi", "india"]} and j.state["subject"] == "Mahatma Gandhi"
    assert asyncio.run(pick("x", found, Jev("offline"), 3)) == ([], "Jev couldn't judge the photos found (Jev is offline)"), "no Jev: none"
    with tempfile.TemporaryDirectory() as d:
        got, why = asyncio.run(gather("Mahatma Gandhi", 3, Jev(), d, get))
        assert [p["title"] for p in got] == ["Mahatma Gandhi statue", "Plaque at the Mahatma Gandhi memorial", "Gandhi Mandapam"] and why is None, \
            ("the broken download left out, a spare in its place", got)
        assert all(Path(p["path"]).read_bytes()[:3] == b"\xff\xd8\xff" and Path(p["path"]).parent == Path(d) for p in got)

    async def down(url, params=None, timeout=None):
        raise OSError("offline")
    assert asyncio.run(gather("x", 3, Jev(), "unused", down)) == ([], "Openverse didn't answer (OSError)"), "offline: none, nothing invented"
    print("pictures self-check: OK")


async def _live(subject):
    found, why = await find(subject)
    print(why or f"{len(found)} photos found")
    for p in found:
        print("-", p["title"], "|", p["credit"], "|", p["page"])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(_live(" ".join(sys.argv[1:])))
    else:
        _check()
