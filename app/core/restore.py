"""Restore points and Rewind (2026-10-17, after Dotpals' bridge/ground.js, MIT, THIRD_PARTY_NOTICES.md). Off until the owner turns them on
(Settings → Restore points). When one of the owner's agents starts a request and when it ends, the working tree of the git repository it
works in (tracked and new files, not ignored ones) is saved as a git tree object, built in a private copy of the index: the owner's index,
branches and files are never touched, and nothing points at the trees, so git's own clean-up removes them in a couple of weeks. A request
can then be rewound: the files it changed put back as they were before it (the ones the owner picks, after a preview), only if nothing
changed since the preview, and undone.

    checkpoint(root)                       the working tree as a tree object (None: not saved: no git, too slow, too many or too big files)
    changes(root, a, b)                    the files that differ between two trees: what a request changed, however it changed them
    plan(root, before, after, now, paths)  what rewinding those paths would do (restore, delete, recreate; changed again since) and its diff
    restore(root, tree, paths)             those paths put back as the tree has them (written through a private index, or deleted)

No program the repository's own config names runs for these commands (hooks, fsmonitor), files go in and come back exactly (no CRLF
conversion), paths are only ever file names (never patterns), and nothing is written outside the repository (links are followed to check).

    python -m core.restore      self-check: a throwaway repository, its index, HEAD and status untouched throughout
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

EXACT = ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.fsmonitor=false", "-c", "core.hooksPath="]
LITERAL = ["--literal-pathspecs"]  # a file called `*.js` or `:(top)x` is only itself
HASH = re.compile(r"^[0-9a-f]{40,64}$")
BIG_FILE, BIG_TOTAL = 10 * 1024 * 1024, 100 * 1024 * 1024  # a new file this big, or new files this big in all: no restore point
LIMIT = 5000  # more changed files than this (a build into a tracked folder): no restore point
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git(args, cwd, timeout=10, env=None, input=None):
    """git in `cwd` -> its output, or None when it failed (not a repository, no git, too slow). `input` goes to its stdin (path lists
    never go on the command line). No lock is taken that the owner's own git could trip over (GIT_OPTIONAL_LOCKS=0)."""
    try:
        r = subprocess.run(["git", *args], cwd=cwd, input=input, capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", **(env or {})})
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def root_of(cwd):
    """The git repository a folder is in (its top folder), or None."""
    if not cwd or not os.path.isdir(cwd):
        return None
    out = git(["rev-parse", "--show-toplevel"], cwd)
    return str(Path(out.strip())) if out and out.strip() else None


def _with_index(root, fn):
    """fn(env) with a private index for the repository (empty, in a folder of its own), deleted after: the owner's is never read or written.
    (Not a copy of theirs, as Dotpals does: on Windows a file whose times hadn't changed would keep the blob their own CRLF setting made,
    another be read again as it is, and a request would seem to change files it never touched; found by the self-check, 2026-10-17.)"""
    d = tempfile.mkdtemp(prefix="monday-index-")
    try:
        return fn({"GIT_INDEX_FILE": os.path.join(d, "index")})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _too_big(root):
    """Why not to save this working tree: too many changed files, or new files too big to copy into the repository; else None."""
    out = git(["-c", "core.quotepath=off", "status", "--porcelain=v1", "-z", "--untracked-files=all"], root, timeout=20)
    if out is None:
        return "git couldn't list the files"
    items = [p for p in out.split("\0") if len(p) >= 4]
    if len(items) > LIMIT:
        return f"more than {LIMIT} files changed"
    total = 0
    for p in items:
        if p[:2] == "??":
            try:
                size = os.path.getsize(os.path.join(root, p[3:]))
            except OSError:
                continue
            total += size
            if size > BIG_FILE or total > BIG_TOTAL:
                return "new files too big to keep (a build, a dump)"
    return None


def checkpoint(root):
    """The repository's working tree (tracked and new files, not ignored ones) saved as a tree object -> its hash, or None."""
    if not root or _too_big(root):
        return None

    def save(env):
        git([*EXACT, "read-tree", "HEAD"], root, env=env)  # the last commit's files (none in a new repository), so a file committed and
        #                                                    since ignored is still kept; with no times recorded every file is read again
        if git([*EXACT, "add", "-A", "--", "."], root, timeout=60, env=env) is None:
            return None
        tree = (git(["write-tree"], root, env=env) or "").strip()
        return tree if HASH.match(tree) else None
    return _with_index(root, save)


def changes(root, a, b):
    """The files that differ between two saved trees (repository-relative, forward slashes): what a request changed, however it did."""
    if not (HASH.match(a or "") and HASH.match(b or "")):
        return None
    out = git(["-c", "core.quotepath=off", "diff", "--name-only", "-z", "--no-renames", a, b], root, timeout=20)
    return None if out is None else sorted(p for p in out.split("\0") if p)


def _tree_files(root, tree):
    """Every file in a saved tree, or None when it isn't one (cleaned up by git, git failed): a failure never reads as "none of them"."""
    if not HASH.match(tree or "") or git(["cat-file", "-e", f"{tree}^{{tree}}"], root) is None:
        return None
    out = git(["-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", "-z", tree], root, timeout=30)
    return None if out is None else {p for p in out.split("\0") if p}


def inside(root, path):
    """A path from git (repository-relative) that stays inside the repository, as a full path; else None."""
    if not path or os.path.isabs(path) or ".." in re.split(r"[\\/]", str(path)):
        return None
    full, top = os.path.abspath(os.path.join(root, path)), os.path.abspath(root)
    return full if full.startswith(top + os.sep) else None


def _folder_inside(root, full):
    """Whether the folder `full` would be written in really is inside the repository, links followed: the nearest folder of it that
    exists must resolve inside (a folder that became a link to somewhere else isn't)."""
    top, d = os.path.realpath(root), os.path.dirname(full)
    while not os.path.isdir(d):
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up
    real = os.path.realpath(d)
    return real == top or real.startswith(top + os.sep)


def _remove_inside(root, path):
    """A file the request created, deleted only if it really is inside the repository and is a file or a link (never a folder)."""
    full = inside(root, path)
    if not full or not _folder_inside(root, full):
        return False
    try:
        if os.path.isdir(full) and not os.path.islink(full):
            return False
        os.remove(full)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def plan(root, before, after, now, paths, most=60_000):
    """What rewinding `paths` from `after` (the request's end) to `before` (its start) would do, with the working tree as it is `now`:
    {"files": [{"path", "change": restore | delete | recreate, "since"}], "patch"}, or None when git can't read a tree. since: the file
    changed again after the request (the owner, or another agent): putting it back loses that too. patch: the diff from now to before."""
    if not paths:
        return {"files": [], "patch": ""}
    had, has = _tree_files(root, before), _tree_files(root, now)
    since_out = git(["-c", "core.quotepath=off", "diff", "--name-only", "-z", "--no-renames", after, now], root, timeout=20) if after else ""
    if had is None or has is None or since_out is None:
        return None
    since = {p for p in since_out.split("\0") if p}
    fits = sum(len(p) + 1 for p in paths) < 20_000  # the diff names them on its command line: only when they fit on one
    patch = (git([*LITERAL, "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "--no-renames", now, before, "--", *paths], root,
                 timeout=20) or "") if fits else ""
    files = [{"path": p, "change": "delete" if p not in had else "restore" if p in has else "recreate", "since": p in since} for p in paths]
    return {"files": files, "patch": patch if len(patch) <= most else patch[:most] + f"\n… ({len(patch) - most} more characters)"}


def restore(root, tree, paths):
    """`paths` put back as `tree` has them: written from it through a private index (the paths on stdin), or deleted when it doesn't have
    them (the request created them). -> {"restored", "deleted", "skipped"}, or None when git couldn't read the tree or write the files
    (then nothing is deleted). Paths outside the repository, and files behind a link that leads out of it, are left alone."""
    asked = list(dict.fromkeys(paths))
    safe = [p for p in asked if inside(root, p)]
    skipped = [p for p in asked if p not in safe]
    if not safe:
        return {"restored": [], "deleted": [], "skipped": skipped}
    present = _tree_files(root, tree)
    if present is None:
        return None
    write = []
    for p in (x for x in safe if x in present):
        (write if _folder_inside(root, inside(root, p)) else skipped).append(p)
    if write:
        def put(env):
            if git([*EXACT, "read-tree", tree], root, env=env) is None:
                return False
            return git([*EXACT, "checkout-index", "-f", "-z", "--stdin"], root, timeout=30, env=env, input="\0".join(write).encode()) is not None
        if not _with_index(root, put):
            return None
    deleted = []
    for p in (x for x in safe if x not in present):
        (deleted if _remove_inside(root, p) else skipped).append(p)
    return {"restored": write, "deleted": deleted, "skipped": skipped}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "shop")
        os.makedirs(os.path.join(root, "src"))

        def run(*a):  # (its own status must not refresh the index either: GIT_OPTIONAL_LOCKS, as git() runs)
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True, creationflags=NO_WINDOW,
                                  env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        Path(root, "src", "billing.ts").write_bytes(b"const vat = Math.floor(x);\r\n")  # CRLF: it must come back exactly
        Path(root, "README.md").write_text("shop\n", encoding="utf-8")
        Path(root, ".gitignore").write_text("dist/\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-qm", "first")
        Path(root, "notes.txt").write_text("the owner's own unsaved note\n", encoding="utf-8")  # changed before the request: kept as it is
        os.makedirs(os.path.join(root, "dist"))
        Path(root, "dist", "big.bin").write_bytes(b"x" * 100)  # ignored: never in a restore point
        head0, status0, index0 = run("rev-parse", "HEAD"), run("status", "--porcelain"), Path(root, ".git", "index").read_bytes()
        assert root_of(os.path.join(root, "src")) == str(Path(root)) and root_of(d) is None

        before = checkpoint(root)  # the request starts
        assert before and HASH.match(before)
        Path(root, "src", "billing.ts").write_bytes(b"const vat = Math.round(x);\r\n")  # the agent's changes: an edit, a new file, a delete
        Path(root, "src", "cart.ts").write_text("export const cart = [];\n", encoding="utf-8")
        os.remove(os.path.join(root, "README.md"))
        after = checkpoint(root)  # the request ends
        assert run("rev-parse", "HEAD") == head0 and Path(root, ".git", "index").read_bytes() == index0, "the owner's HEAD and index untouched"
        got = changes(root, before, after)
        assert got == ["README.md", "src/billing.ts", "src/cart.ts"], got
        assert "dist/big.bin" not in (_tree_files(root, after) or set()) and "notes.txt" in _tree_files(root, before), "ignored files aren't kept"

        Path(root, "src", "billing.ts").write_bytes(b"const vat = Math.ceil(x);\r\n")  # changed again after the request
        now = checkpoint(root)
        p = plan(root, before, after, now, got)
        assert [(f["path"], f["change"], f["since"]) for f in p["files"]] == [
            ("README.md", "recreate", False), ("src/billing.ts", "restore", True), ("src/cart.ts", "delete", False)], p["files"]
        assert "-const vat = Math.ceil(x);" in p["patch"] and "+const vat = Math.floor(x);" in p["patch"], p["patch"]

        r = restore(root, before, got + ["../outside.txt", "C:/Windows/x"])  # the rewind (and two paths that aren't the repository's)
        assert r == {"restored": ["README.md", "src/billing.ts"], "deleted": ["src/cart.ts"], "skipped": ["../outside.txt", "C:/Windows/x"]}, r
        assert Path(root, "src", "billing.ts").read_bytes() == b"const vat = Math.floor(x);\r\n", "back exactly, CRLF and all"
        assert Path(root, "README.md").read_text(encoding="utf-8") == "shop\n" and not Path(root, "src", "cart.ts").exists()
        assert Path(root, "notes.txt").read_text(encoding="utf-8") == "the owner's own unsaved note\n", "a file it didn't pick stays as it is"
        assert run("status", "--porcelain") == status0 and run("rev-parse", "HEAD") == head0 and Path(root, ".git", "index").read_bytes() == index0, \
            "rewound: status as before the request, the owner's index and HEAD never touched"
        u = restore(root, now, got)  # undo: as it was just before the rewind
        assert u["restored"] == ["src/billing.ts", "src/cart.ts"] and u["deleted"] == ["README.md"], u
        assert Path(root, "src", "billing.ts").read_bytes() == b"const vat = Math.ceil(x);\r\n" and Path(root, "src", "cart.ts").exists()

        assert inside(root, "src/../../x") is None and inside(root, "/etc/passwd") is None and inside(root, "src/a.ts").endswith("a.ts")
        assert restore(root, "0" * 40, ["src/a.ts"]) is None and changes(root, "nope", after) is None, "a tree git doesn't have: nothing done"
        Path(root, "dump.bin").write_bytes(b"\0" * (BIG_FILE + 1))
        assert checkpoint(root) is None and "too big" in _too_big(root), "a new file too big to keep: no restore point"
        os.remove(os.path.join(root, "dump.bin"))
        assert checkpoint(d) is None, "not a repository: no restore point"
    print("restore self-check: OK (a request's files saved at its start and end, the changes, the plan, rewound exactly and undone; the "
          "owner's index, HEAD and status untouched; outside paths, unknown trees and big files refused)")
