"""Google, through the owner's own Google account (2026-10-09): Docs, Drive, Calendar, Tasks and Sheets over Google's official
APIs; Gmail and Contacts (read only) only when the owner turns them on. Google Keep has no API for personal accounts (its API
is for Workspace domains), so notes go to keep.google.com in Monday's own Chrome: a browser route, and always said as one.

- OAuth 2.0 for installed apps with the owner's own OAuth client (Google Cloud Console > Credentials > "Desktop app"):
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in Monday/.env, or that client's JSON saved as data/google/client_secret.json.
  The consent screen is Google's own, in the owner's own browser; Monday never sees the password. Loopback redirect to this
  Core, with PKCE and a one-time state.
- Least privilege: each service asks for its own scopes only, when the owner connects it (incremental authorization).
- The refresh token is encrypted with Windows DPAPI (this Windows user on this PC) in data/google/token.bin. Access tokens
  live in memory for their hour. None of it reaches a page, a log, a worker or a model.
- Every write is read back before it is called done. A calendar event's id comes from its content, so a retry can't make
  a second one; a task with the same title made in the last DEDUPE_S is the same task. Nothing here deletes anything.
"""
import asyncio
import base64
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path

import httpx

from .computer import done, failed

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
DOCS = "https://docs.googleapis.com/v1/documents"
DRIVE = "https://www.googleapis.com/drive/v3/files"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
TASKS = "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
PEOPLE = "https://people.googleapis.com/v1/people:searchContacts"
KEEP = "https://keep.google.com/"
G = "https://www.googleapis.com/auth/"
IDENTITY = ["openid", "email"]  # which account it is, shown in Connections
SERVICES = {  # service -> what it can do, the scopes it asks for (least privilege), how it is reached
    "docs": {"label": "Google Docs", "does": "Create documents, read them and add to them", "scopes": [G + "documents", G + "drive.file"]},
    "drive": {"label": "Google Drive", "does": "Find your files by name and date (names and dates only)", "scopes": [G + "drive.metadata.readonly"]},
    "calendar": {"label": "Google Calendar", "does": "Read your events, find free time, add events (never invites anyone)", "scopes": [G + "calendar.events"]},
    "tasks": {"label": "Google Tasks", "does": "Show your tasks and add new ones", "scopes": [G + "tasks"]},
    "sheets": {"label": "Google Sheets", "does": "Create sheets and add rows", "scopes": [G + "spreadsheets"]},
    "gmail": {"label": "Gmail", "does": "Search and read your mail (read only; Monday never sends)", "scopes": [G + "gmail.readonly"], "opt_in": True},
    "contacts": {"label": "Google Contacts", "does": "Look up a contact (read only)", "scopes": [G + "contacts.readonly"], "opt_in": True},
    "keep": {"label": "Google Keep", "does": "Make notes and find them", "scopes": [], "browser": True,
             "note": "Google's Keep API is only for Workspace (business) accounts, so Monday uses keep.google.com in its own Chrome, "
                     "where you sign in to Google yourself once. That is a browser route, not an API."},
}
HTTP_TIMEOUT_S = 20
PENDING_TTL_S = 15 * 60     # a consent screen left open longer than this has to be started again
TOKEN_SLACK_S = 120         # an access token this close to expiring is renewed first
DEDUPE_S = 10 * 60          # the same new task title within this time is the same task, not a second one
TESTING_TOKEN_DAYS = 7      # Google ends a Testing-status app's connection after 7 days (its refresh token expires)
WORK_HOURS = (9, 18)        # free time is looked for inside these local hours
EVENT_MINUTES = 60
FIND_MAX = 8
KEEP_WAIT_S = 2.5           # Keep draws its notes by script after load
GMAIL_MAX = 5


class GoogleError(Exception):
    """A Google request that can't be done, said the way the owner can act on it (never a token, never a stack trace)."""


# -- DPAPI: the refresh token, encrypted for this Windows user on this PC ------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    if os.name != "nt":
        raise GoogleError("Monday keeps the Google sign-in encrypted with Windows' own data protection; this isn't Windows.")
    crypt32, kernel32 = ctypes.WinDLL("crypt32"), ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):  # 0x1: never show a prompt
        raise GoogleError("Windows couldn't " + ("encrypt" if protect else "decrypt") + " the Google sign-in.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


# -- dates, the deterministic way ---------------------------------------------------------------------------------------
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _day(d):
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def span(text, now=None):
    """The period a request names: (start, end, words). today / tonight / tomorrow / yesterday / this week / next week /
    this weekend / a weekday (the next one). None when it names none."""
    now = now or dt.datetime.now().astimezone()
    t, today = text.lower(), _day(now)
    if re.search(r"\byesterday\b", t):
        return today - dt.timedelta(days=1), today, "yesterday"
    if re.search(r"\btomorrow\b", t):
        return today + dt.timedelta(days=1), today + dt.timedelta(days=2), "tomorrow"
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", t):
        return today, today + dt.timedelta(days=1), "today"
    if re.search(r"\bnext week\b", t):
        start = today + dt.timedelta(days=7 - today.weekday())
        return start, start + dt.timedelta(days=7), "next week"
    if re.search(r"\bthis weekend\b|\bweekend\b", t):
        sat = today + dt.timedelta(days=(5 - today.weekday()) % 7)
        return max(sat, today), sat + dt.timedelta(days=2), "this weekend"
    if re.search(r"\b(this week|the week)\b", t):
        return today, today + dt.timedelta(days=7 - today.weekday()), "this week"
    m = re.search(r"\b(?:on\s+)?(?:this\s+|next\s+)?(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        d = today + dt.timedelta(days=(WEEKDAYS.index(m[1]) - today.weekday()) % 7)
        return d, d + dt.timedelta(days=1), m[1].title()
    return None


def clock(text):
    """'at 3pm' / 'at 15:30' / 'at noon' -> (hour, minute), else None."""
    t = text.lower()
    if re.search(r"\b(at\s+)?noon\b", t):
        return 12, 0
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", t) or re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b", t)
    if not m:
        return None
    h, mi, ap = int(m[1]), int(m[2] or 0), (m[3] or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return (h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


def minutes(text):
    """'for an hour' / 'for 30 minutes' / 'for 2 hours' / 'for half an hour' -> minutes, else EVENT_MINUTES."""
    t = text.lower()
    if re.search(r"\bfor half an hour\b", t):
        return 30
    if re.search(r"\bfor an? hour\b", t):
        return 60
    m = re.search(r"\bfor (\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b", t)
    if m:
        n = float(m[1])
        return int(n * 60 if m[2].startswith("h") else n)
    return EVENT_MINUTES


def event_title(text):
    """What the event is called: the request without its verb, its date, time and duration, and the calendar words."""
    t = re.sub(r"^(?:please\s+)?(?:add|create|schedule|put|book|set\s+up|make)\s+(?:an?\s+|the\s+)?", "", text.strip(), flags=re.I)
    for rx in (r"\b(?:to|on|in|into)\s+(?:my\s+|the\s+)?(?:google\s+)?calendar\b", r"\bfor half an hour\b", r"\bfor an? hour\b",
               r"\bfor \d+(?:\.\d+)?\s*(?:minutes?|mins?|hours?|hrs?)\b", r"\b(?:at\s+)?noon\b",
               r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?", r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
               r"\b(?:on\s+)?(?:this\s+|next\s+)?(?:today|tonight|tomorrow|" + "|".join(WEEKDAYS) + r")\b"):
        t = re.sub(rx, " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,.")
    return (t[:1].upper() + t[1:]) if t else None


def _rfc(d):
    return d.isoformat(timespec="seconds")


def _when_text(ev):
    s = ev.get("start") or {}
    if "date" in s:
        return f"{s['date']} (all day)"
    try:
        d = dt.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone()
        return d.strftime("%a %d %b %H:%M")
    except (KeyError, ValueError):
        return "N/A"


def event_id(summary, start, end):
    """A calendar event id from its content: a retry of the same request makes the same id, and Google refuses a second
    event with it (409), so a retry can never make a duplicate. base32hex, as Calendar requires."""
    h = hashlib.sha1(f"monday|{summary}|{_rfc(start)}|{_rfc(end)}".encode()).digest()
    return base64.b32hexencode(h).decode().lower().rstrip("=")[:26]


def doc_text(doc):
    """The plain text of a Google Doc (its body's text runs)."""
    out = []
    for el in (doc.get("body") or {}).get("content") or []:
        for pe in (el.get("paragraph") or {}).get("elements") or []:
            out.append((pe.get("textRun") or {}).get("content") or "")
    return "".join(out)


def _q(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


class Google:
    def __init__(self, root, setting, set_setting, port=8765, transport=None):
        self.dir = Path(root) / "data" / "google"
        self.env_root = Path(root)
        self.setting, self.set_setting = setting, set_setting
        self.redirect = f"http://127.0.0.1:{port}/"
        self.pending = {}           # state -> {"verifier", "services", "at"}: consent screens still open
        self._access = None         # (token, monotonic expiry); memory only
        self._acct = None           # the decrypted account (refresh token + what was granted), cached
        self.transport = transport  # tests: an httpx.MockTransport
        self.last_error = None
        self.chrome = None          # Monday's Chrome, for the Keep route (set by Monday Core)

    # -- the client and the account ------------------------------------------------------------------------------------
    def client(self):
        """(client_id, client_secret, where it came from) or (None, None, what to do)."""
        from .jev import read_env_file
        env = read_env_file(self.env_root / ".env")
        cid = (os.environ.get("GOOGLE_CLIENT_ID") or env.get("GOOGLE_CLIENT_ID") or "").strip()
        sec = (os.environ.get("GOOGLE_CLIENT_SECRET") or env.get("GOOGLE_CLIENT_SECRET") or "").strip()
        if cid and sec:
            return cid, sec, ".env"
        f = self.dir / "client_secret.json"
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            c = j.get("installed") or j.get("web") or {}
            if c.get("client_id") and c.get("client_secret"):
                return c["client_id"], c["client_secret"], "data/google/client_secret.json"
        except (OSError, ValueError):
            pass
        return None, None, ("Google needs an OAuth client of your own first: in Google Cloud Console create a project, enable the "
                            "APIs you want, add yourself as a test user, create Credentials > OAuth client ID > Desktop app, and save "
                            "its JSON as data/google/client_secret.json (or put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env).")

    def account(self):
        if self._acct is None:
            try:
                self._acct = json.loads(_dpapi((self.dir / "token.bin").read_bytes(), False))
            except (OSError, ValueError, GoogleError):
                self._acct = {}
        return self._acct or None

    def _save(self, acct):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "token.bin").write_bytes(_dpapi(json.dumps(acct).encode(), True))
        self._acct = acct

    def enabled(self, service):
        return bool((self.setting("google_services", None) or {}).get(service, not SERVICES[service].get("opt_in")))

    def set_enabled(self, service, on):
        if service not in SERVICES:
            raise GoogleError(f"Unknown Google service {service!r}.")
        cur = dict(self.setting("google_services", None) or {})
        cur[service] = bool(on)
        self.set_setting("google_services", cur)

    def granted(self, service):
        a = self.account() or {}
        return bool(a.get("refresh_token")) and set(SERVICES[service]["scopes"]) <= set(a.get("scopes") or [])

    def status(self):
        """What Connections shows: the client, the account, each service with its real state. No secret in it."""
        cid, _, where = self.client()
        a = self.account() or {}
        state = ("not_configured" if not cid else "expired" if a.get("expired") else "connected" if a.get("refresh_token")
                 else "not_connected")
        rows = []
        for sid, s in SERVICES.items():
            on, got = self.enabled(sid), self.granted(sid) if not s.get("browser") else None
            st = ("off" if not on else "browser" if s.get("browser") else "connected" if got and state == "connected"
                  else "expired" if got and state == "expired" else "not_connected")
            rows.append({"id": sid, "label": s["label"], "does": s["does"], "route": "browser" if s.get("browser") else "api",
                         "scopes": s["scopes"], "granted": got, "enabled": on, "opt_in": bool(s.get("opt_in")), "status": st,
                         "note": s.get("note")})
        at = a.get("connected_at")
        return {"state": state, "client": {"configured": bool(cid), "source": where if cid else None, "how": None if cid else where},
                "account": {"email": a.get("email"), "connected_at": at, "scopes": a.get("scopes") or [],
                            "checked_at": a.get("checked_at"),
                            "testing_expiry": at + TESTING_TOKEN_DAYS * 86400 if at else None} if a.get("refresh_token") else None,
                "detail": self.last_error, "services": rows, "redirect": self.redirect}

    # -- connect / disconnect ---------------------------------------------------------------------------------------
    def connect_url(self, services):
        """The consent screen for these services (plus what's already granted: incremental). Opened in the owner's browser."""
        cid, _, why = self.client()
        if not cid:
            raise GoogleError(why)
        want = [s for s in services if s in SERVICES and not SERVICES[s].get("browser")]
        if not want:
            raise GoogleError("Pick at least one Google service to connect (Keep needs no connection: it's the browser route).")
        scopes = sorted({*IDENTITY, *(x for s in want for x in SERVICES[s]["scopes"])})
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v["at"] < PENDING_TTL_S}
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        self.pending[state] = {"verifier": verifier, "services": want, "at": now}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        q = {"client_id": cid, "redirect_uri": self.redirect, "response_type": "code", "scope": " ".join(scopes), "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
             "include_granted_scopes": "true"}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def _http(self):
        from . import probes
        return httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, transport=self.transport, **({} if self.transport else {"verify": probes.tls()}))

    async def finish(self, state, code):
        """The redirect back from Google: the code becomes tokens (only for a state this Core started, once)."""
        p = self.pending.pop(state or "", None)
        if not p or time.time() - p["at"] > PENDING_TTL_S:
            raise GoogleError("That sign-in link was not started here, or is too old. Start Connect again from Monday.")
        cid, sec, _ = self.client()
        async with self._http() as h:
            r = await h.post(TOKEN_URL, data={"code": code, "client_id": cid, "client_secret": sec, "redirect_uri": self.redirect,
                                              "grant_type": "authorization_code", "code_verifier": p["verifier"]})
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not j.get("access_token"):
            raise GoogleError(f"Google didn't finish the sign-in: {j.get('error_description') or j.get('error') or r.status_code}.")
        old = self.account() or {}
        email = None
        if j.get("id_token"):  # straight from Google's token endpoint over TLS: read for the address only
            try:
                part = j["id_token"].split(".")[1]
                email = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("email")
            except (IndexError, ValueError):
                pass
        acct = {"refresh_token": j.get("refresh_token") or old.get("refresh_token"), "scopes": sorted(set(j.get("scope", "").split())),
                "email": email or old.get("email"), "connected_at": time.time(), "checked_at": time.time()}
        if not acct["refresh_token"]:
            raise GoogleError("Google gave no lasting sign-in (no refresh token). Remove Monday's access in your Google account, then connect again.")
        self._save(acct)
        self._access = (j["access_token"], time.monotonic() + int(j.get("expires_in", 3600)))
        self.last_error = None
        return acct

    async def disconnect(self):
        """Revoke Monday's access at Google, then forget the sign-in here. Every Google service stops at once."""
        a, revoked = self.account() or {}, None
        if a.get("refresh_token"):
            try:
                async with self._http() as h:
                    r = await h.post(REVOKE_URL, data={"token": a["refresh_token"]})
                revoked = r.status_code == 200 or (r.status_code == 400 and "invalid_token" in r.text)  # already revoked at Google
            except httpx.HTTPError:
                revoked = False
        try:
            (self.dir / "token.bin").unlink()
        except FileNotFoundError:
            pass
        self._acct, self._access, self.last_error = {}, None, None
        return {"revoked": revoked, "had_account": bool(a.get("refresh_token"))}

    async def token(self):
        """An access token, renewed with the refresh token when needed. An ended connection is said as one (Reconnect)."""
        if self._access and self._access[1] - time.monotonic() > TOKEN_SLACK_S:
            return self._access[0]
        a = self.account() or {}
        if not a.get("refresh_token"):
            raise GoogleError("Google isn't connected to Monday. Connect it in the workspace: Connections > Google.")
        cid, sec, why = self.client()
        if not cid:
            raise GoogleError(why)
        async with self._http() as h:
            r = await h.post(TOKEN_URL, data={"refresh_token": a["refresh_token"], "client_id": cid, "client_secret": sec,
                                              "grant_type": "refresh_token"})
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200:
            if j.get("error") == "invalid_grant":  # revoked at Google, or a Testing app's 7 days ran out
                self._save({**a, "expired": True})
                self.last_error = "Google ended Monday's connection (revoked, or the 7-day limit of an app in Testing). Reconnect it."
                raise GoogleError(self.last_error)
            raise GoogleError(f"Google's sign-in service answered {r.status_code}: {j.get('error') or 'error'}.")
        if a.get("expired"):
            self._save({k: v for k, v in a.items() if k != "expired"})
        self._access = (j["access_token"], time.monotonic() + int(j.get("expires_in", 3600)))
        return self._access[0]

    async def check(self):
        """A live check: renew the access token and ask Google what it grants. Updates the scopes shown in Connections."""
        tok = await self.token()
        async with self._http() as h:
            r = await h.get(TOKENINFO_URL, params={"access_token": tok})
        if r.status_code != 200:
            raise GoogleError(f"Google's token check answered {r.status_code}.")
        a = self.account() or {}
        self._save({**a, "scopes": sorted(set(r.json().get("scope", "").split())), "checked_at": time.time()})
        return self.status()

    def need(self, service):
        """Can this service be used right now? Raises with what the owner can do about it."""
        s = SERVICES[service]
        if not self.enabled(service):
            raise GoogleError(f"{s['label']} is turned off in Monday's Connections.")
        if s.get("browser"):
            return
        a = self.account() or {}
        if not a.get("refresh_token"):
            raise GoogleError(f"{s['label']} isn't connected to Monday. Connect it in the workspace: Connections > Google.")
        if a.get("expired"):
            raise GoogleError("Google ended Monday's connection (revoked, or the 7-day limit of an app in Testing). Reconnect it in Connections.")
        if not self.granted(service):
            raise GoogleError(f"{s['label']} wasn't part of what you allowed. Connect it in Connections (Google asks you for just that).")

    async def api(self, service, method, url, **kw):
        self.need(service)
        for attempt in (1, 2):
            tok = await self.token()
            async with self._http() as h:
                r = await h.request(method, url, headers={"Authorization": f"Bearer {tok}"}, **kw)
            if r.status_code == 401 and attempt == 1:
                self._access = None  # renewed once, then honest
                continue
            break
        if r.status_code >= 400:
            try:
                err = r.json().get("error") or {}
            except ValueError:
                err = {}
            msg = (err.get("message") if isinstance(err, dict) else str(err)) or r.reason_phrase
            if r.status_code == 403 and "insufficient" in (msg or "").lower():
                raise GoogleError(f"{SERVICES[service]['label']} needs a permission you haven't given Monday. Connect it again in Connections.")
            if r.status_code == 403 and ("has not been used" in (msg or "") or "is disabled" in (msg or "")):
                raise GoogleError(f"The {SERVICES[service]['label']} API is off in your Google Cloud project. Enable it there, then try again.")
            e = GoogleError(f"{SERVICES[service]['label']} answered {r.status_code}: {(msg or '')[:200]}")
            e.status = r.status_code
            raise e
        return r.json() if r.content else {}

    # -- Docs ---------------------------------------------------------------------------------------------------------
    async def doc_create(self, title, text):
        d = await self.api("docs", "POST", DOCS, json={"title": title[:200]})
        did = d["documentId"]
        if text:
            await self.api("docs", "POST", f"{DOCS}/{did}:batchUpdate",
                           json={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]})
        got = doc_text(await self.api("docs", "GET", f"{DOCS}/{did}"))
        ok = text.strip()[:200] in got if text else True
        url = f"https://docs.google.com/document/d/{did}/edit"
        return done(f"Created the Google Doc “{title}”" + (" and checked its text is there." if ok else ", but its text didn't read back."),
                    verified=ok, url=url, doc_id=did, title=title) if ok else failed(
                        f"Created “{title}” but its text didn't read back, so I'm not calling it saved: {url}", url=url, doc_id=did)

    async def doc_append(self, doc_id, text):
        """Adds `text` at the end; nothing already in the document changes."""
        d = await self.api("docs", "GET", f"{DOCS}/{doc_id}")
        before = doc_text(d)
        end = max(1, (((d.get("body") or {}).get("content") or [{}])[-1].get("endIndex") or 2) - 1)
        await self.api("docs", "POST", f"{DOCS}/{doc_id}:batchUpdate",
                       json={"requests": [{"insertText": {"location": {"index": end}, "text": ("\n" if before.strip() else "") + text}}]})
        after = doc_text(await self.api("docs", "GET", f"{DOCS}/{doc_id}"))
        kept = after.startswith(before.rstrip("\n")) and text.strip()[:200] in after
        title = d.get("title") or doc_id
        return done(f"Added it to the end of “{title}”; the rest of the document is as it was.", verified=True, doc_id=doc_id, title=title,
                    url=f"https://docs.google.com/document/d/{doc_id}/edit") if kept else failed(
                        f"I added it to “{title}”, but reading the document back didn't show it as expected. Please look at it.")

    async def doc_read(self, doc_id):
        d = await self.api("docs", "GET", f"{DOCS}/{doc_id}")
        return done(f"{d.get('title')}\n\n{doc_text(d)[:6000]}", verified=True, title=d.get("title"), doc_id=doc_id)

    async def find_files(self, query=None, days=None, docs_only=False, limit=FIND_MAX):
        """Files by name and date, newest first. With Drive connected: all of yours; with only Docs: the ones Monday made."""
        service = "drive" if self.granted("drive") and self.enabled("drive") else "docs"
        if service == "docs" and not docs_only:
            self.need("drive")
        q = ["trashed = false"]
        if docs_only:
            q.append("mimeType = 'application/vnd.google-apps.document'")
        for w in [w for w in re.findall(r"[\w'-]{2,}", query or "") if w.lower() not in ("the", "and", "my", "about", "for", "with")][:4]:
            q.append(f"name contains '{_q(w)}'")
        if days:
            q.append(f"modifiedTime > '{_rfc(days[0].astimezone(dt.timezone.utc))}' and modifiedTime < '{_rfc(days[1].astimezone(dt.timezone.utc))}'")
        j = await self.api(service, "GET", DRIVE, params={"q": " and ".join(q), "orderBy": "modifiedTime desc", "pageSize": limit,
                                                          "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"})
        files = j.get("files") or []
        scope = "" if service == "drive" else " (only the documents Monday made: connect Google Drive to search all of yours)"
        if not files:
            return done(f"No {'documents' if docs_only else 'files'} match{(' “' + query + '”') if query else ''}{scope}.", verified=True, files=[])
        rows = [f"• {f['name']} (changed {f.get('modifiedTime', '')[:10]}) {f.get('webViewLink', '')}" for f in files]
        return done(f"{len(files)} found{scope}:\n" + "\n".join(rows), verified=True, files=files)

    # -- Calendar -----------------------------------------------------------------------------------------------------
    async def events(self, start, end):
        j = await self.api("calendar", "GET", CAL, params={"timeMin": _rfc(start), "timeMax": _rfc(end), "singleEvents": "true",
                                                           "orderBy": "startTime", "maxResults": 50})
        return [e for e in j.get("items") or [] if e.get("status") != "cancelled"]

    async def agenda(self, start, end, words):
        evs = await self.events(start, end)
        if not evs:
            return done(f"Nothing on your calendar {words}.", verified=True, events=[])
        rows = [f"• {_when_text(e)}  {e.get('summary') or '(no title)'}" for e in evs]
        return done(f"{len(evs)} on your calendar {words}:\n" + "\n".join(rows), verified=True, events=evs)

    async def free_time(self, start, end, words, need=EVENT_MINUTES, now=None):
        now = now or dt.datetime.now().astimezone()
        busy = []
        for e in await self.events(start, end):
            if e.get("transparency") == "transparent" or "date" in (e.get("start") or {}):
                continue  # free-marked and all-day events don't block time
            busy.append((dt.datetime.fromisoformat(e["start"]["dateTime"].replace("Z", "+00:00")).astimezone(),
                         dt.datetime.fromisoformat(e["end"]["dateTime"].replace("Z", "+00:00")).astimezone()))
        busy.sort()
        slots, day = [], _day(start)
        while day < end and len(slots) < 5:
            lo, hi = max(day.replace(hour=WORK_HOURS[0]), now), day.replace(hour=WORK_HOURS[1])
            for b0, b1 in [b for b in busy if b[1] > lo and b[0] < hi] + [(hi, hi)]:  # the day's busy times, then its end
                if (b0 - lo).total_seconds() >= need * 60:
                    slots.append((lo, lo + dt.timedelta(minutes=need)))
                    break
                lo = max(lo, b1)
            day += dt.timedelta(days=1)
        if not slots:
            return done(f"No free {need}-minute stretch between {WORK_HOURS[0]}:00 and {WORK_HOURS[1]}:00 {words}.", verified=True, slots=[])
        rows = [f"• {a.strftime('%a %d %b %H:%M')}–{b.strftime('%H:%M')}" for a, b in slots]
        return done(f"Free {need} minutes {words} (between {WORK_HOURS[0]}:00 and {WORK_HOURS[1]}:00):\n" + "\n".join(rows),
                    verified=True, slots=[(_rfc(a), _rfc(b)) for a, b in slots])

    async def event_create(self, summary, start, end):
        """A new event on your own calendar, never with guests (no invitation leaves). Its id comes from its content: asked
        twice, it's the same event."""
        eid = event_id(summary, start, end)
        body = {"id": eid, "summary": summary, "start": {"dateTime": _rfc(start)}, "end": {"dateTime": _rfc(end)},
                "description": "Added by Monday."}
        again = False
        try:
            await self.api("calendar", "POST", CAL, json=body)
        except GoogleError as e:
            if getattr(e, "status", None) != 409:
                raise
            again = True  # it exists already: this request ran before
        got = await self.api("calendar", "GET", f"{CAL}/{eid}")
        ok = got.get("summary") == summary and got.get("status") != "cancelled"
        when = start.strftime("%a %d %b %H:%M") + "–" + end.strftime("%H:%M")
        if not ok:
            return failed(f"Google didn't show the event “{summary}” afterwards, so I'm not calling it added.")
        return done(f"{'It was already on your calendar' if again else 'Added to your calendar'}: {summary}, {when}.",
                    verified=True, event_id=eid, url=got.get("htmlLink"), duplicate=again)

    # -- Tasks --------------------------------------------------------------------------------------------------------
    async def tasks_open(self):
        j = await self.api("tasks", "GET", TASKS, params={"showCompleted": "false", "maxResults": 50})
        items = [t for t in j.get("items") or [] if t.get("title")]
        if not items:
            return done("No open tasks in Google Tasks.", verified=True, tasks=[])
        rows = [f"• {t['title']}" + (f" (due {t['due'][:10]})" if t.get("due") else "") for t in items]
        return done(f"{len(items)} open in Google Tasks:\n" + "\n".join(rows), verified=True, tasks=items)

    async def task_create(self, title, notes=None):
        recent = await self.api("tasks", "GET", TASKS, params={"showCompleted": "false", "maxResults": 100})
        now = dt.datetime.now(dt.timezone.utc)
        for t in recent.get("items") or []:  # the same title made moments ago is this request again (a retry), not a new task
            try:
                age = (now - dt.datetime.fromisoformat(t["updated"].replace("Z", "+00:00"))).total_seconds()
            except (KeyError, ValueError):
                continue
            if (t.get("title") or "").strip().lower() == title.strip().lower() and age < DEDUPE_S:
                return done(f"“{title}” is already in Google Tasks (added {int(age // 60)} min ago).", verified=True, task_id=t["id"], duplicate=True)
        t = await self.api("tasks", "POST", TASKS, json={"title": title, **({"notes": notes} if notes else {})})
        got = await self.api("tasks", "GET", f"{TASKS}/{t['id']}")
        ok = got.get("title") == title
        return done(f"Added to Google Tasks: {title}.", verified=True, task_id=t["id"]) if ok else failed(
            f"Google Tasks didn't show “{title}” afterwards, so I'm not calling it added.")

    # -- Sheets -------------------------------------------------------------------------------------------------------
    async def sheet_create(self, title, rows=None):
        s = await self.api("sheets", "POST", SHEETS, json={"properties": {"title": title}})
        sid = s["spreadsheetId"]
        if rows:
            await self.api("sheets", "POST", f"{SHEETS}/{sid}/values/A1:append", params={"valueInputOption": "RAW"}, json={"values": rows})
        got = await self.api("sheets", "GET", f"{SHEETS}/{sid}", params={"fields": "properties.title,spreadsheetUrl"})
        ok = (got.get("properties") or {}).get("title") == title
        return done(f"Created the sheet “{title}”.", verified=True, url=got.get("spreadsheetUrl"), sheet_id=sid) if ok else failed(
            f"The sheet “{title}” didn't read back.")

    # -- Gmail / Contacts (read only, opt in) -------------------------------------------------------------------------
    async def mail_find(self, query):
        j = await self.api("gmail", "GET", GMAIL, params={"q": query, "maxResults": GMAIL_MAX})
        rows = []
        for m in j.get("messages") or []:
            d = await self.api("gmail", "GET", f"{GMAIL}/{m['id']}", params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]})
            h = {x["name"]: x["value"] for x in (d.get("payload") or {}).get("headers") or []}
            rows.append(f"• {h.get('Subject', '(no subject)')}, from {h.get('From', 'N/A')} ({h.get('Date', '')[:16]})")
        return done((f"{len(rows)} message(s) for “{query}”:\n" + "\n".join(rows)) if rows else f"No mail matches “{query}”.", verified=True)

    async def contact_find(self, query):
        await self.api("contacts", "GET", PEOPLE, params={"query": "", "readMask": "names"})  # Google asks for a warm-up search first
        j = await self.api("contacts", "GET", PEOPLE, params={"query": query, "readMask": "names,emailAddresses,phoneNumbers", "pageSize": 5})
        rows = []
        for r in j.get("results") or []:
            p = r.get("person") or {}
            name = ((p.get("names") or [{}])[0]).get("displayName") or "N/A"
            bits = [x.get("value") for x in (p.get("emailAddresses") or []) + (p.get("phoneNumbers") or []) if x.get("value")]
            rows.append(f"• {name}: {', '.join(bits) or 'no email or number'}")
        return done("\n".join(rows) if rows else f"No contact matches “{query}”.", verified=True)

    # -- Keep: the browser route, in Monday's own Chrome ----------------------------------------------------------------
    async def _keep_page(self, url=KEEP):
        from .chrome import ManualControl
        self.need("keep")
        ch = self.chrome
        if ch is None:
            raise GoogleError("Monday's Chrome isn't available, and Google Keep is reached through it.")
        if ch.control == "owner":
            raise ManualControl("You have control of Monday's Chrome, so I didn't touch it. Press Resume to give it back.")
        r = await ch.navigate(url)
        p = ch.page
        if p is not None and "accounts.google.com" in p.url:
            raise GoogleError("Google Keep needs you signed in to Google in Monday's Chrome. In the browser panel press Take control, "
                              "sign in at keep.google.com yourself, then Resume. (Monday never types passwords.)")
        if not r.get("ok"):
            raise GoogleError(r.get("text") or "Google Keep didn't open.")
        await asyncio.sleep(KEEP_WAIT_S)
        return ch, p

    async def keep_create(self, title, text):
        """A new Keep note, made the way a person would in keep.google.com, then found again by searching Keep for it."""
        import re as _re
        ch, p = await self._keep_page()
        box = p.get_by_role("textbox", name=_re.compile(r"take a note", _re.I))
        if not await box.count():
            box = p.get_by_text(_re.compile(r"^Take a note", _re.I))
        if not await box.count():
            raise GoogleError("Google Keep's “Take a note…” box wasn't on the page (Keep may have changed). Nothing was saved.")
        await box.first.click()
        await asyncio.sleep(0.4)
        if title:
            t = p.get_by_role("textbox", name=_re.compile(r"^title$", _re.I))
            if await t.count():
                await t.first.click()
                await p.keyboard.insert_text(title)
        body = p.get_by_role("textbox", name=_re.compile(r"take a note|^note$", _re.I))
        if await body.count():
            await body.last.click()
        await p.keyboard.insert_text(text)
        close = p.get_by_role("button", name=_re.compile(r"^close$", _re.I))
        if await close.count():
            await close.first.click()
        else:
            await p.keyboard.press("Escape")
        await asyncio.sleep(1.0)
        probe = (title or text).strip()[:40]
        await ch.navigate(KEEP + "#search/text=" + urllib.parse.quote(probe))
        await asyncio.sleep(KEEP_WAIT_S)
        seen = text.strip()[:40].lower() in (await p.evaluate("document.body ? document.body.innerText : ''")).lower()
        name = f"“{title}”" if title else "the note"
        return done(f"Saved {name} in Google Keep (through Monday's Chrome) and found it again by searching Keep.", verified=True,
                    route="browser") if seen else failed(f"I typed {name} into Google Keep, but searching Keep didn't find it, so I'm not "
                                                         "calling it saved. Please look in Monday's Chrome.", route="browser")

    async def keep_append(self, query, text):
        """Adds `text` at the end of the Keep note a Keep search for `query` shows first; then searches Keep for the new words."""
        ch, p = await self._keep_page(KEEP + "#search/text=" + urllib.parse.quote(query))
        card = p.get_by_text(query[:40], exact=False)
        if not await card.count():
            raise GoogleError(f"No Google Keep note matches “{query}”, so nothing was added.")
        await card.first.click()
        await asyncio.sleep(0.6)
        body = p.get_by_role("textbox", name=re.compile(r"note", re.I))
        if not await body.count():
            raise GoogleError("Google Keep's note didn't open for editing (Keep may have changed). Nothing was added.")
        await body.last.click()
        await p.keyboard.press("Control+End")
        await p.keyboard.insert_text("\n" + text)
        close = p.get_by_role("button", name=re.compile(r"^close$", re.I))
        await (close.first.click() if await close.count() else p.keyboard.press("Escape"))
        await asyncio.sleep(1.0)
        await ch.navigate(KEEP + "#search/text=" + urllib.parse.quote(text.strip()[:40]))
        await asyncio.sleep(KEEP_WAIT_S)
        seen = text.strip()[:40].lower() in (await p.evaluate("document.body ? document.body.innerText : ''")).lower()
        return done(f"Added it to the Keep note about “{query}” and found it there again.", verified=True, route="browser") if seen else failed(
            f"I typed it into the Keep note about “{query}”, but searching Keep didn't find it, so I'm not calling it added.", route="browser")

    async def keep_find(self, query=None):
        """What Keep shows for a search (or its recent notes), read from the page in Monday's Chrome."""
        ch, p = await self._keep_page(KEEP + ("#search/text=" + urllib.parse.quote(query) if query else ""))
        text = (await p.evaluate("document.body ? document.body.innerText : ''")).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)[:3000]
        return done(f"Google Keep{(' for “' + query + '”') if query else ''} (read from keep.google.com in Monday's Chrome):\n\n{text}",
                    verified=True, route="browser")


if __name__ == "__main__":  # python -m core.google: offline (a fake Google over httpx.MockTransport); DPAPI for real
    import sys
    import tempfile
    now = dt.datetime(2026, 10, 9, 10, 30).astimezone()  # a Friday
    assert span("what's on tomorrow", now)[2] == "tomorrow" and span("anything today?", now)[0] == _day(now)
    assert span("this week", now)[1] == _day(now) + dt.timedelta(days=3) and span("next week", now)[0].weekday() == 0
    assert span("on tuesday", now)[0].weekday() == 1 and span("the doc from yesterday", now)[2] == "yesterday" and span("my notes", now) is None
    assert clock("tomorrow at 3pm") == (15, 0) and clock("at 15:30") == (15, 30) and clock("at noon") == (12, 0) and clock("12am") == (0, 0)
    assert clock("add a meeting to my calendar") is None and minutes("for 30 minutes") == 30 and minutes("for an hour") == 60
    assert minutes("for 2 hours") == 120 and minutes("lunch") == EVENT_MINUTES
    assert event_title("Add a meeting with Sam to my calendar tomorrow at 3pm for 30 minutes") == "Meeting with Sam"
    assert event_id("A", now, now) == event_id("A", now, now) != event_id("B", now, now) and re.fullmatch(r"[a-v0-9]{26}", event_id("A", now, now))
    assert doc_text({"body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hi\n"}}]}}]}}) == "Hi\n"
    secret = b'{"refresh_token": "1//abc"}'
    sealed = _dpapi(secret, True)
    assert secret not in sealed and _dpapi(sealed, False) == secret, "the sign-in is encrypted on disk and only this user can read it"

    class FakeGoogle:  # Google's endpoints as Monday uses them, in memory
        def __init__(self):
            self.docs, self.events, self.tasks, self.calls, self.revoked = {}, {}, {}, [], []

        def __call__(self, req):
            u, m = str(req.url), req.method
            self.calls.append((m, u.split("?")[0]))
            body = json.loads(req.content) if req.content and req.headers.get("content-type", "").startswith("application/json") else {}
            form = dict(urllib.parse.parse_qsl(req.content.decode())) if req.content and not body else {}
            J = lambda code, j: httpx.Response(code, json=j)  # noqa: E731
            if u.startswith(TOKEN_URL):
                if form.get("grant_type") == "authorization_code":
                    assert form["code_verifier"] and form["code"] == "good", form
                    idt = "x." + base64.urlsafe_b64encode(json.dumps({"email": "owner@example.com"}).encode()).decode().rstrip("=") + ".y"
                    return J(200, {"access_token": "at1", "expires_in": 3600, "refresh_token": "rt1", "id_token": idt,
                                   "scope": "openid email " + " ".join(SERVICES["docs"]["scopes"] + SERVICES["calendar"]["scopes"] + SERVICES["tasks"]["scopes"])})
                if form.get("refresh_token") == "revoked":
                    return J(400, {"error": "invalid_grant"})
                return J(200, {"access_token": "at2", "expires_in": 3600})
            if u.startswith(REVOKE_URL):
                self.revoked.append(form.get("token"))
                return httpx.Response(200)
            assert req.headers.get("authorization", "").startswith("Bearer at"), "every API call carries the access token"
            if u.startswith(DOCS):
                rest = u[len(DOCS):].split("?")[0].strip("/")
                if m == "POST" and not rest:
                    did = f"d{len(self.docs) + 1}"
                    self.docs[did] = {"title": body["title"], "text": "\n"}
                    return J(200, {"documentId": did})
                did, _, op = rest.partition(":")
                d = self.docs[did]
                if op == "batchUpdate":
                    ins = body["requests"][0]["insertText"]
                    i = ins["location"]["index"] - 1
                    d["text"] = d["text"][:i] + ins["text"] + d["text"][i:]
                    return J(200, {})
                return J(200, {"title": d["title"], "body": {"content": [{"endIndex": 1}, {"endIndex": len(d["text"]) + 1, "paragraph": {"elements": [{"textRun": {"content": d["text"]}}]}}]}})
            if u.startswith(CAL):
                rest = u[len(CAL):].split("?")[0].strip("/")
                if m == "POST":
                    if body["id"] in self.events:
                        return J(409, {"error": {"message": "The requested identifier already exists."}})
                    assert "attendees" not in body, "Monday never invites anyone"
                    self.events[body["id"]] = body
                    return J(200, body)
                if rest:
                    return J(200, {**self.events[rest], "htmlLink": "https://calendar.google.com/x"})
                return J(200, {"items": [{"summary": "Standup", "start": {"dateTime": "2026-10-09T10:00:00+05:30"}, "end": {"dateTime": "2026-10-09T11:00:00+05:30"}}]})
            if u.startswith(TASKS):
                rest = u[len(TASKS):].split("?")[0].strip("/")
                if m == "POST":
                    tid = f"t{len(self.tasks) + 1}"
                    self.tasks[tid] = {"id": tid, "title": body["title"], "updated": dt.datetime.now(dt.timezone.utc).isoformat()}
                    return J(200, self.tasks[tid])
                return J(200, self.tasks[rest]) if rest else J(200, {"items": list(self.tasks.values())})
            if u.startswith(DRIVE):
                return J(403, {"error": {"message": "Request had insufficient authentication scopes."}})
            return J(404, {"error": {"message": "not faked"}})

    async def offline():
        with tempfile.TemporaryDirectory() as d:
            store, fake = {}, FakeGoogle()
            Path(d, "data", "google").mkdir(parents=True)
            Path(d, "data", "google", "client_secret.json").write_text(json.dumps({"installed": {"client_id": "cid.apps", "client_secret": "s"}}))
            g = Google(d, lambda k, dflt=None: store.get(k, dflt), store.__setitem__, 8765, transport=httpx.MockTransport(fake))
            assert g.status()["state"] == "not_connected" and g.status()["client"]["configured"]
            try:
                await g.doc_create("x", "y")
                raise AssertionError("worked without a connection")
            except GoogleError as e:
                assert "isn't connected" in str(e)
            url = g.connect_url(["docs", "calendar", "tasks", "keep"])
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            assert q["code_challenge_method"] == "S256" and q["redirect_uri"] == "http://127.0.0.1:8765/" and q["access_type"] == "offline"
            assert "gmail" not in q["scope"] and G + "documents" in q["scope"] and q["include_granted_scopes"] == "true", "only what was asked"
            try:
                await g.finish("forged", "good")
                raise AssertionError("a state this Core didn't start was accepted")
            except GoogleError:
                pass
            acct = await g.finish(q["state"], "good")
            assert acct["email"] == "owner@example.com" and b"rt1" not in Path(d, "data", "google", "token.bin").read_bytes()
            assert q["state"] not in g.pending, "a state works once"
            st = {s["id"]: s for s in g.status()["services"]}
            assert st["docs"]["status"] == "connected" and st["drive"]["status"] == "not_connected" and st["gmail"]["status"] == "off"
            assert st["keep"]["route"] == "browser" and g.status()["account"]["testing_expiry"]
            r = await g.doc_create("Plan", "Ship the browser panel.")
            assert r["ok"] and r["verified"] and fake.docs["d1"]["text"].startswith("Ship the browser panel."), r
            r = await g.doc_append("d1", "And the Google connectors.")
            assert r["ok"] and fake.docs["d1"]["text"].startswith("Ship the browser panel.") and "And the Google connectors." in fake.docs["d1"]["text"], fake.docs
            try:
                await g.find_files("plan", docs_only=False)
                raise AssertionError("searched Drive without Drive's permission")
            except GoogleError as e:
                assert "Google Drive" in str(e)
            start = now.replace(hour=15, minute=0)
            r1 = await g.event_create("Meeting with Sam", start, start + dt.timedelta(hours=1))
            r2 = await g.event_create("Meeting with Sam", start, start + dt.timedelta(hours=1))
            assert r1["verified"] and not r1["duplicate"] and r2["duplicate"] and len(fake.events) == 1, "a retry never makes a second event"
            ag = await g.agenda(_day(now), _day(now) + dt.timedelta(days=1), "today")
            assert "Standup" in ag["text"]
            nine = _day(now).replace(hour=9)  # Standup 10:00-11:00 (+05:30): the first free hour after it, never before "now"
            fr = await g.free_time(_day(now), _day(now) + dt.timedelta(days=1), "today", 60, now=nine)
            first = dt.datetime.fromisoformat(fr["slots"][0][0])
            standup_end = dt.datetime.fromisoformat("2026-10-09T11:00:00+05:30")
            standup_start = dt.datetime.fromisoformat("2026-10-09T10:00:00+05:30")
            assert first >= nine and (first + dt.timedelta(hours=1) <= standup_start or first >= standup_end), fr
            r1, r2 = await g.task_create("Call the bank"), await g.task_create("call the bank")
            assert r1["verified"] and r2.get("duplicate") and len(fake.tasks) == 1, "a retry never makes a second task"
            g.set_enabled("tasks", False)
            try:
                await g.tasks_open()
                raise AssertionError("a service turned off still ran")
            except GoogleError as e:
                assert "turned off" in str(e)
            g._access = None  # Google revoked it at their end: the next call says so, and Connections shows it
            g._save({**g.account(), "refresh_token": "revoked"})
            try:
                await g.doc_read("d1")
                raise AssertionError("an ended connection still worked")
            except GoogleError as e:
                assert "Reconnect" in str(e) and g.status()["state"] == "expired"
            out = await g.disconnect()
            assert out["had_account"] and fake.revoked == ["revoked"] and g.status()["state"] == "not_connected"
            assert not Path(d, "data", "google", "token.bin").exists()
            try:
                await g.doc_read("d1")
                raise AssertionError("worked after disconnect")
            except GoogleError as e:
                assert "isn't connected" in str(e)
    asyncio.run(offline())
    print("google self-check: OK" + ("" if "--live" not in sys.argv else ""))
