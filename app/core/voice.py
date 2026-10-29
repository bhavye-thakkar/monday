"""Monday's voice: which voice speaks, how fast, how high, and whether it speaks at all.

Voices are never invented. They come from the browser that speaks (the HUD reports speechSynthesis.getVoices()) and
from Windows' own voice registry, which is the authority on gender for the voices installed on this PC. Monday only
offers voices known to be female: Windows says so, the name says so ("Google UK English Female"), or the name is a
known female voice. A voice whose gender can't be told is left out, not guessed. The selection lives in SQLite
(settings.voice): voice_provider, voice_id, voice_name, language, speed, pitch, enabled, and how Monday listens:
patience (how long a pause could last before the old pages decided the owner had finished; Windows decides in Monday's window),
follow_up (the mic stays open briefly after a spoken reply) and filming (the mic stays open, and Monday answers only
what is clearly said to it: core/jev.py's prepass decides, see Monday.prepass).
Self-check: python -m core.voice
"""
import re
import sys

# tts_provider: who speaks (core/speech.py). Since 2026-10-13 the owner's own voice (`custom`: cloned on this PC from their recording) is
# the one voice Monday speaks with; the browser's voices and Google's stay reachable only by an explicit choice (the API, "use the browser
# voice"), never as a fallback. voice_* is the voice chosen there; choices remembers the pick in each provider so switching back restores it.
DEFAULT = {"tts_provider": "custom", "voice_provider": "custom", "voice_id": "owner", "voice_name": "Your voice", "language": None,
           "speed": 1.0, "pitch": 1.0, "enabled": True, "patience": "normal", "follow_up": True, "filming": False, "choices": {},
           "interaction_mode": "pure_agent", "wake": True, "wake_sure": 0.80}
# wake: the open microphone acts only on what starts with "okay Monday" (router.wake_rest; the owner, 2026-10-19: "then and then only it'll
# work"); "okay Monday" alone and the next sentence counts too (monday.WAKE_S). Push-to-talk and typing never need it.
# wake_sure (2026-10-27): the bare name's sensitivity, 0-1. "Monday, …" alone (not "okay Monday") opens the ear with the sentence marked
# (hear.py `named`), and Jev is asked whether that sentence is said to Monday (Monday._gesture_doubt): it goes through when Jev is at least
# this sure. Lower lets more of the room in; higher asks for a clearer "to me". POST /api/voice/settings sets it (Monday.set_voice).
WAKE_SURE_MIN, WAKE_SURE_MAX = 0.0, 1.0
# How Monday meets the owner (their brief, 2026-10-06: "pure agentic computer use"). pure_agent: it acts and says nothing, the HUD is
# one strip and the computer changing is the reply; debug: the whole HUD (Jev's decisions, the tool chain, the strip), still silent;
# assistant: spoken replies, as before. The HUD reads it; the server never speaks.
MODES = ("pure_agent", "debug", "assistant")
_MODE_OPS = {"mode_pure": "pure_agent", "mode_debug": "debug", "mode_assistant": "assistant"}
SPEED_STEP, SPEED_MIN, SPEED_MAX = 0.15, 0.6, 2.0
PITCH_STEP, PITCH_MIN, PITCH_MAX = 0.1, 0.6, 1.6
PATIENCE = ("quick", "normal", "relaxed")  # scaled the old pages' endpoint waits; Monday's window sends Windows' final words, so unused there
# Female voices shipped by Windows / Edge (Microsoft), Chrome (Google) and Apple, by first name.
FEMALE = set("""zira heera hazel susan catherine linda eva aria jenny michelle ana ava emma sonia libby maisie natasha clara neerja
swara kalpana hemlata elsa isabella hedda katja amala seraphina denise julie hortense vivienne eloise helena laura sabina dalia
paulina elvira francisca raquel fernanda giovanna yara xiaoxiao xiaoyi huihui yaoyao xiaohan xiaomo nanami haruka ayumi mayu
sunhi heami yuna ines leonor sofie colette fenna dagmar hulda iveta hilda sara noora aino pernille ewa zofia agnieszka irina
svetlana dariya ekaterina polina lesya ozlem emel sevgi hoaimy premwadee achara gadis damayanti yasmin zariyah salma hoda shakir
samantha karen moira tessa veena victoria allison ava fiona serena""".split())
MALE = set("""david mark ravi guy george ryan eric christopher roger steffan andrew brian davis tony jason jacob thomas liam
prabhat madhur hemant kangkang yunxi yunyang ichiro keita injoon stefan conrad henri paul pablo jorge alvaro raul daniel
alex fred ralph tom oliver arthur rishi aaron""".split())
GOOGLE_FEMALE = {"google us english", "google uk english female", "google हिन्दी", "google deutsch", "google français", "google italiano",
                 "google 日本語", "google nederlands", "google português do brasil", "google polski", "google русский", "google bahasa indonesia",
                 "google 한국의", "google 普通话（中国大陆）", "google 粤語（香港）", "google 國語（臺灣）", "google español de estados unidos"}
_ACCENTS = {"british": "en-GB", "uk": "en-GB", "english uk": "en-GB", "american": "en-US", "us": "en-US", "indian": "en-IN",
            "australian": "en-AU", "irish": "en-IE", "canadian": "en-CA", "hindi": "hi-IN"}


def windows_voices():
    """{first name: gender} for the voices Windows has installed (SAPI desktop + OneCore). Empty off Windows."""
    if sys.platform != "win32":
        return {}
    import winreg
    out = {}
    for base in (r"SOFTWARE\Microsoft\Speech\Voices\Tokens", r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i) + r"\Attributes") as a:
                            name = winreg.QueryValueEx(a, "Name")[0]
                            gender = winreg.QueryValueEx(a, "Gender")[0]
                        out[_first_name(name)] = gender.lower()
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _first_name(name):
    n = re.sub(r"^(?:microsoft|google|apple)\s+", "", (name or "").strip(), flags=re.I)
    return (n.split() or [""])[0].lower()


def gender(v, win=None):
    """'female', 'male' or None (unknown: never guessed)."""
    name = (v.get("name") or "").strip()
    low = name.lower()
    if re.search(r"\bfemale\b", low):
        return "female"
    if re.search(r"\bmale\b", low):
        return "male"
    base = re.sub(r"\s*[-(].*$", "", low).strip()
    if base in GOOGLE_FEMALE:
        return "female"
    first = _first_name(name)
    if win and first in win:
        return win[first]
    if first in FEMALE:
        return "female"
    if first in MALE:
        return "male"
    return None


def describe(v, win=None):
    name = v.get("name") or ""
    provider = (re.match(r"^(microsoft|google|apple)\b", name, re.I) or [None])[0]
    natural = bool(re.search(r"natural|neural|online|enhanced|premium", name, re.I))
    offline = bool(v.get("localService"))
    lang = v.get("lang") or ""
    g = gender(v, win)
    return {"id": v.get("voiceURI") or name, "name": name, "lang": lang, "provider": (provider or "browser").title(), "natural": natural,
            "offline": offline, "gender": g,
            "label": f"{'Natural' if natural else 'Standard'} {g or 'voice'} · {'offline' if offline else 'online'} · {lang or 'language unknown'}"}


def catalog(reported, win=None, lang="en"):
    """Female voices only, in a stable order: the owner's language first, then natural before standard, online before
    offline, then by name. The numbering ("voice 2") is this order."""
    seen, out = set(), []
    for v in reported or []:
        d = describe(v, win)
        if d["gender"] != "female" or d["id"] in seen:
            continue
        seen.add(d["id"])
        out.append(d)
    out.sort(key=lambda d: (not d["lang"].lower().startswith(lang), not d["natural"], d["offline"], d["name"].lower()))
    for i, d in enumerate(out, 1):
        d["number"] = i
    return out


def choose(cat, ordinal=None, number=None, query=None, current=None):
    """(voice, why) from what the owner said, or (None, why)."""
    if not cat:
        return None, "no female voice has been reported by Windows (Monday's window reports them when it opens)"
    if number is not None:
        return (cat[number - 1], "") if 1 <= number <= len(cat) else (None, f"there are only {len(cat)} female voices")
    if ordinal is not None:
        idx = next((i for i, v in enumerate(cat) if current and v["id"] == current.get("voice_id")), -1)
        if ordinal == "next":
            return cat[(idx + 1) % len(cat)], ""
        if ordinal == "prev":
            return cat[(idx - 1) % len(cat)], ""
        if ordinal == -1:
            return cat[-1], ""
        return (cat[ordinal - 1], "") if 1 <= ordinal <= len(cat) else (None, f"there are only {len(cat)} female voices")
    q = (query or "").lower().strip()
    if q in ("default", "normal", "usual", "original", "standard"):
        return cat[0], ""
    words = [w for w in re.split(r"\s+", q) if w not in ("female", "woman", "womans", "woman's", "lady", "girl", "voice", "a", "the")]
    for w in words:
        if w in ("natural", "neural", "realistic", "human", "warm"):
            hit = [v for v in cat if v["natural"]]
        elif w in ("offline", "local"):
            hit = [v for v in cat if v["offline"]]
        elif w in ("online", "cloud"):
            hit = [v for v in cat if not v["offline"]]
        elif w in _ACCENTS:
            hit = [v for v in cat if v["lang"].lower() == _ACCENTS[w].lower()]
        else:
            hit = [v for v in cat if w in v["name"].lower()]
        if hit:
            cat = hit
        else:
            return None, f"none of the female voices is {w}"
    return cat[0], ""


def adjust(settings, op):
    s = {**DEFAULT, **(settings or {})}
    if op == "faster":
        s["speed"] = round(min(SPEED_MAX, s["speed"] + SPEED_STEP), 2)
    elif op == "slower":
        s["speed"] = round(max(SPEED_MIN, s["speed"] - SPEED_STEP), 2)
    elif op == "higher":
        s["pitch"] = round(min(PITCH_MAX, s["pitch"] + PITCH_STEP), 2)
    elif op == "lower":
        s["pitch"] = round(max(PITCH_MIN, s["pitch"] - PITCH_STEP), 2)
    elif op in ("mute", "unmute"):
        s["enabled"] = op == "unmute"
        if op == "unmute":
            s["interaction_mode"] = "assistant"  # "turn on your voice" in a silent mode means: talk to me (only assistant mode speaks)
    elif op in _MODE_OPS:
        s["interaction_mode"] = _MODE_OPS[op]
    elif op in ("listen_longer", "listen_shorter", "listen_normal"):
        i = PATIENCE.index(s["patience"]) if s["patience"] in PATIENCE else 1
        s["patience"] = PATIENCE[{"listen_longer": min(i + 1, 2), "listen_shorter": max(i - 1, 0), "listen_normal": 1}[op]]
    elif op in ("follow_up_on", "follow_up_off"):
        s["follow_up"] = op == "follow_up_on"
    elif op in ("filming_on", "filming_off"):
        s["filming"] = op == "filming_on"
    return s


def selected(settings, v):
    """Settings with voice v chosen in the current provider (and remembered for that provider)."""
    s = {**DEFAULT, **(settings or {})}
    pick = {"voice_id": v["id"], "voice_name": v["name"], "language": v["lang"]}
    return {**s, **pick, "voice_provider": v.get("vendor") or v["provider"], "choices": {**(s.get("choices") or {}), s["tts_provider"]: pick}}


def listing(cat, current=None, provider_label="this browser"):
    if not cat:
        return f"I don't have a female voice from {provider_label} yet." + (
            " Monday's window reports Windows' voices when it opens." if provider_label == "this browser" else "")
    cur = (current or {}).get("voice_id")
    return f"Female voices from {provider_label}:\n" + "\n".join(
        f"{v['number']}. {v['name']} — {v['label']}" + (" (current)" if v["id"] == cur else "") for v in cat[:12]) + \
        ("\n…and more in the voice panel." if len(cat) > 12 else "") + \
        "\nSay “use voice 2”, “use the natural female voice”, or pick one in the voice panel."


if __name__ == "__main__":  # python -m core.voice
    reported = [
        {"name": "Microsoft David - English (United States)", "lang": "en-US", "localService": True, "voiceURI": "d"},
        {"name": "Microsoft Zira - English (United States)", "lang": "en-US", "localService": True, "voiceURI": "z"},
        {"name": "Google US English", "lang": "en-US", "localService": False, "voiceURI": "g-us"},
        {"name": "Google UK English Female", "lang": "en-GB", "localService": False, "voiceURI": "g-uk-f"},
        {"name": "Google UK English Male", "lang": "en-GB", "localService": False, "voiceURI": "g-uk-m"},
        {"name": "Microsoft Aria Online (Natural) - English (United States)", "lang": "en-US", "localService": False, "voiceURI": "aria"},
        {"name": "Microsoft Guy Online (Natural) - English (United States)", "lang": "en-US", "localService": False, "voiceURI": "guy"},
        {"name": "Mystery Voice", "lang": "en-US", "localService": True, "voiceURI": "m"},
    ]
    cat = catalog(reported, {"zira": "female", "david": "male"})
    assert [v["id"] for v in cat] == ["aria", "g-uk-f", "g-us", "z"], [v["id"] for v in cat]
    assert choose(cat, ordinal=2)[0]["id"] == "g-uk-f" and choose(cat, number=4)[0]["id"] == "z"
    assert choose(cat, query="natural")[0]["id"] == "aria" and choose(cat, query="offline")[0]["id"] == "z"
    assert choose(cat, query="british")[0]["id"] == "g-uk-f" and choose(cat, number=9)[0] is None
    assert choose(cat, ordinal="next", current={"voice_id": "z"})[0]["id"] == "aria", "next wraps around"
    assert choose(cat, query="pirate")[0] is None and choose([], ordinal=1)[0] is None
    assert adjust({}, "faster")["speed"] == 1.15 and adjust({"speed": 0.6}, "slower")["speed"] == 0.6 and not adjust({}, "mute")["enabled"]
    assert adjust({}, "listen_longer")["patience"] == "relaxed" and adjust({"patience": "relaxed"}, "listen_longer")["patience"] == "relaxed"
    assert adjust({}, "listen_shorter")["patience"] == "quick" and adjust({"patience": "quick"}, "listen_normal")["patience"] == "normal"
    assert adjust({}, "follow_up_off")["follow_up"] is False and adjust({"follow_up": False}, "follow_up_on")["follow_up"] is True
    assert DEFAULT["filming"] is False and adjust({}, "filming_on")["filming"] is True and adjust({"filming": True}, "filming_off")["filming"] is False
    assert DEFAULT["wake"] is True, "the open microphone waits for “okay Monday” unless the owner turns that off"
    assert WAKE_SURE_MIN <= DEFAULT["wake_sure"] <= WAKE_SURE_MAX and DEFAULT["wake_sure"] == 0.80, \
        "the bare name's sentence goes through when Jev is this sure it is said to Monday (jev.ADDRESSED_ROOM's 0.80 until 2026-10-27)"
    assert DEFAULT["interaction_mode"] == "pure_agent" and set(_MODE_OPS.values()) == set(MODES), "silent by default: the computer is the reply"
    assert adjust({}, "mode_debug")["interaction_mode"] == "debug" and adjust({"interaction_mode": "debug"}, "mode_pure")["interaction_mode"] == "pure_agent"
    assert adjust({}, "mode_assistant")["interaction_mode"] == "assistant" and adjust({}, "unmute")["interaction_mode"] == "assistant", "unmute = talk to me"
    assert adjust({"interaction_mode": "assistant"}, "mute")["interaction_mode"] == "assistant", "mute keeps the mode: replies stay on screen"
    s = selected({"tts_provider": "gemini"}, {"id": "Kore", "name": "Kore", "lang": "en-US", "provider": "gemini"})
    s = selected({**s, "tts_provider": "browser"}, {**cat[0], "vendor": "Microsoft"})
    assert s["choices"] == {"gemini": {"voice_id": "Kore", "voice_name": "Kore", "language": "en-US"},
                            "browser": {"voice_id": "aria", "voice_name": cat[0]["name"], "language": "en-US"}}, s["choices"]
    assert s["voice_id"] == "aria" and s["voice_provider"] == "Microsoft", "each provider keeps its own choice"
    assert gender({"name": "Mystery Voice"}) is None, "unknown gender is never guessed"
    print("windows voices:", windows_voices())
    print("voice self-check: OK")
