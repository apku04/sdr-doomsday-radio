"""Copper pours and JLCPCB fabrication outputs."""
import csv
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path

import pcbnew

from .kicad_pcb import mm
from .kicad_sch import _cli


def add_gnd_pours(pcb_path, net="GND", clearance=0.3, min_width=0.25, pitch_mm=4.0, solid_pads=()):
    """GND zone over the whole board on both layers, filled, thermal reliefs on pads.
    solid_pads: [(ref, pad)] connected solid (pads a thermal relief cannot reach well)."""
    board = pcbnew.LoadBoard(str(pcb_path))
    for ref, num in solid_pads:
        for pad in board.FindFootprintByReference(ref).Pads():
            if pad.GetNumber() == num:
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    for fp in board.GetFootprints():
        if fp.GetReference().startswith("H") and fp.GetFPID().GetLibNickname() == "MountingHole":
            fp.Reference().SetVisible(False)
    netinfo = board.FindNet(net)
    bb = board.GetBoardEdgesBoundingBox()
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(netinfo)
        z.SetLocalClearance(mm(clearance))
        z.SetMinThickness(mm(min_width))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetThermalReliefGap(mm(0.3))
        z.SetThermalReliefSpokeWidth(mm(0.4))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((bb.GetLeft(), bb.GetTop()), (bb.GetRight(), bb.GetTop()),
                     (bb.GetRight(), bb.GetBottom()), (bb.GetLeft(), bb.GetBottom())):
            ol.Append(x, y)
        board.Add(z)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    _stitch(board, netinfo, [z for z in board.Zones() if z.GetNetname() == net], pitch_mm)
    filler.Fill(board.Zones())
    pcbnew.SaveBoard(str(pcb_path), board)


def _stitch(board, netinfo, zones, pitch_mm, via_d=0.6, drill=0.3):
    """GND vias on a grid wherever both layers' fill fully surrounds the via (ties islands)."""
    import math
    zf = next(z for z in zones if z.GetLayer() == pcbnew.F_Cu)
    zb = next(z for z in zones if z.GetLayer() == pcbnew.B_Cu)
    bb = board.GetBoardEdgesBoundingBox()
    x = pcbnew.ToMM(bb.GetLeft()) + pitch_mm / 2
    ring = [(math.cos(a) * (via_d / 2 + 0.25), math.sin(a) * (via_d / 2 + 0.25))
            for a in [k * math.pi / 4 for k in range(8)]] + [(0, 0)]
    while x < pcbnew.ToMM(bb.GetRight()):
        y = pcbnew.ToMM(bb.GetTop()) + pitch_mm / 2
        while y < pcbnew.ToMM(bb.GetBottom()):
            ok = all(zf.HitTestFilledArea(pcbnew.F_Cu, pcbnew.VECTOR2I(mm(x + dx), mm(y + dy))) and
                     zb.HitTestFilledArea(pcbnew.B_Cu, pcbnew.VECTOR2I(mm(x + dx), mm(y + dy)))
                     for dx, dy in ring)
            if ok:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
                v.SetWidth(mm(via_d)); v.SetDrill(mm(drill)); v.SetNet(netinfo)
                board.Add(v)
            y += pitch_mm
        x += pitch_mm


GERBER_LAYERS = "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"


def export_fab(circuit, pcb_path, sch_path, outdir):
    outdir = Path(outdir)
    gdir = outdir / "gerbers"
    if gdir.exists():
        shutil.rmtree(gdir)
    gdir.mkdir(parents=True)
    _cli("pcb", "export", "gerbers", "--layers", GERBER_LAYERS, "--subtract-soldermask",
         "--use-drill-file-origin", "-o", str(gdir) + "/", pcb_path)
    _cli("pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th",
         "--generate-map", "--map-format", "pdf", "-o", str(gdir) + "/", pcb_path)
    zpath = outdir / f"{circuit.name}-gerbers-jlcpcb.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gdir.iterdir()):
            if not f.name.endswith(".pdf"):
                z.write(f, f.name)

    groups = defaultdict(list)
    for p in circuit.parts:
        groups[(p.value, p.footprint, p.lcsc, p.note)].append(p.ref)
    bom = outdir / f"{circuit.name}-bom.csv"
    with open(bom, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Qty", "References", "Value", "Footprint", "LCSC", "Note"])
        for (value, fp, lcsc, note), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
            w.writerow([len(refs), " ".join(sorted(refs)), value, fp.split(":", 1)[1], lcsc, note])

    pdf = outdir / f"{circuit.name}-schematic.pdf"
    _cli("sch", "export", "pdf", "-o", pdf, sch_path)
    pos = outdir / f"{circuit.name}-positions.csv"
    _cli("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "-o", pos, pcb_path)
    renders = []
    for side in ("top", "bottom"):
        png = outdir / f"{circuit.name}-{side}.png"
        r = _cli("pcb", "render", "--side", side, "--width", "1600", "--height", "1000", "--quality", "basic",
                 "-o", png, pcb_path)
        if png.exists():
            renders.append(png)
    return {"gerbers_zip": zpath, "bom_csv": bom, "schematic_pdf": pdf, "positions_csv": pos, "renders": renders}
