"""Front-panel board: 4 encoders, 5 buttons, 7 LEDs, OLED socket, MCP23017, ribbon to the HAT.

Front side (F) faces the printed panel: encoders, buttons, LEDs, OLED socket only.
Back side (B) faces into the box: ribbon header, MCP23017 and all passives.
Board = panel minus 5 mm on every side; bottom-left notch leaves room for the panel-mounted
headphone jack.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.checks import run_drc
from lib.circuit import NC, Circuit, Part
from lib.contract import MCP_PINS, PANEL_RIBBON
from lib.fab import add_gnd_pours, export_fab, fix_starved_thermals
from lib.kicad_pcb import add_text, build_pcb, fit_footprint
from lib.kicad_sch import run_erc, write_schematic
from lib.route import autoroute

HERE = Path(__file__).parent
LAYOUT = HERE / "layout.json"
BOARD_OFFSET = (5.0, 5.0)              # board origin in panel coordinates
OUTLINE = [(0, 0), (150, 0), (150, 80), (14, 80), (14, 67), (0, 67)]
HOLES = [(4, 4, "MountingHole:MountingHole_3.2mm_M3"), (146, 4, "MountingHole:MountingHole_3.2mm_M3"),
         (146, 76, "MountingHole:MountingHole_3.2mm_M3"), (4, 63.5, "MountingHole:MountingHole_3.2mm_M3")]
ENCODER_FP = "Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm"
ENCODER_SHAFT = (7.5, 2.5)            # shaft centre relative to footprint origin
BUTTON_FP = "Button_Switch_THT:SW_PUSH-12mm"
BUTTON_CENTRE = (6.25, 2.5)
OLED_HEADER_DY = -18.5                # assumed: module header row 18.5 mm above window centre (verify!)

R0805 = "Resistor_SMD:R_0805_2012Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"

ENCODERS = [("ENC1", "Volume", "VOL"), ("ENC2", "Squelch", "SQL"), ("ENC3", "Tune", "TUNE"), ("ENC4", "Menu", "MENU")]
BUTTONS = [("SW1", "Scan", "BTN_SCAN"), ("SW2", "Manual", "BTN_MANUAL"), ("SW3", "Q1", "BTN_Q1"),
           ("SW4", "Q2", "BTN_Q2"), ("SW5", "Back", "BTN_BACK")]
# ref, layout name, net, colour, series resistor
LEDS = [("D1", "LED_PWR", "LED_PWR", "green", "1k"), ("D2", "LED_SCAN", "LED_SCAN", "amber", "1k"),
        ("D3", "LED_SIG", "LED_SIG", "green", "1k"), ("D4", "LED_MAN", "LED_MAN", "blue", "150R"),
        ("D5", "LED_2M", "LED_2M", "white", "150R"), ("D6", "LED_70CM", "LED_70CM", "white", "150R"),
        ("D7", "LED_FM", "LED_FM", "white", "150R")]
MCP_NUM = {"GPB0": "1", "GPB1": "2", "GPB2": "3", "GPB3": "4", "GPB4": "5", "GPB5": "6", "GPB6": "7",
           "GPB7": "8", "GPA0": "21", "GPA1": "22", "GPA2": "23", "GPA3": "24", "GPA4": "25", "GPA5": "26",
           "GPA6": "27", "GPA7": "28"}
NET_WIDTHS = {"3V3": 0.4, "GND": 0.4}


def layout():
    return json.loads(LAYOUT.read_text())


def circuit():
    c = Circuit("sdr-panel")
    c.add(Part("J1", "Connector_Generic:Conn_02x10_Odd_Even", "Connector_IDC:IDC-Header_2x10_P2.54mm_Vertical",
               "TO HAT", {str(k): v for k, v in PANEL_RIBBON.items()}, group="io", note="2x10 shrouded box header"))
    u = {num: MCP_PINS[port] for port, num in MCP_NUM.items()}
    u.update({"9": "3V3", "10": "GND", "12": "SCL", "13": "SDA", "15": "GND", "16": "GND", "17": "GND",
              "18": "MCP_RST", "19": NC, "20": "PANEL_INT", "11": NC, "14": NC})
    c.add(Part("U1", "Interface_Expansion:MCP23017_SO", "Package_SO:SOIC-28W_7.5x17.9mm_P1.27mm", "MCP23017-E/SO",
               u, group="io", note="I2C 0x20; GPA7/GPB7 used as outputs only"))
    c.add(Part("C1", "Device:C", C0805, "100n", {"1": "3V3", "2": "GND"}, group="io"))
    c.add(Part("C2", "Device:C", C0805, "10u", {"1": "3V3", "2": "GND"}, group="io"))
    c.add(Part("R1", "Device:R", R0805, "10k", {"1": "MCP_RST", "2": "3V3"}, group="io"))
    c.add(Part("C3", "Device:C", C0805, "100n", {"1": "MCP_RST", "2": "GND"}, group="io"))
    c.add(Part("R2", "Device:R", R0805, "10k", {"1": "PANEL_INT", "2": "3V3"}, group="io"))
    c.add(Part("J2", "Connector_Generic:Conn_01x04", "Connector_PinSocket_2.54mm:PinSocket_1x04_P2.54mm_Vertical",
               "OLED 1.5in SH1107", {"1": "GND", "2": "3V3", "3": "SCL", "4": "SDA"}, group="io",
               note="check module pin order GND VCC SCL SDA before soldering"))
    c.add(Part("C4", "Device:C", C0805, "10u", {"1": "3V3", "2": "GND"}, group="io"))

    for i, (ref, _, sig) in enumerate(ENCODERS):
        c.add(Part(ref, "Device:RotaryEncoder_Switch", ENCODER_FP, "EC11E (switch, 20 mm shaft)",
                   {"A": f"ENC_{sig}_A", "B": f"ENC_{sig}_B", "C": "GND", "S1": f"SW_{sig}", "S2": "GND"},
                   group=f"enc{i}", note="EC11 vertical with push switch, M7 bushing", pad_nets={"MP": "GND"}))
        for k, ab in enumerate("AB"):
            c.add(Part(f"R{10 + 2 * i + k}", "Device:R", R0805, "10k", {"1": f"ENC_{sig}_{ab}", "2": "3V3"},
                       group=f"enc{i}"))
            c.add(Part(f"C{10 + 2 * i + k}", "Device:C", C0805, "10n", {"1": f"ENC_{sig}_{ab}", "2": "GND"},
                       group=f"enc{i}"))
    for ref, _, net in BUTTONS:
        c.add(Part(ref, "Switch:SW_Push", BUTTON_FP, "12 mm tactile", {"1": net, "2": "GND"}, group="buttons",
                   note="12x12 mm THT tactile, stem height to suit the panel"))
    for i, (ref, _, net, colour, r) in enumerate(LEDS):
        c.add(Part(ref, "Device:LED", "LED_SMD:LED_0805_2012Metric", f"{colour} 0805", {"1": "GND", "2": f"{net}_A"},
                   group="leds"))
        c.add(Part(f"R{30 + i}", "Device:R", R0805, r, {"1": net, "2": f"{net}_A"}, group="leds"))
    return c


def _board(x, y):
    return x - BOARD_OFFSET[0], y - BOARD_OFFSET[1]


def placements():
    ctl = layout()["controls"]
    p = {}
    for ref, name, _ in ENCODERS:
        bx, by = _board(ctl[name]["x"], ctl[name]["y"])
        p[ref] = (bx - ENCODER_SHAFT[0], by - ENCODER_SHAFT[1], 0, "F")
    for ref, name, _ in BUTTONS:
        bx, by = _board(ctl[name]["x"], ctl[name]["y"])
        p[ref] = (bx - BUTTON_CENTRE[0], by - BUTTON_CENTRE[1], 0, "F")
    for i, (ref, name, *_rest) in enumerate(LEDS):
        bx, by = _board(ctl[name]["x"], ctl[name]["y"])
        p[ref] = (bx, by, 0, "F")
        p[f"R{30 + i}"] = (bx, by - 3.0 if by < 50 else by + 3.0, 0, "B")
    ox, oy = _board(ctl["OLED"]["x"], ctl["OLED"]["y"])
    p["J2"] = (ox - 3.81, oy + OLED_HEADER_DY, 90, "F")
    p["C4"] = (ox + 7.0, oy + OLED_HEADER_DY, 0, "B")
    # ribbon header on the back, pins running down from y = 9 (fit, because B-side flipping mirrors it)
    jx, jy, jrot = fit_footprint("Connector_IDC:IDC-Header_2x10_P2.54mm_Vertical", "B",
                                 {"1": (77.0, 9.0), "3": (77.0, 11.54)})
    p["J1"] = (jx, jy, jrot, "B")
    p["U1"] = (76.0, 45.0, 90, "B")
    p["C1"] = (70.0, 53.5, 0, "B")
    p["C2"] = (74.0, 53.5, 0, "B")
    p["R1"] = (86.5, 50.0, 90, "B")
    p["C3"] = (88.5, 50.0, 90, "B")
    p["R2"] = (86.5, 41.0, 90, "B")
    for i in range(4):
        for k in range(2):
            y = 12.0 + (2 * i + k) * 2.6
            p[f"R{10 + 2 * i + k}"] = (63.0, y, 0, "B")
            p[f"C{10 + 2 * i + k}"] = (67.5, y, 0, "B")
    return p


def positions(pcb_path):
    """Control centres, board outline and holes in panel coordinates, for the mechanical CAD."""
    import pcbnew
    board = pcbnew.LoadBoard(str(pcb_path))
    lay = layout()
    ox, oy = BOARD_OFFSET
    out = {"panel": {"w": lay["panel"]["w"], "h": lay["panel"]["h"]}, "board_offset": [ox, oy],
           "board_outline": [[x + ox, y + oy] for x, y in OUTLINE],
           "holes": [[x + ox, y + oy, 3.2] for x, y, _ in HOLES], "controls": {}}
    for name, ctl in lay["controls"].items():
        entry = dict(ctl)
        if ctl.get("ref"):
            fp = board.FindFootprintByReference(ctl["ref"])
            pads = list(fp.Pads())
            if ctl["kind"] == "encoder":
                pts = [q.GetPosition() for q in pads if q.GetNumber() == "MP"]
            else:
                pts = [q.GetPosition() for q in pads] if ctl["kind"] == "button" else [fp.GetPosition()]
            entry["x"] = round(pcbnew.ToMM(sum(q.x for q in pts) / len(pts)) + ox, 3)
            entry["y"] = round(pcbnew.ToMM(sum(q.y for q in pts) / len(pts)) + oy, 3)
        out["controls"][name] = entry
    return out


def build(outdir=HERE / "out"):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    c = circuit()
    sch, pcb = outdir / "sdr-panel.kicad_sch", outdir / "sdr-panel.kicad_pcb"
    write_schematic(c, sch, title="SDR receiver front panel")
    build_pcb(c, OUTLINE, placements(), HOLES, pcb, title="SDR receiver front panel")
    autoroute(pcb, c, net_widths=NET_WIDTHS)
    add_text(pcb, [("SDR PANEL v1  -  back side", 45, 40, "B", 1.5, 0), ("TO HAT", 70.5, 6.0, "B", 1.2, 0),
                   ("OLED: GND VCC SCL SDA", 52, 2.5, "F", 1.0, 0)])
    add_gnd_pours(pcb, "GND")
    fix_starved_thermals(pcb)
    pos_path = outdir / "positions.json"
    pos_path.write_text(json.dumps(positions(pcb), indent=2))
    res = {"sch": sch, "pcb": pcb, "positions": pos_path, "erc": run_erc(sch), "drc": run_drc(pcb)}
    res["fab"] = export_fab(c, pcb, sch, outdir / "fab")
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    a = ap.parse_args()
    r = build(a.out)
    (Path(a.out) / "result.json").write_text(json.dumps({k: str(v) if not isinstance(v, list) else v
                                                         for k, v in r.items() if k != "fab"}, indent=1))
    print("ERC:", r["erc"] or "clean")
    print("DRC:", r["drc"] or "clean")
