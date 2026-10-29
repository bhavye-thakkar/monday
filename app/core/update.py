"""Monday's updates (docs/adr/0010-update-channel.md; the owner, 2026-10-28: "when we update anything and push it then in the notch it'll say
that we have a new update and update it"). A folder, the channel, holds latest.json, its signature latest.json.sig and Monday-<v>.msi,
put there by `installer/build.py --publish`. check() reads it, verifies the owner's own signature and says whether a newer Monday is
there; stage() copies the package beside the app and checks its SHA-256 against the signed one. Nothing here installs or runs anything:
on the owner's click the window opens the verified package with Windows Installer's own wizard (core/desktop.py).

    python -m core.update            the self-check (a throwaway key, channel and package; nothing of the owner's touched)
"""
import base64
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANNEL = Path(os.environ.get("MONDAY_UPDATE_CHANNEL") or r"E:\monday\releases")  # this PC's, where build.py --publish puts them
CHANNEL_FILE = ROOT / "update-channel.txt"  # in a package published with --channel: that folder is its default
STAGE = ROOT / "data" / "tmp" / "updates"   # the package's copy, checked, before the wizard opens it
MANIFEST, SIGNATURE = "latest.json", "latest.json.sig"
UPDATE_CHECK_S = 6 * 3600                   # how often a running Core looks again (and once after each start)
MANIFEST_MAX = 64_000                       # a manifest is a few hundred bytes: anything bigger isn't one
# the owner's update key, its public half (installer/build.py --keygen made the pair on 2026-10-29; the private half is
# E:\monday\keys\update-signing.key, never packaged): only what it signed is offered
PUBLIC_KEY = "RgAfRKGlvSZVoraDLUcCTxm80j/0G9YKRqA4Zzejckw="


def version_tuple(v):
    """'0.3.0' -> (0, 3, 0); None for anything that isn't dotted numbers."""
    return tuple(int(x) for x in v.split(".")) if isinstance(v, str) and re.fullmatch(r"\d{1,4}(?:\.\d{1,4}){1,3}", v) else None


def channel(setting=None):
    """Where updates come from: the owner's choice (Settings -> General -> Updates), else the folder a published package names, else
    this PC's own."""
    if setting:
        return Path(setting)
    try:
        named = CHANNEL_FILE.read_text(encoding="utf-8").strip()
        if named:
            return Path(named)
    except OSError:
        pass
    return DEFAULT_CHANNEL


def source_tree(root=ROOT):
    """Monday running from its source folder (this PC): the setup doesn't carry installer/, so an installed copy has none."""
    return (Path(root) / "installer" / "build.py").is_file()


def disk_version(root=ROOT):
    """VERSION as the code on disk says it now (a Core that loaded an older file may be behind it)."""
    try:
        m = re.search(r'^VERSION = "([\d.]+)"', (Path(root) / "core" / "monday.py").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def verify(data, sig_b64, key=None):
    """The manifest's exact bytes against its signature, with the owner's public key. False for anything that doesn't verify."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(key or PUBLIC_KEY, validate=True))
        pub.verify(base64.b64decode(sig_b64.strip(), validate=True), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def read(folder, key=None):
    """The channel's signed manifest -> (manifest dict, None) or (None, (state, words))."""
    folder = Path(folder)
    try:
        data = (folder / MANIFEST).read_bytes()
        sig = (folder / SIGNATURE).read_text(encoding="ascii")
    except FileNotFoundError:
        return None, ("no_channel", f"No update has been published in {folder} yet." if folder.is_dir() else
                      f"Updates come from {folder}, which isn't there on this PC.")
    except (OSError, UnicodeDecodeError) as e:
        return None, ("error", f"{folder} couldn't be read: {getattr(e, 'strerror', None) or e}.")
    if len(data) > MANIFEST_MAX or not verify(data, sig, key):
        return None, ("refused", f"The update in {folder} isn't signed with your update key, so it isn't offered.")
    try:
        m = json.loads(data.decode("utf-8"))
    except ValueError:
        return None, ("refused", f"The update manifest in {folder} isn't readable.")
    ok = isinstance(m, dict) and version_tuple(m.get("version")) and isinstance(m.get("msi"), str) \
        and re.fullmatch(r"Monday-[\d.]+\.msi", m["msi"]) and isinstance(m.get("sha256"), str) \
        and re.fullmatch(r"[0-9a-f]{64}", m["sha256"]) and isinstance(m.get("size"), int) and m["size"] > 0
    return (m, None) if ok else (None, ("refused", f"The update manifest in {folder} is missing what it must name."))


def check(current, folder, root=ROOT, key=None):
    """Is a newer Monday in the channel? current: the running Core's VERSION. -> {"state", "detail", "current", "latest", "action",
    "channel"}: state up_to_date / available / restart / no_channel / refused / error; action "install" (an installed copy: the package),
    "restart" (the source folder already has that version on disk), or None."""
    out = {"current": current, "channel": str(folder), "latest": None, "action": None}
    m, why = read(folder, key)
    if why:
        return {**out, "state": why[0], "detail": why[1]}
    latest = {k: m.get(k) for k in ("version", "notes", "published", "size", "msi")}
    if version_tuple(m["version"]) <= (version_tuple(current) or (0,)):
        return {**out, "latest": latest, "state": "up_to_date", "detail": f"Monday {current} is the newest."}
    if not source_tree(root):
        return {**out, "latest": latest, "state": "available", "action": "install",
                "detail": f"Monday {m['version']} is ready to install."}
    here = disk_version(root)
    if here and version_tuple(here) >= version_tuple(m["version"]):
        return {**out, "latest": latest, "state": "restart", "action": "restart",
                "detail": f"Monday {m['version']} is on this PC: a restart loads it."}
    return {**out, "latest": latest, "state": "available",
            "detail": f"Monday {m['version']} is in {folder}; this source folder still has {here or 'an older version'}."}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stage(folder, dest=STAGE, key=None):
    """The channel's package copied beside the app and checked against the signed manifest, read again now. -> (path, None) or
    (None, why). Older copies in dest are removed (C: is often full)."""
    m, why = read(folder, key)
    if why:
        return None, why[1]
    src, dest = Path(folder) / m["msi"], Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / m["msi"]
    try:
        if not (out.is_file() and out.stat().st_size == m["size"] and sha256(out) == m["sha256"]):
            tmp = out.with_suffix(".part")
            shutil.copyfile(src, tmp)
            if tmp.stat().st_size != m["size"] or sha256(tmp) != m["sha256"]:
                tmp.unlink(missing_ok=True)
                return None, f"{m['msi']} in {folder} isn't the package the update names (its size or SHA-256 differs): not opened."
            os.replace(tmp, out)
    except OSError as e:
        return None, f"{m['msi']} couldn't be copied from {folder}: {e.strerror or e}."
    for old in dest.glob("Monday-*.msi"):
        if old != out:
            old.unlink(missing_ok=True)
    return out, None


def sign(manifest, private_pem):
    """(installer/build.py --publish) The manifest's bytes as published and their signature, with the owner's private key (PEM)."""
    from cryptography.hazmat.primitives import serialization
    data = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    key = serialization.load_pem_private_key(private_pem, password=None)
    return data, base64.b64encode(key.sign(data)).decode("ascii") + "\n"


if __name__ == "__main__":
    import tempfile

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    assert version_tuple("0.3.0") == (0, 3, 0) and version_tuple("1.10") == (1, 10) and version_tuple("x") is None and version_tuple(None) is None
    assert version_tuple("0.10.0") > version_tuple("0.9.9"), "numbers, not text"
    assert PUBLIC_KEY != "PUBLIC_KEY_PLACEHOLDER" and len(base64.b64decode(PUBLIC_KEY)) == 32, "the owner's public key is in place"
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = base64.b64encode(priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    other = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with tempfile.TemporaryDirectory() as d:
        ch, src, inst = Path(d) / "channel", Path(d) / "src", Path(d) / "installed"
        ch.mkdir()
        (src / "installer").mkdir(parents=True)
        (src / "installer" / "build.py").write_text("")
        (src / "core").mkdir()
        (inst / "core").mkdir(parents=True)
        assert check("0.2.0", Path(d) / "nowhere", inst, pub)["state"] == "no_channel", "a folder that isn't there: couldn't check"
        assert check("0.2.0", ch, inst, pub)["state"] == "no_channel" and "No update has been published" in check("0.2.0", ch, inst, pub)["detail"]
        package = b"MSI" * 1000
        (ch / "Monday-0.3.0.msi").write_bytes(package)
        man = {"version": "0.3.0", "msi": "Monday-0.3.0.msi", "sha256": hashlib.sha256(package).hexdigest(), "size": len(package),
               "notes": "Updates in the notch.", "published": "2026-10-29T12:00:00+05:30"}
        data, sig = sign(man, pem)
        (ch / MANIFEST).write_bytes(data)
        (ch / SIGNATURE).write_text(sig)
        st = check("0.2.0", ch, inst, pub)
        assert st["state"] == "available" and st["action"] == "install" and st["latest"]["version"] == "0.3.0", st
        assert check("0.3.0", ch, inst, pub)["state"] == "up_to_date" and check("0.4.0", ch, inst, pub)["state"] == "up_to_date", "never a lower one"
        (src / "core" / "monday.py").write_text('VERSION = "0.3.0"\n')
        assert check("0.2.0", ch, src, pub)["action"] == "restart", "the source folder already has it on disk: a restart loads it"
        (src / "core" / "monday.py").write_text('VERSION = "0.2.0"\n')
        st = check("0.2.0", ch, src, pub)
        assert st["state"] == "available" and st["action"] is None and "still has 0.2.0" in st["detail"], st
        # the owner's key only: a manifest signed with another key, or changed after signing, is never offered
        d2, s2 = sign(man, other)
        (ch / SIGNATURE).write_text(s2)
        assert check("0.2.0", ch, inst, pub)["state"] == "refused", "another key's signature"
        (ch / SIGNATURE).write_text(sig)
        (ch / MANIFEST).write_bytes(data.replace(b"0.3.0", b"0.9.0"))
        assert check("0.2.0", ch, inst, pub)["state"] == "refused", "a manifest changed after it was signed"
        (ch / MANIFEST).write_bytes(data)
        # the package: copied and checked against the signed hash; a changed one is never handed to the wizard
        got, why = stage(ch, Path(d) / "stage", pub)
        assert why is None and got.read_bytes() == package and not (Path(d) / "stage" / "Monday-0.3.0.part").exists(), why
        (ch / "Monday-0.3.0.msi").write_bytes(b"EVIL" * 750)
        (Path(d) / "stage" / "Monday-0.3.0.msi").unlink()
        got, why = stage(ch, Path(d) / "stage", pub)
        assert got is None and "SHA-256 differs" in why and not list((Path(d) / "stage").glob("*.msi")), why
        bad = dict(man, msi="..\\evil.msi")
        db, sb = sign(bad, pem)
        (ch / MANIFEST).write_bytes(db)
        (ch / SIGNATURE).write_text(sb)
        assert check("0.2.0", ch, inst, pub)["state"] == "refused", "a manifest naming anything but Monday-<v>.msi in the channel"
    assert channel("D:/updates") == Path("D:/updates") and channel(None) in (DEFAULT_CHANNEL, Path(CHANNEL_FILE.read_text().strip()) if
                                                                             CHANNEL_FILE.exists() else DEFAULT_CHANNEL)
    assert disk_version() is not None and source_tree() == (ROOT / "installer" / "build.py").is_file(), \
        "the version on disk is readable here, the source folder or an installed copy"
    print("update self-check: OK")
