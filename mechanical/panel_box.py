"""Printed front panel + box for the SDR emergency receiver (hand-wired panel).

Run:  "C:\\Program Files\\FreeCAD 1.0\\bin\\freecadcmd.exe" mechanical/panel_box.py
Outputs (mechanical/out/), all already in print orientation for a Bambu Lab P1S:
  panel.stl         front panel, printed FACE DOWN (smooth front from the plate), no supports
  panel_labels.stl  label inlays, same origin as panel.stl: in Bambu Studio add as a part of the
                    panel and give it a second filament (AMS); without AMS skip it = engraved text
  oled_clamp.stl    bar that clamps the OLED module into its pocket (2 x M2.5 x 8 screws)
  box.stl           enclosure, printed standing on its BACK wall, no supports
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


# ---------------------------------------------------------------- box
def build_box():
    outer = box(BOX_W, BOX_H, BOX_D, 0, 0, -BOX_D)
    outer = outer.makeFillet(3.0, [e for e in outer.Edges if abs(e.tangentAt(e.FirstParameter).z) > 0.99])
    shell = outer.cut(box(BOX_W - 2 * WALL, BOX_H - 2 * WALL, BOX_D, WALL, WALL, -(BOX_D - WALL)))
    adds, cuts = [], []
    ox, oy = WALL + FIT, WALL + FIT          # panel origin in box XY

    # panel screw ribs: full-length along the wall (prints without overhangs, stiffens the wall)
    for x, y in PANEL_SCREWS:
        X, Y = ox + x, oy + py(y)
        if y < 10 or y > PANEL_H - 10:       # top / bottom wall
            wy = BOX_H - WALL if y < 10 else WALL
            y0, y1 = sorted((wy, Y))
            adds.append(box(6.0, y1 - y0 + 3.0, BOX_D - PANEL_T - WALL, X - 3.0, y0 - 1.5, -(BOX_D - WALL)))
        else:                                # side walls
            wx = WALL if x < 10 else BOX_W - WALL
            x0, x1 = sorted((wx, X))
            adds.append(box(x1 - x0 + 3.0, 6.0, BOX_D - PANEL_T - WALL, x0 - 1.5, Y - 3.0, -(BOX_D - WALL)))
        cuts.append(cyl(SCREW_PILOT, 12, X, Y, -PANEL_T + 0.01))

    # Raspberry Pi 3B lying on the floor, USB/Ethernet end against the back wall
    pi_back = -(BOX_D - WALL) + 2.5          # board edge; ports overhang ~2 mm into the window
    pi_front = pi_back + 85.0
    pi_x0 = BOX_W / 2 - 28.0                 # board spans x0 .. x0 + 56; micro-USB edge at x0 + 56
    for hz in (pi_front - 3.5, pi_front - 61.5):
        for hx in (pi_x0 + 56 - 3.5, pi_x0 + 56 - 52.5):
            d = Part.makeBox(6.0, PI_STANDOFF, 6.0, V(-3.0, 0, -3.0))
            d.rotate(V(0, 0, 0), V(0, 1, 0), 45)      # diamond: self-supporting when printed upright
            d.translate(V(hx, WALL, hz))
            adds.append(d)
            cuts.append(cyl(SCREW_PILOT, PI_STANDOFF + 0.5, hx, WALL + PI_STANDOFF + 0.01, hz, (0, -1, 0)))
    board_top = WALL + PI_STANDOFF + 1.4
    back_z = -BOX_D - 1
    # back wall: Pi USB + Ethernet window
    cuts.append(box(56.0, 18.6, WALL + 2, pi_x0, board_top - 1.0, back_z))
    # back wall: USB-C power-in (panel-mount USB-C -> micro-USB cable) and LNA 5 V DC jack
    ux, uy = BOX_W - 26.0, 22.0
    cuts.append(box(12.6, 6.6, WALL + 2, ux - 6.3, uy - 3.3, back_z))
    for sx in (-1, 1):
        cuts.append(cyl(3.2, WALL + 2, ux + sx * 11.0, uy, -BOX_D + WALL + 0.5))
    lx, ly = BOX_W - 26.0, 52.0
    cuts.append(cyl(8.1, WALL + 2, lx, ly, -BOX_D + WALL + 0.5))
    # back labels (read from behind: mirrored)
    back_txt = [text_solid("5V IN", 3.5, ux, uy - 9.0, TEXT_DEPTH + 0.01, z_top=-BOX_D + TEXT_DEPTH, mirror=True),
                text_solid("LNA 5V", 3.5, lx, ly - 9.0, TEXT_DEPTH + 0.01, z_top=-BOX_D + TEXT_DEPTH, mirror=True),
                text_solid("SDR  USB  LAN", 3.5, pi_x0 + 28, board_top + 22.0, TEXT_DEPTH + 0.01,
                           z_top=-BOX_D + TEXT_DEPTH, mirror=True)]
    # vents: top wall above the Pi, and the side wall next to it
    for i in range(8):
        vx = pi_x0 + 4 + i * 6.5
        cuts.append(box(3.0, WALL + 2, 50.0, vx, BOX_H - WALL - 1, pi_front - 75.0))
    for i in range(4):
        cuts.append(box(WALL + 2, 3.0, 50.0, BOX_W - WALL - 1, 12.0 + i * 6.5, pi_front - 75.0))

    for a in adds:
        shell = shell.fuse(a)
    shell = shell.cut(Part.makeCompound(cuts + back_txt)).removeSplitter()
    return shell, {"pi_standoffs_z": [pi_front - 3.5, pi_front - 61.5]}


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
    shell, info = build_box()
    assert shell.isValid(), "box solid invalid"
    shell.translate(V(0, 0, BOX_D))   # back wall on the plate
    write_stl(shell, "box.stl")
    (OUT / "panel_holes.json").write_text(json.dumps(holes, indent=1))
    lift.exportStep(str(OUT / "panel.step"))
    shell.exportStep(str(OUT / "box.step"))
    print("panel", [round(v, 1) for v in (lift.BoundBox.XLength, lift.BoundBox.YLength, lift.BoundBox.ZLength)])
    print("box", [round(v, 1) for v in (shell.BoundBox.XLength, shell.BoundBox.YLength, shell.BoundBox.ZLength)])


main()
