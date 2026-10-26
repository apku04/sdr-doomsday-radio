"""Run with plain Python after building: python mechanical/tests/test_mechanical.py"""
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mechanical" / "out"
POS = ROOT / "hardware" / "panel" / "layout.json"   # hand-wired panel: layout is the source
P1S = 256.0


def stl_bbox_and_edges(path):
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 80)[0]
    assert len(data) == 84 + n * 50, f"{path.name}: not a binary STL"
    lo = [1e9] * 3; hi = [-1e9] * 3
    edges = {}
    for i in range(n):
        v = struct.unpack_from("<12f", data, 84 + i * 50)[3:]
        pts = [tuple(round(c, 4) for c in v[k:k + 3]) for k in (0, 3, 6)]
        for p in pts:
            for a in range(3):
                lo[a] = min(lo[a], p[a]); hi[a] = max(hi[a], p[a])
        for a, b in ((0, 1), (1, 2), (2, 0)):
            e = tuple(sorted((pts[a], pts[b])))
            edges[e] = edges.get(e, 0) + 1
    open_edges = sum(1 for c in edges.values() if c != 2)
    return [h - l for l, h in zip(lo, hi)], open_edges


def test_holes_match_positions():
    pos = json.loads(POS.read_text())
    holes = json.loads((OUT / "panel_holes.json").read_text())
    for name, ctl in pos["controls"].items():
        if ctl["kind"] in ("encoder", "button", "led", "jack", "oled"):
            h = holes[name]
            assert abs(h["x"] - ctl["x"]) < 0.05 and abs(h["y"] - ctl["y"]) < 0.05, name


def test_parts_fit_p1s_and_are_closed():
    stls = sorted(OUT.glob("*.stl"))
    assert {"panel.stl", "panel_labels.stl", "tray.stl", "lid.stl", "oled_clamp.stl"} <= {p.name for p in stls}
    assert "box.stl" not in {p.name for p in stls}
    for p in stls:
        size, open_edges = stl_bbox_and_edges(p)
        assert max(size) <= P1S - 6, (p.name, size)
        assert open_edges == 0, (p.name, open_edges)


def test_panel_and_box_sizes():
    size, _ = stl_bbox_and_edges(OUT / "panel.stl")
    assert sorted(round(s, 1) for s in size[:2]) == [90.0, 160.0]
    tray, _ = stl_bbox_and_edges(OUT / "tray.stl")      # printed on its bottom: X width, Y depth, Z height
    lid, _ = stl_bbox_and_edges(OUT / "lid.stl")        # printed upside down
    assert round(tray[0], 1) == 166.4 and round(tray[1], 1) == 130.0
    assert round(lid[0], 1) == 166.4 and round(lid[1], 1) == 130.0
    assert round(tray[2] + 3.0, 1) == 96.4               # tray + 3 mm lid plate = box height


if __name__ == "__main__":
    import sys, traceback
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, "-", e)
    sys.exit(1 if fails else 0)
