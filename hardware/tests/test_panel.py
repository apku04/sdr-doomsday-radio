import json

import pcbnew
import pytest

from lib.contract import MCP_PINS, PANEL_RIBBON

MCP_PIN_NUMBERS = {"GPB0": "1", "GPB1": "2", "GPB2": "3", "GPB3": "4", "GPB4": "5", "GPB5": "6", "GPB6": "7",
                   "GPB7": "8", "GPA0": "21", "GPA1": "22", "GPA2": "23", "GPA3": "24", "GPA4": "25",
                   "GPA5": "26", "GPA6": "27", "GPA7": "28"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from panel import panel
    return panel.build(tmp_path_factory.mktemp("panel")), panel


def test_panel_contract():
    from panel import panel
    c = panel.circuit()
    assert {int(k): v for k, v in c.part("J1").pins.items()} == PANEL_RIBBON
    u = c.part("U1").pins
    for port, func in MCP_PINS.items():
        net = u[MCP_PIN_NUMBERS[port]]
        assert net == func, (port, net, func)


def test_parts_at_layout(built):
    res, panel = built
    board = pcbnew.LoadBoard(str(res["pcb"]))
    layout = json.loads(panel.LAYOUT.read_text())
    ox, oy = panel.BOARD_OFFSET
    for name, ctl in layout["controls"].items():
        if not ctl.get("ref"):
            continue
        fp = board.FindFootprintByReference(ctl["ref"])
        pads = list(fp.Pads())
        if ctl["kind"] == "encoder":
            mp = [p.GetPosition() for p in pads if p.GetNumber() == "MP"]
            cx = pcbnew.ToMM(sum(p.x for p in mp) / 2); cy = pcbnew.ToMM(sum(p.y for p in mp) / 2)
        elif ctl["kind"] == "button":
            cx = pcbnew.ToMM(sum(p.GetPosition().x for p in pads) / len(pads))
            cy = pcbnew.ToMM(sum(p.GetPosition().y for p in pads) / len(pads))
        else:
            cx, cy = pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)
        assert abs(cx + ox - ctl["x"]) < 0.05 and abs(cy + oy - ctl["y"]) < 0.05, (name, cx + ox, cy + oy)


def test_positions_json_written(built):
    res, _ = built
    pos = json.loads(res["positions"].read_text())
    assert {"Volume", "Squelch", "Tune", "Menu", "Scan", "Manual", "Q1", "Q2", "Back", "OLED"} <= set(pos["controls"])
    assert pos["panel"] == {"w": 160, "h": 90}


def test_panel_erc_drc_clean(built):
    res, _ = built
    assert res["erc"] == []
    assert res["drc"] == []


def _pads_off_board(pcb_path):
    board = pcbnew.LoadBoard(str(pcb_path))
    outline = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(outline)
    bad = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if not outline.Contains(pad.GetPosition()):
                bad.append(f"{fp.GetReference()}.{pad.GetNumber()}")
    return bad


def test_all_pads_on_board(built):
    res, _ = built
    assert _pads_off_board(res["pcb"]) == []


def test_unrouted_layout_pads_on_board(tmp_path):
    from panel import panel
    from lib.kicad_pcb import build_pcb
    pcb = tmp_path / "p.kicad_pcb"
    build_pcb(panel.circuit(), panel.OUTLINE, panel.placements(), panel.HOLES, pcb)
    assert _pads_off_board(pcb) == []
