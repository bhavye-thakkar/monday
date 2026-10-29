"""python -m core [--open]  ->  Monday Core on http://127.0.0.1:8765, for this computer only.

--open  opens Monday's desktop app once Core answers (used by "Start Monday.cmd"): its native windows (core/desktop.py), whose
supervisor then watches this Core instead of starting its own. Never a browser.
If a Monday Core is already running on the port, this only opens the app instead of failing to bind.
"""
import sys
import threading
import time

import httpx
import uvicorn

for _stream in (sys.stdout, sys.stderr):  # the start-up table prints ✓/✗; a cp1252 console or pipe must not crash on it
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

import faulthandler  # noqa: E402

faulthandler.enable()  # a native crash (an access violation in a Windows API call) prints its Python stack instead of vanishing

from .app import HOST, PORT, app  # noqa: E402

def open_hud():
    """Monday's desktop app (core/desktop.py): its native windows, or the running app's brought forward. Never a browser."""
    import subprocess
    from pathlib import Path
    win = sys.platform == "win32"
    subprocess.Popen([str(Path(sys.base_exec_prefix) / "pythonw.exe") if win else sys.executable, "-m", "core.desktop"],  # (also from MondayCore.exe)
                     cwd=Path(__file__).resolve().parent.parent, close_fds=True, creationflags=0x08000000 if win else 0,
                     start_new_session=not win)


def core_answers():
    try:
        return httpx.get(f"http://127.0.0.1:{PORT}/api/hello", timeout=1.5).json().get("version") is not None
    except (httpx.HTTPError, ValueError):
        return False


if core_answers():
    print(f"Monday Core is already running on http://127.0.0.1:{PORT}")
    if "--open" in sys.argv:
        open_hud()
    sys.exit(0)

if "--open" in sys.argv:
    def open_when_ready():
        for _ in range(60):
            if core_answers():
                open_hud()
                return
            time.sleep(0.5)
    threading.Thread(target=open_when_ready, daemon=True).start()

uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)
