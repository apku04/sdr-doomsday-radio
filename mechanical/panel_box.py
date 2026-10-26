"""Printed front panel + box for the SDR emergency receiver (hand-wired panel).

Run:  "C:\\Program Files\\FreeCAD 1.0\\bin\\freecadcmd.exe" mechanical/panel_box.py
Outputs (mechanical/out/), all already in print orientation for a Bambu Lab P1S:
  panel.stl         front panel, printed FACE DOWN (smooth front from the plate), no supports
  panel_labels.stl  label inlays, same origin as panel.stl: in Bambu Studio add as a part of the
                    panel and give it a second filament (AMS); without AMS skip it = engraved text
  oled_clamp.stl    bar that clamps the OLED module into its pocket (2 x M2.5 x 8 screws)
  tray.stl          enclosure without its top, printed standing on its bottom, no supports
  lid.stl           lid (4 x M2.5 screws into the tray columns), printed upside down
  panel_holes.json  control centres actually cut (checked against layout.json by the tests)

Coordinates while modelling ("front view"): X right, Y up, Z out of the front face.
Panel front face Z = 0, panel back Z = -PANEL_T. Box front rim Z = 0, back wall at Z = -BOX_D.
"""
import json
import math
import os
from pathlib import Path

import FreeCAD as App
import MeshPart
import Part

ROOT = Path(os.environ.get("SDR_ROOT", Path(__file__).resolve().parent.parent)) if "__file__" in dir() else Path(r"C:\Users\apku0\sdr-hat")
LAYOUT = json.loads((ROOT / "hardware" / "panel" / "layout.json").read_text())
OUT = ROOT / "mechanical" / "out"
FONT = r"C:\Windows\Fonts\arialbd.ttf"

# ---- parameters: change to match your parts --------------------------------------------
PANEL_W, PANEL_H, PANEL_T = 160.0, 90.0, 3.0
ENCODER_HOLE = 7.5        # KY-040 / EC11 M7 bushing
BUTTON_HOLE = 7.2         # 7 mm panel-mount tactile pushbutton (owner)
LED_HOLE = 3.1            # 3 mm LED (owner) — use 5.1 for 5 mm LEDs
LED_COLLAR = (5.0, 4.0)   # back collar outer diameter, length (LEDs are 8 mm apart: keep < 8)
JACK_HOLE = 6.2           # 3.5 mm panel jack (PJ-392, M6)
OLED_WINDOW = (30.0, 30.0)
OLED_MODULE = (36.0, 36.0, 4.0)   # module board w, h and total thickness (board + glass) — MEASURE
OLED_HEADER_SIDE = "top"  # side of the module with the 4 pins (rim is left open there)
TEXT_DEPTH = 0.6
SCREW_HOLE = 2.9          # M2.5 clearance in the panel
SCREW_PILOT = 2.2         # M2.5 self-tapping pilot in the printed bosses
# panel-to-box screws (panel coordinates, y down), clear of all controls and labels
PANEL_SCREWS = [(40, 2.6), (120, 2.6), (30, 87.4), (130, 87.4), (2.6, 45), (157.4, 45)]

WALL = 3.0
FIT = 0.2                 # clearance around the panel in the box opening
BOX_W = PANEL_W + 2 * (WALL + FIT)
BOX_H = PANEL_H + 2 * (WALL + FIT)
BOX_D = 130.0
PI_STANDOFF = 5.0
# -----------------------------------------------------------------------------------------


def V(x, y, z):
    return App.Vector(x, y, z)


def box(w, h, d, x, y, z):
    return Part.makeBox(w, h, d, V(x, y, z))


def cyl(d, length, x, y, z, direction=(0, 0, -1)):
    return Part.makeCylinder(d / 2, length, V(x, y, z), V(*direction))


def py(y):
    """panel y (down from top) -> model Y (up)."""
    return PANEL_H - y


def text_solid(s, size, cx, cy, depth, z_top=0.0, mirror=False):
    """Text centred on (cx, cy) in model XY, occupying Z [z_top - depth, z_top]."""
    chars = Part.makeWireString(s, FONT, size, 0)
    faces = []
    for wires in chars:
        if wires:
            faces.append(Part.makeFace(wires, "Part::FaceMakerBullseye"))
    shape = Part.makeCompound(faces)
    bb = shape.BoundBox
    shape.translate(V(-(bb.XMin + bb.XMax) / 2, -(bb.YMin + bb.YMax) / 2, 0))
    if mirror:
        shape = shape.mirror(V(0, 0, 0), V(1, 0, 0))
    shape.translate(V(cx, cy, z_top - depth))
    solids = [f.extrude(V(0, 0, depth)) for f in shape.Faces]
    return solids[0].multiFuse(solids[1:]) if len(solids) > 1 else solids[0]


def rounded_plate(w, h, t, r, x=0.0, y=0.0, z=0.0):
    p = box(w, h, t, x, y, z)
    return p.makeFillet(r, [e for e in p.Edges if abs(e.tangentAt(e.FirstParameter).z) > 0.99])


def frustum_rect(w0, h0, w1, h1, cx, cy, z0, z1):
    def rect(w, h, z):
        return Part.makePolygon([V(cx - w / 2, cy - h / 2, z), V(cx + w / 2, cy - h / 2, z),
                                 V(cx + w / 2, cy + h / 2, z), V(cx - w / 2, cy + h / 2, z),
                                 V(cx - w / 2, cy - h / 2, z)])
    return Part.makeLoft([rect(w0, h0, z0), rect(w1, h1, z1)], True)


# ---------------------------------------------------------------- panel
def build_panel():
    ctl = LAYOUT["controls"]
    panel = rounded_plate(PANEL_W, PANEL_H, PANEL_T, 3.0, z=-PANEL_T)
    cuts, adds, labels, label_tools = [], [], [], []
    holes = {}

    def label(s, size, x, y):
        labels.append(text_solid(s, size, x, py(y), TEXT_DEPTH))
        # cutting tool pokes 0.5 mm out of the face: a flush (coplanar) cut leaves sliver edges
        label_tools.append(text_solid(s, size, x, py(y), TEXT_DEPTH + 0.5, z_top=0.5))

    for name, c in ctl.items():
        x, y, kind = c["x"], c["y"], c["kind"]
        X, Y = x, py(y)
        holes[name] = {"x": x, "y": y}
        if kind == "encoder":
            cuts.append(cyl(ENCODER_HOLE, 10, X, Y, 1))
            label(c["label"], 3.2, x, y + c["cap"] / 2 + 4.5)
            if c.get("sub"):
                label(c["sub"], 2.2, x, y + c["cap"] / 2 + 8.2)
        elif kind == "button":
            cuts.append(cyl(BUTTON_HOLE, 10, X, Y, 1))
            ly = y + BUTTON_HOLE / 2 + 4.0
            label(c["label"], 2.6 if ly > 85 else 3.0, x, min(ly, 86.8))
        elif kind == "led":
            cuts.append(cyl(LED_HOLE, 20, X, Y, 1))
            od, ln = LED_COLLAR
            adds.append(cyl(od, ln, X, Y, -PANEL_T).cut(cyl(LED_HOLE, ln + 1, X, Y, -PANEL_T + 0.5)))
            label(c["label"], 2.2, x, y + LED_HOLE / 2 + 2.3)
        elif kind == "jack":
            cuts.append(cyl(JACK_HOLE, 10, X, Y, 1))
            label(c["label"], 2.4, x + 12.5, y)
        elif kind == "oled":
            ww, wh = c.get("window", OLED_WINDOW)
            cuts.append(box(ww, wh, 10, X - ww / 2, Y - wh / 2, -8))
            cuts.append(frustum_rect(ww + 3, wh + 3, ww, wh, X, Y, 0.01, -1.5))   # chamfered window
            mw, mh, mt = OLED_MODULE
            iw, ih = mw + 0.5, mh + 0.5
            rim = box(iw + 3.2, ih + 3.2, mt, X - iw / 2 - 1.6, Y - ih / 2 - 1.6, -PANEL_T - mt)
            rim = rim.cut(box(iw, ih, mt + 1, X - iw / 2, Y - ih / 2, -PANEL_T - mt - 0.5))
            gap = 14.0  # leave the header side open for the wires
            if OLED_HEADER_SIDE == "top":
                rim = rim.cut(box(gap, 3, mt + 1, X - gap / 2, Y + ih / 2 - 0.5, -PANEL_T - mt - 0.5))
            adds.append(rim)
            for sx in (-1, 1):   # clamp-bar bosses
                bx = X + sx * (iw / 2 + 5.0)
                adds.append(cyl(7.0, mt, bx, Y, -PANEL_T))
                cuts.append(cyl(SCREW_PILOT, mt + 0.1, bx, Y, -PANEL_T - 0.01))
    for x, y in PANEL_SCREWS:
        X, Y = x, py(y)
        cuts.append(cyl(SCREW_HOLE, 10, X, Y, 1))
        cuts.append(Part.makeCone(SCREW_HOLE / 2 + 1.6, SCREW_HOLE / 2, 1.6, V(X, Y, 0.01), V(0, 0, -1)))
    label("SDR RX", 4.0, 141, 80)

    for a in adds:
        panel = panel.fuse(a)
    panel = panel.cut(Part.makeCompound(cuts))
    text = labels[0].multiFuse(labels[1:])
    panel = panel.cut(label_tools[0].multiFuse(label_tools[1:])).removeSplitter()
    return panel, text, holes


def oled_clamp():
    mw, mh, mt = OLED_MODULE
    length = mw + 0.5 + 2 * 5.0 + 7.0
    bar = rounded_plate(length, 7.0, 2.5, 1.5, -length / 2, -3.5, 0)
    for sx in (-1, 1):
        bar = bar.cut(cyl(SCREW_HOLE, 5, sx * ((mw + 0.5) / 2 + 5.0), 0, 3))
    return bar


# ---------------------------------------------------------------- box = tray + lid
LID_T = 3.0
TRAY_H = BOX_H - LID_T            # tray walls stop under the lid plate
LIP = (1.6, 4.0)                  # lid locating lip: thickness, depth
LID_COLUMNS = [(6.5, -(BOX_D - 6.5)), (BOX_W - 6.5, -(BOX_D - 6.5)), (6.5, -65.0), (BOX_W - 6.5, -65.0)]  # (X, Z)
PI_BACK = -(BOX_D - WALL) + 2.5   # Pi board edge; ports overhang ~2 mm into the back window
PI_FRONT = PI_BACK + 85.0
PI_X0 = BOX_W / 2 - 28.0          # board spans X0 .. X0 + 56; micro-USB edge at X0 + 56


def _prism(points_xy, z0, z1):
    """Extrude a closed XY polygon from z0 to z1."""
    w = Part.makePolygon([V(x, y, z0) for x, y in points_xy] + [V(points_xy[0][0], points_xy[0][1], z0)])
    return Part.Face(w).extrude(V(0, 0, z1 - z0))


def _panel_screw_xy(x, y):
    return WALL + FIT + x, WALL + FIT + py(y)


def build_tray():
    outer = box(BOX_W, TRAY_H, BOX_D, 0, 0, -BOX_D)
    outer = outer.makeFillet(3.0, [e for e in outer.Edges if abs(e.tangentAt(e.FirstParameter).y) > 0.99])
    tray = outer.cut(box(BOX_W - 2 * WALL, BOX_H, BOX_D, WALL, WALL, -(BOX_D - WALL)))   # open top + front
    adds, cuts = [], []
    zr0, zr1 = -(BOX_D - WALL), -PANEL_T   # ribs run from the back wall to just behind the panel

    for x, y in PANEL_SCREWS:
        X, Y = _panel_screw_xy(x, y)
        if y > PANEL_H - 10:                   # bottom edge: rib standing on the floor
            adds.append(box(6.0, Y + 3.0 - WALL, zr1 - zr0, X - 3.0, WALL, zr0))
        elif y > 10:                           # side edges: rib on the wall, 45 deg underside (no supports)
            wx = WALL if x < 10 else BOX_W - WALL
            s = 1 if x < 10 else -1
            xe = X + s * 3.0
            pts = [(wx, Y + 3.0), (xe, Y + 3.0), (xe, Y - 3.0), (wx, Y - 3.0 - abs(xe - wx))]
            adds.append(_prism(pts, zr0, zr1))
        else:
            continue                           # top edge: these screws go into the lid
        cuts.append(cyl(SCREW_PILOT, 12, X, Y, -PANEL_T + 0.01))

    for cx, cz in LID_COLUMNS:                 # lid screw columns, floor to lid
        adds.append(box(7.0, TRAY_H - WALL, 7.0, cx - 3.5, WALL, cz - 3.5))
        cuts.append(cyl(SCREW_PILOT, 10, cx, TRAY_H + 0.01, cz, (0, -1, 0)))

    for hz in (PI_FRONT - 3.5, PI_FRONT - 61.5):          # Pi 3B standoffs (M2.5 holes 58 x 49 mm)
        for hx in (PI_X0 + 56 - 3.5, PI_X0 + 56 - 52.5):
            adds.append(cyl(6.0, PI_STANDOFF, hx, WALL, hz, (0, 1, 0)))
            cuts.append(cyl(SCREW_PILOT, PI_STANDOFF + 0.5, hx, WALL + PI_STANDOFF + 0.01, hz, (0, -1, 0)))

    board_top = WALL + PI_STANDOFF + 1.4
    back_z = -BOX_D - 1
    cuts.append(box(56.0, 18.6, WALL + 2, PI_X0, board_top - 1.0, back_z))               # Pi USB + LAN window
    ux, uy = BOX_W - 26.0, 22.0                                                          # USB-C power in
    cuts.append(box(12.6, 6.6, WALL + 2, ux - 6.3, uy - 3.3, back_z))
    for sx in (-1, 1):
        cuts.append(cyl(3.2, WALL + 2, ux + sx * 11.0, uy, -BOX_D + WALL + 0.5))
    lx, ly = BOX_W - 26.0, 52.0                                                          # LNA 5 V DC jack
    cuts.append(cyl(8.1, WALL + 2, lx, ly, -BOX_D + WALL + 0.5))
    zt = -BOX_D + TEXT_DEPTH
    back_txt = [text_solid("5V IN", 3.5, ux, uy - 9.0, TEXT_DEPTH + 0.01, z_top=zt, mirror=True),
                text_solid("LNA 5V", 3.5, lx, ly - 9.0, TEXT_DEPTH + 0.01, z_top=zt, mirror=True),
                text_solid("SDR  USB  LAN", 3.5, PI_X0 + 28, board_top + 22.0, TEXT_DEPTH + 0.01, z_top=zt,
                           mirror=True)]
    for i in range(4):                                                                   # side vents by the Pi
        cuts.append(box(WALL + 2, 3.0, 50.0, BOX_W - WALL - 1, 12.0 + i * 6.5, PI_FRONT - 75.0))

    for a in adds:
        tray = tray.fuse(a)
    return tray.cut(Part.makeCompound(cuts + back_txt)).removeSplitter()


def build_lid():
    lid = box(BOX_W, LID_T, BOX_D, 0, TRAY_H, -BOX_D)
    lid = lid.makeFillet(3.0, [e for e in lid.Edges if abs(e.tangentAt(e.FirstParameter).y) > 0.99])
    t, d = LIP
    c = 0.2
    y0 = TRAY_H - d
    front = -PANEL_T - 12.0                    # keep the lip clear of the panel and its top screws
    lips = [box(t, d, front + BOX_D - WALL - c, WALL + c, y0, -(BOX_D - WALL - c)),
            box(t, d, front + BOX_D - WALL - c, BOX_W - WALL - c - t, y0, -(BOX_D - WALL - c)),
            box(BOX_W - 2 * (WALL + c), d, t, WALL + c, y0, -(BOX_D - WALL - c))]
    adds, cuts = lips[:], []
    for cx, cz in LID_COLUMNS:                 # notch the lip around the columns, screw holes through
        cuts.append(box(8.6, d + 0.1, 8.6, cx - 4.3, y0 - 0.05, cz - 4.3))
        cuts.append(cyl(SCREW_HOLE, LID_T + 1, cx, BOX_H + 0.5, cz, (0, -1, 0)))
        cuts.append(Part.makeCone(SCREW_HOLE / 2 + 1.6, SCREW_HOLE / 2, 1.6, V(cx, BOX_H + 0.01, cz), V(0, -1, 0)))
    for x, y in PANEL_SCREWS:                  # bosses for the panel's top screws
        if y >= 10:
            continue
        X, Y = _panel_screw_xy(x, y)
        adds.append(box(6.0, TRAY_H - (Y - 3.0), 9.0, X - 3.0, Y - 3.0, -PANEL_T - 9.0))
        cuts.append(cyl(SCREW_PILOT, 10, X, Y, -PANEL_T + 0.01))
    for i in range(8):                         # vents above the Pi
        cuts.append(box(3.0, LID_T + 2, 50.0, PI_X0 + 4 + i * 6.5, TRAY_H - 1, PI_FRONT - 75.0))
    for a in adds:
        lid = lid.fuse(a)
    return lid.cut(Part.makeCompound(cuts)).removeSplitter()


def _upright(shape, flip=False):
    """Model Y (up) -> print Z. flip=True turns the part upside down (lid: top face on the plate)."""
    s = shape.copy()
    s.rotate(V(0, 0, 0), V(1, 0, 0), -90 if flip else 90)
    s.translate(V(-s.BoundBox.XMin, -s.BoundBox.YMin, -s.BoundBox.ZMin))
    return s


# ---------------------------------------------------------------- export
def to_print_panel(shape):
    s = shape.copy()
    s.rotate(V(0, PANEL_H / 2, 0), V(1, 0, 0), 180)   # front face down on the plate
    s.translate(V(0, 0, -s.BoundBox.ZMin))
    return s


def write_stl(shape, name):
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.05, AngularDeflection=0.25, Relative=False)
    mesh.write(str(OUT / name))
    return mesh


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panel, text, holes = build_panel()
    assert panel.isValid(), "panel solid invalid"
    lift = to_print_panel(panel)
    write_stl(lift, "panel.stl")
    t = text.copy()
    t.rotate(V(0, PANEL_H / 2, 0), V(1, 0, 0), 180)
    t.translate(V(0, 0, -panel.copy().BoundBox.ZMin * 0 - to_print_panel(panel).BoundBox.ZMin))
    # same transform as the panel: rotate, then the panel's lift
    p2 = panel.copy(); p2.rotate(V(0, PANEL_H / 2, 0), V(1, 0, 0), 180)
    t = text.copy(); t.rotate(V(0, PANEL_H / 2, 0), V(1, 0, 0), 180); t.translate(V(0, 0, -p2.BoundBox.ZMin))
    write_stl(t, "panel_labels.stl")
    write_stl(oled_clamp(), "oled_clamp.stl")
    tray, lid = build_tray(), build_lid()
    assert tray.isValid() and lid.isValid(), "tray/lid solid invalid"
    tray_p, lid_p = _upright(tray), _upright(lid, flip=True)
    write_stl(tray_p, "tray.stl")
    write_stl(lid_p, "lid.stl")
    (OUT / "panel_holes.json").write_text(json.dumps(holes, indent=1))
    lift.exportStep(str(OUT / "panel.step"))
    tray_p.exportStep(str(OUT / "tray.step"))
    lid_p.exportStep(str(OUT / "lid.step"))
    for old_file in ("box.stl", "box.step"):
        (OUT / old_file).unlink(missing_ok=True)
    print("panel", [round(v, 1) for v in (lift.BoundBox.XLength, lift.BoundBox.YLength, lift.BoundBox.ZLength)])
    for n, s in (("tray", tray_p), ("lid", lid_p)):
        print(n, [round(v, 1) for v in (s.BoundBox.XLength, s.BoundBox.YLength, s.BoundBox.ZLength)])


main()
