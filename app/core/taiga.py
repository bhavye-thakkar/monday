"""Taiga-S1 (github.com/shhivv/biome-s1, huggingface.co/shhivv/taiga-s1; MIT): a 1.2M-parameter "System 1" next-command model for FreeCAD's
PartDesign workbench. Given FreeCAD's live state, a goal (an ordered list of features) and the commands FreeCAD offers right now, it scores
each command. No LLM, no vision model, no screenshots; and it drives FreeCAD only, over a local socket (freecad_s1.runtime). FreeCAD isn't
installed on this PC, so Monday lists it as detected, not supported, with that reason, and never offers it for anything else.

Measured here 2026-10-13 (`python -m core.taiga --infer`, the author's own code in E:\\monday\\taiga\\venv, CPU): weights 4,924,516 bytes,
1,228,163 F32 parameters, 0.21 s to load, one decision 17 ms p50 / 26 ms p95 (featurization included; the author quotes ~1 ms for the
forward pass on their CPU), 267 MB of process memory with torch. The owner's "5 MB" is the weights file, and it is within it; the runtime
that runs it (torch, numpy, safetensors) is hundreds of MB on disk and in memory, and is said so.
Source and weights: E:\\monday\\taiga\\biome-s1 (a git clone; release/hf holds the Hugging Face repo's files). MONDAY_TAIGA_DIR overrides.
Self-check: python -m core.taiga [--infer]
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DIR = Path(os.environ.get("MONDAY_TAIGA_DIR") or r"E:\monday\taiga")
WEIGHTS = DIR / "biome-s1" / "release" / "hf" / "model.safetensors"
PYTHON = DIR / "venv" / "Scripts" / "python.exe"
SIZE_LIMIT = 5_000_000  # the owner's "5 MB" (decimal bytes): the weights file
PARAMS = 1_228_163      # parameter_count(S1Model) on the released config (checked 2026-10-13)
PROBE_S = 180

# One real decision on the author's own test fixture (tests/test_model.py), timed; prints one JSON line.
_PROBE = r"""
import json, os, random, time
import torch, psutil
from freecad_s1.model.net import from_pretrained, parameter_count
from freecad_s1.rollout import Policy
from freecad_s1.goals import sample_goal
from freecad_s1.schema import Node, SelItem, ShapeInfo, State
from freecad_s1.actions import CATALOGUE
t0 = time.perf_counter()
model = from_pretrained(os.environ["TAIGA_WEIGHTS_DIR"], device="cpu")
load = time.perf_counter() - t0
tree = [Node("Body", "PartDesign::Body", num={"active_body": 1.0}),
        Node("Sketch1", "Sketcher::SketchObject", parent=0, depth=1, num={"n_geo": 0.4, "closed": 1.0, "sk_w": 20.0}, geo={"line": 4}, cons={"Horizontal": 2})]
state = State(doc_open=True, workbench="PartDesignWorkbench", has_body=True, tree=tree,
              selection=[SelItem("face", "Pad", "PartDesign::Pad", ["Face6"], (0, 0, 1), 10.0, 1)],
              recent=["PartDesign_Body", "Select:Plane:XY"], shape=ShapeInfo(True, 1000.0, 600.0, (10, 10, 10), 6, 12, 1, ["+Z"]))
goal, acts = sample_goal(3, random.Random(0)), list(CATALOGUE)[:30]
pol = Policy(model, torch.device("cpu"))
probs = pol.score(state, goal, acts)
times = []
for _ in range(50):
    t = time.perf_counter(); pol.score(state, goal, acts); times.append(time.perf_counter() - t)
times.sort()
print(json.dumps({"params": parameter_count(model), "load_s": round(load, 3), "p50_ms": round(times[25] * 1000, 2), "p95_ms": round(times[47] * 1000, 2),
                  "rss_mb": round(psutil.Process().memory_info().rss / 1e6), "top": list(probs.items())[:3], "actions": len(acts)}))
"""


def freecad():
    """FreeCAD on this PC (the only thing Taiga-S1 operates), or None."""
    for name in ("FreeCAD", "FreeCADCmd", "freecad"):
        p = shutil.which(name)
        if p:
            return p
    for base in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")), Path(r"E:\Program Files")):
        for p in base.glob("FreeCAD*/bin/FreeCAD.exe"):
            return str(p)
    return None


def status():
    """What is really here, without running anything: the weights (bytes, within the 5 MB limit?), the runtime, FreeCAD."""
    size = WEIGHTS.stat().st_size if WEIGHTS.exists() else None
    fc, py = freecad(), (str(PYTHON) if PYTHON.exists() else None)
    info = {"weights": str(WEIGHTS), "weights_bytes": size, "params": PARAMS if size else None, "limit_bytes": SIZE_LIMIT,
            "within_limit": size is not None and size <= SIZE_LIMIT, "freecad": fc, "python": py}
    if size is None:
        return {"status": "unavailable", "detail": f"Taiga-S1's weights aren't here ({WEIGHTS}).", **info}
    if not py:
        return {"status": "unavailable", "detail": f"Taiga-S1's runtime isn't set up ({PYTHON}: a venv with torch, numpy, safetensors and the author's package).", **info}
    mb = f"{size / 1e6:.2f} MB"
    if not fc:
        return {"status": "detected_unsupported", "detail": f"Taiga-S1 is here ({mb}, {PARAMS:,} parameters; it loads and scores on this CPU) but it "
                                                           "drives FreeCAD only, and FreeCAD isn't installed on this PC: it can't operate anything here.", **info}
    return {"status": "available", "detail": f"Taiga-S1 ({mb}) and FreeCAD ({fc}) are here; Monday has no FreeCAD step yet (not built).", **info}


def probe():
    """A real inference in the runtime: load the weights, score the author's fixture, time 50 decisions. -> the numbers, or {"error"}."""
    if not PYTHON.exists() or not WEIGHTS.exists():
        return {"error": status()["detail"]}
    try:
        r = subprocess.run([str(PYTHON), "-"], input=_PROBE, capture_output=True, text=True, timeout=PROBE_S, cwd=str(DIR),
                           env={**os.environ, "TAIGA_WEIGHTS_DIR": str(WEIGHTS.parent)}, creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": f"the probe didn't run: {e}"}
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("{")), None)
    if r.returncode != 0 or not line:
        return {"error": (r.stderr.strip().splitlines() or ["no output"])[-1][:300]}
    return json.loads(line)


if __name__ == "__main__":  # python -m core.taiga [--infer]
    st = status()
    assert st["status"] in ("available", "unavailable", "detected_unsupported") and st["detail"], st
    assert st["weights_bytes"] is None or (st["within_limit"] == (st["weights_bytes"] <= SIZE_LIMIT)), "the 5 MB verdict is the file's size, nothing else"
    assert freecad() is not None or st["status"] != "available", "never available without FreeCAD"
    print("taiga:", st["status"], "·", st["detail"])
    if "--infer" in sys.argv:
        p = probe()
        assert "error" not in p, p
        assert p["params"] == PARAMS and p["p50_ms"] > 0 and p["top"] and p["top"][0][1] >= p["top"][-1][1], p
        print("inference:", json.dumps(p))
    print("taiga self-check: OK")
