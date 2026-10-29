"""Monday's own Chrome (Monday 2.0, phase 8). `Chrome` is the ChromeTool; `observe()` is the ChromeObserver: a
BrowserState Jev decides from (url, title, tabs, the visible links/buttons/fields, a text excerpt, errors, the last
action).

- Monday's own profile (PROFILE). Chrome 136+ refuses remote control of the everyday profile, and Monday must not use
  the owner's logins behind their back: a site the owner wants Monday to use, they sign in to once, here, themselves.
- Playwright drives the installed Chrome over a private pipe (no debugging port for other programs to find), and its
  clicks and keys go to the page, not through the owner's mouse and keyboard: Monday's Chrome works while they type.
- Elements come from the DOM and their accessible names, never blind coordinates. Each observation tags the visible
  ones b1, b2… and actions name those refs (or a label). Password fields are listed, never read, never typed into.
- Automation stays visible: Chrome's "controlled by automated test software" bar is left on, and a captcha is the
  owner's to solve.
- One browser, two people (2026-10-09): the owner watches it live in the workspace (`frames()`: the page's own screencast)
  and can take control (`take`): from then on every action of Monday's is refused (ManualControl) until they give it
  back (`give_back`), and a task that wanted the browser waits for that and looks again before acting.
"""
import asyncio
import base64
import contextlib
import functools
import importlib.util
import os
import re
import time
import urllib.parse
from pathlib import Path

from .computer import SEARCH, browser_info, done, failed, site_word, url_for

PROFILE = (Path("E:/monday/chrome-profile") if os.path.isdir("E:/")  # large, busy folders live on E: here, not in OneDrive
           else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Monday" / "chrome-profile")
SHOTS = Path(__file__).resolve().parent.parent / "data" / "screenshots"
MAX_ELEMENTS = 40      # per observation: a small state judges better (Jev 1.13 notes)
TEXT_MAX = 1500        # page text in an observation; read() returns more
READ_MAX = 6000
NAV_TIMEOUT_MS = 20000
NETWORK_IDLE_MS = 5000  # an audit waits this long for a loaded page to go quiet before it measures it
ACT_TIMEOUT_MS = 5000
SETTLE_S = 0.5         # after a click or Enter, before looking at what changed
RESULTS_WAIT_S = 3.0   # a search page's results can be drawn by script after load (Bing: ~1.5 s, measured 2026-09-30)
BING_RELOAD_WAIT_MS = 2000  # measured 2026-09-30: DOM ready at 0.35 s, Bing's own reload at 1.14 s
CAPTCHA_HOLD_S = 1800  # after Google asks for a captcha, general web searches go to Bing this long (it would ask again: 1.7-2.8 s lost each time)
LOG_MAX = 50
FRAME_QUALITY = 60     # the live view's JPEG quality: a page's text stays readable at ~40-90 KB a frame
FRAME_MAX = (1600, 1000)  # the largest frame Chrome sends (CSS px); the workspace's panel is smaller
FRAME_MIN_GAP_S = 0.08  # at most ~12 frames a second to a viewer: smooth enough to watch, cheap to send
WINDOW_MODE = os.environ.get("MONDAY_CHROME_WINDOW", "panel")  # where Chrome's own window lives: "panel" (out of sight, the workspace
#   shows its pages; the owner, 2026-10-09: "Chrome popping up" over their work) or "screen" (on the desktop, comes forward, as before)
PANEL_SIZE = (1280, 820)  # Chrome's window while out of sight (logical px): the size the pages are laid out for in the panel
FOCUS_WATCH_S = 1.0         # after an action, this long the keyboard is watched (Chrome activates its window asynchronously)
FOCUS_WATCH_LAUNCH_S = 4.0  # after a launch: measured 2026-10-09, Chrome took the keyboard later than 0.2 s after launching
FOCUS_POLL_S = 0.1
FRAME_STALL_S = 1.5    # no new frame this long while the page in front isn't the one last shown: one screenshot of it instead
FRAME_KEEPALIVE_S = 10  # a still page: the same picture again this often, so the viewer's connection stays open
CAPTCHA =re.compile(r"/sorry/|captcha|unusual traffic|are you a robot|verify you are human|made by a human", re.I)
TOPIC_CHECK_N = 10     # results read to tell whether a web search was answered at all (three of them are said)
WEB_ENGINES = ("google", "bing", "duckduckgo")  # whose results must be about the search: an engine that takes Monday's Chrome for a robot may answer with anything
# words of a search that say nothing about its subject: results that share only these with it are not about it
_COMMON = frozenset("the and for with from that this these those what which who whom whose how why when where are was were has have had its his her their "
                    "your you can could would should will does did not but all any some more most about into over under than then them they there here".split())
KEYS = {"enter": "Enter", "return": "Enter", "esc": "Escape", "escape": "Escape", "tab": "Tab", "backspace": "Backspace",
        "delete": "Delete", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
        "pageup": "PageUp", "pagedown": "PageDown", "home": "Home", "end": "End", "space": "Space",
        "ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift", "win": "Meta", "cmd": "Meta"}

# The visible, usable elements of the page, tagged data-monday-ref="bN" so an action can name one.
ELEMENTS_JS = r"""(max) => {
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, summary, [role=button], [role=link], [role=tab],' +
    '[role=menuitem], [role=checkbox], [role=radio], [role=switch], [role=option], [role=textbox], [role=searchbox],' +
    '[role=combobox], [contenteditable=""], [contenteditable=true]';
  const shown = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth &&
      s.visibility !== 'hidden' && s.display !== 'none' && !e.disabled; };
  const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const label = (e) => clean(e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) ||
    e.innerText || e.getAttribute('placeholder') || e.getAttribute('title') || e.getAttribute('alt') ||
    (e.type === 'submit' || e.type === 'button' ? e.value : ''));
  const kind = (e) => e.getAttribute('role') || { A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox', SUMMARY: 'button' }[e.tagName] ||
    (e.tagName === 'INPUT' ? ({ checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button', reset: 'button', search: 'searchbox' }[e.type] || 'textbox') : 'textbox');
  document.querySelectorAll('[data-monday-ref]').forEach((e) => e.removeAttribute('data-monday-ref'));
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    if (out.length >= max) break;
    if (!shown(e)) continue;
    const ref = 'b' + (out.length + 1), secret = e.type === 'password';
    e.setAttribute('data-monday-ref', ref);
    const x = { ref, role: kind(e), name: label(e) };
    if (secret) x.password = true;
    else if ('value' in e && e.tagName !== 'BUTTON' && !['submit', 'button', 'reset', 'checkbox', 'radio'].includes(e.type) && e.value) x.value = clean(e.value);
    if (e.type === 'checkbox' || e.type === 'radio') x.checked = e.checked;
    if (e.tagName === 'A' && e.href && !e.href.startsWith('javascript:')) x.href = e.href.slice(0, 120);
    out.push(x);
  }
  return out;
}"""


# Search results: visible headings inside links (Google's h3, Bing's h2, DuckDuckGo's result titles, YouTube's video
# titles), first come first, skipping the page's own chrome. Titles only: the owner reads the rest on the page.
RESULTS_JS = r"""(n) => {
  const seen = new Set(), out = [];
  const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  for (const e of document.querySelectorAll('a h3, a h2, h3 a, h2 a, a#video-title, [data-testid="result-title-a"]')) {
    const a = e.closest('a') || e.querySelector('a'), title = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (!a || !title || title.length < 4 || seen.has(title) || !shown(e)) continue;
    if (e.closest('.b_ad, .b_adTop, .b_adBottom, .sb_add, #tads, #tadsb, #bottomads, [data-text-ad], [data-testid="ad"], .result--ad')) continue;  // paid results are not the answer
    seen.add(title);
    out.push({ title: title.slice(0, 90), url: a.href });
    if (out.length >= n) break;
  }
  return out;
}"""

# YouTube (2026-10-20, the owner: "play Honey Singh song on YouTube" failed after 15 steps on the YouTube app's window): its results'
# first real video (a video's own row: never an ad slot, a Short, a mix or a channel), and the player's own state.
YT_FIRST_JS = r"""() => {
  const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  for (const a of document.querySelectorAll('ytd-video-renderer a#video-title')) {
    const href = a.getAttribute('href') || '';
    if (!/^\/watch\?v=/.test(href) || !shown(a) || a.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ytd-reel-shelf-renderer')) continue;
    a.setAttribute('data-monday-video', '1');
    return { title: (a.getAttribute('title') || a.innerText || '').trim().slice(0, 120), url: a.href };
  }
  return null;
}"""
YT_STATE_JS = r"""() => {
  const p = document.querySelector('#movie_player'), v = document.querySelector('#movie_player video, video.html5-main-video');
  if (!v) return null;
  return { playing: !v.paused && !v.ended && v.readyState > 2, time: v.currentTime, ad: !!(p && p.classList.contains('ad-showing')),
           title: document.title.replace(/[‪-‮⁦-⁩]/g, '').replace(/^\(\d+\)\s*/, '').replace(/\s*-\s*YouTube$/, '') };
}"""
AD_SKIP = (".ytp-skip-ad-button", ".ytp-ad-skip-button", ".ytp-ad-skip-button-modern", "button[id^='skip-button']",
           ".ytp-ad-overlay-close-button")  # YouTube's own Skip (shown once an ad may be skipped) and an overlay ad's ×
# What a person presses to get an ad or a promo out of the way: only words that dismiss. Never Accept, Allow, Sign in, Subscribe, Buy,
# Install, Try, Get or a plain "Close" (on the desktop that can be a window's own); executor's loop uses the same words.
AD_DISMISS = re.compile(r"^\W*(?:skip(?: (?:ad|ads|this ad|trial))?|dismiss|no,? thanks?|not now|maybe later|remind me later|close (?:this )?ad|"
                        r"close (?:the )?(?:promo|offer|banner)|continue without (?:it|premium))\W*$", re.I)
PLAY_VIDEO_WAIT_S = 45.0  # the video playing after its click: an ad that can't be skipped runs up to ~30 s first
AD_WATCH_S = 2.0          # after that, the tab is looked at this often for YouTube's Skip (the next song of a mix starts with an ad)


# What core/webfacts.py reads from the rendered page: the same facts it reads from raw markup, plus this load's paint and layout
# timings (LCP and layout shifts are watched for half a second after load). Runs inside the page; nothing is changed.
FACTS_JS = r"""async (maxLinks) => {
  const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim();
  const abs = (h) => { try { return new URL(h, document.baseURI).href; } catch { return null; } };
  const meta = {}, og = {}, tw = {};
  for (const m of document.querySelectorAll('meta')) {
    const n = (m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('http-equiv') || '').toLowerCase(), c = m.getAttribute('content');
    if (!n || c == null) continue;
    if (n.startsWith('og:')) { if (!(n.slice(3) in og)) og[n.slice(3)] = c; }
    else if (n.startsWith('twitter:')) { if (!(n.slice(8) in tw)) tw[n.slice(8)] = c; }
    else if (!(n in meta)) meta[n] = c;
  }
  const rel = (l) => (l.getAttribute('rel') || '').toLowerCase().split(/\s+/);
  const linkTags = [...document.querySelectorAll('link[rel]')];
  const canonical = linkTags.filter((l) => rel(l).includes('canonical') && l.getAttribute('href')).map((l) => abs(l.getAttribute('href')));
  const alternates = linkTags.filter((l) => rel(l).includes('alternate') && l.getAttribute('hreflang')).map((l) => ({ hreflang: l.getAttribute('hreflang'), href: l.getAttribute('href') || '' }));
  const icons = linkTags.filter((l) => rel(l).some((r) => r === 'icon' || r === 'apple-touch-icon')).map((l) => l.getAttribute('href') || '');
  const shown = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(shown).slice(0, 80).map((e) => ({ level: +e.tagName[1], text: clean(e.textContent).slice(0, 90) }));
  const imgs = [...document.images];
  const images = { total: imgs.length, missing_alt: imgs.filter((i) => !i.hasAttribute('alt')).length, empty_alt: imgs.filter((i) => i.getAttribute('alt') === '').length,
    lazy: imgs.filter((i) => i.loading === 'lazy').length, unsized: imgs.filter((i) => !(i.getAttribute('width') && i.getAttribute('height'))).length,
    modern: imgs.filter((i) => /\.(webp|avif|svg)(\?|$)/i.test(i.currentSrc || i.src)).length };
  const host = location.hostname.replace(/^www\./, '');
  const same = (h) => { h = h.replace(/^www\./, ''); return h === host || h.endsWith('.' + host) || host.endsWith('.' + h); };
  const links = { internal: 0, external: 0, nofollow: 0, empty: 0, generic: 0, hrefs: [] };
  for (const a of document.querySelectorAll('a[href]')) {
    const href = (a.getAttribute('href') || '').trim();
    if (!href || href.startsWith('#') || /^javascript:/i.test(href)) { links.empty++; continue; }
    if (/^(mailto|tel):/i.test(href)) continue;
    const u = abs(href);
    if (!u || !/^https?:/.test(u)) continue;
    if ((a.getAttribute('rel') || '').toLowerCase().includes('nofollow')) links.nofollow++;
    if (same(new URL(u).hostname)) { links.internal++; if (links.hrefs.length < maxLinks) links.hrefs.push(u.split('#')[0]); } else links.external++;
    if (/^(click here|here|read more|learn more|more|link|this|details)$/i.test(clean(a.textContent))) links.generic++;
  }
  const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => {
    try {
      const types = [], stack = [JSON.parse(s.textContent)];
      while (stack.length) {
        const x = stack.pop();
        if (Array.isArray(x)) stack.push(...x);
        else if (x && typeof x === 'object') {
          const t = x['@type'];
          if (typeof t === 'string') types.push(t); else if (Array.isArray(t)) types.push(...t.filter((y) => typeof y === 'string'));
          if (x['@graph']) stack.push(...[].concat(x['@graph']));
        }
      }
      return { types, ok: true };
    } catch { return { types: [], ok: false }; }
  });
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const watch = (type) => new Promise((res) => {
    const got = [];
    try { new PerformanceObserver((l) => got.push(...l.getEntries())).observe({ type, buffered: true }); } catch { return res(null); }
    setTimeout(() => res(got), 500);
  });
  const [lcpE, lsE, paintE] = await Promise.all([watch('largest-contentful-paint'), watch('layout-shift'), watch('paint')]);
  const fcp = (paintE || []).find((e) => e.name === 'first-contentful-paint');
  const text = clean(document.body ? document.body.innerText : '');
  return { dom: { title: document.title, lang: document.documentElement.lang || null, charset: document.characterSet, doctype: !!document.doctype, meta, og, twitter: tw,
      canonical, alternates, icons, headings, images, links, jsonld, words: text ? text.split(' ').length : 0, text_excerpt: text.slice(0, 1500), viewport_w: innerWidth, scroll_w: document.documentElement.scrollWidth },
    perf: { ttfb: nav.responseStart ? Math.round(nav.responseStart) : null, dcl: nav.domContentLoadedEventEnd ? Math.round(nav.domContentLoadedEventEnd) : null,
      load: nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null, fcp: fcp ? Math.round(fcp.startTime) : null,
      lcp: lcpE && lcpE.length ? Math.round(lcpE[lcpE.length - 1].startTime) : null,
      cls: lsE ? +lsE.filter((x) => !x.hadRecentInput).reduce((a, x) => a + x.value, 0).toFixed(4) : null, protocol: nav.nextHopProtocol || null } };
}"""


def available():
    """(True, None) when Monday can run its Chrome here, else (False, why)."""
    if not importlib.util.find_spec("playwright"):
        return False, "Playwright isn't installed (pip install playwright; the installed Chrome is used, no browser download)."
    if not (browser_info("chrome") or {}).get("exe"):
        return False, "Google Chrome isn't installed."
    return True, None


def short(e):
    """A Playwright error's first line, without its call log."""
    return str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__


def key_name(keys):
    """["ctrl", "l"] -> "Control+L" (Playwright's key names)."""
    return "+".join(KEYS.get(k.lower(), k.upper() if len(k) == 1 else k.title()) for k in keys)


def on_topic(query, results):
    """False when the results' titles and addresses mention none of the search's words, or just one of three or more. Bing answers a
    browser it takes for a robot with results for something else, or for one word of the search, and says nothing. Measured
    2026-10-05 in Monday's Chrome: "TypeSafe Jev model" got "Install Flutter", "latest Node.js LTS version" got news headlines (1 word
    of 4). In an ordinary browser the same day, seven searches got 2 of 2 up to 7 of 7, and the most rambling one 3 of 8. Nothing to
    judge by is not off topic."""
    # ponytail: shared words, not meaning. Ask Jev instead if good results get refused or junk gets read out.
    words = {w for w in re.findall(r"\w+", query.lower().replace("'s", "").replace("’s", "")) if len(w) > 2 and w not in _COMMON}
    text = " ".join(f"{x.get('title', '')} {x.get('url', '')}" for x in results).lower()
    return not words or not results or sum(bool(re.search(rf"\b{re.escape(w)}", text)) for w in words) > (len(words) >= 3)


class ManualControl(RuntimeError):
    """The owner has taken control of Monday's Chrome: Monday sends it nothing until they give it back."""


def _acts(f):
    """An action of Monday's on the browser (navigation, input, tabs): refused while the owner has control. Reading and
    observing are not actions: they never touch what the owner is doing."""
    @functools.wraps(f)
    async def act(self, *a, **k):
        if self.control == "owner":
            raise ManualControl("You have control of Monday's Chrome, so I didn't touch it. Press Resume to give it back.")
        async with self._keep_focus():  # out of sight, nothing Monday does may take the owner's keyboard
            return await f(self, *a, **k)
    return act


class Chrome:
    def __init__(self, headless=False, profile=PROFILE, window=None):
        self.headless, self.profile = headless, Path(profile)
        self.window = "screen" if headless else (window or WINDOW_MODE)  # "panel" | "screen" (see WINDOW_MODE)
        self._page = None
        self.pw = self.ctx = self.page = None
        self.control = "monday"  # who drives it: "monday", or "owner" after Take control (Monday's actions are then refused)
        self._ad_task = None     # the ad watcher on the YouTube tab Monday played in (watch_ads)
        self.turn = asyncio.Event()  # set while Monday may act; a task that needs the browser waits on it
        self.turn.set()
        self.taken = None       # {"url", "title", "at", "by"}: where the page was when the owner took control
        self.driver = None      # the task now using the browser (what the panel's Stop stops)
        self.on_change = None   # callback(view): the browser panel's state changed (Monday Core publishes it)
        self.loading = False
        self.frame, self.frame_meta, self.frame_seq = None, {}, 0  # the last screencast frame (JPEG) of the page in front
        self.frame_url, self.frame_at = None, 0.0  # which address that frame shows, and when it came
        self.viewers, self._cast, self._frame_ev, self._pub = 0, None, asyncio.Event(), None
        self.logs = {}          # page -> its console errors/warnings, uncaught exceptions and failed requests
        self.previous = None    # the last action and what came of it (BrowserState.previous)
        self.pids = {}          # window process id -> is it Monday's Chrome (its command line names Monday's profile)
        self.captcha_until = 0.0  # monotonic time until which a general web search skips Google (it asked for a captcha)
        self.bing_reloaded = False  # Bing reloads the first search of each browser session (~0.8 s after load, &rdr=1)

    # -- the browser ------------------------------------------------------------------------------------------------
    async def ensure(self):
        """Monday's Chrome, started when it isn't running (the owner may have closed it)."""
        if self.ctx:
            if self.page is None or self.page.is_closed():
                self.page = self.ctx.pages[-1] if self.ctx.pages else await self.ctx.new_page()
            return self.ctx
        ok, why = available()
        if not ok:
            raise RuntimeError(why)
        from playwright.async_api import async_playwright
        self.profile.mkdir(parents=True, exist_ok=True)
        self.pw = self.pw or await async_playwright().start()
        async with self._keep_focus(FOCUS_WATCH_LAUNCH_S):
            await self._launch()
        return self.ctx

    async def _launch(self):
        self.ctx = await self.pw.chromium.launch_persistent_context(
            str(self.profile), channel="chrome", headless=self.headless, no_viewport=not self.headless, accept_downloads=True,
            # the live view needs pages that keep painting when another window covers Monday's Chrome
            args=["--no-first-run", "--no-default-browser-check", "--disable-features=CalculateNativeWinOcclusion",
                  "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"])
        self.ctx.on("close", lambda *_: self._closed())
        self.ctx.on("page", self._watch)
        for p in self.ctx.pages:
            self._watch(p)
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        self.place_windows()

    @contextlib.asynccontextmanager
    async def _keep_focus(self, watch_s=FOCUS_WATCH_S):
        """Out of sight ("panel"), Chrome must never take the owner's keyboard: whatever it activates in here (its window at launch, a
        new tab, a tab switch), the window that had the keyboard before gets it back. Measured 2026-10-09: launched off-screen,
        Chrome still became the foreground window."""
        prev = foreground_window() if self.window == "panel" and not self.headless and os.name == "nt" else None
        try:
            yield
        finally:
            if prev:
                self._spawn(self._guard_focus(prev, watch_s))  # in the background: the action itself isn't held up

    async def _guard_focus(self, prev, watch_s):
        """For a moment after an action: the keyboard back to `prev` whenever Monday's Chrome takes it. If the owner goes to another
        window themselves, that is theirs, and the guard stops."""
        end = time.monotonic() + watch_s
        while time.monotonic() < end and self.window == "panel":
            await asyncio.sleep(FOCUS_POLL_S)
            now = foreground_window()
            if now == prev:
                continue
            if now not in self._windows():
                return
            if not give_focus_back(prev):
                await self._hand_back(now)
                if foreground_window() in self._windows():
                    return  # Windows wouldn't: stop, rather than fight over the keyboard

    async def _hand_back(self, hwnd):
        """Windows refused to move the keyboard (its foreground lock): hide Chrome's off-screen window for a moment, so Windows itself
        gives the keyboard to the window the owner used before it, then show it again without activating it (still out of sight)."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.ShowWindow(wintypes.HWND(hwnd), 0)  # SW_HIDE
        await asyncio.sleep(0.05)
        user32.ShowWindow(wintypes.HWND(hwnd), 4)  # SW_SHOWNOACTIVATE
        self.place_windows()  # showing it again brings its taskbar button back: out of sight means off the taskbar too

    # -- Chrome's own window: out of sight while the workspace shows its pages ("panel"), or on the desktop ("screen") -------------
    def _windows(self):
        """Monday's Chrome's visible top-level windows (its browser process's command line names Monday's profile)."""
        if os.name != "nt" or self.headless or not self.ctx:
            return []
        import ctypes
        from ctypes import wintypes
        import psutil
        user32, out, mine = ctypes.windll.user32, [], str(self.profile).lower().replace("\\", "/")

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def each(h, _):
            if not user32.IsWindowVisible(h) or not user32.GetWindowTextLengthW(h):
                return True
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls, 64)
            if cls.value != "Chrome_WidgetWin_1":
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value not in self.pids:
                try:
                    cmd = " ".join(psutil.Process(pid.value).cmdline())
                except Exception:
                    cmd = ""
                self.pids[pid.value] = mine in cmd.lower().replace("\\", "/")
            if self.pids[pid.value]:
                out.append(h)
            return True
        user32.EnumWindows(each, 0)
        return out

    def place_windows(self):
        """Chrome's window where it belongs now: out of sight and off the taskbar ("panel": it never covers the owner's work or takes
        their keyboard; the workspace shows the page), or back on the desktop and the taskbar ("screen", Pop out)."""
        hs = self._windows()
        if not hs:
            return 0
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        for h in hs:
            hw = wintypes.HWND(h)
            if user32.IsIconic(hw) or user32.IsZoomed(hw):
                user32.ShowWindow(hw, 4)  # SW_SHOWNOACTIVATE: a minimized window paints nothing
            scale = (user32.GetDpiForWindow(hw) or 96) / 96
            w, hh = round(PANEL_SIZE[0] * scale), round(PANEL_SIZE[1] * scale)
            if self.window == "panel":
                x, y = user32.GetSystemMetrics(76) - w - 400, user32.GetSystemMetrics(77)  # left of every display (SM_X/YVIRTUALSCREEN)
            else:
                area = (ctypes.c_long * 4)()
                user32.SystemParametersInfoW(0x0030, 0, area, 0)
                w, hh = min(w, area[2] - area[0]), min(hh, area[3] - area[1])
                x, y = area[0] + (area[2] - area[0] - w) // 2, area[1] + (area[3] - area[1] - hh) // 2
            user32.SetWindowPos(hw, None, x, y, w, hh, 0x0014)  # SWP_NOZORDER | SWP_NOACTIVATE
        taskbar(hs, show=self.window == "screen")
        return len(hs)

    async def pop(self, out):
        """Pop out: Chrome's own window on the desktop, in front (for what the panel can't do: a sign-in window, a file picker).
        Pop in: back out of sight."""
        self.window = "screen" if out else "panel"
        await self.ensure()
        self.place_windows()
        if out and self.page:
            await self.page.bring_to_front()
        self.changed()
        return await self.view()

    async def _front(self, page=None, tab=False):
        """Bring a page forward. On the desktop ("screen") Chrome's window comes forward, as it always did. Out of sight ("panel") the
        window never does (it would take the owner's keyboard); a tab switch still makes that tab the one Chrome draws, which the
        live view needs."""
        p = page or self.page
        if self.headless or p is None:
            return
        if self.window == "screen":
            await p.bring_to_front()
        elif tab:
            async with self._keep_focus():
                await activate_tab(p)

    @property
    def page(self):
        return self._page

    @page.setter
    def page(self, p):  # the tab in front changed: the live view follows it
        if p is not self._page:
            self._page = p
            self.changed(recast=True)

    def _closed(self):
        self.ctx = self.page = None
        self.bing_reloaded = False
        self.logs, self.pids = {}, {}
        self._cast, self.frame = None, None
        self.changed()

    def is_front(self):
        """True when the owner's front window is Monday's Chrome: a click or key there then goes through the page."""
        if not self.ctx:
            return False
        from .computer import foreground
        w = foreground()
        if not w:
            return False
        if w.pid not in self.pids:
            try:
                import psutil
                cmd = " ".join(psutil.Process(w.pid).cmdline())
            except Exception:
                cmd = ""
            self.pids[w.pid] = str(self.profile).lower().replace("\\", "/") in cmd.lower().replace("\\", "/")
        return self.pids[w.pid]

    def _watch(self, page):
        log = self.logs.setdefault(page, [])

        def add(kind, text):
            log.append({"kind": kind, "text": str(text)[:300], "at": time.time()})
            del log[:-LOG_MAX]
        page.on("console", lambda m: add(m.type, m.text) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: add("exception", e))
        page.on("requestfailed", lambda r: add("request failed", f"{r.method} {r.url[:120]} ({r.failure})"))

        def started(r):
            if r.is_navigation_request() and r.frame == page.main_frame and page is self.page:
                self.loading = True
                self.changed()

        def loaded(*_):
            if page is self.page:
                self.loading = False
            self.changed()

        def gone(*_):
            self.logs.pop(page, None)
            if page is self._page and self.ctx:  # the owner closed the tab in front: the next one is in front now
                self.page = self.ctx.pages[-1] if self.ctx.pages else None
            self.changed()
        page.on("request", started)
        page.on("domcontentloaded", loaded)
        page.on("load", loaded)
        page.on("framenavigated", lambda f: self.changed() if f == page.main_frame else None)  # a new address (still loading)
        page.on("close", gone)
        if self.window == "panel" and self.ctx:
            self._spawn(self._place_soon())
        self.changed()

    async def close(self):
        if self.ctx:
            await self.ctx.close()
        if self.pw:
            await self.pw.stop()
        self.pw = None
        self._closed()

    async def _where(self, page=None):
        """The page by its title, else its site: never a long URL (it's spoken)."""
        page = page or self.page
        return (await page.title()).strip() or urllib.parse.urlsplit(page.url).hostname or page.url

    def _did(self, action, r):
        self.previous = {"action": action, "ok": r["ok"], "result": r["text"][:200]}
        return r

    # -- ChromeObserver ---------------------------------------------------------------------------------------------
    async def observe(self):
        """BrowserState: what Jev (or the owner) needs to decide the next browser step."""
        await self.ensure()
        p = self.page
        try:
            els = await p.evaluate(ELEMENTS_JS, MAX_ELEMENTS)
            info = await p.evaluate("(n) => ({ready: document.readyState, text: (document.body ? document.body.innerText : '')"
                                    ".replace(/\\n{3,}/g, '\\n\\n').slice(0, n), y: scrollY, h: document.documentElement.scrollHeight,"
                                    " vh: innerHeight})", TEXT_MAX)
        except Exception:  # mid-navigation: the page's context is being replaced
            els, info = [], {"ready": "loading", "text": "", "y": 0, "h": 0, "vh": 0}
        tabs = []
        for i, t in enumerate(self.ctx.pages):
            tabs.append({"i": i + 1, "title": (await t.title())[:80], "url": t.url[:120], "active": t is p})
        errors = [x for x in self.logs.get(p, []) if x["kind"] in ("error", "exception", "request failed")]
        return {"url": p.url, "title": await p.title(), "tabs": tabs, "ready": info["ready"], "elements": els,
                "text": info["text"], "scroll": {"y": info["y"], "height": info["h"], "view": info["vh"]},
                "errors": len(errors), "captcha": bool(CAPTCHA.search(p.url) or CAPTCHA.search(info["text"][:400])),
                "previous": self.previous}

    async def _text(self, limit=READ_MAX):
        return await self.page.evaluate("(n) => (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n').slice(0, n)", limit)

    async def read(self, limit=READ_MAX):
        await self.ensure()
        title, url = await self.page.title(), self.page.url
        text = await self._text(limit)
        return self._did("read", done(f"{title or url}\n{url}\n\n{text.strip()}", verified=True, url=url, title=title))

    async def console(self):
        """What went wrong on this page since it loaded: console errors/warnings, uncaught exceptions, failed requests."""
        await self.ensure()
        log = self.logs.get(self.page, [])
        if not log:
            return done(f"No console errors, warnings or failed requests on {await self._where()} since it loaded.",
                        verified=True, entries=[])
        lines = [f"{x['kind']}: {x['text']}" for x in log[-15:]]
        return done(f"{len(log)} problem(s) on {await self._where()}:\n" + "\n".join(lines), verified=True, entries=log[-15:])

    async def screenshot(self):
        await self.ensure()
        SHOTS.mkdir(parents=True, exist_ok=True)
        path = SHOTS / f"chrome-{time.strftime('%Y%m%d-%H%M%S')}.png"
        await self.page.screenshot(path=str(path))
        return self._did("screenshot", done(f"Saved a screenshot of {await self._where()}. File: {path}",
                                            verified=path.exists(), path=str(path)))  # the path after the sentence that's spoken

    # -- ChromeTool: navigation and tabs ------------------------------------------------------------------------------
    @_acts
    async def open(self, url=None):
        """Monday's Chrome in front: on `url`, else on whatever it shows."""
        if url:
            return await self.navigate(url)
        await self.ensure()
        await self._front()
        return self._did("open Chrome", done(f"Monday's Chrome is open: {await self._where()}.", verified=True))

    @_acts
    async def navigate(self, url, new_tab=False, front=True):
        target = url_for(url) or url
        if not urllib.parse.urlsplit(target).scheme:
            target = "https://" + target
        await self.ensure()
        if new_tab:
            async with self._keep_focus():
                self.page = await self.ctx.new_page()
        self.logs[self.page] = []  # errors are counted from this load on
        try:
            resp = await self.page.goto(target, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            return self._did(f"go to {target}", failed(f"Chrome couldn't load {target}: {short(e)}"))
        if front:
            await self._front()
        return self._did(f"go to {target}", await self._landed(target, resp.status if resp else None))

    @staticmethod
    def _challenge(url, title=None):
        return failed(f"{site_word(url).title()} is asking to confirm a person is there (a captcha). Monday doesn't solve those: "
                      f"please do it in Monday's Chrome, then ask again.", url=url, title=title, captcha=True)

    async def _landed(self, target, status=None):
        """Where the page actually is, said honestly: the site asked for, somewhere else (a sign-in), an error, a captcha."""
        url, title = self.page.url, await self.page.title()
        where = await self._where()
        if CAPTCHA.search(url) or CAPTCHA.search(title):
            return self._challenge(url, title)
        if status and status >= 400:
            return failed(f"{target} answered with HTTP {status} ({where}).", url=url, title=title, status=status)
        if site_word(url) != site_word(target):
            return done(f"Chrome went to {urllib.parse.urlsplit(url).hostname} ({where}) instead of {urllib.parse.urlsplit(target).hostname}"
                        f", probably a sign-in or a redirect.", verified=False, url=url, title=title)
        return done(f"Opened {where} in Monday's Chrome.", verified=True, url=url, title=title)

    @_acts
    async def search(self, query, site=None):
        """Search, then read the top results off the page (the brief's "results are read"). A general web search that
        Google answers with a captcha goes to Bing instead, and says so: another search engine, not a way around the
        captcha. For CAPTCHA_HOLD_S after that, general searches go straight to Bing (Google would only ask again);
        a search that names Google still goes to Google."""
        images = (site or "").lower() in ("images", "google images")  # Google's image search asks for the same captcha: Bing's then
        bing = SEARCH["bing images" if images else "bing"]
        q, held = urllib.parse.quote_plus(query), (not site or images) and time.monotonic() < self.captcha_until
        r = await self.navigate((bing if held else SEARCH.get((site or "").lower() or None) or SEARCH[None]).format(q))
        if r.get("captcha") and (not site or images):
            self.captcha_until = time.monotonic() + CAPTCHA_HOLD_S
            r = await self.navigate(bing.format(q))
            if r["ok"]:
                r["text"] = f"Google asked for a captcha, so I searched with Bing. {r['text']}"
        elif held and r["ok"]:
            r["text"] = f"I searched with Bing (Google asked for a captcha a little while ago). {r['text']}"
        if r["ok"] and not self.bing_reloaded and site_word(self.page.url) == "bing":
            # wait out that reload: else the results read (and the next click's target) are on a page about to vanish
            try:
                await self.page.wait_for_url(re.compile(r"[?&]rdr=1"), timeout=BING_RELOAD_WAIT_MS, wait_until="domcontentloaded")
            except Exception:
                pass
            self.bing_reloaded = True
        if r["ok"]:
            top = await self.results(TOPIC_CHECK_N)
            if not top:  # a check drawn inside the page (DuckDuckGo's)? Its address and title look like any search
                try:
                    body = await self._text()
                except Exception:  # the page is navigating under the read: nothing to judge by
                    body = ""
                if CAPTCHA.search(body):
                    return self._did(f"search {query}", self._challenge(r.get("url") or "", r.get("title")))
            engine = site_word(r.get("url") or "")
            if engine in WEB_ENGINES and not images and not on_topic(query, top):  # (a site's own search, or pictures, are taken at their word)
                return self._did(f"search {query}", failed(
                    f"{engine.title()}'s results aren't about “{query[:80]}”. It answered with something else ({top[0]['title']}…) and looks to be "
                    f"limiting automated searches, so I haven't read them out.", url=r.get("url"), title=r.get("title"), query=query, off_topic=True))
            top = top[:3]
            if top:
                r = {**r, "results": top, "text": r["text"] + " Top results: " + "; ".join(t["title"] for t in top) + "."}
            r = {**r, "query": query}  # a later step ("the SEO report for that") can tell which site the owner searched for
        return r

    @_acts
    async def load_measured(self, url):
        """Load `url` and measure it (core/webfacts.py): the main document's redirect chain, status and headers, every
        request's size, the rendered page's facts (FACTS_JS) and this load's paint and layout timings. Waits for the page to go
        quiet, at most NETWORK_IDLE_MS. Not ok only when the page couldn't be loaded at all; an error page is still measured."""
        target = url_for(url) or url
        if not urllib.parse.urlsplit(target).scheme:
            target = "https://" + target
        await self.ensure()
        page, finished = await self.ctx.new_page(), []  # a tab of its own: the page before it (search results) still loads things that aren't this site's
        self.page = page  # and the owner finds the site they asked about in front, the results in the tab beside it

        def fin(req):  # (a plain function: Playwright tags its handlers, which a bound list.append can't take)
            finished.append(req)
        page.on("requestfinished", fin)
        self.logs[page] = []
        try:
            resp = await page.goto(target, wait_until="load", timeout=NAV_TIMEOUT_MS)
            await self._front(page, tab=True)
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
            except Exception:  # a page that never goes quiet (polling, streaming) is measured as it stands
                pass
        except Exception as e:
            return {"ok": False, "verified": False, "text": f"Chrome couldn't load {target}: {short(e)}"}
        finally:
            page.remove_listener("requestfinished", fin)
        chain, req = [], resp.request if resp else None
        while req is not None:
            r = await req.response()
            chain.append({"url": req.url, "status": r.status if r else None})
            req = req.redirected_from
        chain.reverse()
        rows = []
        for req in finished:
            if not req.url.startswith(("http://", "https://")):
                continue
            try:
                sz, res = await req.sizes(), await req.response()
                rows.append({"url": req.url, "type": req.resource_type, "status": res.status if res else None,
                             "bytes": (sz.get("responseBodySize") or 0) + (sz.get("responseHeadersSize") or 0),
                             "cache": (res.headers.get("cache-control") if res else None), "encoding": (res.headers.get("content-encoding") if res else None)})
            except Exception:  # a request that vanished mid-measurement is left out
                continue
        try:
            got = await page.evaluate(FACTS_JS, 60)
        except Exception as e:  # the page navigated away or is a viewer (a PDF): the crawler's view still stands
            got = {"dom": None, "perf": None}
            self.logs.setdefault(page, []).append({"kind": "exception", "text": f"the page couldn't be read: {short(e)}", "at": time.time()})
        self.previous = {"action": f"look at {target}", "ok": True, "result": page.url[:200]}
        return {"ok": True, "verified": True, "text": f"Loaded {page.url}", "final_url": page.url, "status": resp.status if resp else None, "chain": chain,
                "headers": dict(resp.headers) if resp else {}, "dom": got["dom"], "perf": got["perf"], "protocol": (got["perf"] or {}).get("protocol"),
                "requests": rows, "console": list(self.logs.get(page, []))}

    async def results(self, n=3, wait_s=RESULTS_WAIT_S):
        """The first result titles on a search page, as the page shows them (headings inside links). Results that a
        page's script draws after load (Bing: ~1.5 s) are waited for, up to `wait_s`."""
        end, last = time.monotonic() + wait_s, None
        while True:
            try:
                found = await self.page.evaluate(RESULTS_JS, n)
            except Exception:  # the page is navigating under the read: Bing reloads itself, and not only on a session's first search
                found = None
            if found and found == last:  # the same twice in a row: the page has stopped changing (2026-10-05: results of another search were read out)
                return found
            if time.monotonic() > end:
                return found or last or []
            last = found
            await asyncio.sleep(0.3)

    async def dismiss_ads(self):
        """What stands between the owner and the page's purpose, out of the way as a person would (the owner, 2026-10-20: "dismiss the ad
        as it is necessary for the end result"): YouTube's Skip and an overlay ad's ×, then a button whose own words only dismiss
        (AD_DISMISS: "No thanks", "Dismiss", "Not now"…). Never anything that accepts, signs in or buys. -> the words of what was pressed."""
        p, pressed = self.page, []
        for sel in AD_SKIP:
            try:
                loc = p.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click(timeout=ACT_TIMEOUT_MS)
                    pressed.append("Skip ad")
                    break
            except Exception:  # (gone as it was pressed: the ad ended by itself)
                continue
        try:
            loc = p.get_by_role("button", name=AD_DISMISS)
            for i in range(min(await loc.count(), 3)):
                b = loc.nth(i)
                words = ((await b.get_attribute("aria-label")) or (await b.inner_text()) or "").strip()
                if await b.is_visible() and AD_DISMISS.match(words):
                    await b.click(timeout=ACT_TIMEOUT_MS)
                    pressed.append(words)
        except Exception:  # a page navigating under the look: nothing pressed
            pass
        return pressed

    def watch_ads(self, page):
        """After Monday plays a video there: while the tab stays on YouTube and the browser is Monday's (not the owner's, after Take
        control), YouTube's own Skip is pressed whenever it shows (the owner, 2026-10-20: "there are a lot of ads"; the next song of a
        mix starts with one). Only AD_SKIP: what the owner does on the page is theirs."""
        if self._ad_task is not None and not self._ad_task.done():
            return

        async def run():
            while self.ctx is not None and not page.is_closed() and "youtube.com" in (page.url or ""):
                if self.control == "monday":
                    for sel in AD_SKIP:
                        try:
                            loc = page.locator(sel)
                            if await loc.count() and await loc.first.is_visible():
                                await loc.first.click(timeout=ACT_TIMEOUT_MS)
                                break
                        except Exception:  # (the page navigating, or the ad gone as it was pressed)
                            break
                await asyncio.sleep(AD_WATCH_S)
        self._ad_task = asyncio.ensure_future(run())

    @_acts
    async def video_link(self, query, locate=None):
        """The first real video YouTube finds for `query`, found here but played elsewhere (the owner's own YouTube app): its address
        and title, read off the results page; else Jev's look clicks it (play_video's way) and the address it lands on is taken, the
        playing here stopped at once. -> done(url=, title=) or failed."""
        act = f"find {query} on YouTube"
        await self.ensure()
        r = await self.navigate(SEARCH["youtube"].format(urllib.parse.quote_plus(query)), front=False)
        if not r.get("ok"):
            return self._did(act, r)
        first, end = None, time.monotonic() + RESULTS_WAIT_S * 3
        while time.monotonic() < end and not first:
            try:
                first = await self.page.evaluate(YT_FIRST_JS)
            except Exception:
                first = None
            if not first:
                await asyncio.sleep(0.4)
        if first:
            return self._did(act, done(f"Found “{first['title']}” on YouTube.", verified=True, url=first["url"], title=first["title"]))
        if locate is None:
            return self._did(act, failed(f"YouTube showed no video for “{query}”.", url=self.page.url))
        png, where = await self.page.screenshot(), None
        for what in ("the title of the first video in the results", f"the first video in YouTube's results for “{query}”"):
            where = await locate(png, what)
            if where:
                break
        if not where:
            return self._did(act, failed(f"YouTube's page for “{query}” shows no video I could read, and Jev didn't find one in its picture "
                                         "either.", url=self.page.url))
        ratio = await self.page.evaluate("window.devicePixelRatio") or 1
        await self.page.mouse.click(where[0] / ratio, where[1] / ratio)
        await self._settle()
        url, title = self.page.url, (await self.page.title()).replace(" - YouTube", "")
        await self.page.goto("about:blank")  # (it plays where the owner sees it, not here)
        if "/watch" not in url:
            return self._did(act, failed(f"Jev's pick on YouTube's page for “{query}” wasn't a video.", url=url))
        return self._did(act, done(f"Found “{title}” on YouTube, by Jev's look at the page.", verified=True, url=url, title=title, by="jev"))

    @_acts
    async def play_video(self, query, locate=None):
        """Play the first video YouTube finds for `query`, in Monday's Chrome: its results page, the first real video clicked (a click, so
        it may play with sound), ads skipped and promos dismissed as they come, then the player itself says it plays (not an ad, not
        paused). When no video on the page can be read, Jev looks at the page's picture for one (the owner, 2026-10-20: "if it fails then
        it will ask the jev where the video is"): locate(png, target) -> (x, y) in the picture's pixels, or None."""
        act = f"play {query} on YouTube"
        await self.ensure()
        r = await self.navigate(SEARCH["youtube"].format(urllib.parse.quote_plus(query)))
        if not r.get("ok"):
            return self._did(act, r)
        pressed, first, end = [], None, time.monotonic() + RESULTS_WAIT_S * 3
        while time.monotonic() < end and not first:
            pressed += await self.dismiss_ads()
            try:
                first = await self.page.evaluate(YT_FIRST_JS)
            except Exception:  # still being drawn
                first = None
            if not first:
                await asyncio.sleep(0.4)
        by = None
        try:
            if first:
                await self.page.locator('a[data-monday-video="1"]').first.click(timeout=ACT_TIMEOUT_MS)
            elif locate is not None:
                png, where = await self.page.screenshot(), None
                # (a title is what Jev's second look confirms: asked for "the first video" it boxed an artist card's Mix tile, and doubted it)
                for what in ("the title of the first video in the results", f"the first video in YouTube's results for “{query}”"):
                    where = await locate(png, what)
                    if where:
                        break
                if not where:
                    return self._did(act, failed(f"YouTube's page for “{query}” shows no video I could read, and Jev didn't find one in its "
                                                 "picture either.", url=self.page.url))
                ratio = await self.page.evaluate("window.devicePixelRatio") or 1
                await self.page.mouse.click(where[0] / ratio, where[1] / ratio)
                by = "Jev's look at the page"
            else:
                return self._did(act, failed(f"YouTube showed no video for “{query}”.", url=self.page.url))
        except Exception as e:
            return self._did(act, failed(f"Clicking the video didn't work: {short(e)}", url=self.page.url))
        await self._settle()
        clicked, state, nudges, nudged_at = time.monotonic(), None, 0, time.monotonic()
        while time.monotonic() - clicked < PLAY_VIDEO_WAIT_S:
            pressed += await self.dismiss_ads()
            try:
                state = await self.page.evaluate(YT_STATE_JS)
            except Exception:
                state = None
            if state and state["playing"] and not state["ad"]:
                break
            if state and state["ad"]:
                nudged_at = time.monotonic()  # (an ad plays: Play is pressed only once the video itself stands still)
            elif state and not state["playing"] and nudges < 3 and time.monotonic() - nudged_at > 4:
                # held back (Chrome's autoplay rule), or left at 0:00 after a skipped ad (live, 2026-10-20): the player's own Play, then its K key
                nudges, nudged_at = nudges + 1, time.monotonic()
                try:
                    if nudges == 1:
                        await self.page.locator(".ytp-play-button").first.click(timeout=ACT_TIMEOUT_MS)
                    else:
                        await self.page.locator("#movie_player").first.press("k", timeout=ACT_TIMEOUT_MS)
                except Exception:
                    pass
            await asyncio.sleep(0.5)
        title = (state or {}).get("title") or (first or {}).get("title") or query
        way = (f", found by {by}" if by else "") + (f" ({'; '.join(dict.fromkeys(pressed))} pressed on the way)" if pressed else "")
        if state:  # (playing, or an ad before it: its later ads are skipped too)
            self.watch_ads(self.page)
        if state and state["playing"] and not state["ad"]:
            return self._did(act, done(f"Playing “{title}” on YouTube{way}.", verified=True, url=self.page.url, title=title, dismissed=pressed))
        if state and state["ad"]:
            return self._did(act, done(f"YouTube is playing an ad before “{title}”; the video starts after it{way}.", verified=None,
                                       url=self.page.url, title=title, dismissed=pressed))
        shows = ("no player" if not state else f"it paused at {int(state['time'] // 60)}:{int(state['time'] % 60):02d}")
        return self._did(act, failed(f"I opened “{title}” on YouTube, but its player doesn't show it playing ({shows}){way}.", url=self.page.url,
                                     title=title, player=state))

    @_acts
    async def back(self):
        await self.ensure()
        before = self.page.url
        await self.page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        moved = self.page.url != before
        return self._did("back", done(f"Went back to {await self._where()}." if moved else "There was no page to go back to.",
                                      verified=moved, url=self.page.url))

    @_acts
    async def new_tab(self, url=None):
        await self.ensure()
        if url:
            return await self.navigate(url, new_tab=True)
        async with self._keep_focus():
            self.page = await self.ctx.new_page()
        return self._did("new tab", done(f"Opened a new tab ({len(self.ctx.pages)} open).", verified=True))

    @_acts
    async def switch_tab(self, i):
        await self.ensure()
        pages = self.ctx.pages
        if not 1 <= i <= len(pages):
            return failed(f"There is no tab {i}; Monday's Chrome has {len(pages)}.")
        self.page = pages[i - 1]
        await self._front(tab=True)
        return self._did(f"switch to tab {i}", done(f"Switched to tab {i}: {await self._where()}.", verified=True))

    @_acts
    async def close_tab(self, i=None):
        await self.ensure()
        pages = self.ctx.pages
        page = pages[i - 1] if i and 1 <= i <= len(pages) else self.page if i is None else None
        if page is None:
            return failed(f"There is no tab {i}; Monday's Chrome has {len(pages)}.")
        title = await self._where(page)
        await page.close()
        left = self.ctx.pages if self.ctx else []
        if page is self.page:
            self.page = left[-1] if left else None
        return self._did("close tab", done(f"Closed the tab {title!r} ({len(left)} left).", verified=page.is_closed()))

    # -- ChromeTool: acting on the page -----------------------------------------------------------------------------
    async def _find(self, ref=None, name=None, field=False):
        """A locator for one element: an observed ref (b3), else by its label/name. None when nothing matches."""
        await self.ensure()
        p = self.page
        if ref:
            loc = p.locator(f'[data-monday-ref="{ref}"]')
            return loc if await loc.count() == 1 else None
        if not name:
            if not field:
                return None
            loc = p.locator("input:focus, textarea:focus, [contenteditable]:focus")
            if await loc.count():
                return loc.first
            loc = p.locator("input[type=search], input[type=text], input:not([type]), textarea, [role=searchbox], [role=combobox]")
            return loc.first if await loc.count() else None
        tries = ([p.get_by_label(name), p.get_by_placeholder(name), p.get_by_role("searchbox", name=name), p.get_by_role("textbox", name=name),
                  p.get_by_role("combobox", name=name)] if field else
                 [p.get_by_role("button", name=name), p.get_by_role("link", name=name), p.get_by_role("tab", name=name),
                  p.get_by_role("menuitem", name=name), p.get_by_text(name)])
        for loc in tries:
            try:
                if await loc.count():
                    return loc.first
            except Exception:
                continue
        return None

    @_acts
    async def click(self, ref=None, name=None):
        loc = await self._find(ref, name)
        what = name or ref
        if loc is None:
            return self._did(f"click {what}", failed(f"I couldn't find “{what}” on {await self._where()}"
                                                     + (" (the page changed: look again)." if ref else ".")))
        label = name or (await loc.get_attribute("aria-label")) or (await loc.inner_text()).strip()[:60] or what
        before, tabs = self.page.url, list(self.ctx.pages)
        try:
            await loc.click(timeout=ACT_TIMEOUT_MS)
        except Exception as e:
            return self._did(f"click {label}", failed(f"Clicking “{label}” didn't work: {short(e)}"))
        await asyncio.sleep(SETTLE_S)
        opened = [p for p in self.ctx.pages if p not in tabs]
        if opened:  # the link opens in a new tab (Bing's results all do): follow it, as the owner's eyes would
            self.page = opened[-1]
            await self._settle()
            await self._front(tab=True)
            return self._did(f"click {label}", done(f"Clicked “{label}”; it opened in a new tab: {await self._where()}.",
                                                    verified=True, url=self.page.url))
        moved = self.page.url != before
        if moved:
            await self._settle()
        return self._did(f"click {label}", done(f"Clicked “{label}”" + (f"; now on {await self._where()}." if moved else "."),
                                                verified=True if moved else None, url=self.page.url))

    async def _settle(self, tries=6):
        """Wait out the page's own redirects (a search engine's click-through page) until its address holds still."""
        url = None
        for _ in range(tries):
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception:
                pass
            if self.page.url == url:
                return
            url = self.page.url
            await asyncio.sleep(SETTLE_S)

    @_acts
    async def type(self, text, ref=None, field=None, enter=False):
        loc = await self._find(ref, field, field=True)
        what = field or ref or "a text field"
        if loc is None:
            return self._did(f"type into {what}", failed(f"I couldn't find {what!r} to type into on {await self._where()}."))
        if (await loc.get_attribute("type") or "").lower() == "password":
            return self._did(f"type into {what}", failed("That's a password field: Monday never types passwords. Please type it yourself."))
        if not field:  # name it as the page does, not by its ref ("Search", not "b3")
            what = next((x for x in [await loc.get_attribute(a) for a in ("aria-label", "placeholder", "title", "name")] if x), what)
        before = self.page.url
        try:
            await loc.fill(text, timeout=ACT_TIMEOUT_MS)
            try:
                seen = (await loc.input_value()) == text
            except Exception:  # contenteditable: no value, read its text
                seen = text.strip() in (await loc.inner_text())
            if enter:
                await loc.press("Enter")
                await asyncio.sleep(SETTLE_S)
        except Exception as e:
            return self._did(f"type into {what}", failed(f"Typing into {what} didn't work: {short(e)}"))
        moved = self.page.url != before
        tail = f" and pressed Enter; now on {await self._where()}." if enter and moved else " and pressed Enter." if enter else "."
        return self._did(f"type into {what}", done(f"Typed “{text[:60]}” into {what}" + tail,
                                                   verified=(seen or moved) if enter else seen, url=self.page.url))

    @_acts
    async def download(self, ref=None, name=None, folder=None):
        """Click a link or button that downloads (the one named, else the first that looks like a file) and save the file
        in the owner's Downloads folder, never over an existing file. Verified on disk. The owner approved it first
        (router.permit: every download asks)."""
        await self.ensure()
        loc = await self._find(ref, name)
        if loc is None and not (ref or name):
            loc = self.page.locator("a[download], a[href*='download' i], a[href$='.pdf' i], a[href$='.zip' i], a[href$='.csv' i], "
                                    "a[href$='.exe' i], a[href$='.msi' i], a[href$='.docx' i], a[href$='.xlsx' i]").first
            loc = loc if await loc.count() else None
        what = name or ref or "a download link"
        if loc is None:
            return self._did(f"download {what}", failed(f"I couldn't find {what!r} to download on {await self._where()}. Say which link."))
        from .files import known_folder
        dest = Path(folder) if folder else known_folder("downloads")
        try:
            async with self.page.expect_download(timeout=NAV_TIMEOUT_MS) as info:
                await loc.click(timeout=ACT_TIMEOUT_MS)
            dl = await info.value
        except Exception as e:
            return self._did(f"download {what}", failed(f"No download started from {what!r}: {short(e)}"))
        target, n = dest / dl.suggested_filename, 1
        while target.exists():  # never over an existing file: "report (1).pdf"
            target, n = dest / f"{Path(dl.suggested_filename).stem} ({n}){Path(dl.suggested_filename).suffix}", n + 1
        await dl.save_as(str(target))
        ok = target.exists() and target.stat().st_size > 0
        size = f"{target.stat().st_size / 1024:.0f} KB" if target.exists() else "nothing saved"
        return self._did(f"download {what}", done(f"Downloaded {target.name} ({size}). It's in {dest}.", verified=ok, path=str(target))
                         if ok else failed(f"The download of {target.name} came out empty."))

    @_acts
    async def press(self, keys):
        await self.ensure()
        k = key_name(keys)
        await self.page.keyboard.press(k)
        await asyncio.sleep(SETTLE_S)
        return self._did(f"press {k}", done(f"Pressed {k} in {await self._where()}.", verified=None))

    @_acts
    async def scroll(self, direction="down", pages=1):
        await self.ensure()
        y0 = await self.page.evaluate("scrollY")
        await self.page.mouse.wheel(0, (-1 if direction == "up" else 1) * 700 * pages)
        await asyncio.sleep(0.3)
        y1 = await self.page.evaluate("scrollY")
        return self._did(f"scroll {direction}", done(f"Scrolled {direction}." if y1 != y0 else f"The page can't scroll further {direction}.",
                                                     verified=y1 != y0))

    # -- the owner's browser panel: what it shows, the live view, who has control -------------------------------------
    def changed(self, recast=False):
        """Something the panel shows changed: publish the view once for a burst of events; follow a new front tab."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no event loop (the offline self-check)
            return
        if recast and self.viewers:
            self._spawn(self._recast())
        if self.on_change and not self._pub:
            self._pub = loop.call_later(0.15, lambda: self._spawn(self._publish()))

    async def _place_soon(self):
        await asyncio.sleep(0.3)  # the new window exists by then
        self.place_windows()

    def _spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self._tasks = getattr(self, "_tasks", set())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def _publish(self):
        self._pub = None
        try:
            v = await self.view()
        except Exception:  # mid-navigation, or closing: the next event publishes again
            return
        if self.on_change:
            self.on_change(v)

    async def view(self):
        """The panel's state: open or not, the page, the tabs, loading, who has control, which task is using it."""
        base = {"running": bool(self.ctx), "control": self.control, "driver": self.driver, "taken": self.taken,
                "headless": self.headless, "viewers": self.viewers, "window": self.window}
        if not self.ctx or not self.page:
            return base
        tabs = []
        for i, t in enumerate(self.ctx.pages):
            try:
                title = (await t.title())[:80]
            except Exception:
                title = ""
            tabs.append({"i": i + 1, "title": title, "url": t.url[:300], "active": t is self.page})
        return {**base, "url": self.page.url, "title": next((t["title"] for t in tabs if t["active"]), ""), "tabs": tabs,
                "loading": self.loading}

    async def take(self, by="owner"):
        """Take control: from now on every action of Monday's is refused; the page, the tabs and the session stay as they are."""
        if self.control != "owner":
            title = ""
            if self.page:
                try:
                    title = await self.page.title()
                except Exception:
                    pass
            self.control = "owner"
            self.taken = {"url": self.page.url if self.page else None, "title": title, "at": time.time(), "by": by}
            self.turn.clear()
            self.changed()
        return self.taken

    def give_back(self):
        """Resume: Monday may act again. A task that waited looks at the page again before its next step."""
        was, self.control, self.taken = self.taken, "monday", None
        self.turn.set()
        self.changed()
        return was

    async def stop_loading(self):
        """Stop: whatever the page in front is still loading stops (the page itself stays)."""
        if self.page and not self.page.is_closed():
            try:
                await self.page.evaluate("window.stop()")
            except Exception:  # navigating right now: nothing to stop in the old document
                pass
        self.loading = False
        self.changed()

    async def frames(self):
        """The live view: the page in front as JPEG frames from Chrome's own screencast (the newest one; a slow viewer skips).
        Self-healing (2026-10-09: the panel stayed black when it began watching while Chrome was still launching): attaching is
        tried again until it holds, a new tab in front is followed, and a page that changed without painting anything the
        screencast saw (a still page, an error page, a process swap) is shown by one screenshot."""
        self.viewers += 1
        try:
            seq = -1
            while self.ctx:
                if self._cast is None or self._cast[0] is not self.page:
                    await self._recast()
                if self.frame is not None and self.frame_seq != seq:
                    seq = self.frame_seq
                    yield self.frame
                    await asyncio.sleep(FRAME_MIN_GAP_S)
                    continue
                self._frame_ev.clear()
                try:
                    await asyncio.wait_for(self._frame_ev.wait(), FRAME_STALL_S)
                except asyncio.TimeoutError:
                    page = self.page
                    if page is not None and not page.is_closed() and (self.frame is None or self.frame_url != page.url):
                        await self._snap(page)
                    elif self.frame is not None and time.monotonic() - self.frame_at > FRAME_KEEPALIVE_S:
                        self.frame_at = time.monotonic()
                        yield self.frame
        finally:
            self.viewers -= 1
            if not self.viewers:
                await self._uncast()

    async def _recast(self):
        """Point the screencast at the page in front. Not attached (Chrome still launching, the page mid-navigation): the caller
        tries again on its next turn."""
        await self._uncast()
        p = self.page
        if not (self.ctx and p) or p.is_closed() or not self.viewers:
            return
        try:
            cdp = await self.ctx.new_cdp_session(p)
        except Exception:
            return

        def on_frame(params):
            if not self._cast or self._cast[1] is not cdp:
                return
            self.frame, self.frame_meta = base64.b64decode(params["data"]), params.get("metadata") or {}
            self.frame_url, self.frame_at = p.url, time.monotonic()
            self.frame_seq += 1
            self._frame_ev.set()
            self._spawn(self._ack(cdp, params["sessionId"]))
        cdp.on("Page.screencastFrame", on_frame)
        try:
            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": FRAME_QUALITY,
                                                    "maxWidth": FRAME_MAX[0], "maxHeight": FRAME_MAX[1]})
        except Exception:
            try:
                await cdp.detach()
            except Exception:
                pass
            return
        self._cast = (p, cdp)

    @staticmethod
    async def _ack(cdp, sid):
        try:
            await cdp.send("Page.screencastFrameAck", {"sessionId": sid})
        except Exception:  # the page closed
            pass

    async def _snap(self, p):
        """One frame by screenshot: for a page that changed without painting anything the screencast saw."""
        try:
            shot = await p.screenshot(type="jpeg", quality=FRAME_QUALITY, timeout=ACT_TIMEOUT_MS)
            w, h = await p.evaluate("[innerWidth, innerHeight]")
        except Exception:  # navigating right now: the next turn tries again
            return
        self.frame, self.frame_meta, self.frame_url, self.frame_at = shot, {"deviceWidth": w, "deviceHeight": h}, p.url, time.monotonic()
        self.frame_seq += 1
        self._frame_ev.set()

    async def _uncast(self):
        cast, self._cast = self._cast, None
        if cast:
            try:
                await cast[1].send("Page.stopScreencast")
                await cast[1].detach()
            except Exception:  # the page is already gone
                pass

    async def owner_input(self, ev):
        async with self._keep_focus():
            return await self._owner_input(ev)

    async def _owner_input(self, ev):
        """The owner's own click, wheel or keys from the live view, sent to the page in front. x and y are fractions of the
        frame. What they type is passed through and never logged or kept."""
        p = self.page
        if not p or p.is_closed():
            raise RuntimeError("Monday's Chrome isn't open.")
        kind = ev.get("type")
        w, h = self.frame_meta.get("deviceWidth"), self.frame_meta.get("deviceHeight")
        if not (w and h):
            w, h = await p.evaluate("[innerWidth, innerHeight]")
        x, y = min(max(float(ev.get("x") or 0), 0), 1) * w, min(max(float(ev.get("y") or 0), 0), 1) * h
        if kind == "click":
            button = ev.get("button") if ev.get("button") in ("left", "right", "middle") else "left"
            await p.mouse.click(x, y, button=button, click_count=min(max(int(ev.get("count") or 1), 1), 3))
        elif kind == "wheel":
            await p.mouse.move(x, y)
            await p.mouse.wheel(float(ev.get("dx") or 0), float(ev.get("dy") or 0))
        elif kind == "key":
            await p.keyboard.press(str(ev["key"])[:40])
        elif kind == "text":
            await p.keyboard.insert_text(str(ev["text"])[:2000])
        else:
            raise ValueError(f"unknown input {kind!r}")

    async def owner_nav(self, action, url=None, i=None):
        if action == "popout":  # the one time Chrome's own window is meant to come forward
            return await self.pop(True)
        async with self._keep_focus():
            return await self._owner_nav(action, url, i)

    async def _owner_nav(self, action, url=None, i=None):
        """The owner's address bar, back/forward/reload/stop and tabs. Starts Monday's Chrome when it isn't open."""
        await self.ensure()
        p, pages = self.page, self.ctx.pages
        try:
            if action == "go":
                await p.goto(address(url or ""), wait_until="commit", timeout=NAV_TIMEOUT_MS)
            elif action == "back":
                await p.go_back(wait_until="commit", timeout=NAV_TIMEOUT_MS)
            elif action == "forward":
                await p.go_forward(wait_until="commit", timeout=NAV_TIMEOUT_MS)
            elif action == "reload":
                await p.reload(wait_until="commit", timeout=NAV_TIMEOUT_MS)
            elif action == "stop":
                await self.stop_loading()
            elif action in ("tab", "close_tab"):
                if not i or not 1 <= int(i) <= len(pages):
                    raise ValueError(f"There is no tab {i}.")
                t = pages[int(i) - 1]
                if action == "tab":
                    self.page = t
                    await self._front(t, tab=True)  # a tab that isn't in front doesn't paint: the live view needs it in front
                else:
                    await t.close()
            elif action in ("popout", "popin"):
                return await self.pop(action == "popout")
            elif action == "new_tab":
                async with self._keep_focus():
                    self.page = await self.ctx.new_page()
                if url:
                    await self.page.goto(address(url), wait_until="commit", timeout=NAV_TIMEOUT_MS)
            else:
                raise ValueError(f"unknown browser action {action!r}")
        except ValueError:
            raise
        except Exception as e:
            raise RuntimeError(f"Chrome couldn't do that: {short(e)}")
        self.changed()
        return await self.view()


def foreground_window():
    return ctypes_user32().GetForegroundWindow() if os.name == "nt" else None


def ctypes_user32():
    import ctypes
    return ctypes.windll.user32


def give_focus_back(hwnd):
    """Chrome took the keyboard when it shouldn't have: give it back to the window that had it. Windows lets only the foreground
    thread hand the foreground on, so this thread joins Chrome's input queue for the moment it takes."""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    if not hwnd or not u32.IsWindow(wintypes.HWND(hwnd)):
        return False
    fg = u32.GetForegroundWindow()
    theirs, mine = u32.GetWindowThreadProcessId(wintypes.HWND(fg), None), k32.GetCurrentThreadId()
    joined = bool(theirs) and theirs != mine and u32.AttachThreadInput(mine, theirs, True)
    try:
        ok = bool(u32.SetForegroundWindow(wintypes.HWND(hwnd)))
    finally:
        if joined:
            u32.AttachThreadInput(mine, theirs, False)
    return ok and u32.GetForegroundWindow() == hwnd


def taskbar(hwnds, show):
    """Add or remove these windows' taskbar buttons (ITaskbarList): out of sight, Chrome's window shouldn't leave a button the owner
    clicks to find nothing."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    def guid(s):
        import uuid
        u = uuid.UUID(s)
        return GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    obj = ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(guid("56FDF344-FD6D-11d0-958A-006097C9A090")), None, 1,
                              ctypes.byref(guid("56FDF342-FD6D-11d0-958A-006097C9A090")), ctypes.byref(obj)) != 0:
        return False
    vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    call = lambda i, *a, t=(): ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *t)(vt[i])(obj, *a)  # noqa: E731
    call(3)  # HrInit
    for h in hwnds:
        call(4 if show else 5, wintypes.HWND(h), t=(wintypes.HWND,))  # AddTab / DeleteTab
    call(2)  # Release
    return True


async def activate_tab(page):
    """Make this tab the one its window draws, through DevTools (Target.activateTarget)."""
    cdp = await page.context.new_cdp_session(page)
    try:
        info = await cdp.send("Target.getTargetInfo")
        await cdp.send("Target.activateTarget", {"targetId": info["targetInfo"]["targetId"]})
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


def address(text):
    """What the owner typed in the address bar: an address (http, https or about), else a Google search for the words.
    No file:// or chrome:// pages."""
    t = text.strip()
    if re.match(r"^https?://", t, re.I) or t == "about:blank":
        return t
    if re.match(r"^[a-z][\w+.-]*:", t, re.I) and not re.match(r"^(localhost|[\w.-]+\.[a-z]{2,}):\d", t, re.I):
        raise ValueError("Only web addresses (http, https) open here.")
    if " " not in t and (re.match(r"^(localhost|127\.0\.0\.1)(:\d+)?(/|$)", t) or re.search(r"^[\w-]+(\.[\w-]+)*\.[a-z]{2,}(:\d+)?([/?#]|$)", t, re.I)):
        return ("http://" if t.startswith(("localhost", "127.0.0.1")) else "https://") + t
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(t)


MJPEG_PART = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"


async def mjpeg(frames):
    """The live view as MJPEG for an <img> (`GET /api/browser/stream`). Each frame ends with the next part's boundary AND headers:
    Chromium (WebView2 too) shows a part only once the next part has begun. With the boundary alone, every frame showed one frame
    late and a still page stayed black until the keepalive (2026-10-09: one frame not shown in 8 s; with the headers, 0.1 s)."""
    yield MJPEG_PART
    async for jpg in frames:
        yield jpg + b"\r\n" + MJPEG_PART


if __name__ == "__main__":  # python -m core.chrome [--live]: offline checks; --live drives a headless Chrome (no window)
    import sys
    import tempfile
    assert key_name(["ctrl", "l"]) == "Control+L" and key_name(["enter"]) == "Enter" and key_name(["shift", "tab"]) == "Shift+Tab"
    assert short(Exception("Timeout 5000ms exceeded.\nCall log:\n  - waiting for locator")) == "Timeout 5000ms exceeded."
    assert CAPTCHA.search("https://www.google.com/sorry/index?continue=") and not CAPTCHA.search("https://www.google.com/search?q=x")
    assert "E:" in str(PROFILE) or "Monday" in str(PROFILE)

    async def offline_search():  # Google's captcha: Bing now and for a while after; a search naming Google still goes there
        c, went = Chrome(), []
        c.bing_reloaded = True  # (the wait for Bing's reload needs a live page)

        async def nav(url, **k):
            went.append(site_word(url))
            return {"ok": "google" not in url, "captcha": "google" in url, "text": "Opened it."}

        async def res(*a, **k):
            return []
        c.navigate, c.results = nav, res
        r = await c.search("jev")
        assert went == ["google", "bing"] and r["text"].startswith("Google asked for a captcha"), (went, r)
        r = await c.search("jev")
        assert went[2:] == ["bing"] and "a little while ago" in r["text"], (went, r)
        await c.search("jev", site="google")
        assert went[3:] == ["google"], went
        await c.search("Dussehra Maidan", site="images")  # Google's image search would ask too: Bing's images while the hold lasts
        assert went[4:] == ["bing"], went
        c.captcha_until = 0
        await c.search("Dussehra Maidan", site="images")
        assert went[5:] == ["google", "bing"], went
        # results about something else (Bing, to a browser it takes for a robot) are not read out; a check drawn inside the page is a captcha
        junk = [{"title": "Install Flutter", "url": "https://docs.flutter.dev/install"}, {"title": "ChatGPT MD - GitHub", "url": "https://github.com/bramses/chatgpt-md"}]
        shown, body = [junk], ["186,000 results"]
        c.navigate = lambda url, **k: asyncio.sleep(0, {"ok": True, "text": "Opened it.", "url": url, "title": "Search"})
        c.results, c._text = (lambda *a, **k: asyncio.sleep(0, shown[0])), (lambda *a: asyncio.sleep(0, body[0]))
        r = await c.search("TypeSafe Jev model")
        assert not r["ok"] and r["off_topic"] and r["text"].startswith("Bing's results aren't about “TypeSafe Jev model”.") and "Install Flutter" in r["text"], r
        assert not (await c.search("TypeSafe Jev model", site="bing"))["ok"], "an engine asked for by name is checked too"
        assert (await c.search("jev", site="youtube"))["ok"], "a site's own search is taken at its word"
        shown[0] = [{"title": "Introducing System One Models & Jev", "url": "https://typesafe.ai/blog"}] * 4
        r = await c.search("what TypeSafe's Jev model is")
        assert r["ok"] and len(r["results"]) == 3 and "Top results: Introducing System One" in r["text"] and r["query"], r
        shown[0], body[0] = [], "Unfortunately, bots use DuckDuckGo too. Please complete the following challenge to confirm this search was made by a human."
        r = await c.search("jev", site="duckduckgo")
        assert not r["ok"] and r["captcha"] and r["text"].startswith("Duckduckgo is asking to confirm a person is there"), r
        body[0] = "No results found for jev."
        assert (await c.search("jev", site="duckduckgo"))["ok"], "no results is not a captcha"
    assert not on_topic("TypeSafe Jev model", [{"title": "Install Flutter", "url": "https://docs.flutter.dev/install"}])
    assert on_topic("TypeSafe's Jev", [{"title": "What is Jev?", "url": "https://x.example"}]) and on_topic("jev", []) and on_topic("what is this", [{"title": "x", "url": "y"}])
    assert on_topic("node lts", [{"title": "Download", "url": "https://nodejs.org/en/download"}]), "the address counts, and so does the start of a longer word"
    news = [{"title": "Latest News: Today's big news headlines from India and around the …", "url": "https://www.bing.com/ck/a?1"}]
    assert not on_topic("latest Node.js LTS version", news), "one word of four is an answer to another search"
    assert on_topic("can you find me some good places to eat near the Gateway of India tonight",
                    [{"title": "THE 10 BEST Restaurants Near Gateway Of India - Tripadvisor", "url": "https://www.bing.com/ck/a?2"}]), "3 of 8, measured: real results"
    assert on_topic("latest news", news), "with two words, one is not enough to tell"
    asyncio.run(offline_search())
    # 2026-10-20: what gets an ad or a promo out of the way, and what never does (a page's or an app's own buttons)
    for words in ("Skip Ad", "Skip ads", "Skip", "Dismiss", "No thanks", "No, thanks", "Not now", "Maybe later", "Close ad", "Skip trial"):
        assert AD_DISMISS.match(words), words
    for words in ("Accept all", "Allow", "Close", "Sign in", "Subscribe", "Get Premium", "Try it free", "Skip navigation", "Install", "Buy now", "OK"):
        assert not AD_DISMISS.match(words), words

    async def two():
        yield b"A"
        yield b"B"

    async def played():
        return b"".join([part async for part in mjpeg(two())])
    assert asyncio.run(played()) == MJPEG_PART + b"A\r\n" + MJPEG_PART + b"B\r\n" + MJPEG_PART, \
        "each frame is followed by the next part's headers, or the panel shows it a frame late (black on a still page)"
    assert address("github.com") == "https://github.com" and address("localhost:8765/x") == "http://localhost:8765/x"
    assert address("https://a.example/b?c") == "https://a.example/b?c" and address("docs.python.org/3/") == "https://docs.python.org/3/"
    assert address("latest playwright docs") == "https://www.google.com/search?q=latest+playwright+docs"
    for bad in ("file:///C:/Users", "chrome://settings", "javascript:alert(1)"):
        try:
            address(bad)
            raise AssertionError(bad)
        except ValueError:
            pass

    async def offline_control():  # Take control: every action of Monday's is refused, reading is not; Resume gives it back
        c = Chrome()
        assert (await c.take("test"))["by"] == "test" and c.control == "owner" and not c.turn.is_set()
        for act in (c.navigate("x.com"), c.click(name="Go"), c.type("hi"), c.press(["enter"]), c.scroll(), c.search("jev"), c.back()):
            try:
                await act
                raise AssertionError("acted while the owner had control")
            except ManualControl:
                pass
        assert c.give_back()["by"] == "test" and c.control == "monday" and c.turn.is_set() and c.taken is None
        assert (await c.view()) == {"running": False, "control": "monday", "driver": None, "taken": None, "headless": False, "viewers": 0,
                                    "window": "panel"}, "not open; its window would live out of sight (the panel shows its pages)"
        assert Chrome(headless=True).window == "screen" and Chrome(window="screen").window == "screen"
    asyncio.run(offline_control())
    if "--live" in sys.argv:
        page = ("<title>Monday check</title><label>Name <input id=n></label> <label>Password <input type=password value=hunter2></label>"
                "<button onclick=\"document.title='hello '+n.value; console.error('checked')\">Go</button>"
                "<a href='about:blank#two'>Second page</a><div style='height:3000px'></div>")

        async def executor_chrome(call):  # what core/executor._chrome makes of a refusal
            try:
                return await call
            except ManualControl as e:
                return {"ok": False, "text": str(e), "manual": True}

        async def live():
            with tempfile.TemporaryDirectory() as d:
                c = Chrome(headless=True, profile=d)
                t0 = time.perf_counter()
                await c.ensure()
                await c.page.set_content(page)
                print(f"started in {time.perf_counter() - t0:.2f}s")
                s = await c.observe()
                roles = [(e["role"], e["name"]) for e in s["elements"]]
                assert ("textbox", "Name") in roles and ("button", "Go") in roles and ("link", "Second page") in roles, roles
                pw = next(e for e in s["elements"] if e.get("password"))
                assert "value" not in pw, "a password's value is never read"
                ref = next(e["ref"] for e in s["elements"] if e["name"] == "Name")
                r = await c.type("Monday", ref=ref)
                assert r["ok"] and r["verified"], r
                r = await c.type("secret", ref=pw["ref"])
                assert not r["ok"] and "never types passwords" in r["text"], r
                r = await c.click(name="Go")
                assert r["ok"] and await c.page.title() == "hello Monday", (r, await c.page.title())
                assert "checked" in (await c.console())["text"], "the page's console error is caught"
                assert (await c.scroll("down"))["verified"]
                assert (await c.screenshot())["verified"]
                await c.page.set_content("<a href='data:text/plain;base64,TW9uZGF5IGRvd25sb2Fk' download='notes.txt'>Get the notes</a>")
                r = await c.download(folder=d)  # no name: the first link that looks like a file
                assert r["ok"] and r["verified"] and Path(r["path"]).read_text() == "Monday download", r
                r = await c.download(name="Get the notes", folder=d)
                assert r["ok"] and Path(r["path"]).name == "notes (1).txt", ("never over an existing file", r)
                await c.page.set_content(page)
                await c.page.set_content("<a href='https://a.example/1'><h3>First result</h3></a><a href='https://a.example/2'><h3>Second one</h3></a>"
                                         "<a href='https://a.example/1'><h3>First result</h3></a><h3>Not a link</h3>")
                assert [x["title"] for x in await c.results()] == ["First result", "Second one"], "result titles: linked, deduplicated"
                await c.page.set_content("<a href='https://example.com/' target=_blank>Example in a new tab</a>")
                r = await c.click(name="Example in a new tab")  # Bing's results open like this: Monday follows the new tab
                assert r["ok"] and r["verified"] and "new tab" in r["text"] and "example.com" in c.page.url, r
                assert (await c.close_tab())["ok"] and len(c.ctx.pages) == 1
                await c.page.set_content(page)
                r = await c.new_tab("https://example.com")
                print("example.com:", r["text"], r["verified"])
                assert r["ok"] and len((await c.observe())["tabs"]) == 2
                assert (await c.switch_tab(1))["ok"] and await c.page.title() == "Monday check"
                assert (await c.close_tab(2))["ok"] and len(c.ctx.pages) == 1
                print("state:", {k: v for k, v in (await c.observe()).items() if k in ("url", "title", "tabs", "errors", "previous")})
                # the live view and the owner's hands: frames of the page in front; while they have control Monday can't click,
                # their own click through the view does, and Resume gives it back
                await c.page.set_content("<title>panel</title><button style='position:fixed;left:0;top:0;width:100vw;height:50vh' "
                                         "onclick=\"document.title='owner clicked'\">Big</button>")
                views = []
                c.on_change = views.append
                frames = c.frames()
                jpg = await asyncio.wait_for(anext(frames), 5)
                assert jpg[:2] == bytes([0xFF, 0xD8]) and c.viewers == 1 and c.frame_meta.get("deviceWidth"), (jpg[:4], c.frame_meta)
                await c.take("test")
                r = await executor_chrome(c.click(name="Big"))
                assert not r["ok"] and r.get("manual") and await c.page.title() == "panel", r
                await c.owner_input({"type": "click", "x": 0.5, "y": 0.25})
                await asyncio.sleep(0.3)
                assert await c.page.title() == "owner clicked"
                v = await c.owner_nav("new_tab", "about:blank")
                assert len(v["tabs"]) == 2 and v["tabs"][1]["active"] and v["control"] == "owner", v
                await c.owner_nav("close_tab", i=2)
                c.give_back()
                await asyncio.sleep(0.3)
                assert views and views[-1]["control"] == "monday" and views[-1]["running"], views[-1:]
                await frames.aclose()
                assert c.viewers == 0 and c._cast is None
                # a viewer that begins while attaching fails (Chrome still launching) gets a picture, and attaches on a later turn
                real, calls = c.ctx.new_cdp_session, []

                async def flaky(p):
                    calls.append(1)
                    if len(calls) == 1:
                        raise RuntimeError("Target page, context or browser has been closed")
                    return await real(p)
                c.ctx.new_cdp_session = flaky
                frames = c.frames()
                jpg = await asyncio.wait_for(anext(frames), 6)
                assert jpg[:2] == bytes([0xFF, 0xD8]), "a screenshot stands in while the screencast can't attach"
                await asyncio.wait_for(anext(frames), 8)
                assert len(calls) >= 2 and c._cast is not None, ("attaching is tried again", calls)
                await frames.aclose()
                c.ctx.new_cdp_session = real
                await c.close()
        asyncio.run(live())
    print("chrome self-check: OK")
