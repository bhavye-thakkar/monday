"""The phone companion's transport (docs/adr/0009): a second, narrow, authenticated HTTPS listener beside the loopback-only Core.

Core itself keeps binding 127.0.0.1 with its guard and window token untouched. This module serves the Flutter app in E:\\monday\\mobile
on its own port (MONDAY_REMOTE_PORT, default 8790) from inside the same process, only while the owner's setting `remote_access` is on
(Settings → Phone; off by default). It calls the same Monday methods the window uses (command, stop_task, decide_permission, the bus)
and keeps no state of its own but the `devices` table (db migration 12) and, in memory, the live pairing code, the limits and the
streams.

- Transport: HTTPS with a self-signed certificate made once (data/remote/cert.pem + key.pem); the phone pins its SHA-256 fingerprint,
  learned in the pairing answer. Across networks ngrok runs as a child only when NGROK_AUTHTOKEN is set (the environment or .env) and
  MONDAY_TUNNEL isn't "0"; without it the companion works on the LAN only, and state() says so in words.
- Pairing: an 8-digit code from `secrets`, PAIR_CODE_S long, single use, kept only as a hash; PAIR_TRIES wrong codes a minute per
  address, then a PAIR_LOCKOUT_S lockout. The phone receives, once, a device id and a secret; only sha256(secret) is stored.
- Every other route: `Authorization: Bearer <device_id>.<secret>`, a constant-time compare; 401 "unknown" | "revoked" | "expired";
  RATE requests a minute per device. The allow-list below is the whole surface: no settings, keys, files, engines, shutdown, pages.
- A phone's decision is the owner's Approve / Deny on one pending ask (only with the device's `approve` capability); never "always":
  router.permit and the standing grants stay Monday's own (AGENTS.md rule 3).
- Audit: every mutation writes a receipt with the device's name and the route, never a token, a secret or a code.

python -m core.remote    self-check: the sub-app driven in-process against a stand-in Core (a real core.db on a scratch path).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
REMOTE_HOST = "0.0.0.0"          # every interface: the phone comes in over the LAN (or ngrok, which connects to 127.0.0.1)
REMOTE_PORT = 8790               # MONDAY_REMOTE_PORT overrides it
PAIR_CODE_S = 120                # a pairing code lives this long
PAIR_TRIES = 5                   # wrong codes a minute per address before the lockout
PAIR_LOCKOUT_S = 300
RATE = (60, 60.0)                # requests per device in a window of seconds
ROTATE_GRACE_S = 120             # the previous secret stays good this long after a rotation
SSE_PING_S = 15
AGENTS_EVERY_S = 1.0             # the AGENTS snapshot a stream gets at most this often
SEEN_EVERY_S = 60.0              # last_seen / last_address written at most this often
REFUSED_RECEIPT_S = 60.0         # refusals are counted; one receipt per address per this many seconds
RECENT_S = 600                   # finished tasks still shown among the agents
TEXT_MAX = 20000                 # a command's length
NAME_MAX = 40                    # a device's name
CERT_DAYS = 3650                 # the self-signed certificate's life
CERT_CN = "Monday"
NGROK_EXE = ROOT.parent / "ngrok" / "ngrok.exe"   # E:\monday\ngrok\ngrok.exe (2026-10-10); else `ngrok` on PATH
TUNNEL_RETRY_S = 15              # ngrok gone: started again after this
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TEST_SOURCE = "preflight"        # Monday.TEST: the preflight's rows never reach the phone
CAPABILITIES = ("approve", "control")
CODERS = ("claude_code", "codex", "gemini_cli", "kilo", "grok_build")  # agents.HEADLESS; the rest are computer agents or engines
STATUS_WORDS = {"QUEUED": "queued", "STARTING": "running", "RUNNING": "running", "WAITING": "waiting", "WAITING_FOR_PERMISSION": "waiting",
                "WAITING_FOR_USER": "waiting", "COMPLETED": "done", "FAILED": "failed", "CANCELLED": "cancelled"}
STEP_WORDS = {"running": "running", "done": "done", "failed": "failed", "stopped": "stopped"}  # anything else is pending
TASK_TYPES = ("TASK_CREATED", "TASK_STARTED", "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED")
PERMISSION_ENDS = ("PERMISSION_GRANTED", "PERMISSION_DENIED", "PERMISSION_EXPIRED")
CLOSE = "_CLOSE"                 # a stream's own stop word (never sent)


def _log(text):
    print(time.strftime("%H:%M:%S"), f"phone companion: {text}", flush=True)


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _sse(eid, typ, payload):
    head = f"id: {eid}\n" if eid else ""
    return f"{head}event: {typ}\ndata: {json.dumps(payload, default=str)}\n\n"


def placeholder_id(stream_id):
    """A streamed reply's negative placeholder id on the phone: stable for one stream id, below 2^31 in size, never 0."""
    n = int.from_bytes(hashlib.sha256(str(stream_id or "").encode()).digest()[:4], "big")
    return -((n % (2 ** 31 - 1)) + 1)


def lan_ips():
    """This PC's own IPv4 addresses (loopback left out), the one its default route uses first."""
    out = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent: the route decides the address
            out.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ipaddress.ip_address(ip).is_loopback:
                out.append(ip)
    except (OSError, ValueError):
        pass
    return [ip for ip in out if not ipaddress.ip_address(ip).is_loopback]


def _words(detail):
    if isinstance(detail, dict):
        return ", ".join(f"{k}: {v}" for k, v in detail.items() if v not in (None, "", [], {}))
    if isinstance(detail, list):
        return ", ".join(str(x) for x in detail)
    return str(detail or "")


def _text(v):
    return v if isinstance(v, str) or v is None else json.dumps(v, default=str)


class PairIn(BaseModel):
    code: str = ""
    name: str = ""
    platform: str = ""
    app_version: str = ""


class CommandIn(BaseModel):
    text: str = ""
    client_id: str | None = None


class _Nosniff:
    """X-Content-Type-Options on every answer (a pure ASGI middleware: it leaves the event stream alone)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send2(msg):
            if msg["type"] == "http.response.start":
                msg["headers"] = [*msg.get("headers", []), (b"x-content-type-options", b"nosniff")]
            await send(msg)
        await self.app(scope, receive, send2)


class Remote:
    def __init__(self, core, host=REMOTE_HOST, port=None):
        self.core = core
        self.host = host
        self.port = int(port if port is not None else os.environ.get("MONDAY_REMOTE_PORT") or REMOTE_PORT)
        self.app = self._build()
        self.on = False
        self.error = None            # why the listener isn't up, in words
        self.fingerprint = None      # the certificate's SHA-256, hex
        self.public = None           # ngrok's https address while it is up
        self.tunnel = ("not_configured", "")
        self.env = None              # (name) -> value for NGROK_*: jev.setting (the environment, then .env); a self-check sets its own
        self.server = self._serve_task = self._sock = None
        self._ngrok = self._ngrok_task = None
        self._ngrok_err = None
        self._pairing = None         # (sha256 of the live code, expires_at); the code itself is never kept
        self._pair_fails, self._pair_lock = {}, {}   # address -> recent wrong tries; address -> locked until
        self._rate, self._seen = {}, {}              # device id -> recent request times; device id -> last_seen written at
        self._streams = {}                           # device id -> its open event streams' queues
        self._refused = {}                           # address -> {"since", "counts"}: refusals, one receipt a minute

    # -- the PC ---------------------------------------------------------------------------------------------------------------------
    def pc(self):
        pid = self.core.db.setting("pc_id")
        if not pid:
            pid = secrets.token_hex(8)
            self.core.db.set_setting("pc_id", pid)
        return {"id": pid, "name": socket.gethostname(), "version": self._version()}

    def _version(self):
        v = getattr(self.core, "version", None)
        if v:
            return v
        try:
            from .monday import VERSION
            return VERSION
        except Exception:
            return ""

    def _running(self):
        return sum(1 for r in getattr(self.core, "runs", {}).values() if not getattr(r, "background", False))

    def _env(self, name):
        if self.env is None:
            from . import jev  # (lazy: the self-check sets its own reader)
            self.env = jev.setting
        return self.env(name)

    # -- lifecycle --------------------------------------------------------------------------------------------------------------------
    async def start(self):
        """The listener, when `remote_access` is on. -> True when it listens; False with self.error in words otherwise."""
        if not self.core.db.setting("remote_access", False):
            self.on, self.error = False, None
            _log("off")
            return False
        if self.server:
            return True
        try:
            cert, key = await asyncio.to_thread(self._ensure_cert)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((self.host, self.port))   # bound here, not by uvicorn: its own bind failure calls sys.exit in the loop
            sock.listen(128)
            self.port = sock.getsockname()[1]
        except OSError as e:
            self.on, self.error = False, f"can't listen on {self.host}:{self.port}: {e}"
            _log(self.error)
            return False
        import uvicorn
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", access_log=False, log_config=None,
                                lifespan="off", ssl_certfile=str(cert), ssl_keyfile=str(key), timeout_graceful_shutdown=2)
        self.server, self._sock, self.error = uvicorn.Server(config), sock, None
        self._serve_task = self.core.spawn(self._serve(sock))
        self.on = True
        lan = lan_ips()
        _log(f"on: https://{lan[0] if lan else self.host}:{self.port} (certificate {self.fingerprint[:16]}…)")
        self._tunnel_start()
        return True

    async def _serve(self, sock):
        try:
            await self.server._serve(sockets=[sock])  # (not serve(): that captures the process signals the main server owns)
        except Exception as e:
            self.error = f"listener stopped: {type(e).__name__}: {e}"
            _log(self.error)
        finally:
            self.server, self.on = None, False
            try:
                sock.close()
            except OSError:
                pass

    async def stop(self):
        self.on = False
        await self._tunnel_stop()
        for qs in list(self._streams.values()):
            for q in list(qs):
                q.put_nowait({"type": CLOSE})
        if self.server:
            self.server.should_exit = True
        if self._serve_task:
            try:
                await asyncio.wait_for(self._serve_task, 6)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        self.server = self._serve_task = self._sock = None
        _log("stopped")

    def state(self):
        """Settings → Phone's words: on, the addresses, the tunnel, how many devices, the fingerprint. Nothing claimed that isn't so."""
        on = bool(self.on and self.server)
        tunnel, why = self.tunnel if on else self._tunnel_plan()
        n = (self.core.db.one("SELECT COUNT(*) AS n FROM devices WHERE revoked_at IS NULL") or {}).get("n") or 0
        return {"on": on, "port": self.port, "lan": [f"https://{ip}:{self.port}" for ip in lan_ips()] if on else [],
                "public": self.public if on else None, "tunnel": tunnel, "why": why, "devices": n, "fingerprint": self.fingerprint,
                "error": self.error}

    # -- the certificate ----------------------------------------------------------------------------------------------------------------
    def _ensure_cert(self):
        """data/remote/cert.pem + key.pem beside this Core's database, made once; -> (cert path, key path); self.fingerprint set."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        folder = self.core.db.path.parent / "remote"
        folder.mkdir(parents=True, exist_ok=True)
        cert, key = folder / "cert.pem", folder / "key.pem"
        if not (cert.is_file() and key.is_file()):
            import datetime as dt
            priv = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CERT_CN)])
            sans = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in lan_ips()]
            now = dt.datetime.now(dt.timezone.utc)
            built = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(priv.public_key())
                     .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
                     .not_valid_after(now + dt.timedelta(days=CERT_DAYS))
                     .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                     .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                     .sign(priv, hashes.SHA256()))
            key.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            try:
                os.chmod(key, 0o600)  # (Windows keeps the folder's rights; the owner's account is the only reader of data/ anyway)
            except OSError:
                pass
            cert.write_bytes(built.public_bytes(serialization.Encoding.PEM))
            _log(f"certificate made: {cert}")
        self.fingerprint = x509.load_pem_x509_certificate(cert.read_bytes()).fingerprint(hashes.SHA256()).hex()
        return cert, key

    # -- ngrok ----------------------------------------------------------------------------------------------------------------------------
    def _tunnel_plan(self):
        """(state, why) before anything runs: what is configured, in words naming what to do."""
        if os.environ.get("MONDAY_TUNNEL") == "0":
            return "not_configured", "MONDAY_TUNNEL=0: no tunnel; the phone reaches this PC on its LAN only."
        if not self._env("NGROK_AUTHTOKEN"):
            return "not_configured", (f"On the LAN only: no NGROK_AUTHTOKEN. For other networks add NGROK_AUTHTOKEN=<your ngrok authtoken> "
                                      f"to {ROOT / '.env'} (ngrok dashboard → Your Authtoken), then turn Phone off and on.")
        if not (NGROK_EXE.is_file() or shutil.which("ngrok")):
            return "failed", f"ngrok isn't at {NGROK_EXE} or on PATH; on the LAN only."
        return "starting", "ngrok starts with the listener."

    def _tunnel_start(self):
        self.tunnel = self._tunnel_plan()
        if self.tunnel[0] != "starting":
            _log(f"tunnel: {self.tunnel[1]}")
            return
        exe = NGROK_EXE if NGROK_EXE.is_file() else shutil.which("ngrok")
        self._ngrok_task = self.core.spawn(self._tunnel_loop(str(exe), self._env("NGROK_AUTHTOKEN")))

    async def _tunnel_loop(self, exe, token):
        domain = self._env("NGROK_DOMAIN")
        argv = [exe, "http", "--inspect=false", "--log=stdout", "--log-format=json", *([f"--domain={domain}"] if domain else []),
                f"127.0.0.1:{self.port}"]
        from . import probes
        env = {**probes.worker_env(), "NGROK_AUTHTOKEN": token}  # the token in the child's environment, never on its command line
        while self.on:
            try:
                proc = await asyncio.create_subprocess_exec(*argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            except OSError as e:
                self.tunnel = ("failed", f"ngrok couldn't start: {e}")
                _log(self.tunnel[1])
                return
            self._ngrok, self._ngrok_err, self.public = proc, None, None
            self.tunnel = ("starting", "ngrok is starting")
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                self._ngrok_line(line.decode("utf-8", "replace").rstrip(), token)
            code = await proc.wait()
            self._ngrok, self.public = None, None
            if not self.on:
                return
            self.tunnel = ("failed", f"ngrok exited (code {code}): {self._ngrok_err or 'no reason given'}; starting it again in {TUNNEL_RETRY_S} s")
            _log(self.tunnel[1])
            await asyncio.sleep(TUNNEL_RETRY_S)

    def _ngrok_line(self, line, token):
        text = line.replace(token, "[redacted]") if token else line
        try:
            j = json.loads(text)
        except ValueError:
            j = {}
        if j.get("msg") == "started tunnel" and str(j.get("url", "")).startswith("https://"):
            self.public = j["url"]
            self.tunnel = ("online", f"ngrok: {self.public}")
        elif j.get("err") or j.get("lvl") in ("eror", "crit"):
            self._ngrok_err = str(j.get("err") or j.get("msg"))[:300]
        _log(f"ngrok: {text[:400]}")

    async def _tunnel_stop(self):
        proc, self._ngrok = self._ngrok, None
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except OSError:
                pass
        if self._ngrok_task:
            try:
                await asyncio.wait_for(self._ngrok_task, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        self._ngrok_task, self.public = None, None

    # -- the owner's side (Monday's window, through core/app.py with the window token) -----------------------------------------------
    def start_pairing(self):
        """A new 8-digit code, PAIR_CODE_S long, single use; only its hash stays here. The window shows the code and the addresses."""
        code = f"{secrets.randbelow(10 ** 8):08d}"
        until = time.time() + PAIR_CODE_S
        self._pairing = (_sha(code), until)
        return {"code": code, "expires_at": until, "on": bool(self.on),
                "addresses": {"lan": [f"https://{ip}:{self.port}" for ip in lan_ips()], "public": self.public}}

    def devices(self):
        rows = self.core.db.all("SELECT id, name, platform, app_version, capabilities, created_at, last_seen, last_address, revoked_at, "
                                "rotated_at FROM devices ORDER BY id DESC")
        return [{**r, "id": str(r["id"]), "capabilities": self._caps(r)} for r in rows]

    def revoke(self, device_id, by="window"):
        """The device's credential is dead from now on; its open streams hear DEVICE_REVOKED and close. -> False when there is no such device."""
        row = self.core.db.one("SELECT * FROM devices WHERE id=?", int(device_id))
        if not row:
            return False
        if not row.get("revoked_at"):
            self.core.db.update("devices", "id", row["id"], revoked_at=time.time())
        self.core.receipt(None, "remote.revoke", "success", {"device": row.get("name"), "by": by, "route": "DELETE /remote/devices"})
        self.core.emit("DEVICE_REVOKED", {"device_id": str(row["id"]), "name": row.get("name")}, persist=False)
        return True

    def set_capability(self, device_id, key, value):
        if key not in CAPABILITIES:
            raise ValueError(f"No such capability: {key}. One of {', '.join(CAPABILITIES)}.")
        row = self.core.db.one("SELECT * FROM devices WHERE id=?", int(device_id))
        if not row:
            raise KeyError("No such device.")
        caps = {**self._caps(row), key: bool(value)}
        self.core.db.update("devices", "id", row["id"], capabilities=caps)
        self.core.receipt(None, "remote.capability", "success", {"device": row.get("name"), key: bool(value)})
        return caps

    # -- helpers ------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _caps(row):
        c = row.get("capabilities")
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except ValueError:
                c = None
        c = c if isinstance(c, dict) else {}
        return {k: bool(c.get(k, True)) for k in CAPABILITIES}

    @staticmethod
    def _addr(request):
        return request.client.host if request.client else "?"

    def _refuse(self, request, reason, status, detail):
        """Count the refusal (one `remote.refused` receipt per address per REFUSED_RECEIPT_S, with the counts) and raise it."""
        addr, now = self._addr(request), time.time()
        r = self._refused.setdefault(addr, {"since": now, "counts": {}})
        r["counts"][reason] = r["counts"].get(reason, 0) + 1
        if now - r["since"] >= REFUSED_RECEIPT_S or sum(r["counts"].values()) == 1:
            self.core.receipt(None, "remote.refused", "refused", {"address": addr, "route": f"{request.method} {request.url.path}",
                                                                  "counts": dict(r["counts"])})
            self._refused[addr] = {"since": now, "counts": {}}
        raise HTTPException(status, detail)

    def _limited(self, device_id):
        q, now = self._rate.setdefault(device_id, deque()), time.time()
        while q and q[0] < now - RATE[1]:
            q.popleft()
        if len(q) >= RATE[0]:
            return True
        q.append(now)
        return False

    def _locked_for(self, addr):
        left = self._pair_lock.get(addr, 0) - time.time()
        return left if left > 0 else 0

    def _pair_failed(self, addr):
        q, now = self._pair_fails.setdefault(addr, deque()), time.time()
        while q and q[0] < now - 60:
            q.popleft()
        q.append(now)
        if len(q) >= PAIR_TRIES:
            self._pair_lock[addr] = now + PAIR_LOCKOUT_S
            q.clear()

    def _code_ok(self, code):
        p = self._pairing
        return bool(p and p[1] >= time.time() and hmac.compare_digest(_sha((code or "").strip()), p[0]))

    async def _device(self, request):
        """The device behind `Authorization: Bearer <id>.<secret>`, or 401 {"detail": unknown | revoked | expired}; then the rate limit."""
        scheme, _, cred = (request.headers.get("authorization") or "").partition(" ")
        did, _, secret = cred.strip().partition(".")
        if scheme.lower() != "bearer" or not did.isdigit() or not secret:
            self._refuse(request, "unknown", 401, "unknown")
        row = self.core.db.one("SELECT * FROM devices WHERE id=?", int(did))
        if not row:
            self._refuse(request, "unknown", 401, "unknown")
        digest, now = _sha(secret), time.time()
        if not hmac.compare_digest(digest, row.get("token_hash") or ""):
            if row.get("previous_hash") and hmac.compare_digest(digest, row["previous_hash"]):
                if (row.get("previous_until") or 0) < now:
                    self._refuse(request, "expired", 401, "expired")
            else:
                self._refuse(request, "unknown", 401, "unknown")
        if row.get("revoked_at"):
            self._refuse(request, "revoked", 401, "revoked")
        if self._limited(row["id"]):
            self._refuse(request, "rate", 429, "too many requests")
        if now - self._seen.get(row["id"], 0) >= SEEN_EVERY_S:
            self._seen[row["id"]] = now
            self.core.db.update("devices", "id", row["id"], last_seen=now, last_address=self._addr(request))
        row["capabilities"] = self._caps(row)
        return row

    def _receipt(self, task_id, tool, status, device, route, **more):
        self.core.receipt(task_id, tool, status, {"device": device.get("name"), "route": route, **more})

    # -- the phone's shapes (lib/core/models.dart) -----------------------------------------------------------------------------------
    @staticmethod
    def _skip(m):
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        return (m.get("source") == TEST_SOURCE or meta.get("chatroom") or m.get("kind") == "permission"
                or (m.get("kind") == "system" and meta.get("quiet")))

    @staticmethod
    def _message(m):
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        role = "user" if m.get("role") == "user" else "system" if m.get("kind") == "system" else "assistant"
        text = m.get("text") or ""
        if m.get("kind") == "task" and not text:
            t = m.get("task") if isinstance(m.get("task"), dict) else {}
            text = t.get("title") or m.get("task_title") or t.get("prompt") or m.get("task_prompt") or ""
        return {"id": m["id"], "role": role, "text": text, "ts": m.get("created_at"), "task_id": m.get("task_id"),
                "source": m.get("source") or "", "model": meta.get("model")}

    @staticmethod
    def _progress(steps):
        steps = [s for s in steps or [] if isinstance(s, dict)]
        total = max((s.get("total") or 0 for s in steps), default=0)
        return {"done": sum(1 for s in steps if s.get("status") == "done"), "total": total} if total else None

    @staticmethod
    def _step(s):
        status = STEP_WORDS.get(s.get("status"), "pending")
        v = s.get("verified")
        verified = ("true" if v is True else "false" if v is False else "unknown") if status in ("done", "failed") else None
        ms = s.get("ms")
        return {"n": s.get("index"), "tool": s.get("tool"), "action": s.get("action"), "target": s.get("target"), "status": status,
                "verified": verified, "took": round(ms / 1000, 3) if isinstance(ms, (int, float)) else None}

    def _task(self, t):
        steps = [s for s in (t.get("steps") or []) if isinstance(s, dict)]
        return {"id": t["id"], "prompt": t.get("prompt") or t.get("title") or "", "status": STATUS_WORDS.get(t.get("status"), str(t.get("status") or "").lower()),
                "agent": t.get("agent") or "", "started": t.get("started_at"), "ended": t.get("finished_at"), "result": _text(t.get("result")),
                "error": t.get("error"), "steps": [self._step(s) for s in steps], "progress": self._progress(steps), "source": t.get("source") or ""}

    @staticmethod
    def _approval(p):
        what = p.get("title") or ""
        if p.get("detail"):
            what = f"{what}: {_words(p['detail'])}" if what else _words(p["detail"])
        return {"id": str(p["id"]), "task_id": p.get("task_id"), "what": what, "risk": p.get("risk"), "asked_at": p.get("created_at"),
                "agent": p.get("who") or p.get("agent")}

    @staticmethod
    def _kind(run):
        route = getattr(run, "route", None) or {}
        if route.get("bot"):
            return "bot"
        if route.get("tool") == "research" or route.get("kind") == "research":
            return "research"
        return "coder" if getattr(run, "agent", "") in CODERS else "computer"

    def agents(self):
        """The island's real rows for the phone: what runs now, what just finished (RECENT_S), the bots, the owner's own sessions."""
        core, db, now = self.core, self.core.db, time.time()
        pending = {p["task_id"] for p in db.all("SELECT task_id FROM permissions WHERE status='PENDING'")}
        try:
            from .router import AGENT_NAMES
        except Exception:
            AGENT_NAMES = {}
        rows = []
        runs = getattr(core, "runs", {})
        for tid, run in reversed(list(runs.items())):
            t = db.task(tid) or {}
            steps = [s for s in (t.get("steps") or []) if isinstance(s, dict)]
            last = steps[-1] if steps else {}
            waiting = tid in pending
            rows.append({"id": tid, "name": (getattr(run, "route", None) or {}).get("bot") or AGENT_NAMES.get(run.agent, run.agent),
                         "kind": self._kind(run), "task_id": tid,
                         "state": "waiting" if waiting or str(getattr(run, "status", "")).startswith("WAITING") else "working",
                         "step": t.get("current_action") or last.get("label") or "", "tool": last.get("tool") or "", "waiting": waiting,
                         "progress": self._progress(steps), "result": None, "error": None})
        for t in db.all("SELECT * FROM tasks WHERE finished_at > ? AND status IN ('COMPLETED','FAILED','CANCELLED') "
                        "ORDER BY finished_at DESC LIMIT 10", now - RECENT_S):
            if t["id"] in runs:
                continue
            route = t.get("route") if isinstance(t.get("route"), dict) else {}
            rows.append({"id": t["id"], "name": route.get("bot") or AGENT_NAMES.get(t.get("agent"), t.get("agent")),
                         "kind": "bot" if route.get("bot") else "coder" if t.get("agent") in CODERS else "computer", "task_id": t["id"],
                         "state": "done" if t["status"] == "COMPLETED" else "failed", "step": t.get("current_action") or "", "tool": "",
                         "waiting": False, "progress": self._progress(t.get("steps")), "result": _text(t.get("result")),
                         "error": t.get("error") or ("Stopped" if t["status"] == "CANCELLED" else None)})
        try:
            from . import bots
            for b in bots.all_(db):
                st = bots.status(db, b)
                last = st.get("last") or {}
                if st["state"] != "idle" and last.get("id") in runs:
                    continue  # its task's own row says it
                rows.append({"id": f"bot:{b['name']}", "name": b["name"], "kind": "bot", "task_id": last.get("id") if st["state"] != "idle" else None,
                             "state": st["state"], "step": last.get("title") or "", "tool": b.get("agent") or "", "waiting": st["state"] == "waiting",
                             "progress": None, "result": None, "error": None})
        except Exception as e:  # bots are a window feature; without them the list is still right
            _log(f"bots not listed: {e!r}"[:200])
        for w in getattr(core, "watched", None) or []:
            rows.append({"id": f"watched:{w.get('key')}", "name": w.get("name") or w.get("agent") or "Session", "kind": "watched", "task_id": None,
                         "state": w.get("state") if w.get("state") in ("working", "done", "idle") else "idle", "step": w.get("title") or "",
                         "tool": w.get("agent") or "", "waiting": False, "progress": None, "result": None, "error": None})
        return rows

    # -- the event stream ---------------------------------------------------------------------------------------------------------------
    def _frames(self, ev):
        """One bus event -> the SSE frames the phone gets (none for a type it doesn't show), the data translated to its shapes."""
        core, typ, d = self.core, ev.get("type"), ev.get("data") or {}
        if typ == "MESSAGE_CREATED":
            m = d.get("message") or {}
            if not m or self._skip(m):
                return []
            meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
            out = {**self._message(m), "client_id": m.get("client_id"),
                   "replaces": placeholder_id(meta["stream_id"]) if meta.get("stream_id") else None}
        elif typ == "MESSAGE_DELTA":
            if d.get("chatroom") or not d.get("stream_id"):
                return []
            out = {"message_id": placeholder_id(d["stream_id"]), "delta": d.get("delta") or "", "model": d.get("model"), "done": False}
        elif typ in TASK_TYPES or typ in ("TASK_STEP", "TASK_PROGRESS"):
            view = d.get("task") if typ in TASK_TYPES and isinstance(d.get("task"), dict) else (core.task_view(ev.get("task_id")) if ev.get("task_id") else None)
            if not view:
                return []
            typ = "TASK_PROGRESS" if typ == "TASK_STEP" else typ
            out = self._task(view)
        elif typ == "PERMISSION_REQUIRED":
            if not isinstance(d.get("permission"), dict):
                return []
            out = self._approval(d["permission"])
        elif typ in PERMISSION_ENDS:
            p = d.get("permission") if isinstance(d.get("permission"), dict) else {}
            if p.get("id") is None:
                return []
            typ = "PERMISSION_EXPIRED" if p.get("status") == "EXPIRED" else typ
            out = {"id": str(p["id"])}
        elif typ == "CORE_STATE":
            out = {"state": d.get("state"), "tasks_running": self._running()}
        elif typ == "DEVICE_REVOKED":
            out = {"device_id": d.get("device_id")}
        else:
            return []
        return [_sse(ev.get("id"), typ, {"type": typ, "id": ev.get("id"), "ts": ev.get("ts"), "task_id": ev.get("task_id"), "data": out})]

    def _agents_frame(self):
        return _sse(None, "AGENTS", {"type": "AGENTS", "id": None, "ts": time.time(), "task_id": None, "data": {"agents": self.agents()}})

    async def _stream(self, device, last):
        core, did = self.core, device["id"]
        q = core.bus.subscribe()
        self._streams.setdefault(did, set()).add(q)
        try:
            yield "retry: 3000\n\n"
            seen = 0
            if last is not None:
                # ponytail: events_after reads 500 rows of every type; a phone away through more than that catches up from its next reload
                for ev in core.db.events_after(int(last)):
                    seen = ev["id"]
                    for frame in self._frames({"id": ev["id"], "type": ev["type"], "ts": ev["ts"], "task_id": ev["task_id"], "data": ev["data"]}):
                        yield frame
            yield _sse(None, "HELLO", {"type": "HELLO", "id": None, "ts": time.time(), "task_id": None,
                                       "data": {"state": core.state, "name": core.name, "replayed_to": seen, "tasks_running": self._running()}})
            agents_at, agents_due = 0.0, False
            while True:
                if getattr(q, "overflowed", False):
                    return  # the phone reconnects with Last-Event-ID
                wait = max(0.0, min(SSE_PING_S, agents_at + AGENTS_EVERY_S - time.time())) if agents_due else SSE_PING_S
                try:
                    ev = await asyncio.wait_for(q.get(), wait)
                except asyncio.TimeoutError:
                    if agents_due and time.time() >= agents_at + AGENTS_EVERY_S:
                        agents_at, agents_due = time.time(), False
                        yield self._agents_frame()
                    else:
                        yield ": ping\n\n"
                    continue
                typ = ev.get("type")
                if typ == CLOSE:
                    return
                if typ == "DEVICE_REVOKED":
                    if (ev.get("data") or {}).get("device_id") != str(did):
                        continue  # another device's
                    for frame in self._frames(ev):
                        yield frame
                    return
                if ev.get("id") and ev["id"] <= seen:
                    continue
                for frame in self._frames(ev):
                    yield frame
                if typ and (typ.startswith("TASK_") or typ.startswith("AGENT_")):
                    if time.time() - agents_at >= AGENTS_EVERY_S:
                        agents_at, agents_due = time.time(), False
                        yield self._agents_frame()
                    else:
                        agents_due = True
        finally:
            core.bus.unsubscribe(q)
            self._streams.get(did, set()).discard(q)

    # -- the sub-application: the allow-list ----------------------------------------------------------------------------------------------
    def _build(self):
        app = FastAPI(title="Monday phone", docs_url=None, redoc_url=None, openapi_url=None)
        app.add_middleware(_Nosniff)
        core = self.core
        db = core.db
        device_of = self._device

        async def dev(request: Request):
            return await device_of(request)

        @app.get("/remote/ping")
        async def ping():
            return Response(status_code=204)

        @app.post("/remote/pair")
        async def pair(body: PairIn, request: Request):
            addr = self._addr(request)
            left = self._locked_for(addr)
            if left:
                self._refuse(request, "locked", 429, f"too many wrong codes: try again in {int(left) + 1} s")
            if not self._code_ok(body.code):
                self._pair_failed(addr)
                self._refuse(request, "wrong_code", 401, "wrong or expired code")
            self._pairing = None  # single use
            secret, now = secrets.token_urlsafe(32), time.time()
            name = " ".join(body.name.split())[:NAME_MAX] or "Phone"
            caps = {k: True for k in CAPABILITIES}
            did = db.insert("devices", token_hash=_sha(secret), name=name, platform=body.platform.strip()[:40], app_version=body.app_version.strip()[:40],
                            capabilities=caps, created_at=now, last_seen=now, last_address=addr)
            core.receipt(None, "remote.pair", "success", {"device": name, "platform": body.platform.strip()[:40], "route": "POST /remote/pair"})
            lan = lan_ips()
            return {"device_id": str(did), "secret": secret, "pc": self.pc(), "fingerprint": self.fingerprint,
                    "addresses": {"lan": f"https://{lan[0]}:{self.port}" if lan else None, "public": self.public}, "capabilities": caps}

        @app.get("/remote/hello")
        async def hello(request: Request):
            d = await dev(request)
            return {"ok": True, "pc": self.pc(), "state": core.state, "tasks_running": self._running(), "capabilities": d["capabilities"],
                    "time": time.time()}

        @app.post("/remote/command")
        async def command(body: CommandIn, request: Request):
            d = await dev(request)
            if not d["capabilities"]["control"]:
                self._refuse(request, "control", 403, "this phone may not send commands")
            text = body.text.strip()
            if not text:
                raise HTTPException(400, "empty")
            if len(text) > TEXT_MAX:
                raise HTTPException(413, f"longer than {TEXT_MAX} characters")
            client_id = (body.client_id or "").strip()[:64] or None
            try:
                r = await core.command(text, source=f"phone:{d['name']}", client_id=client_id)
            except ValueError as e:
                raise HTTPException(400, str(e))
            m = r.get("message")
            if not m:
                raise HTTPException(409, str(r.get("ignored") or "not accepted"))
            dup = bool(r.get("duplicate"))
            self._receipt(None, "remote.command", "duplicate" if dup else "accepted", d, "POST /remote/command", message_id=m["id"])
            return {"accepted": True, "message_id": m["id"], "task_id": m.get("task_id"), "duplicate": dup}

        @app.get("/remote/messages")
        async def messages(request: Request, limit: int = 50, before: int | None = None, after: int | None = None):
            await dev(request)
            limit = max(1, min(limit, 200))
            cut = (" AND m.source IS NOT ? AND json_extract(m.meta, '$.chatroom') IS NULL AND m.kind != 'permission'"
                   " AND NOT (m.kind = 'system' AND COALESCE(json_extract(m.meta, '$.quiet'), 0))")
            cols = "SELECT m.*, t.title AS task_title, t.prompt AS task_prompt FROM messages m LEFT JOIN tasks t ON t.id = m.task_id WHERE "
            if after is not None:
                rows = db.all(f"{cols}m.id > ? AND m.id < ?{cut} ORDER BY m.id LIMIT ?", after, before or 2 ** 62, TEST_SOURCE, limit)
            else:
                rows = db.all(f"{cols}m.id < ?{cut} ORDER BY m.id DESC LIMIT ?", before or 2 ** 62, TEST_SOURCE, limit)[::-1]
            latest = (db.one("SELECT MAX(id) AS m FROM messages") or {}).get("m") or 0
            return {"messages": [self._message(m) for m in rows], "latest_id": latest}

        @app.get("/remote/tasks")
        async def tasks(request: Request, limit: int = 30):
            await dev(request)
            return {"tasks": [self._task(t) for t in db.tasks(None, max(1, min(limit, 200)))]}

        def task_or_404(tid):
            t = core.task_view(tid)
            if not t:
                raise HTTPException(404, "No such task.")
            return t

        @app.get("/remote/tasks/{tid}")
        async def task(tid: str, request: Request):
            await dev(request)
            return self._task(task_or_404(tid))

        @app.post("/remote/tasks/{tid}/stop")
        async def stop(tid: str, request: Request):
            d = await dev(request)
            if not d["capabilities"]["control"]:
                self._refuse(request, "control", 403, "this phone may not stop tasks")
            task_or_404(tid)
            if not await core.stop_task(tid, by=f"phone:{d['name']}"):
                raise HTTPException(409, "That task is not running.")
            self._receipt(tid, "remote.stop", "requested", d, "POST /remote/tasks/{id}/stop")
            return {"ok": True}

        @app.get("/remote/approvals")
        async def approvals(request: Request):
            await dev(request)
            return {"pending": [self._approval(p) for p in db.all("SELECT * FROM permissions WHERE status='PENDING' ORDER BY id DESC")]}

        @app.post("/remote/approvals/{pid}/{decision}")
        async def decide(pid: int, decision: Literal["allow", "deny"], request: Request):
            d = await dev(request)
            p = db.one("SELECT * FROM permissions WHERE id=?", pid)
            if not p:
                raise HTTPException(404, "No such permission request.")
            if not d["capabilities"]["approve"]:  # the owner turned this phone's approvals off in Settings → Phone
                self._refuse(request, "approve", 403, "this phone may not answer approvals")
            if p["status"] != "PENDING":
                raise HTTPException(409, f"Already {p['status'].lower()}.")
            p = core.decide_permission(pid, decision == "allow", by=f"phone:{d['name']}")  # one ask, one answer; never "always"
            self._receipt(p.get("task_id"), "remote.decide", decision, d, "POST /remote/approvals/{id}/{decision}", permission_id=pid)
            return {"ok": True, "id": str(pid), "status": p.get("status")}

        @app.get("/remote/agents")
        async def agents(request: Request):
            await dev(request)
            return {"agents": self.agents()}

        @app.post("/remote/rotate")
        async def rotate(request: Request):
            d = await dev(request)
            secret, now = secrets.token_urlsafe(32), time.time()
            db.update("devices", "id", d["id"], previous_hash=d["token_hash"], previous_until=now + ROTATE_GRACE_S, token_hash=_sha(secret),
                      rotated_at=now)
            self._receipt(None, "remote.rotate", "success", d, "POST /remote/rotate")
            return {"device_id": str(d["id"]), "secret": secret}

        @app.delete("/remote/devices/me")
        async def forget(request: Request):
            d = await dev(request)
            self.revoke(d["id"], by=f"phone:{d['name']}")
            return {"ok": True}

        @app.get("/remote/events")
        async def events(request: Request, since: int | None = None):
            d = await dev(request)
            last = request.headers.get("last-event-id") or since
            try:
                last = int(last) if last is not None else None
            except ValueError:
                last = None
            return StreamingResponse(self._stream(d, last), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        return app


# -- self-check ---------------------------------------------------------------------------------------------------------------------------
def _selfcheck():
    import tempfile
    import types
    from fastapi.testclient import TestClient
    from .db import DB

    # its own database: beside a harness's MONDAY_DB (preflight --selfchecks), else a temp folder; never the owner's
    folder = Path(os.environ["MONDAY_DB"]).parent if os.environ.get("MONDAY_DB") else Path(tempfile.mkdtemp(prefix="monday-remote-"))
    (folder / "remote-check.db").unlink(missing_ok=True)
    db = DB(folder / "remote-check.db")

    class Bus:
        def __init__(self):
            self.subs = set()

        def subscribe(self):
            q = asyncio.Queue()
            self.subs.add(q)
            return q

        def unsubscribe(self, q):
            self.subs.discard(q)

        def publish(self, type_, data, task_id=None, persist=True):
            ev = {"type": type_, "ts": time.time(), "task_id": task_id, "data": data}
            if persist:
                ev["id"] = db.insert("events", ts=ev["ts"], type=type_, task_id=task_id, data=data)
            for q in list(self.subs):
                q.put_nowait(ev)
            return ev

    class Core:
        version = "test"

        def __init__(self):
            self.db, self.bus, self.state, self.runs, self.watched, self.stops = db, Bus(), "IDLE", {}, [], []

        name = "Monday"

        def spawn(self, coro):
            return asyncio.ensure_future(coro)

        def emit(self, type_, data=None, task_id=None, persist=True):
            return self.bus.publish(type_, data or {}, task_id, persist)

        def receipt(self, task_id, tool, status, detail, started_at=None):
            db.insert("receipts", task_id=task_id, tool=tool, status=status, detail=detail, started_at=time.time(), completed_at=time.time())

        async def command(self, text, source="desktop", client_id=None, attachments=None, timing=None, utterance=None):
            dup = db.one("SELECT * FROM messages WHERE client_id=?", client_id) if client_id else None
            if dup:
                return {"message": dup, "duplicate": True}
            mid = db.insert("messages", role="user", kind="text", text=text, source=source, client_id=client_id, created_at=time.time())
            m = db.one("SELECT * FROM messages WHERE id=?", mid)
            self.emit("MESSAGE_CREATED", {"message": m})
            return {"message": m, "routing": True}

        async def stop_task(self, tid, by):
            self.stops.append((tid, by))
            return tid in self.runs

        def decide_permission(self, pid, approve, by, expired=False):
            p = db.one("SELECT * FROM permissions WHERE id=?", pid)
            if not p or p["status"] != "PENDING":
                return p
            db.update("permissions", "id", pid, status="GRANTED" if approve else "DENIED", decided_at=time.time(), decided_by=by)
            p = db.one("SELECT * FROM permissions WHERE id=?", pid)
            self.emit("PERMISSION_GRANTED" if approve else "PERMISSION_DENIED", {"permission": p}, p["task_id"])
            return p

        def task_view(self, tid):
            return db.task(tid)

    core = Core()
    r = Remote(core, host="127.0.0.1", port=0)
    r.env = lambda name: {"NGROK_DOMAIN": "example.ngrok-free.dev"}.get(name)
    c = TestClient(r.app, base_url="https://phone.test")
    secrets_seen = []

    # the certificate: made once, the fingerprint stable
    cert, key = r._ensure_cert()
    fp1 = r.fingerprint
    stamp = key.stat().st_mtime_ns
    r._ensure_cert()
    assert r.fingerprint == fp1 and len(fp1) == 64 and int(fp1, 16) and key.stat().st_mtime_ns == stamp, "the certificate is made once"
    assert cert.is_file() and key.is_file() and b"BEGIN PRIVATE KEY" in key.read_bytes()

    # pairing: a good code once, wrong, reused, expired, the lockout
    pairing = r.start_pairing()
    code = pairing["code"]
    assert len(code) == 8 and code.isdigit() and pairing["expires_at"] > time.time() and "lan" in pairing["addresses"]
    assert c.post("/remote/pair", json={"code": "00000000", "name": "x"}).status_code == 401
    p = c.post("/remote/pair", json={"code": code, "name": "  Bhavy's  Pixel  ", "platform": "android", "app_version": "0.1.0"})
    assert p.status_code == 200, p.text
    p = p.json()
    assert p["device_id"] == "1" and len(p["secret"]) > 30 and p["fingerprint"] == fp1 and p["pc"]["name"] == socket.gethostname()
    assert p["pc"]["version"] == "test" and p["capabilities"] == {"approve": True, "control": True} and p["addresses"]["public"] is None
    assert p["pc"]["id"] == r.pc()["id"] and db.setting("pc_id") == p["pc"]["id"], "the PC id is a setting made once"
    assert db.one("SELECT name, platform, capabilities FROM devices WHERE id=1") == {"name": "Bhavy's Pixel", "platform": "android", "capabilities": {"approve": True, "control": True}}
    secrets_seen += [code, p["secret"]]
    bearer1 = {"authorization": f"Bearer 1.{p['secret']}"}
    assert c.post("/remote/pair", json={"code": code, "name": "again"}).status_code == 401, "single use"
    r.start_pairing()
    r._pairing = (r._pairing[0], time.time() - 1)
    assert c.post("/remote/pair", json={"code": code, "name": "late"}).status_code == 401, "expired"
    for _ in range(2):  # 3 wrong tries so far; the 5th locks the address
        assert c.post("/remote/pair", json={"code": "1", "name": "x"}).status_code == 401
    locked = c.post("/remote/pair", json={"code": "1", "name": "x"})
    assert locked.status_code == 429 and "try again" in locked.json()["detail"], locked.text
    r._pair_lock.clear()  # the lockout, lifted for the rest of the check
    good = r.start_pairing()["code"]
    assert c.post("/remote/pair", json={"code": good, "name": "x"}).status_code == 200, "after the lockout a good code pairs"
    secrets_seen.append(good)
    pairing2 = r.start_pairing()
    secrets_seen.append(pairing2["code"])
    p2 = c.post("/remote/pair", json={"code": pairing2["code"], "name": "Tablet", "platform": "ios"}).json()
    secrets_seen.append(p2["secret"])
    bearer2 = {"authorization": f"Bearer {p2['device_id']}.{p2['secret']}"}

    # auth before anything else
    assert c.get("/remote/hello").status_code == 401 and c.get("/remote/hello").json()["detail"] == "unknown"
    assert c.get("/remote/hello", headers={"authorization": "Bearer 1.nope"}).json()["detail"] == "unknown"
    assert c.get("/remote/hello", headers={"authorization": "Bearer 99.x"}).json()["detail"] == "unknown"
    assert c.get("/remote/hello", headers={"authorization": "Basic 1.x"}).status_code == 401
    assert c.get("/remote/ping").status_code == 204, "reachability needs no credential"
    h = c.get("/remote/hello", headers=bearer1)
    assert h.status_code == 200 and h.headers["x-content-type-options"] == "nosniff", h.text
    h = h.json()
    assert h["ok"] is True and h["state"] == "IDLE" and h["tasks_running"] == 0 and h["capabilities"] == {"approve": True, "control": True}
    assert h["pc"]["name"] == socket.gethostname() and h["time"] > 0
    assert db.one("SELECT last_address FROM devices WHERE id=1")["last_address"] == "testclient"
    # the allow-list: nothing else answers
    for path in ("/", "/docs", "/openapi.json", "/api/settings", "/api/keys", "/api/shutdown", "/remote/settings", "/remote/keys",
                 "/remote/shutdown", "/remote/devices", "/remote/files", "/monday.html"):
        assert c.get(path, headers=bearer1).status_code in (404, 405), path
        assert c.post(path, headers=bearer1).status_code in (404, 405), path
    assert c.get("/remote/hello", headers={"host": "192.168.1.9:8790", **bearer1}).status_code == 200, "the Host header isn't restricted"
    assert "text/html" not in c.get("/nothing").headers.get("content-type", "")

    # the rate limit
    r._rate[1] = deque([time.time()] * RATE[0])
    assert c.get("/remote/hello", headers=bearer1).status_code == 429
    r._rate[1].clear()

    # command: idempotent by client id; the source names the phone
    a = c.post("/remote/command", json={"text": "open notepad", "client_id": "abc"}, headers=bearer1).json()
    b = c.post("/remote/command", json={"text": "open notepad", "client_id": "abc"}, headers=bearer1).json()
    assert a["accepted"] and a["duplicate"] is False and b["duplicate"] is True and a["message_id"] == b["message_id"] and a["task_id"] is None
    assert db.one("SELECT source FROM messages WHERE id=?", a["message_id"])["source"] == "phone:Bhavy's Pixel"
    assert c.post("/remote/command", json={"text": "   "}, headers=bearer1).status_code == 400
    assert c.post("/remote/command", json={"text": "x" * (TEXT_MAX + 1)}, headers=bearer1).status_code == 413

    # messages: roles mapped, the rest skipped
    now = time.time()
    db.insert("tasks", id="TASK-1", title="Open Notepad", prompt="open notepad", agent="hermes", status="RUNNING", created_at=now, started_at=now,
              steps=[{"index": 1, "total": 2, "tool": "computer", "action": "open", "label": "Open Notepad", "status": "done", "verified": True, "ms": 1500},
                     {"index": 2, "total": 2, "tool": "computer", "action": "type", "label": "Type", "status": "running"}])
    db.insert("tasks", id="TASK-2", title="Summarize", prompt="summarize the page", agent="claude_code", status="COMPLETED", created_at=now - 5,
              started_at=now - 4, finished_at=now - 1, result="Done: two lines", steps=[{"index": 1, "tool": "browser", "action": "read", "label": "Read", "status": "done"}])
    db.insert("tasks", id="TASK-3", title="Old", prompt="old", agent="codex", status="CANCELLED", created_at=now - 5000, finished_at=now - 4000)
    db.insert("messages", role="monday", kind="text", text="Sure.", meta={"model": "llama3.2:1b", "reply_to": a["message_id"]}, created_at=now)
    db.insert("messages", role="monday", kind="task", text="", task_id="TASK-1", created_at=now)
    db.insert("messages", role="monday", kind="permission", text="May I?", permission_id=1, created_at=now)
    db.insert("messages", role="monday", kind="system", text="quiet", meta={"quiet": True}, created_at=now)
    db.insert("messages", role="monday", kind="system", text="New chat", meta={"chat_start": True}, created_at=now)
    db.insert("messages", role="user", kind="text", text="test", source=TEST_SOURCE, created_at=now)
    db.insert("messages", role="user", kind="text", text="room talk", meta={"chatroom": "r1"}, created_at=now)
    ms = c.get("/remote/messages?limit=50", headers=bearer1).json()
    got = [(m["role"], m["text"]) for m in ms["messages"]]
    assert got == [("user", "open notepad"), ("assistant", "Sure."), ("assistant", "Open Notepad"), ("system", "New chat")], got
    assert ms["messages"][1]["model"] == "llama3.2:1b" and ms["messages"][0]["model"] is None and ms["messages"][2]["task_id"] == "TASK-1"
    assert ms["messages"][0]["source"] == "phone:Bhavy's Pixel" and ms["messages"][0]["ts"] > 0 and ms["latest_id"] >= 8
    assert [m["id"] for m in c.get("/remote/messages?limit=1", headers=bearer1).json()["messages"]] == [ms["messages"][-1]["id"]], "the newest page"
    older = c.get(f"/remote/messages?before={ms['messages'][1]['id']}&limit=10", headers=bearer1).json()["messages"]
    assert [m["text"] for m in older] == ["open notepad"]
    assert [m["text"] for m in c.get(f"/remote/messages?after={a['message_id']}&limit=1", headers=bearer1).json()["messages"]] == ["Sure."]

    # tasks: status words, steps, progress only with a total
    ts = c.get("/remote/tasks", headers=bearer1).json()["tasks"]
    assert [t["id"] for t in ts] == ["TASK-1", "TASK-2", "TASK-3"] and [t["status"] for t in ts] == ["running", "done", "cancelled"]
    t1 = c.get("/remote/tasks/TASK-1", headers=bearer1).json()
    assert t1["progress"] == {"done": 1, "total": 2} and t1["started"] == now and t1["ended"] is None and t1["agent"] == "hermes"
    assert t1["steps"][0] == {"n": 1, "tool": "computer", "action": "open", "target": None, "status": "done", "verified": "true", "took": 1.5}
    assert t1["steps"][1]["status"] == "running" and t1["steps"][1]["verified"] is None and t1["steps"][1]["took"] is None
    t2 = c.get("/remote/tasks/TASK-2", headers=bearer1).json()
    assert t2["progress"] is None and t2["result"] == "Done: two lines" and t2["steps"][0]["verified"] == "unknown" and t2["prompt"] == "summarize the page"
    assert c.get("/remote/tasks/TASK-9", headers=bearer1).status_code == 404
    assert c.post("/remote/tasks/TASK-9/stop", headers=bearer1).status_code == 404
    assert c.post("/remote/tasks/TASK-2/stop", headers=bearer1).status_code == 409, "not running"
    core.runs["TASK-1"] = types.SimpleNamespace(agent="hermes", status="RUNNING", route={}, background=False, task_id="TASK-1")
    assert c.post("/remote/tasks/TASK-1/stop", headers=bearer1).json() == {"ok": True} and core.stops[-1] == ("TASK-1", "phone:Bhavy's Pixel")
    assert c.get("/remote/hello", headers=bearer1).json()["tasks_running"] == 1

    # approvals: one answer, with the capability only, never "always"
    pid = db.insert("permissions", task_id="TASK-1", agent="hermes", kind="shell", title="Run a command", detail={"command": "rm x"}, risk="MEDIUM",
                    status="PENDING", created_at=now, who="Scout")
    ap = c.get("/remote/approvals", headers=bearer1).json()["pending"]
    assert ap == [{"id": str(pid), "task_id": "TASK-1", "what": "Run a command: command: rm x", "risk": "MEDIUM", "asked_at": now, "agent": "Scout"}], ap
    ag = c.get("/remote/agents", headers=bearer1).json()["agents"]
    assert ag[0]["id"] == "TASK-1" and ag[0]["state"] == "waiting" and ag[0]["waiting"] is True and ag[0]["kind"] == "computer" and ag[0]["progress"] == {"done": 1, "total": 2}
    assert ag[1]["id"] == "TASK-2" and ag[1]["state"] == "done" and ag[1]["kind"] == "coder" and ag[1]["result"] == "Done: two lines"
    assert all(a["id"] != "TASK-3" for a in ag), "an old task isn't an agent row"
    r.set_capability(p2["device_id"], "approve", False)
    assert c.post(f"/remote/approvals/{pid}/allow", headers=bearer2).status_code == 403
    assert c.post(f"/remote/approvals/{pid}/always", headers=bearer1).status_code in (404, 422)
    assert db.one("SELECT status FROM permissions WHERE id=?", pid)["status"] == "PENDING"
    assert c.post(f"/remote/approvals/{pid}/allow", headers=bearer1).json()["status"] == "GRANTED"
    assert db.one("SELECT decided_by FROM permissions WHERE id=?", pid)["decided_by"] == "phone:Bhavy's Pixel"
    assert c.post(f"/remote/approvals/{pid}/deny", headers=bearer1).status_code == 409
    assert c.post("/remote/approvals/999/allow", headers=bearer1).status_code == 404
    assert c.get("/remote/hello", headers=bearer2).json()["capabilities"] == {"approve": False, "control": True}
    try:
        r.set_capability(1, "shutdown", True)
        raise AssertionError("an unknown capability")
    except ValueError:
        pass
    assert [d["id"] for d in r.devices()] == ["3", "2", "1"] and all("token_hash" not in d and "previous_hash" not in d for d in r.devices())

    # rotation: the new secret works, the old one through the grace only
    rot = c.post("/remote/rotate", headers=bearer1).json()
    secrets_seen.append(rot["secret"])
    bearer1b = {"authorization": f"Bearer 1.{rot['secret']}"}
    assert rot["device_id"] == "1" and rot["secret"] != p["secret"]
    assert c.get("/remote/hello", headers=bearer1).status_code == 200, "the old secret in the grace"
    assert c.get("/remote/hello", headers=bearer1b).status_code == 200
    db.update("devices", "id", 1, previous_until=time.time() - 1)
    assert c.get("/remote/hello", headers=bearer1).json()["detail"] == "expired"
    assert c.get("/remote/hello", headers=bearer1b).status_code == 200

    # forget and revoke
    assert c.delete("/remote/devices/me", headers=bearer2).json() == {"ok": True}
    assert c.get("/remote/hello", headers=bearer2).json()["detail"] == "revoked"
    assert r.revoke(99) is False and r.state()["devices"] == 2
    st = r.state()
    assert st["on"] is False and st["tunnel"] == "not_configured" and "NGROK_AUTHTOKEN" in st["why"] and ".env" in st["why"], st
    assert st["fingerprint"] == fp1 and st["lan"] == [] and st["public"] is None

    # events: replay, translation, the placeholder ids, the AGENTS snapshot, revocation closes the stream
    async def stream_check():
        db.set_setting("remote_access", False)
        assert await r.start() is False and r.on is False and r.server is None, "off by default: nothing listens"
        core.emit("MESSAGE_CREATED", {"message": db.one("SELECT * FROM messages WHERE id=?", a["message_id"])})
        core.emit("TASK_COMPLETED", {"task": db.task("TASK-2")}, "TASK-2")
        core.emit("PERMISSION_REQUIRED", {"permission": db.one("SELECT * FROM permissions WHERE id=?", pid)}, "TASK-1")
        core.emit("TASK_STEP", {"step": {"index": 2}, "agent": "hermes"}, "TASK-1")
        core.emit("NOT_FOR_PHONES", {"x": 1})
        last_id = db.one("SELECT MAX(id) AS m FROM events")["m"]
        chunks = []

        async def receive():
            await asyncio.Event().wait()

        async def send(msg):
            if msg["type"] == "http.response.body":
                chunks.append(msg.get("body", b""))
            elif msg["type"] == "http.response.start":
                chunks.append(f"[{msg['status']}]".encode())
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "https", "path": "/remote/events",
                 "raw_path": b"/remote/events", "query_string": b"", "root_path": "", "client": ("10.0.0.2", 4242), "server": ("10.0.0.1", 8790),
                 "headers": [(b"authorization", bearer1b["authorization"].encode()), (b"last-event-id", b"0")]}
        task = asyncio.create_task(r.app(scope, receive, send))
        await asyncio.sleep(0.3)
        core.emit("MESSAGE_DELTA", {"stream_id": "s1", "delta": "Hel", "model": "m"}, persist=False)
        core.emit("MESSAGE_DELTA", {"stream_id": "s1", "delta": "lo", "model": "m"}, persist=False)
        mid = db.insert("messages", role="monday", kind="text", text="Hello", meta={"stream_id": "s1", "model": "m"}, created_at=time.time())
        core.emit("MESSAGE_CREATED", {"message": db.one("SELECT * FROM messages WHERE id=?", mid)})
        core.emit("CORE_STATE", {"state": "EXECUTING"}, persist=False)
        core.emit("TASK_PROGRESS", {"current_action": "Typing", "agent": "hermes"}, "TASK-1")  # the first TASK_* live: AGENTS at once
        core.emit("PERMISSION_DENIED", {"permission": {**db.one("SELECT * FROM permissions WHERE id=?", pid), "status": "EXPIRED"}}, "TASK-1")
        core.emit("TASK_PROGRESS", {"current_action": "Typing more", "agent": "hermes"}, "TASK-1")  # within the second: AGENTS waits
        await asyncio.sleep(1.4)  # the deferred AGENTS snapshot is due
        r.revoke(99)
        core.emit("DEVICE_REVOKED", {"device_id": "2"}, persist=False)  # another phone's: not this stream's
        await asyncio.sleep(0.1)
        assert not task.done()
        assert r.revoke(1, by="window") is True
        await asyncio.wait_for(task, 5)
        return b"".join(chunks).decode(), last_id, mid

    body, last_id, mid = asyncio.run(stream_check())
    assert body.startswith("[200]retry: 3000\n\n"), body[:80]
    frames = [f for f in body.split("\n\n") if f.startswith(("id:", "event:"))]
    evs = []
    for f in frames:
        lines = dict(l.split(": ", 1) for l in f.splitlines())
        evs.append((lines["event"], int(lines["id"]) if "id" in lines else None, json.loads(lines["data"])))
    types_ = [e[0] for e in evs]
    replayed = ["MESSAGE_CREATED", "PERMISSION_GRANTED", "MESSAGE_CREATED", "TASK_COMPLETED", "PERMISSION_REQUIRED", "TASK_PROGRESS", "HELLO"]
    assert types_[:7] == replayed, types_
    assert all(e[1] for e in evs[:6]) and evs[5][2]["data"]["id"] == "TASK-1" and evs[5][2]["data"]["status"] == "running", "a replayed TASK_STEP is the task now"
    assert evs[1][2]["data"] == {"id": str(pid)} and evs[0][2]["data"]["client_id"] == "abc" and evs[0][2]["data"]["role"] == "user"
    assert evs[3][2]["data"]["status"] == "done" and evs[3][2]["data"]["progress"] is None and evs[3][2]["task_id"] == "TASK-2"
    assert evs[4][2]["data"]["what"] == "Run a command: command: rm x" and evs[4][2]["data"]["agent"] == "Scout"
    assert evs[6][2]["data"]["replayed_to"] == last_id and evs[6][1] is None and evs[6][2]["data"]["tasks_running"] == 1
    live = evs[7:]
    want = ["MESSAGE_DELTA", "MESSAGE_DELTA", "MESSAGE_CREATED", "CORE_STATE", "TASK_PROGRESS", "AGENTS", "PERMISSION_EXPIRED", "TASK_PROGRESS",
            "AGENTS", "DEVICE_REVOKED"]
    assert [e[0] for e in live] == want, [e[0] for e in live]
    d1, d2, created = live[0][2]["data"], live[1][2]["data"], live[2][2]["data"]
    assert d1["message_id"] == d2["message_id"] < 0 and d1["message_id"] > -(2 ** 31) and d1["delta"] + d2["delta"] == "Hello" and d1["done"] is False
    assert created["replaces"] == d1["message_id"] and created["id"] == mid and created["role"] == "assistant" and created["model"] == "m"
    assert live[0][1] is None and live[2][1] and live[3][2]["data"] == {"state": "EXECUTING", "tasks_running": 1}
    assert live[4][2]["data"]["id"] == "TASK-1" and live[6][2]["data"] == {"id": str(pid)} and live[9][2]["data"] == {"device_id": "1"}
    assert [x["id"] for x in live[5][2]["data"]["agents"]][:2] == ["TASK-1", "TASK-2"]
    assert live[8][2]["ts"] - live[5][2]["ts"] >= AGENTS_EVERY_S - 0.1, "the second snapshot waited its second"
    assert "NOT_FOR_PHONES" not in body and "token_hash" not in body
    assert placeholder_id("s1") == placeholder_id("s1") != placeholder_id("s2") and -(2 ** 31) < placeholder_id("x") < 0

    # a real bind (loopback, an ephemeral port): the listener answers over TLS with the pinned certificate, then stops
    async def listen_check():
        import ssl
        import httpx
        db.set_setting("remote_access", True)
        r.host, r.port = "127.0.0.1", 0
        assert await r.start() is True and r.on and r.port > 0, r.error
        assert r.state()["on"] is True and r.state()["tunnel"] == "not_configured" and r.state()["port"] == r.port
        pem = await asyncio.to_thread(ssl.get_server_certificate, ("127.0.0.1", r.port))
        assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() == fp1, "the wire's certificate is the one pairing names"
        async with httpx.AsyncClient(verify=False, timeout=5) as h:
            assert (await h.get(f"https://127.0.0.1:{r.port}/remote/ping")).status_code == 204
            u = await h.get(f"https://127.0.0.1:{r.port}/remote/hello")
            assert u.status_code == 401 and u.json() == {"detail": "unknown"} and u.headers["x-content-type-options"] == "nosniff"
            assert (await h.get(f"https://127.0.0.1:{r.port}/remote/hello", headers=bearer1b)).status_code == 401, "revoked"
        await r.stop()
        assert r.on is False and r.server is None
        db.set_setting("remote_access", False)
    asyncio.run(listen_check())

    # receipts: names and routes, never a secret or a code
    rows = db.all("SELECT tool, status, detail FROM receipts ORDER BY id")
    tools = {x["tool"] for x in rows}
    assert {"remote.pair", "remote.command", "remote.stop", "remote.decide", "remote.rotate", "remote.revoke", "remote.refused", "remote.capability"} <= tools, tools
    dump = json.dumps(rows)
    assert all(s not in dump for s in secrets_seen), "a secret or a code in a receipt"
    assert all(s not in json.dumps(db.all("SELECT * FROM devices")) for s in secrets_seen), "a secret stored in clear"
    stop_r = next(x for x in rows if x["tool"] == "remote.stop")
    assert stop_r["detail"]["device"] == "Bhavy's Pixel" and stop_r["detail"]["route"].startswith("POST /remote/tasks")
    refused = [x for x in rows if x["tool"] == "remote.refused"]
    assert refused and refused[0]["detail"]["counts"] and refused[0]["detail"]["address"] == "testclient"
    assert sum(x["status"] == "duplicate" for x in rows if x["tool"] == "remote.command") == 1

    db.c.close()
    if not os.environ.get("MONDAY_DB"):
        shutil.rmtree(folder, ignore_errors=True)  # the temp folder (its database, certificate and key) goes with the check
    print("remote self-check: OK (14 parts: certificate, pairing, lockout, auth, allow-list, rate limit, command, messages, tasks, approvals, "
          "rotation, revocation, events, listener; receipts without secrets)")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _selfcheck()
