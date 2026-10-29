"""Monday's Windows setup (2026-10-17, the owner: "this type of setup", Coucou's installer): installer/out/Monday-<version>.msi, the standard
Windows Installer wizard (installer/monday.wxs, WiX 5): Welcome, where (%LocalAppData%\\Programs\\Monday unless changed), Install, Finish
with "Launch Monday"; for this Windows user, no admin. Python 3.14 from python.org is the one thing it needs on the PC (the wizard says so
when it's missing); Monday's Python packages come with it.

    python installer/build.py        stage, build, then check what the package holds (an administrative extract: nothing is installed)
    python installer/build.py --publish [--channel <folder>] [--notes "..."]
                                     ...and put it with its signed manifest in the update channel (docs/adr/0010): installed copies
                                     offer it from their next check; --channel also makes that folder the package's own default
    python installer/build.py --keygen   once: the owner's update key (E:\\monday\\keys, never packaged; its public half goes into
                                     core/update.py PUBLIC_KEY)

Needs WiX 5 as a .NET tool (dotnet tool install --global wix --version 5.0.2; wix extension add -g WixToolset.UI.wixext/5.0.2: installed
with the owner's OK, 2026-10-17). What goes in: the app's code and assets by name (APP), never its data\\ (the database, logs, uploads)
or Monday\\ (the vault and its .env: the owner's keys) (PRIVATE), nor a key, token or database anywhere (NEVER), checked on the built
package too; PySide6 (the window: Qt), pythonnet (Windows' voices) and its WinRT projection (pylib, beside the app as E:\\monday\\pylib is); the packages Monday imports and what
they need (PACKAGES), copied from this PC's own installs, nothing downloaded. Optional features' packages (Playwright, OpenCV, numpy, the
voice models) stay out: an installed Monday says those aren't installed, as it would here.
"""
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # E:\monday\app
PYLIB = ROOT.parent / "pylib"           # E:\monday\pylib
STAGE, ART, OUT = HERE / "stage", HERE / "art", HERE / "out"
WIX = Path(os.environ.get("USERPROFILE", "")) / ".dotnet" / "tools" / "wix.exe"
SIGNING_KEY = ROOT.parent / "keys" / "update-signing.key"  # the owner's update key (docs/adr/0010): outside the app, never packaged
APP = ["core", "static", "Start Monday.cmd", "preflight.py", "README.md", "THIRD_PARTY_NOTICES.md"]
NEVER = re.compile(r"\.env$|window\.token$|\.db(-journal|-wal)?$|(^|[\\/])__pycache__([\\/]|$)|\.pyc$|\.key$|update-signing", re.I)  # anywhere: keys, tokens (not .pem: certifi's cacert.pem is HTTPS itself)
PRIVATE = re.compile(r"^app[\\/](.+[\\/])?(data|Monday|\.claude|graphify-out)([\\/]|$)", re.I)  # the app's own data and the vault
WEB = re.compile(r"\.(html?|css|js|chm)$", re.I)  # packages' docs and web parts: a Windows app only (preflight's rule); nothing Monday runs
OLD_SHELL = ("webview", "bottle", "proxy_tools", "pywebview")  # pylib's WebView2 shell, gone 2026-10-15: nothing imports it
PACKAGES = ["fastapi", "httpx", "psutil", "pydantic", "uvicorn", "pillow", "pywin32", "typesafe-sdk", "httpx2", "PyYAML", "pypdf",
            "requests", "cryptography", "winrt-runtime", "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections", "winrt-Windows.Globalization",
            "winrt-Windows.Graphics.Imaging", "winrt-Windows.Media.Control", "winrt-Windows.Media.Ocr", "winrt-Windows.Storage.Streams"]


def version():
    return re.search(r'^VERSION = "([\d.]+)"', (ROOT / "core" / "monday.py").read_text(encoding="utf-8"), re.M).group(1)


def copy_tree(src, dst, skip=lambda rel: False):
    """src (a file, or a folder's contents) into dst, but never what NEVER names, nor what skip(rel) says."""
    for p in [src] if src.is_file() else sorted(src.rglob("*")):
        rel = Path(p.name) if src.is_file() else p.relative_to(src)
        if p.is_file() and not NEVER.search(str(rel)) and not skip(rel):
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst / rel)


def closure(names):
    """The distributions Monday needs: those named and every one they require on Windows (their own extras and other platforms left out)."""
    seen, out = set(), []

    def walk(name):
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            return
        seen.add(key)
        try:
            d = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            return  # (a requirement for another Python or platform: exceptiongroup, httpx2-jsfetch)
        out.append(d)
        for r in d.requires or []:
            if "extra ==" in r or ("platform_system" in r and "Windows" not in r) or "python_version <" in r:
                continue
            walk(re.match(r"[A-Za-z0-9_.\-]+", r).group(0))
    for n in names:
        walk(n)
    return out


def stage(channel=None):
    shutil.rmtree(STAGE, ignore_errors=True)
    for name in APP:
        copy_tree(ROOT / name, STAGE / "app" / name if (ROOT / name).is_dir() else STAGE / "app")
    if channel:  # the folder this package's Monday looks in for updates (core/update.py CHANNEL_FILE)
        (STAGE / "app" / "update-channel.txt").write_text(str(channel), encoding="utf-8")
    copy_tree(PYLIB, STAGE / "pylib", skip=lambda rel: WEB.search(rel.name) or rel.parts[0].split("-")[0].lower() in OLD_SHELL)
    for d in closure(PACKAGES):
        base = Path(d.locate_file(""))
        for f in d.files or []:
            if str(f).startswith(".."):  # console scripts (Scripts\): Monday runs none of them
                continue
            src, dst = base / f, STAGE / "lib" / f
            if src.is_file() and not NEVER.search(str(f)) and not WEB.search(str(f)):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    staged = [p.relative_to(STAGE) for p in STAGE.rglob("*") if p.is_file()]
    bad = [str(r) for r in staged if NEVER.search(str(r)) or PRIVATE.search(str(r)) or (WEB.search(r.name) and r.parts[0] != "app")]
    assert not bad, f"never in the package: {bad[:5]}"
    print(f"staged {len(staged)} files, {sum((STAGE / r).stat().st_size for r in staged) / 1e6:.1f} MB", flush=True)


def art():
    """The wizard's two pictures in Monday's look: its icon on the dark of its window (WixUI: banner 493x58, dialog 493x312)."""
    from PIL import Image, ImageDraw
    ART.mkdir(exist_ok=True)
    icon = Image.open(ROOT / "static" / "monday-256.png").convert("RGBA")
    dlg = Image.new("RGB", (493, 312), (255, 255, 255))
    ImageDraw.Draw(dlg).rectangle((0, 0, 163, 311), fill=(16, 17, 20))
    big = icon.resize((104, 104), Image.LANCZOS)
    dlg.paste(big, ((164 - 104) // 2, 92), big)
    dlg.save(ART / "dialog.bmp")
    ban = Image.new("RGB", (493, 58), (255, 255, 255))
    small = icon.resize((44, 44), Image.LANCZOS)
    ban.paste(small, (493 - 44 - 10, 7), small)
    ban.save(ART / "banner.bmp")


def build():
    if not WIX.exists():
        sys.exit("WiX 5 isn't installed: dotnet tool install --global wix --version 5.0.2, then wix extension add -g "
                 "WixToolset.UI.wixext/5.0.2")
    OUT.mkdir(exist_ok=True)
    msi = OUT / f"Monday-{version()}.msi"
    subprocess.run([str(WIX), "build", str(HERE / "monday.wxs"), "-ext", "WixToolset.UI.wixext", "-arch", "x64",
                    "-d", f"Version={version()}", "-d", f"Stage={STAGE}", "-d", f"Art={ART}", "-o", str(msi)], check=True)
    print(f"built {msi.name}: {msi.stat().st_size / 1e6:.1f} MB", flush=True)
    return msi


def check(msi):
    """What the package holds, from an administrative extract (msiexec /a: files out of the package, nothing installed or registered)."""
    with tempfile.TemporaryDirectory(prefix="monday-msi-") as d:
        log = Path(d) / "extract.log"
        r = subprocess.run(["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={Path(d) / 'x'}", "/l*", str(log)])
        assert r.returncode == 0, f"msiexec /a: exit {r.returncode} (log: {log.read_text(encoding='utf-16', errors='replace')[-800:]})"
        root = Path(d) / "x"
        files = [p.relative_to(root) for p in root.rglob("*") if p.is_file() and p.suffix.lower() != ".msi"]
        names = {str(p).replace("\\", "/").lower() for p in files}
        for need in ("app/core/desktop.py", "app/core/__main__.py", "app/core/qtwindow.py", "app/static/monday.ico", "pylib/clr.py",
                     "pylib/PySide6/__init__.py", "lib/fastapi/__init__.py", "lib/pywin32.pth", "lib/psutil/__init__.py",
                     "lib/cryptography/__init__.py", "app/core/update.py"):
            assert any(n.endswith(need.lower()) for n in names), f"missing from the package: {need}"  # (names are lowercased above)
        inside = [Path(*p.parts[p.parts.index("Monday") + 1:]) if "Monday" in p.parts else p for p in files]  # (under the install folder)
        bad = [str(p) for p in inside if NEVER.search(str(p)) or PRIVATE.search(str(p))]
        assert not bad, f"never in the package: {bad[:5]}"
        print(f"checked: {len(files)} files; the app, pylib and lib are there; no data, vault, .env, database or token", flush=True)


def keygen():
    """Once: the owner's update key (docs/adr/0010). The private half here, outside the app; the public half printed for core/update.py.
    Never made over an existing one: a new key makes every installed Monday refuse the next update until it is reinstalled by hand."""
    if SIGNING_KEY.exists():
        sys.exit(f"{SIGNING_KEY} already exists: keep using it.")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    SIGNING_KEY.parent.mkdir(parents=True, exist_ok=True)
    SIGNING_KEY.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")
    print(f"made {SIGNING_KEY}; its public half, for core/update.py PUBLIC_KEY: {pub}")


def publish(msi, channel, notes=""):
    """The package and its signed manifest into the update channel (docs/adr/0010). The package goes first, then the signature and the
    manifest, so a Monday that reads the channel meanwhile finds the old version or the new one, never a manifest without its package."""
    sys.path.insert(0, str(ROOT))
    from core import update
    if not SIGNING_KEY.exists():
        sys.exit(f"No update key at {SIGNING_KEY}: run `python installer/build.py --keygen` once first.")
    man = {"version": version(), "msi": msi.name, "sha256": update.sha256(msi), "size": msi.stat().st_size, "notes": notes,
           "published": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    data, sig = update.sign(man, SIGNING_KEY.read_bytes())
    assert update.verify(data, sig), "core/update.py's PUBLIC_KEY isn't this key's public half: installed copies would refuse it"
    channel.mkdir(parents=True, exist_ok=True)
    part = channel / f"{msi.name}.part"
    shutil.copyfile(msi, part)
    os.replace(part, channel / msi.name)
    for name, body in ((update.SIGNATURE, sig.encode("ascii")), (update.MANIFEST, data)):
        tmp = channel / f"{name}.part"
        tmp.write_bytes(body)
        os.replace(tmp, channel / name)
    for old in channel.glob("Monday-*.msi"):  # (only the newest is offered; older packages only take space)
        if old.name != msi.name:
            old.unlink()
    got = update.check("0.0.1", channel, root=STAGE / "app" if (STAGE / "app").is_dir() else ROOT)
    print(f"published Monday {man['version']} in {channel}: {man['size'] / 1e6:.1f} MB, SHA-256 {man['sha256'][:12]}…; "
          f"an installed Monday reads it as: {got['state']} ({got['detail']})", flush=True)


def arg(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv and sys.argv.index(name) + 1 < len(sys.argv) else None


if __name__ == "__main__":
    if "--keygen" in sys.argv:
        keygen()
        sys.exit(0)
    where = Path(arg("--channel")) if arg("--channel") else None
    stage(where)
    art()
    built = build()
    check(built)
    if "--publish" in sys.argv:
        publish(built, where or Path(os.environ.get("MONDAY_UPDATE_CHANNEL") or r"E:\monday\releases"), arg("--notes") or "")
    shutil.rmtree(STAGE, ignore_errors=True)  # (the staged copies: the package has them)
    shutil.rmtree(ART, ignore_errors=True)
