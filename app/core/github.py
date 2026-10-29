"""GitHub on the island (the 2026-10-26 recording: "PRs, CI and your streak"), through the owner's own signed-in GitHub CLI (`gh`): Monday never
holds a token, gh answers as the owner. Read only, with fixed arguments: their open pull requests, the ones waiting on their review, the newest
CI run on one repository's default branch (the one they chose, else the one they pushed to last), its stars, their contribution calendar.
Nothing is written, opened, merged or commented.

    read(repo=None, run=None)   {"ok", "why", "login", "prs": [{"repo", "number", "title", "url"}], "reviews", "repo", "stars",
                                 "ci": {"state", "title", "url", "at"} | None, "days": [[date, count], …] oldest first, "at"}
    streak(days)                the days in a row with a contribution, up to today (or yesterday while today is empty)

python -m core.github          self-check on canned gh answers
python -m core.github --live   the owner's own, read through gh
"""
import json
import os
import shutil
import subprocess
import sys
import time

GH_TIMEOUT_S = 20        # one gh call
PR_LIMIT = 20            # open pull requests listed
REPO_LIMIT = 100        # the owner's repositories read for the overall (their count, the stars across them)
MENU_REPOS = 10         # ...of which the widget's repository menu offers the ones pushed to last
REVIEW_LIMIT = 50        # review requests counted
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
CALENDAR_Q = ("query{viewer{contributionsCollection{contributionCalendar{totalContributions "
              "weeks{contributionDays{date contributionCount}}}}}}")


def gh_exe():
    """The GitHub CLI on this PC (its install folder too: a window started before it was installed has an older PATH)."""
    return shutil.which("gh") or next((p for p in (r"C:\Program Files\GitHub CLI\gh.exe",) if os.path.isfile(p)), None)


def _runner(exe):
    def run(*args):
        """One gh call -> (its stdout, None) or (None, why)."""
        try:
            r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GH_TIMEOUT_S,
                               creationflags=NO_WINDOW, env={**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"})
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"gh didn't answer ({type(e).__name__})"
        if r.returncode:
            return None, (r.stderr or r.stdout or f"gh exited {r.returncode}").strip().splitlines()[-1][:200]
        return r.stdout, None
    return run


def _json(out):
    try:
        return json.loads(out or "null")
    except ValueError:
        return None


def read(repo=None, run=None):
    """The owner's GitHub as gh sees it now (see the module). A part gh can't answer is left out with its reason; ok is False only when gh
    itself is missing or not signed in."""
    if run is None:
        exe = gh_exe()
        if not exe:
            return {"ok": False, "why": "GitHub CLI isn't installed (winget install GitHub.cli), so there's nothing to read.", "at": time.time()}
        run = _runner(exe)
    out, why = run("api", "user", "--jq", ".login")
    if why:
        signed_out = any(w in why.lower() for w in ("auth login", "not logged", "authentication", "401", "bad credentials"))
        return {"ok": False, "why": "GitHub CLI isn't signed in: run `gh auth login` once." if signed_out else f"GitHub didn't answer: {why}",
                "at": time.time()}
    got = {"ok": True, "why": None, "login": out.strip(), "prs": [], "reviews": None, "repo": repo, "stars": None, "ci": None, "days": [],
           "at": time.time(), "missing": []}
    out, why = run("search", "prs", "--author=@me", "--state=open", "--json", "number,title,repository,url", "--limit", str(PR_LIMIT))
    rows = _json(out) if not why else None
    if isinstance(rows, list):
        got["prs"] = [{"repo": (r.get("repository") or {}).get("nameWithOwner") or (r.get("repository") or {}).get("name") or "",
                       "number": r.get("number"), "title": r.get("title") or "", "url": r.get("url") or ""} for r in rows]
    else:
        got["missing"].append(f"pull requests: {why or 'no answer'}")
    out, why = run("search", "prs", "--review-requested=@me", "--state=open", "--json", "number", "--limit", str(REVIEW_LIMIT))
    rows = _json(out) if not why else None
    if isinstance(rows, list):
        got["reviews"] = len(rows)
    else:
        got["missing"].append(f"reviews: {why or 'no answer'}")
    out, why = run("repo", "list", "--limit", str(REPO_LIMIT), "--json", "nameWithOwner,stargazerCount,defaultBranchRef,pushedAt")
    repos = _json(out) if not why else None
    if isinstance(repos, list) and repos:
        mine = next((r for r in repos if repo and r.get("nameWithOwner", "").lower() == repo.lower()), None) or \
            max(repos, key=lambda r: r.get("pushedAt") or "")
        got["repo"], got["stars"] = mine.get("nameWithOwner"), mine.get("stargazerCount")
        got["repos"] = [r.get("nameWithOwner") for r in sorted(repos, key=lambda r: r.get("pushedAt") or "", reverse=True)
                        if r.get("nameWithOwner")][:MENU_REPOS]  # (the widget's repository menu: the ten pushed to last)
        got["repo_count"], got["stars_all"] = len(repos), sum(int(r.get("stargazerCount") or 0) for r in repos)  # the overall (2026-10-26)
        branch = (mine.get("defaultBranchRef") or {}).get("name") or "main"
        out, why = run("run", "list", "--repo", got["repo"], "--branch", branch, "--limit", "1", "--json",
                       "status,conclusion,displayTitle,url,createdAt,workflowName")
        runs = _json(out) if not why else None
        if isinstance(runs, list):
            got["ci"] = None if not runs else {"state": runs[0].get("conclusion") or runs[0].get("status") or "unknown",
                                               "title": runs[0].get("displayTitle") or runs[0].get("workflowName") or "",
                                               "url": runs[0].get("url") or "", "at": runs[0].get("createdAt"), "branch": branch}
        else:
            got["missing"].append(f"CI: {why or 'no answer'}")
    elif why:
        got["missing"].append(f"repositories: {why}")
    out, why = run("api", "graphql", "-f", f"query={CALENDAR_Q}")
    cal = (((_json(out) or {}).get("data") or {}).get("viewer") or {}).get("contributionsCollection", {}).get("contributionCalendar") if not why else None
    if isinstance(cal, dict):
        got["days"] = [[d.get("date"), int(d.get("contributionCount") or 0)] for w in cal.get("weeks") or [] for d in w.get("contributionDays") or []]
        got["total"] = cal.get("totalContributions")
    else:
        got["missing"].append(f"contributions: {why or 'no answer'}")
    return got


def best_streak(days):
    """The longest run of days in a row with at least one contribution in the calendar (the overall, 2026-10-26)."""
    best = run = 0
    for _d, c in days or []:
        run = run + 1 if c else 0
        best = max(best, run)
    return best


def streak(days, today=None):
    """Days in a row with at least one contribution, ending today (or yesterday, while today has none yet)."""
    counts = {d: c for d, c in days or []}
    today = today or time.strftime("%Y-%m-%d")
    dates = sorted(counts)
    if today not in counts and dates:
        today = dates[-1]
    n, i = 0, dates.index(today) if today in dates else -1
    if i >= 0 and not counts[dates[i]]:
        i -= 1  # today empty so far: the run up to yesterday still counts
    while i >= 0 and counts[dates[i]]:
        n, i = n + 1, i - 1
    return n


def ci_words(ci):
    """The CI line as the recording says it: "all green", "failing", "running", or N/A."""
    if not ci:
        return "no runs"
    s = str(ci.get("state") or "").lower()
    return {"success": "all green", "failure": "failing", "cancelled": "cancelled", "timed_out": "failing", "startup_failure": "failing",
            "action_required": "needs you", "skipped": "skipped"}.get(s, "running" if s in ("in_progress", "queued", "requested", "waiting",
                                                                                                 "pending") else s or "N/A")


def _check():
    calls = []
    answers = {
        ("api", "user"): ("bhavye-thakkar\n", None),
        ("search", "prs", "--author=@me"): (json.dumps([{"number": 307, "title": "Windows & Linux 0.2.0", "url": "https://github.com/o/coucou/pull/307",
                                                         "repository": {"nameWithOwner": "o/coucou", "name": "coucou"}}]), None),
        ("search", "prs", "--review-requested=@me"): (json.dumps([{"number": 1}, {"number": 2}]), None),
        ("repo", "list"): (json.dumps([{"nameWithOwner": "o/old", "stargazerCount": 1, "defaultBranchRef": {"name": "main"}, "pushedAt": "2026-10-01T00:00:00Z"},
                                       {"nameWithOwner": "o/coucou", "stargazerCount": 3800, "defaultBranchRef": {"name": "main"},
                                        "pushedAt": "2026-10-25T00:00:00Z"}]), None),
        ("run", "list"): (json.dumps([{"status": "completed", "conclusion": "success", "displayTitle": "Release", "url": "u", "createdAt": "x"}]), None),
        ("api", "graphql"): (json.dumps({"data": {"viewer": {"contributionsCollection": {"contributionCalendar": {"totalContributions": 4, "weeks": [
            {"contributionDays": [{"date": "2026-10-23", "contributionCount": 1}, {"date": "2026-10-24", "contributionCount": 2},
                                  {"date": "2026-10-25", "contributionCount": 1}, {"date": "2026-10-26", "contributionCount": 0}]}]}}}}}), None),
    }

    def run(*args):
        calls.append(args)
        return next((v for k, v in answers.items() if args[:len(k)] == k), (None, "unexpected"))
    g = read(run=run)
    assert g["ok"] and g["login"] == "bhavye-thakkar" and g["prs"] == [{"repo": "o/coucou", "number": 307, "title": "Windows & Linux 0.2.0",
                                                                         "url": "https://github.com/o/coucou/pull/307"}], g
    assert (g["reviews"], g["repo"], g["stars"], g["ci"]["state"], len(g["days"]), g["missing"]) == (2, "o/coucou", 3800, "success", 4, []), g
    assert ("run", "list", "--repo", "o/coucou", "--branch", "main", "--limit", "1", "--json",
            "status,conclusion,displayTitle,url,createdAt,workflowName") in calls, "CI of the repository pushed to last, its default branch"
    assert {c[:2] for c in calls} == {("api", "user"), ("search", "prs"), ("repo", "list"), ("run", "list"), ("api", "graphql")}, \
        f"read only: exactly these five gh reads, nothing else ({sorted({c[:2] for c in calls})})"
    g2 = read(repo="o/old", run=run)
    assert (g2["repo"], g2["stars"]) == ("o/old", 1), "the repository the owner chose"
    assert streak(g["days"], "2026-10-26") == 3 and streak(g["days"], "2026-10-25") == 3 and streak([], "2026-10-26") == 0
    assert streak([["2026-10-24", 1], ["2026-10-25", 0], ["2026-10-26", 2]], "2026-10-26") == 1, "a gap ends it"
    assert (g["repo_count"], g["stars_all"], g["total"]) == (2, 3801, 4), "the overall: every repository listed, the stars across them"
    assert ("repo", "list", "--limit", str(REPO_LIMIT)) == next(c for c in calls if c[:2] == ("repo", "list"))[:4], "all of them, not ten"
    assert best_streak(g["days"]) == 3 and best_streak([["a", 1], ["b", 0], ["c", 1], ["d", 1]]) == 2 and best_streak([]) == 0
    assert (ci_words({"state": "success"}), ci_words({"state": "failure"}), ci_words({"state": "in_progress"}), ci_words(None)) == \
        ("all green", "failing", "running", "no runs")
    out = read(run=lambda *a: (None, "To get started with GitHub CLI, please run:  gh auth login"))
    assert not out["ok"] and "gh auth login" in out["why"], out
    out = read(run=lambda *a: (None, "connection refused") if a[:2] != ("api", "user") else ("me\n", None))
    assert out["ok"] and out["prs"] == [] and out["reviews"] is None and len(out["missing"]) == 4, "a part gh can't answer is said, not invented"
    print("github self-check: OK")


if __name__ == "__main__":
    if "--live" in sys.argv:
        g = read()
        print(json.dumps({k: v for k, v in g.items() if k != "days"}, indent=1), "days:", len(g.get("days") or []), "streak:", streak(g.get("days")))
    else:
        _check()
