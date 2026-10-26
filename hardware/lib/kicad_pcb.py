"""Build a KiCad PCB from a Circuit with pcbnew: outline, footprints at given placements, nets."""
from pathlib import Path

import pcbnew

from .circuit import NC

FOOTPRINT_DIR = Path(r"C:\Program Files\KiCad\9.0\share\kicad\footprints")

# JLCPCB 2-layer standard capability, with margin
RULES = dict(clearance=0.2, track=0.25, via_d=0.6, via_drill=0.3, hole_clear=0.25, edge_clear=0.4)


def mm(v):
    return pcbnew.FromMM(float(v))


def load_footprint(fp_id):
    lib, name = fp_id.split(":", 1)
    fp = pcbnew.FootprintLoad(str(FOOTPRINT_DIR / f"{lib}.pretty"), name)
    if fp is None:
        raise ValueError(f"footprint not found: {fp_id}")
    return fp


def _outline(board, w, h, r=2.0):
    def seg(x0, y0, x1, y1):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0))); s.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); board.Add(s)

    def arc(cx, cy, sx, sy, ex, ey):
        a = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        a.SetCenter(pcbnew.VECTOR2I(mm(cx), mm(cy)))
        a.SetStart(pcbnew.VECTOR2I(mm(sx), mm(sy)))
        a.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T), True)
        a.SetLayer(pcbnew.Edge_Cuts); a.SetWidth(mm(0.1)); board.Add(a)

    seg(r, 0, w - r, 0); seg(w, r, w, h - r); seg(w - r, h, r, h); seg(0, h - r, 0, r)
    arc(w - r, r, w - r, 0, w, r)
    arc(w - r, h - r, w, h - r, w - r, h)
    arc(r, h - r, r, h, 0, h - r)
    arc(r, r, 0, r, r, 0)


def build_pcb(circuit, size, placements, holes, path, rules=RULES, net_widths=None, title=""):
    """placements: ref -> (x_mm, y_mm, rot_deg, side 'F'|'B'); holes: [(x, y, footprint_id)]."""
    board = pcbnew.BOARD()
    ds = board.GetDesignSettings()
    ds.m_MinClearance = mm(rules["clearance"])
    ds.m_TrackMinWidth = mm(0.15)
    ds.m_ViasMinSize = mm(rules["via_d"])
    ds.m_MinThroughDrill = mm(rules["via_drill"])
    ds.m_HoleClearance = mm(rules["hole_clear"])
    ds.m_CopperEdgeClearance = mm(rules["edge_clear"])
    nc = ds.m_NetSettings.GetDefaultNetclass()
    nc.SetClearance(mm(rules["clearance"])); nc.SetTrackWidth(mm(rules["track"]))
    nc.SetViaDiameter(mm(rules["via_d"])); nc.SetViaDrill(mm(rules["via_drill"]))
    tb = board.GetTitleBlock(); tb.SetTitle(title or circuit.name)

    w, h = size
    _outline(board, w, h)
    nets = {}
    for name in circuit.nets():
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        nets[name] = ni

    for part in circuit.parts:
        if part.ref not in placements:
            raise ValueError(f"no placement for {part.ref}")
        x, y, rot, side = placements[part.ref]
        fp = load_footprint(part.footprint)
        fp.SetReference(part.ref)
        fp.SetValue(part.value)
        fp.SetPath(pcbnew.KIID_PATH(f"/{part.uuid}"))
        board.Add(fp)
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        fp.SetOrientationDegrees(rot)
        if side == "B":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
        for pad in fp.Pads():
            net = part.pins.get(pad.GetNumber(), NC)
            if net is not NC:
                pad.SetNet(nets[net])

    for i, (x, y, fp_id) in enumerate(holes):
        fp = load_footprint(fp_id)
        fp.SetReference(f"H{i + 1}")
        board.Add(fp)
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(path), board)
    return path


def pcb_pad_nets(path):
    board = pcbnew.LoadBoard(str(path))
    return {(fp.GetReference(), pad.GetNumber()): pad.GetNetname()
            for fp in board.GetFootprints() for pad in fp.Pads()}
