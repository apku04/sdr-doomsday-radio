"""DRC through kicad-cli: returns a list of human-readable error strings (empty = clean)."""
import json
import tempfile
from pathlib import Path

from .kicad_sch import _cli


def run_drc(pcb_path, include_warnings=False):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "drc.json"
        args = ["pcb", "drc", "--format", "json", "--units", "mm", "--severity-all", "-o", out]
        _cli(*args, pcb_path)
        rep = json.loads(out.read_text(encoding="utf-8"))
    probs = []
    for key in ("violations", "unconnected_items", "schematic_parity"):
        for v in rep.get(key, []):
            if v["severity"] == "error" or include_warnings:
                where = "; ".join(i.get("description", "") for i in v.get("items", []))
                probs.append(f"{v['severity']}: {v['type']}: {v['description']} [{where}]")
    return probs
