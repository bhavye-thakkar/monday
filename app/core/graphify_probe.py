"""Runs under graphify's own interpreter (it lives in its uv tool venv, not Monday's Python).

usage: python graphify_probe.py <project_root>
Prints JSON: which files changed since the last Graphify analysis, using graphify's own manifest.
"""
import json
import sys
from pathlib import Path

from graphify.detect import detect_incremental

root = Path(sys.argv[1]).resolve()
manifest = root / "graphify-out" / "manifest.json"
out = {"code_changed": [], "docs_changed": [], "deleted": []}
if manifest.exists():
    # graphify stamps code with an AST hash (`graphify update`) and docs with a semantic hash.
    ast = detect_incremental(root, str(manifest), kind="ast")
    sem = detect_incremental(root, str(manifest), kind="semantic")
    out["code_changed"] = ast["new_files"].get("code", [])
    out["docs_changed"] = [f for t in ("document", "paper", "image") for f in sem["new_files"].get(t, [])]
    out["deleted"] = ast["deleted_files"]
    out["total_files"] = ast.get("total_files")
print(json.dumps(out))
