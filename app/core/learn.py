"""What Monday learns from its own outcomes, by code (the owner, 2026-10-06: "it should automatically learn from itself"). Nothing here
trains a model, and nothing is claimed that wasn't seen: Monday remembers what worked, with its evidence, and forgets what stopped working.

- Apps as they are heard: the recognizer gives "jetgbt" for ChatGPT. A name that only resolved by sound or spelling and then opened
  (verified) is kept as it was said; a name that failed to open and was followed within a minute by a verified open of an app it sounds
  or looks like is corrected to that app ("you then opened ChatGPT"). A learned name that fails is forgotten.
- Recipes: the controls the open-ended loop found for a goal in an app ("click my photo" in the Camera -> "Take photo"), kept when Jev saw
  the goal achieved after at most three clicks, keys or scrolls. Next time the same goal (the same words, or most of them) in the same
  app replays them without Jev choosing, is checked on screen the same way, and is forgotten the first time that check fails.
- The browser for a web address: the owner's "mine" / "yours" answers; the same answer three times in a row becomes the default and the
  question stops ("ask me which browser" brings it back).
- The owner's own words (2026-10-29, ADR-0013: "it should be dynamic"): a short form or slip the fast model put plainly ("yt" →
  "youtube", "wht" → "what") when that made an exact command; read before the rules from then on (router.normal), forgotten when a
  task routed with it fails.

Storage: Monday's settings table (the one database), four keys: learned_apps, recipes, browser_answers, learned_words. GET /api/learned
shows them.
"""
import difflib
import re
import time

APP_CORRECTION_S = 60    # a verified open this soon after a failed one corrects the failed name, when the two sound or look alike
APP_MIN_RATIO = 0.6      # how alike a failed name and the app then opened must be spelled, when they don't sound alike
RECIPE_MAX_STEPS = 3     # a recipe is a short way to a goal; a longer run was a search, not a way
RECIPE_MATCH = 0.8       # the share of a goal's words a stored recipe must share, in the same app
RECIPE_KEEP = 200
BROWSER_STREAK = 3       # the same browser answer this many times in a row becomes the default
WORDS_KEEP = 300         # the owner's learned words kept, the newest
_STRIP = re.compile(r"^(?:(?:hey|hi|hello|ok|okay)\s+)?monday[\s,]*|^(?:(?:please|now)\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|i\s+want\s+you\s+to\s+)+", re.I)
_FILLER = {"the", "a", "an", "my", "me", "please", "now", "that", "this", "it", "for", "to", "of", "in", "on", "and", "then", "up"}


def goal_key(goal):
    """A goal's words, lowercased, without the name, politeness and filler, in order: the key recipes are matched by."""
    words = re.findall(r"[a-z0-9]+", _STRIP.sub(" ", (goal or "").lower()))
    return " ".join(w for w in words if w not in _FILLER)


def app_key(w):
    """The app a window belongs to, for recipes: its process, except a Store app's (ApplicationFrameHost) is its window title."""
    if not w:
        return ""
    p = (getattr(w, "process", "") or "").lower()
    return (getattr(w, "title", "") or "").lower().strip() if p == "applicationframehost.exe" else p


class Learn:
    def __init__(self, get, put, now=time.time):
        self.get, self.put, self.now = get, put, now
        self._failed = None  # (name as said, when): the last app name that couldn't be opened

    # -- apps as they are heard ------------------------------------------------------------------------------------------------
    def apps(self):
        return dict(self.get("learned_apps") or {})

    def app_alias(self, said):
        e = self.apps().get(said or "")
        return e["app"] if e else None

    def learn_app(self, said, app, why):
        d = self.apps()
        d[said] = {"app": app, "why": why, "at": self.now(), "uses": (d.get(said) or {}).get("uses", 0)}
        self.put("learned_apps", d)
        return d[said]

    def forget_app(self, said):
        d = self.apps()
        if d.pop(said or "", None) is not None:
            self.put("learned_apps", d)

    def used_app(self, said):
        d = self.apps()
        if said in d:
            d[said]["uses"] = d[said].get("uses", 0) + 1
            self.put("learned_apps", d)

    def failed_open(self, said):
        self._failed = (said, self.now())

    def opened(self, said, app, exact, sounds_like=None):
        """A verified open of `app` for the name `said`: keep the name when it only resolved by sound or spelling (not exact), and
        correct the name that failed just before when it sounds or looks like this app. -> what was learned (a list)."""
        learned = []
        if not exact and said and said != app.lower():
            learned.append(self.learn_app(said, app, "heard as it sounds"))
        f, self._failed = self._failed, None
        if f and self.now() - f[1] <= APP_CORRECTION_S and f[0] != said:
            alike = bool(sounds_like and sounds_like(f[0], app.lower())) or difflib.SequenceMatcher(None, f[0], app.lower()).ratio() >= APP_MIN_RATIO
            if alike:
                learned.append(self.learn_app(f[0], app, f"you then opened {app}"))
        return learned

    # -- recipes: the controls that got a goal done in an app ---------------------------------------------------------------------
    def recipes(self):
        return list(self.get("recipes") or [])

    def recipe(self, goal, app):
        key, best = goal_key(goal), None
        words = set(key.split())
        if not words or not app:
            return None
        for r in self.recipes():
            if r.get("app") != app:
                continue
            rw = set(r["goal"].split())
            share = len(words & rw) / max(len(words | rw), 1)
            if share >= RECIPE_MATCH and (best is None or share > best[0]):
                best = (share, r)
        return best[1] if best else None

    def learn_recipe(self, goal, app, steps):
        if not steps or len(steps) > RECIPE_MAX_STEPS or not app:
            return None
        key = goal_key(goal)
        rs = [r for r in self.recipes() if not (r.get("app") == app and r.get("goal") == key)]
        r = {"goal": key, "app": app, "steps": steps, "wins": 1, "at": self.now()}
        rs.append(r)
        self.put("recipes", rs[-RECIPE_KEEP:])
        return r

    def recipe_worked(self, r):
        rs = self.recipes()
        for x in rs:
            if x.get("app") == r.get("app") and x.get("goal") == r.get("goal"):
                x["wins"], x["at"] = x.get("wins", 0) + 1, self.now()
        self.put("recipes", rs)

    def forget_recipe(self, r):
        self.put("recipes", [x for x in self.recipes() if not (x.get("app") == r.get("app") and x.get("goal") == r.get("goal"))])

    # -- the browser for a web address --------------------------------------------------------------------------------------------
    def browser_answer(self, whose):
        """One answer to "my Chrome, or your browser?" -> the browser that became the default right now, else None."""
        d = dict(self.get("browser_answers") or {})
        last = (list(d.get("last") or []) + [whose])[-BROWSER_STREAK:]
        d["last"] = last
        became = whose if len(last) == BROWSER_STREAK and len(set(last)) == 1 and d.get("default") != whose else None
        if became:
            d["default"] = became
        self.put("browser_answers", d)
        return became

    def browser_default(self):
        return (self.get("browser_answers") or {}).get("default")

    def forget_browser(self):
        self.put("browser_answers", {})

    # -- the owner's own words (ADR-0013) -------------------------------------------------------------------------------------
    def words(self):
        """{said: meant}: the short forms and slips learned, for router.normal."""
        return {k: e["to"] for k, e in (self.get("learned_words") or {}).items() if e.get("to")}

    def learn_words(self, pairs, why):
        """Pairs the fast model's plain words taught (router.taught), kept with why; the newest WORDS_KEEP."""
        if not pairs:
            return []
        d = dict(self.get("learned_words") or {})
        for said, meant in pairs:
            d[said] = {"to": meant, "why": why, "at": self.now(), "uses": (d.get(said) or {}).get("uses", 0)}
        self.put("learned_words", dict(sorted(d.items(), key=lambda kv: kv[1]["at"])[-WORDS_KEEP:]))
        return list(pairs)

    def forget_words(self, said):
        """A task routed with these words failed: they are forgotten. -> the ones that were known."""
        d = dict(self.get("learned_words") or {})
        gone = [w for w in said or () if d.pop(w, None)]
        if gone:
            self.put("learned_words", d)
        return gone

    def summary(self):
        return {"apps": self.apps(), "recipes": self.recipes(), "browser": self.get("browser_answers") or {},
                "words": self.get("learned_words") or {}}


if __name__ == "__main__":  # python -m core.learn: the rules, with a store in memory and a clock in hand
    store, clock = {}, [1000.0]
    L = Learn(store.get, store.__setitem__, now=lambda: clock[0])
    assert goal_key("Monday, please click my photo now") == "click photo" and goal_key("Take a photo of me") == "take photo"
    assert app_key(type("W", (), {"process": "ApplicationFrameHost.exe", "title": "Camera"})()) == "camera"
    assert app_key(type("W", (), {"process": "Spotify.exe", "title": "Tu Pyar Ka Sagar Hai"})()) == "spotify.exe" and app_key(None) == ""
    # apps: a name that only resolved by sound is kept; an exact one isn't
    assert L.opened("chatgbd", "ChatGPT", exact=False) and L.app_alias("chatgbd") == "ChatGPT"
    assert not L.opened("camera", "Camera", exact=True) and L.app_alias("camera") is None
    # a failed name corrected by the open that followed, only when alike and soon
    L.failed_open("check gpt"); clock[0] += 10
    assert [e["why"] for e in L.opened("chatgpt", "ChatGPT", exact=True)] == ["you then opened ChatGPT"] and L.app_alias("check gpt") == "ChatGPT"
    L.failed_open("jetgbt"); clock[0] += 10
    assert not L.opened("camera", "Camera", exact=True), "Camera is nothing like jetgbt: not learned"
    L.failed_open("jetgbt"); clock[0] += 120
    assert not L.opened("chatgpt", "ChatGPT", exact=True), "too long after the failure"
    L.failed_open("jetgbt"); clock[0] += 5
    assert L.opened("chatgpt", "ChatGPT", exact=True, sounds_like=lambda a, b: True) and L.app_alias("jetgbt") == "ChatGPT", "sound-alike counts"
    L.forget_app("jetgbt"); assert L.app_alias("jetgbt") is None
    L.used_app("chatgbd"); assert L.apps()["chatgbd"]["uses"] == 1
    # recipes: learned from a short run, matched by most of the words in the same app, forgotten on failure
    steps = [{"action": "click", "label": "Click “Switch to photo mode”", "args": {"target": "Switch to photo mode"}},
             {"action": "click", "label": "Click “Take photo”", "args": {"target": "Take photo"}}]
    assert L.learn_recipe("click my photo", "camera", steps) and L.recipe("Click my photo", "camera")["steps"] == steps
    assert L.recipe("take my photo", "camera") is None and L.recipe("click my photo", "spotify.exe") is None, "other words or another app: no recipe"
    assert L.recipe("please click my photo now", "camera") is not None, "filler words don't matter"
    assert L.learn_recipe("x", "camera", steps * 2) is None and L.learn_recipe("x", "camera", []) is None, "long or empty runs aren't recipes"
    r = L.recipe("click my photo", "camera"); L.recipe_worked(r); assert L.recipe("click my photo", "camera")["wins"] == 2
    L.forget_recipe(r); assert L.recipe("click my photo", "camera") is None
    # the browser: three of the same answers in a row, then the default; a different answer breaks the streak
    assert L.browser_answer("owner") is None and L.browser_answer("monday") is None and L.browser_answer("owner") is None
    assert L.browser_answer("owner") is None and L.browser_answer("owner") == "owner" and L.browser_default() == "owner"
    assert L.browser_answer("owner") is None, "already the default: said once"
    L.forget_browser(); assert L.browser_default() is None
    # the owner's own words: learned from the model's plain words, read from then on, forgotten when their task fails (ADR-0013)
    assert L.learn_words([("yt", "youtube"), ("wht", "what")], "made “play lofi on YouTube”") == [("yt", "youtube"), ("wht", "what")]
    assert L.words() == {"yt": "youtube", "wht": "what"} and L.learn_words([], "x") == []
    assert L.forget_words(["yt", "never"]) == ["yt"] and L.words() == {"wht": "what"}
    assert set(L.summary()) == {"apps", "recipes", "browser", "words"}
    print("learn self-check: OK (apps as heard, recipes, the browser answer, the owner's words)")
