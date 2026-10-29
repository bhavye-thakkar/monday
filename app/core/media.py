"""Media: what is playing, and the transport controls, through Windows' own media sessions (the list behind the volume
flyout). Every result is checked against what Windows reports afterwards: "Playing Blinding Lights by The Weeknd" is said
only when that app's session says so. Spotify search-and-play goes through the app itself: its own search link, then the
Play button of the matching result (UI Automation: no mouse, no typing), then the session.

python -m core.media           -> offline self-check
python -m core.media --live    -> prints the media sessions (plays and pauses nothing)
"""
import asyncio
import os
import re
import sys
import time
import urllib.parse

from .computer import POLL_S, done, failed

STATE_WAIT_S = 2.5        # a pause or resume shows in the session within this
SKIP_WAIT_S = 4           # the next track's title
RESULTS_WAIT_S = 10       # Spotify's results after its search link (a cold start is still loading its page)
RESEND_S = 3.5            # its page still shows something else: send the link once more (a navigation, safe to repeat)
PLAY_WAIT_S = 8           # the picked result playing, by title
MATCH_MIN = 0.5           # the share of the owner's words a result must carry to be played unasked
NEAR_TOP = 0.75           # ...or Spotify's own top result, when its name is this close to the words (a name heard wrong: "Rajit Sing")
SPOTIFY_ELEMENTS = 600    # the results sit after the whole library sidebar in the tree
SPOTIFY = "spotify"
ADAPTERS = (SPOTIFY,)     # players with their own adapter here (search, the owner's library, checks); any other is worked by the general
#                           loop (executor._play_in), never replaced by one of these


def adapter(app):
    """Does this player have its own adapter here (music)? Else the general loop plays in it."""
    return (app or "").strip().lower() in ADAPTERS
APP_NAMES = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave", "zunemusic": "Media Player",
             "zunevideo": "Movies & TV", "vlc": "VLC"}
FILLER = {"the", "a", "an", "by", "from", "of", "on", "in", "song", "songs", "track", "tracks", "music", "some", "something", "please", "spotify"}
NOT_PLAYING_AD = ("advertisement", "spotify", "")  # what a session is titled while an ad runs
BROWSERS = ("Chrome", "Edge", "Firefox", "Brave")


def _words(text):
    return [w for w in re.findall(r"[\w']+", (text or "").lower()) if w not in FILLER]


def _plain(text):
    return " ".join(re.findall(r"[\w']+", (text or "").lower()))


def app_name(app_id):
    """"SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify" -> "Spotify"."""
    low = (app_id or "").lower()
    return next((v for k, v in APP_NAMES.items() if k in low), re.sub(r"\.exe$", "", re.split(r"[!_]", app_id or "")[0], flags=re.I) or "an app")


def describe(x):
    title = x["title"] or "something untitled"
    return f"{title} by {x['artist']}" if x["artist"] else title


def view(x):
    """A session without its handle: what a result, the task log and the interfaces may carry."""
    return {k: x[k] for k in ("app", "name", "status", "title", "artist")} if x else None


def available():
    try:
        import winrt.windows.media.control  # noqa: F401
        return True
    except ImportError:
        return False


async def sessions():
    """Every app that has media loaded: [{app, name, status, title, artist, s}], what is playing first, then Windows' current one."""
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status
    m = await Manager.request_async()
    cur = m.get_current_session()
    cur_id = cur.source_app_user_model_id if cur else None
    out = []
    for s in m.get_sessions():
        try:
            p = await s.try_get_media_properties_async()
            status = Status(s.get_playback_info().playback_status).name.lower()
        except OSError:  # it closed between the listing and the reading
            continue
        out.append({"app": s.source_app_user_model_id, "name": app_name(s.source_app_user_model_id), "status": status,
                    "title": p.title or "", "artist": p.artist or "", "s": s})
    return sorted(out, key=lambda x: (x["status"] != "playing", x["app"] != cur_id))


def pick(found, app=None):
    """The session a command is about: the named app's, else the first (playing before paused, Windows' current one first).
    "YouTube" plays in a browser, so it means a browser's session."""
    if app:
        a = app.lower().replace(" ", "")
        if a in ("youtube", "youtubemusic"):
            return next((x for x in found if x["name"] in BROWSERS), None)
        return next((x for x in found if a in x["app"].lower() or a == x["name"].lower().replace(" ", "")), None)
    return found[0] if found else None


APP_VIDEO_WAIT_S = 40.0  # a video opened in the owner's own app: seen playing within this, an ad before it included
SKIP_AFTER_S = 12.0      # ...and its Skip still looked for this long once it plays (a skippable ad shows Skip 5 s in)
APP_POLL_S = 1.2         # ...looked at this often
NUDGES, NUDGE_AFTER_S = 3, 3.0  # its own session paused (autoplay held, or stopped after its ad: seen in Monday's Chrome 2026-10-20) this
#                                 long after it opened: asked to play, this many times at most, as long apart


async def play_in_app(c, app, url, title, name="YouTube", say=lambda _t: None):
    """A video in the owner's own app for it (their YouTube app: their account; the owner, 2026-10-20: "if the app is available then in
    the app only"): opened at its address (computer.open_web_app), the player's Skip pressed on screen as it shows (UI Automation: best
    effort, an ad without one plays out), Play asked of its media session while it sits paused, and Windows' media session seen playing it.
    "It" is told by the video's own words (up to three of them in the window's title and the session's), so nothing of the owner's other
    windows or videos is pressed. -> done(verified=True), done(verified=None) when no session shows it, or failed when the app didn't open."""
    from .chrome import AD_DISMISS
    from .computer import open_web_app
    say(f"Opening it in your {name} app")
    try:
        open_web_app(app, url)
    except OSError as e:
        return failed(f"Your {name} app didn't open: {e.strerror or e}.")
    words = set(_words(title)[:5])
    this = lambda text: bool(words) and len(words & set(_words(text))) >= min(3, len(words))  # noqa: E731
    pressed, seen_at, last, nudged = [], None, None, 0
    end, nudge_at = time.monotonic() + APP_VIDEO_WAIT_S, time.monotonic() + NUDGE_AFTER_S
    while time.monotonic() < end and not (seen_at and time.monotonic() - seen_at >= SKIP_AFTER_S):
        await asyncio.sleep(APP_POLL_S)
        w = next((w for w in c.windows_for(name) if this(w.title)), None)  # (its window shows the video's title: not another tab's)
        if w is not None:
            try:
                els = await c.elements(w, query="Skip", limit=30)
                btn = next((e for e in els if AD_DISMISS.match(e.get("name") or "") and e.get("role") in ("Button", "Hyperlink")), None)
                if btn:
                    await c.act_on({"element": btn}, w, "click")
                    pressed.append(btn.get("name") or "Skip")
            except Exception:  # (the window going or changing under the look: looked at again)
                pass
        try:
            mine = [x for x in await sessions() if this(x["title"])]
        except (ImportError, OSError):
            mine = []
        now = next((x for x in mine if x["status"] == "playing"), None)
        held = next((x for x in mine if x["status"] == "paused"), None)
        if now:
            last, seen_at = now, seen_at or time.monotonic()
        elif held and not seen_at and nudged < NUDGES and time.monotonic() >= nudge_at:
            try:
                await held["s"].try_play_async()
                pressed.append("Play")
            except OSError:
                pass
            nudged, nudge_at = nudged + 1, time.monotonic() + NUDGE_AFTER_S
    way = f" ({', '.join(dict.fromkeys(pressed))} pressed on the way)" if pressed else ""
    if seen_at:
        return done(f"Playing “{title}” in your {name} app{way}.", verified=True, media=view(last), app=name, url=url)
    return done(f"I opened “{title}” in your {name} app{way}; Windows' media sessions don't show it playing yet, so I can't confirm it.",
                verified=None, app=name, url=url)


async def _until(app, pred, timeout):
    """The app's session once pred(session) holds, else its last reading when the time is up (None: it has no session)."""
    end = time.monotonic() + timeout
    while True:
        x = pick(await sessions(), app)
        if (x and pred(x)) or time.monotonic() >= end:
            return x
        await asyncio.sleep(POLL_S)


async def control(op, app=None):
    """play | pause | toggle | next | previous on a media session, checked against what Windows shows afterwards.
    None when the sessions can't be read here (the caller then presses the media key, and says it couldn't check)."""
    try:
        found = await sessions()
    except (ImportError, OSError):
        return None
    x = pick(found, app)
    if not x:
        return failed(f"{app_name(app)} has nothing loaded to {op}." if app else "Nothing is playing or paused on this PC right now.")
    name, what = x["name"], describe(x)
    if op == "toggle":
        op = "pause" if x["status"] == "playing" else "play"
    if op in ("play", "pause"):
        want = op == "play"
        if (x["status"] == "playing") == want:
            return done(f"{what} is already {'playing' if want else 'paused'} in {name}.", verified=True, media=view(x))
        await (x["s"].try_play_async() if want else x["s"].try_pause_async())
        now = await _until(x["app"], lambda y: (y["status"] == "playing") == want, STATE_WAIT_S)
        ok = (now["status"] == "playing") == want if now else not want  # no session left is not playing
        return done(f"{'Playing' if want else 'Paused'} {describe(now or x)} in {name}." if ok else
                    f"I asked {name} to {op}, but Windows still shows it {'paused' if want else 'playing'}.", verified=ok, media=view(now or x))
    was = (x["title"], x["artist"])
    await (x["s"].try_skip_next_async() if op == "next" else x["s"].try_skip_previous_async())
    changed = lambda y: bool(y["title"]) and (y["title"], y["artist"]) != was  # noqa: E731  (the title is blank for a moment between tracks)
    now = await _until(x["app"], changed, SKIP_WAIT_S)
    if now and changed(now):
        return done(f"{'Skipped' if op == 'next' else 'Went back'} to {describe(now)} in {name}.", verified=True, media=view(now))
    if op == "previous":  # early in a track "previous" restarts it, and a title can't show that
        return done(f"I asked {name} for the previous track; it still shows {what}, which it may have restarted.", verified=None, media=view(x))
    return done(f"I asked {name} for the next track, but it still shows {what}.", verified=False, media=view(x))


async def now_playing(app=None):
    try:
        x = pick(await sessions(), app)
    except (ImportError, OSError):
        return failed("I can't read Windows' media sessions on this PC.")
    if not x:
        return done(f"{app_name(app)} has nothing loaded." if app else "Nothing is playing or paused right now.", verified=True)
    return done(f"{x['name']} is {'playing' if x['status'] == 'playing' else x['status'] + ' on'} {describe(x)}.", verified=True, media=view(x))


# -- Spotify: its search results, read from its own window ---------------------------------------------------------------
def shown_query(els):
    """The search Spotify's page shows right now (its filter links carry it), so the last search's rows aren't taken for this one's."""
    for e in els:
        m = re.search(r"/search/([^/?#]+)", str(e.get("value") or ""))
        if m:
            return urllib.parse.unquote_plus(urllib.parse.unquote(m[1]))
    return ""


def results(els):
    """Spotify's search results that can be played, in its own order (its top result first): [{title, kind, by, url, play}]."""
    start = next((i for i, e in enumerate(els) if e.get("role") == "DataGrid" and (e.get("name") or "").lower() == "search results"), None)
    if start is None:
        return []
    depth, rows, row = els[start].get("depth", 0), [], None
    for e in els[start + 1:]:
        d, name = e.get("depth", 0), (e.get("name") or "").strip()
        if d <= depth:
            break
        if d == depth + 1 and e.get("role") == "DataItem":
            row = {"title": name, "kind": "", "by": "", "url": "", "play": None}
            rows.append(row)
        elif row is None:
            continue
        elif e.get("role") == "Hyperlink" and not row["url"] and re.search(r"/(?:track|episode|album|artist|playlist|show)/", str(e.get("value") or "")):
            row["url"] = e["value"]
        elif " • " in name and not row["kind"]:  # "Song • The Weeknd"
            row["kind"], _, row["by"] = name.partition(" • ")
        elif e.get("role") == "Button" and (name == "Play" or name.startswith("Play ")) and not row["play"]:
            row["play"] = e
    return [r for r in rows if r["play"]]


def choose(rows, query, skip=0, kind=None):
    """The result to play for what the owner asked: an exact title match wins outright (so a named track is never passed over
    for the second result); otherwise the one whose title and artist carry the most of their words. `kind` ("album"/"artist")
    keeps only results of that kind. skip=1 is the next best (a recovery when the first turned out wrong). None when nothing
    carries half the words."""
    want, qplain, ranked = _words(query), _plain(query), []
    for i, r in enumerate(rows):
        if kind and not (r["kind"] or "").lower().startswith(kind.lower()):
            continue
        have = set(_words(f"{r['title']} {r['by']}"))
        cover = sum(w in have for w in want) / max(1, len(want))
        if cover >= MATCH_MIN:
            spoken = "/episode/" in r["url"] or "/show/" in r["url"]
            exact = _plain(r["title"]) == qplain or _plain(f"{r['title']} {r['by']}") == qplain  # the song they named, not its remix
            ranked.append(((exact, cover, not spoken, -len(set(_words(r["title"])) - set(want)), -i), r))
    ranked.sort(key=lambda t: t[0], reverse=True)
    if not ranked and not skip:  # a name heard wrong ("Rajit Sing", 2026-10-20): Spotify's own top result, when it is nearly that name
        import difflib
        top = next((r for r in rows if not kind or (r["kind"] or "").lower().startswith(kind.lower())), None)
        if top and difflib.SequenceMatcher(None, _plain(query), _plain(top["title"])).ratio() >= NEAR_TOP:
            return top
    return ranked[skip][1] if skip < len(ranked) else None


# The owner's OWN Liked Songs is the authenticated account's collection (this URL); a "Liked Songs" shared by a friend is a
# /playlist/<id>. So a "play my liked songs" request resolves to the row whose link is the collection, never a searched result.
LIKED_URL = "/collection/tracks"


def liked_play(els):
    """The Play button of the owner's own Liked Songs: among the library's "Play Liked Songs" buttons, the one whose row links
    to the collection URL. Else any "Play Liked Songs" (the sidebar's pinned one). None when the library isn't showing it."""
    play = [(i, e) for i, e in enumerate(els)
            if e.get("role") == "Button" and (e.get("name") or "").lower() == "play liked songs" and e.get("element_token")]
    coll = [i for i, e in enumerate(els) if e.get("role") == "Hyperlink" and LIKED_URL in str(e.get("value") or "")]
    if play and coll:
        return min(play, key=lambda p: abs(p[0] - coll[0]))[1]
    return play[0][1] if play else None


def playlist_play(els, name):
    """The Play button of the owner's library playlist whose name best matches `name` (its words), else None. Dynamic: it matches
    whatever playlists the account actually has ("Play Workout" for "my workout playlist")."""
    want, best, score = set(_words(name)), None, 0.0
    if not want:
        return None
    for e in els:
        n = e.get("name") or ""
        if e.get("role") == "Button" and n.lower().startswith("play ") and e.get("element_token"):
            have = set(_words(n[5:]))
            cover = sum(w in have for w in want) / len(want)
            if cover > score:
                best, score = e, cover
    return best if score >= MATCH_MIN else None


async def _set_shuffle(c, w, say):
    """Turn Spotify's shuffle on before playing the collection (best effort; a random liked song). Verifying shuffle state isn't
    reliable through UIA, so this is not asserted."""
    els = await c.elements(w, limit=SPOTIFY_ELEMENTS)
    btn = next((e for e in els if e.get("role") in ("Button", "CheckBox") and "shuffle" in (e.get("name") or "").lower()
                and ("enable" in (e.get("name") or "").lower() or "turn on" in (e.get("name") or "").lower())), None)
    if btn:
        say("Turning on shuffle")
        await c.act_on({"element": btn}, w, "click")


async def _play_collection(c, shuffle, say):
    """Fall back to the Liked Songs page itself when the sidebar didn't show its Play (collapsed or scrolled): navigate to the
    authenticated account's collection URL and press its Play."""
    app = await c.apps.find(SPOTIFY)
    say("Opening your Liked Songs")
    try:
        os.startfile("spotify:collection:tracks")
    except OSError as e:
        return failed(f"Spotify didn't open your Liked Songs: {(e.strerror or str(e)).rstrip('.')}.")
    end, w, btn = time.monotonic() + RESULTS_WAIT_S, None, None
    while time.monotonic() < end and btn is None:
        await asyncio.sleep(0.4)
        w = next(iter(c.windows_for(SPOTIFY, app)), None)
        els = await c.elements(w, limit=SPOTIFY_ELEMENTS) if w else []
        btn = next((e for e in els if e.get("role") == "Button" and (e.get("name") or "") in ("Play Liked Songs", "Play")
                    and e.get("element_token")), None)
    if btn is None:
        return failed("I opened your Liked Songs, but couldn't find its Play button. Press Play to start them.", app="Spotify")
    if shuffle and w:
        await _set_shuffle(c, w, say)
    await c.act_on({"element": btn}, w, "click")
    now = await _until(SPOTIFY, lambda y: y["status"] == "playing", PLAY_WAIT_S)
    if now and now["status"] == "playing":
        return done(f"Playing {describe(now)} from your Liked Songs.", verified=True, media=view(now), app="Spotify")
    return failed("I opened your Liked Songs and pressed Play, but Windows doesn't show Spotify playing.", media=view(now), app="Spotify")


async def music(c, args, say=lambda _t: None, skip=0):
    """Play what the owner asked, by mode (core/router.music_target). A named track/album/artist is a search picked by metadata
    (choose); the owner's Liked Songs or one of their playlists is their REAL library, read from Spotify's window, never a text
    search. Verified against Windows' media session (that playback started; the current track where a specific one was named)."""
    mode = args.get("mode", "track")
    if mode in ("track", "album", "artist"):
        return await spotify_play(c, args.get("query", ""), say=say, skip=skip,
                                  kind={"album": "album", "artist": "artist"}.get(mode))
    if not available():
        return failed("Playing from your library needs Windows' media sessions to check it.")
    app = await c.apps.find(SPOTIFY)
    if not app:
        return failed("Spotify isn't installed on this PC.")
    r = await c.open_app(app)
    if not r["ok"]:
        return r
    label = "your Liked Songs" if mode == "liked" else f"your “{args.get('name', '')}” playlist"
    say(f"Opening {label}")
    end, w, btn = time.monotonic() + RESULTS_WAIT_S, None, None
    while time.monotonic() < end and btn is None:
        await asyncio.sleep(0.4)
        w = next(iter(c.windows_for(SPOTIFY, app)), None)
        els = await c.elements(w, limit=SPOTIFY_ELEMENTS) if w else []
        btn = liked_play(els) if mode == "liked" else playlist_play(els, args.get("name", ""))
    if btn is None:
        if mode == "liked":
            return await _play_collection(c, args.get("shuffle"), say)
        return failed(f"I couldn't find {label} in your Spotify library. Your playlists there are what I can play by name.", app="Spotify")
    if mode == "liked" and args.get("shuffle") and w:
        await _set_shuffle(c, w, say)
    say(f"Playing {label}")
    await c.act_on({"element": btn}, w, "click")
    now = await _until(SPOTIFY, lambda y: y["status"] == "playing" and _plain(y["title"]) not in NOT_PLAYING_AD, PLAY_WAIT_S)
    if now and now["status"] == "playing" and _plain(now["title"]) not in NOT_PLAYING_AD:
        return done(f"Playing {describe(now)} from {label}.", verified=True, media=view(now), app="Spotify")
    if now and now["status"] == "playing":
        return done(f"Started {label}; Spotify is playing an ad first.", verified=None, media=view(now), app="Spotify")
    return failed(f"I pressed Play on {label}, but Windows doesn't show Spotify playing.", media=view(now), app="Spotify")


def matches(x, query, row=None):
    """Does what Windows shows playing carry the owner's words (in its title or artist), or the title of the picked result?"""
    have, want = set(_words(f"{x['title']} {x['artist']}")), _words(query)
    return bool(want and all(w in have for w in want)) or bool(row and _plain(row["title"]) and _plain(row["title"]) in _plain(x["title"]))


async def spotify_search(c, query, say=lambda _t: None):
    """Spotify's own search for `query`, in the app. -> (its window, the playable rows), or (None, why not)."""
    app = await c.apps.find(SPOTIFY)
    if not app:
        return None, failed("Spotify isn't installed on this PC.")
    r = await c.open_app(app)
    if not r["ok"]:
        return None, r
    say(f"Searching Spotify for {query}")
    link = "spotify:search:" + urllib.parse.quote(query)
    try:
        os.startfile(link)
    except OSError as e:
        return None, failed(f"Spotify didn't take its search link: {(e.strerror or str(e)).rstrip('.')}.")
    end, again, w = time.monotonic() + RESULTS_WAIT_S, time.monotonic() + RESEND_S, None
    while time.monotonic() < end:
        await asyncio.sleep(0.4)
        w = next(iter(c.windows_for(SPOTIFY, app)), None)
        els = await c.elements(w, limit=SPOTIFY_ELEMENTS) if w else []
        rows = results(els) if _plain(shown_query(els)) == _plain(query) else []
        if rows:
            return w, rows
        if again and time.monotonic() >= again:  # seen 2026-10-05: a link sent while Spotify was coming forward went nowhere, and the wait ran out
            again = None
            os.startfile(link)
    return None, failed(f"Spotify is open, but its results for “{query}” didn't show within {RESULTS_WAIT_S}s.")


async def spotify_play(c, query, say=lambda _t: None, skip=0, kind=None):
    """Open Spotify, search it for `query`, press Play on the matching result (of `kind` when given: an album, an artist), and
    check Windows' session for it."""
    if not available():
        return failed("Playing a named song needs Windows' media sessions to check it (pip install winrt-Windows.Media.Control).")
    try:
        cur = pick(await sessions(), SPOTIFY)
    except OSError:
        cur = None
    if cur and not skip and not kind and matches(cur, query):  # already the loaded song: play it, don't search for it again
        return await control("play", SPOTIFY)
    w, rows = await spotify_search(c, query, say)
    if w is None:
        return rows
    row = choose(rows, query, skip, kind=kind)
    if not row:
        return failed(f"Spotify has no{' other' if skip else ''} result that matches “{query}”. Its first is {rows[0]['title']}"
                      + (f" ({rows[0]['kind'].lower()} by {rows[0]['by']})." if rows[0]["by"] else "."), results=[r["title"] for r in rows[:5]])
    picked = row["title"] + (f" by {row['by']}" if row["by"] else "")
    say(f"Selecting {picked}")
    await c.act_on({"element": row["play"]}, w, "click")
    say("Checking that it plays")
    # An artist's Play plays their songs, and Windows names a song's first artist only (2026-10-20: Arijit Singh's Play gave "Gehra Hua
    # by Shashwat Sachdev", said as a failure): a new song playing is that, said as from their page, not as seen to be theirs.
    artist = (row["kind"] or "").lower().startswith("artist")
    new = lambda y: not cur or cur["status"] != "playing" or (y["title"], y["artist"]) != (cur["title"], cur["artist"])  # noqa: E731
    now = await _until(SPOTIFY, lambda y: y["status"] == "playing" and (matches(y, query, row) or (artist and new(y))), PLAY_WAIT_S)
    if now and now["status"] == "playing" and matches(now, query, row):
        return done(f"Playing {describe(now)}.", verified=True, media=view(now), app="Spotify", query=query)
    if now and now["status"] == "playing" and artist and new(now) and _plain(now["title"]) not in NOT_PLAYING_AD:
        return done(f"Playing {describe(now)}, from {row['title']}'s page on Spotify.", verified=None, media=view(now), app="Spotify", query=query)
    if now and now["status"] == "playing" and _plain(now["title"]) in NOT_PLAYING_AD:
        return done(f"Spotify started with an ad; {picked} is what I picked to follow it.", verified=None, media=view(now), app="Spotify", query=query)
    if now and now["status"] == "playing":  # the step's purpose failed: a recovery may try the next result (executor._recover)
        return failed(f"I pressed Play on {picked}, but Spotify shows {describe(now)} playing.", media=view(now), app="Spotify", query=query)
    return failed(f"I pressed Play on {picked}, but Windows doesn't show Spotify playing.", media=view(now), app="Spotify", query=query)


# Media players Monday can press Play in, by the words in their window title or process (any app with a Windows media session
# is controlled through that session once something is loaded; this is for the moment before). Not a fixed list of behaviours:
# the Play button is found in whatever the app shows (the player-bar pattern Previous · Play · Next, else a button named Play).
PLAYERS = {"spotify": "Spotify", "youtube music": "YouTube Music", "media player": "Media Player", "groove": "Groove Music", "vlc": "VLC",
           "itunes": "iTunes", "apple music": "Apple Music", "winamp": "Winamp", "foobar": "foobar2000", "musicbee": "MusicBee", "amazon music": "Amazon Music"}
PREVIOUS = ("Previous", "Previous track", "Skip back", "Skip to previous")
PLAY_NAMES = ("Play", "Pause", "Play all", "Resume", "Play/Pause")


def player_of(w):
    """The media player a window belongs to, by its title or process, else None."""
    if not w:
        return None
    text = f"{w.title} {w.process}".lower()
    return next((label for key, label in PLAYERS.items() if key in text), None)


def play_button(els):
    """The Play control among an app's elements: the one in a player bar (right after Previous), else the first button named Play."""
    i = next((i for i, e in enumerate(els) if e.get("role") == "Button" and e.get("name") in PREVIOUS), None)
    if i is not None:
        b = next((e for e in els[i + 1:i + 4] if e.get("role") == "Button" and e.get("name") in PLAY_NAMES), None)
        if b:
            return b
    return next((e for e in els if e.get("role") == "Button" and e.get("name") in PLAY_NAMES), None)


async def resume(c, app=None, say=lambda _t: None, fallback=SPOTIFY):
    """Nothing is loaded anywhere ("open X and play", "let's listen to something"): press Play in a media app. The app named, else the
    player in front, else a player that is open, else the owner's default music app (`fallback`) when installed. Checked against the
    app's Windows media session."""
    label = PLAYERS.get((app or "").lower().replace("_", " ")) or (app.title() if app else None)
    if not label:
        label = player_of(c.front()) or next((player_of(w) for w in computer_windows() if player_of(w)), None)
    if not label:
        found = await c.apps.find(fallback)
        if not found:
            return failed("Nothing is playing or paused right now, and no music player is open or installed to start something.")
        label = PLAYERS.get(fallback.lower(), fallback.title())
    app_entry = await c.apps.find(label)
    if not app_entry:
        return failed(f"{label} isn't installed, so I can't press Play in it.")
    r = await c.open_app(app_entry)  # brings a running player forward, starts it otherwise
    if not r["ok"]:
        return r
    say(f"Pressing Play in {label}")
    end, w, button = time.monotonic() + RESULTS_WAIT_S, None, None
    while time.monotonic() < end and button is None:
        await asyncio.sleep(0.4)
        w = next(iter(c.windows_for(label, app_entry)), None)
        button = play_button(await c.elements(w, limit=SPOTIFY_ELEMENTS)) if w else None
    if button is None:
        return failed(f"{label} is open, but I can't find its Play button.")
    if button["name"] != "Pause":
        await c.act_on({"element": button}, w, "click")
    key = label.lower().replace(" ", "")
    now = await _until(key, lambda y: y["status"] == "playing", PLAY_WAIT_S)
    if now is None:  # the app has no media session yet (or Windows names it differently): any session that started playing counts
        now = await _until(None, lambda y: y["status"] == "playing", 1.0)
    if now and now["status"] == "playing":
        return done(f"Playing {describe(now)} in {now['name'] or label}.", verified=True, media=view(now), app=label)
    return failed(f"I pressed Play in {label}, but Windows doesn't show it playing.", media=view(now), app=label)


def computer_windows():
    from . import computer
    return computer.windows()


if __name__ == "__main__":
    E = lambda role, name, depth, value=None: {"role": role, "name": name, "depth": depth, "value": value, "element_token": name}  # noqa: E731
    page = [E("Hyperlink", "All", 15, "https://xpui.app.spotify.com/search/Blinding%2BLights"), E("DataGrid", "Search results", 14),
            E("DataItem", "Blinding Lights", 15), E("Hyperlink", "Blinding Lights", 18, "https://xpui.app.spotify.com/track/0VjIj"),
            E("DataItem", "Song • The Weeknd", 17), E("Button", "Add to Liked Songs", 18), E("Button", "Play", 18),
            E("DataItem", "The Weeknd - Blinding Lights", 15), E("Button", "Play The Weeknd - Blinding Lights", 18),
            E("Hyperlink", "The Weeknd - Blinding Lights", 18, "https://xpui.app.spotify.com/episode/76s"), E("DataItem", "Episode • Dissect DJs", 17),
            E("DataItem", "Blinding Lights - Slowed", 15), E("Button", "Play Blinding Lights - Slowed", 18),
            E("Hyperlink", "Blinding Lights - Slowed", 18, "https://xpui.app.spotify.com/track/0ZDH"), E("DataItem", "Song • sped up", 17),
            E("DataItem", "No play button here", 15), E("Button", "Home", 3)]
    assert shown_query(page) == "Blinding Lights" and shown_query([]) == ""
    rows = results(page)
    assert [r["title"] for r in rows] == ["Blinding Lights", "The Weeknd - Blinding Lights", "Blinding Lights - Slowed"], rows
    assert (rows[0]["kind"], rows[0]["by"], rows[0]["play"]["name"]) == ("Song", "The Weeknd", "Play") and "/track/" in rows[0]["url"]
    assert choose(rows, "Blinding Lights")["title"] == "Blinding Lights", "the plain title, Spotify's top result"
    assert choose(rows, "play the song Blinding Lights by The Weeknd")["by"] == "The Weeknd"
    assert choose(rows, "Blinding Lights", skip=1)["title"] == "Blinding Lights - Slowed", "the next best is a song, not the podcast episode"
    assert choose(rows, "Blinding Lights slowed")["title"] == "Blinding Lights - Slowed"
    assert choose(rows, "Bohemian Rhapsody") is None and choose([], "x") is None and choose(rows, "Blinding Lights", skip=9) is None
    singer = [{"title": "Arijit Singh", "kind": "Artist", "by": "", "url": "https://open.spotify.com/artist/x", "play": {}}]
    assert choose(singer, "Rajit Sing") is singer[0] and choose(singer, "Badshah") is None and choose(singer, "Rajit Sing", skip=1) is None, \
        "a name heard wrong plays Spotify's own top result when it is nearly that name (2026-10-20), never a different one"
    # an exact title wins outright even when a longer remix would cover the same words first in Spotify's order
    exact_rows = [{"title": "Blinding Lights - Slowed", "kind": "Song", "by": "sped up", "url": "/track/a", "play": {}},
                  {"title": "Blinding Lights", "kind": "Song", "by": "The Weeknd", "url": "/track/b", "play": {}}]
    assert choose(exact_rows, "Blinding Lights")["by"] == "The Weeknd", "the exact title, not the remix listed first"
    album_rows = [{"title": "After Hours", "kind": "Song", "by": "The Weeknd", "url": "/track/c", "play": {}},
                  {"title": "After Hours", "kind": "Album", "by": "The Weeknd", "url": "/album/d", "play": {}}]
    assert choose(album_rows, "After Hours", kind="album")["url"].startswith("/album/"), "kind=album keeps only the album"
    assert choose(album_rows, "After Hours", kind="artist") is None
    # the owner's own Library, read from Spotify's window (dynamic: whatever playlists they actually have)
    L = lambda role, name, value=None: {"role": role, "name": name, "value": value, "element_token": f"{role}:{name}"}  # noqa: E731
    lib = [L("Button", "Play Workout"), L("Button", "Liked Songs Pinned Playlist • Owner"), L("Button", "Play Liked Songs"),
           L("Hyperlink", "Liked Songs", "https://xpui.app.spotify.com/collection/tracks"),
           L("Button", "Liked Songs Playlist • A Friend"), L("Button", "Play Liked Songs"),
           L("Hyperlink", "Liked Songs", "https://xpui.app.spotify.com/playlist/69exFriend"),
           L("Button", "My Gym Mix Playlist • Owner"), L("Button", "Play My Gym Mix")]
    assert liked_play(lib) is lib[2], "the owner's own Liked Songs (its row links to /collection/tracks), not a friend's shared one"
    assert liked_play([L("Button", "Play Liked Songs")]) is not None, "falls back to the sidebar's pinned one with no collection link"
    assert liked_play([L("Button", "Play Workout")]) is None
    assert playlist_play(lib, "workout")["name"] == "Play Workout" and playlist_play(lib, "gym mix")["name"] == "Play My Gym Mix"
    assert playlist_play(lib, "nonexistent playlist here") is None
    S = lambda title, artist, status="playing": {"app": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "name": "Spotify", "status": status,  # noqa: E731
                                                 "title": title, "artist": artist, "s": None}
    assert matches(S("Blinding Lights", "The Weeknd"), "blinding lights") and matches(S("Starboy", "The Weeknd, Daft Punk"), "something from The Weeknd")
    assert not matches(S("Advertisement", ""), "Blinding Lights") and matches(S("Blinding Lights - Slowed", "x"), "zzz", rows[2])
    assert app_name("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify") == "Spotify" and app_name("Chrome") == "Chrome" and app_name("vlc.exe") == "VLC"
    assert describe(S("Hawa Hawa", "Mohit Chauhan")) == "Hawa Hawa by Mohit Chauhan" and describe(S("", "")) == "something untitled"
    both = [S("Video", "", "paused"), S("Song", "A")]
    assert pick(both, "spotify") is both[0] and pick(both, "vlc") is None and pick([]) is None and "s" not in view(both[0])
    assert pick([{**both[0], "app": "Chrome", "name": "Chrome"}], "YouTube")["name"] == "Chrome" and pick(both, "youtube") is None

    async def in_app():  # the owner's YouTube app, with stand-ins: nothing opens, nothing plays
        from types import SimpleNamespace as NS

        from . import computer as cmp
        asked, clicks, opened = [], [], []

        class Sess:
            def __init__(self, title, status):
                self.x = {"app": "Chrome", "name": "Chrome", "status": status, "title": title, "artist": "", "s": self}

            async def try_play_async(self):
                asked.append(self.x["title"])
                self.x["status"] = "playing"
        theirs, ours = Sess("Singh family vlog", "paused"), Sess("Arijit Singh: Tum Hi Ho | Aashiqui 2", "paused")
        app_w, other_w = NS(title="Arijit Singh: Tum Hi Ho | Aashiqui 2 - YouTube"), NS(title="Singh family vlog - YouTube - Google Chrome")

        async def elements(w, query=None, limit=0):
            return [{"role": "Button", "name": "Skip Ad"}]  # (in every window: only the video's own may be pressed)

        async def act_on(target, w, how):
            clicks.append(w.title)
        c = NS(windows_for=lambda _n: [other_w, app_w], elements=elements, act_on=act_on)
        keep = (globals()["sessions"], cmp.open_web_app, APP_POLL_S, NUDGE_AFTER_S, SKIP_AFTER_S)

        async def found():
            return [theirs.x, ours.x]
        globals().update(sessions=found, APP_POLL_S=0.01, NUDGE_AFTER_S=0.0, SKIP_AFTER_S=0.05)
        cmp.open_web_app = lambda app, url: opened.append(url)
        try:
            r = await play_in_app(c, ("proxy", "--app-id=x"), "https://www.youtube.com/watch?v=1", "Arijit Singh: Tum Hi Ho | Aashiqui 2")
        finally:
            globals().update(sessions=keep[0], APP_POLL_S=keep[2], NUDGE_AFTER_S=keep[3], SKIP_AFTER_S=keep[4])
            cmp.open_web_app = keep[1]
        assert opened == ["https://www.youtube.com/watch?v=1"] and r["ok"] and r["verified"] is True, r
        assert asked == ["Arijit Singh: Tum Hi Ho | Aashiqui 2"], f"Play asked of the video's own paused session only, once: {asked}"
        assert clicks and set(clicks) == {app_w.title}, f"Skip pressed in the app's window only, not the owner's other one: {clicks}"
        assert "Skip Ad, Play pressed on the way" in r["text"], r["text"]
    asyncio.run(in_app())
    if "--live" in sys.argv:
        async def live():
            t0 = time.perf_counter()
            found = await sessions()
            print(f"{len(found)} media session(s) in {(time.perf_counter() - t0) * 1000:.0f} ms")
            for x in found:
                print(f"  {x['name']}: {x['status']}: {describe(x)}")
            print((await now_playing())["text"])
        asyncio.run(live())
    print("media self-check: OK")
