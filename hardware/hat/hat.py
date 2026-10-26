"""SDR receiver HAT for Raspberry Pi 3B: panel ribbon, RTC, power monitor, switched LNA power.

Power path: J3 (5 V from the back-panel USB-C) -> F1 polyfuse -> Q1 reverse-polarity P-FET
-> R_SHUNT (INA219 current sense) -> Pi 5 V pins 2/4 (back-powering through the header).
Do not also power the Pi through its micro-USB socket.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.checks import run_drc
from lib.circuit import NC, Circuit, Part
from lib.contract import BCM_TO_HEADER, HEADER_POWER, PANEL_RIBBON, PI_BCM
from lib.fab import add_gnd_pours, export_fab
from lib.kicad_pcb import add_text, build_pcb, fit_footprint
from lib.kicad_sch import run_erc, write_schematic
from lib.route import autoroute

SIZE = (65.0, 56.5)
HOLES = [(3.5, 3.5, "MountingHole:MountingHole_2.7mm_M2.5"), (61.5, 3.5, "MountingHole:MountingHole_2.7mm_M2.5"),
         (3.5, 52.5, "MountingHole:MountingHole_2.7mm_M2.5"), (61.5, 52.5, "MountingHole:MountingHole_2.7mm_M2.5")]

R0805 = "Resistor_SMD:R_0805_2012Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
SOT23 = "Package_TO_SOT_SMD:SOT-23"

NET_WIDTHS = {"5V_IN": 1.0, "5V_F": 1.0, "5V_P": 1.0, "5V": 1.0, "GND": 0.5, "LNA_SW": 0.5, "LNA_5V": 0.5,
              "3V3": 0.4}


def circuit():
    c = Circuit("sdr-hat")
    j1 = {str(p): NC for p in range(1, 41)}
    j1.update({str(p): n for p, n in HEADER_POWER.items()})
    for net, bcm in PI_BCM.items():
        j1[str(BCM_TO_HEADER[bcm])] = net
    c.add(Part("J1", "Connector_Generic:Conn_02x20_Odd_Even",
               "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical", "Pi GPIO socket", j1,
               group="pi", note="2x20 female header, 8.5 mm (standard HAT height)"))
    c.add(Part("J2", "Connector_Generic:Conn_02x10_Odd_Even", "Connector_IDC:IDC-Header_2x10_P2.54mm_Vertical",
               "PANEL", {str(k): v for k, v in PANEL_RIBBON.items()}, group="pi",
               note="2x10 shrouded box header, keyed"))

    # power input and protection
    c.add(Part("J3", "Connector:Screw_Terminal_01x02",
               "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal", "5V IN",
               {"1": "5V_IN", "2": "GND"}, group="power", note="from back-panel USB-C (5.1k CC) module"))
    c.add(Part("F1", "Device:Polyfuse", "Fuse:Fuse_1812_4532Metric", "3A hold", {"1": "5V_IN", "2": "5V_F"},
               group="power", note="PTC 1812, 3 A hold / 6 V, e.g. Bourns MF-MSMF300"))
    c.add(Part("Q1", "Transistor_FET:IRF7404", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "AO4407A",
               {"1": "5V_P", "2": "5V_P", "3": "5V_P", "4": "Q1_G", "5": "5V_F", "6": "5V_F", "7": "5V_F",
                "8": "5V_F"}, group="power", note="P-FET reverse-polarity protection (AO4407A / IRF7404)"))
    c.add(Part("R1", "Device:R", R0805, "10k", {"1": "Q1_G", "2": "GND"}, group="power"))
    c.add(Part("R_SH", "Device:R", "Resistor_SMD:R_2512_6332Metric", "0.02R 1%",
               {"1": "5V_P", "2": "5V"}, group="power", note="current shunt 20 mOhm 1 W 2512"))
    c.add(Part("C1", "Device:C", C0805, "10u", {"1": "5V_IN", "2": "GND"}, group="power"))
    c.add(Part("C2", "Device:C", C0805, "10u", {"1": "5V", "2": "GND"}, group="power"))
    c.add(Part("D1", "Device:LED", "LED_SMD:LED_0805_2012Metric", "green 5V", {"1": "D1_K", "2": "5V"},
               group="power"))
    c.add(Part("R2", "Device:R", R0805, "1k", {"1": "D1_K", "2": "GND"}, group="power"))

    # power monitor
    c.add(Part("U1", "Sensor_Energy:INA219AxD", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "INA219AID",
               {"8": "5V_P", "7": "5V", "5": "3V3", "6": "GND", "3": "SDA", "4": "SCL", "1": "GND", "2": "GND"},
               group="monitor", note="I2C 0x40"))
    c.add(Part("C3", "Device:C", C0805, "100n", {"1": "3V3", "2": "GND"}, group="monitor"))

    # real-time clock
    rtc = {"2": "3V3", "14": "VBAT", "15": "SDA", "16": "SCL", "1": NC, "3": NC, "4": NC}
    rtc.update({str(p): "GND" for p in range(5, 14)})
    c.add(Part("U2", "Timer_RTC:DS3231M", "Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm", "DS3231M+", rtc,
               group="rtc", note="I2C 0x68, MEMS RTC"))
    c.add(Part("C4", "Device:C", C0805, "100n", {"1": "3V3", "2": "GND"}, group="rtc"))
    c.add(Part("BT1", "Device:Battery_Cell", "Battery:BatteryHolder_Keystone_3034_1x20mm", "CR2032",
               {"1": "VBAT", "2": "GND"}, group="rtc", note="Keystone 3034 SMD holder"))

    # switched 5 V for the external LNA
    c.add(Part("R3", "Device:R", R0805, "1k", {"1": "LNA_EN", "2": "Q2_G"}, group="lna"))
    c.add(Part("R4", "Device:R", R0805, "100k", {"1": "Q2_G", "2": "GND"}, group="lna"))
    c.add(Part("Q2", "Transistor_FET:2N7002", SOT23, "2N7002", {"1": "Q2_G", "2": "GND", "3": "Q3_G"}, group="lna"))
    c.add(Part("R5", "Device:R", R0805, "10k", {"1": "Q3_G", "2": "5V"}, group="lna"))
    c.add(Part("Q3", "Transistor_FET:AO3401A", SOT23, "AO3401A", {"1": "Q3_G", "2": "5V", "3": "LNA_SW"}, group="lna"))
    c.add(Part("F2", "Device:Polyfuse", "Fuse:Fuse_1206_3216Metric", "0.5A hold", {"1": "LNA_SW", "2": "LNA_5V"},
               group="lna", note="PTC 1206 0.5 A hold"))
    c.add(Part("J4", "Connector_Generic:Conn_01x02", "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical",
               "LNA 5V", {"1": "LNA_5V", "2": "GND"}, group="lna", note="JST-XH 2-pin to back-panel LNA socket"))
    return c


def placements():
    jx, jy, jrot = fit_footprint("Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical", "B",
                                 {"1": (8.37, 4.77), "2": (8.37, 2.23), "3": (10.91, 4.77)})
    return {
        "J1": (jx, jy, jrot, "B"),
        "J2": (21.07, 50.5, 90, "F"),
        "J3": (5.5, 33.0, 90, "F"),
        "C1": (11.0, 41.0, 0, "F"),
        "F1": (15.0, 33.5, 90, "F"),
        "Q1": (13.5, 23.0, 90, "F"),
        "R1": (19.5, 27.5, 90, "F"),
        "R_SH": (14.0, 12.5, 0, "F"),
        "C2": (7.0, 9.5, 90, "F"),
        "U1": (24.0, 16.0, 0, "F"),
        "C3": (24.0, 10.5, 0, "F"),
        "D1": (7.0, 44.0, 90, "F"),
        "R2": (7.0, 48.0, 90, "F"),
        "U2": (27.0, 33.0, 0, "F"),
        "C4": (27.0, 25.5, 0, "F"),
        "BT1": (47.5, 22.0, 0, "F"),
        "R3": (40.5, 37.5, 0, "F"),
        "R4": (40.5, 40.5, 0, "F"),
        "Q2": (45.0, 39.0, 0, "F"),
        "R5": (49.5, 36.0, 0, "F"),
        "Q3": (50.0, 40.0, 0, "F"),
        "F2": (55.0, 40.0, 90, "F"),
        "J4": (59.5, 41.5, 90, "F"),
    }


LABELS = [("SDR HAT v1", 32.5, 9.0, "F", 1.5, 0), ("5V IN", 5.5, 25.0, "F", 1.0, 0),
          ("+", 10.5, 33.0, "F", 1.4, 0), ("-", 10.5, 28.0, "F", 1.4, 0),
          ("PANEL", 32.5, 43.0, "F", 1.0, 0), ("LNA 5V", 59.0, 46.5, "F", 0.9, 0),
          ("power from J3 only, not micro-USB", 32.5, 55.3, "B", 1.0, 0)]


def build(outdir=Path(__file__).parent / "out"):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    c = circuit()
    sch = outdir / "sdr-hat.kicad_sch"
    pcb = outdir / "sdr-hat.kicad_pcb"
    write_schematic(c, sch, title="SDR receiver HAT (Pi 3B)")
    build_pcb(c, SIZE, placements(), HOLES, pcb, title="SDR receiver HAT")
    autoroute(pcb, c, net_widths=NET_WIDTHS)
    add_text(pcb, LABELS)
    add_gnd_pours(pcb, "GND", solid_pads=[("BT1", "2")])
    res = {"sch": sch, "pcb": pcb, "erc": run_erc(sch), "drc": run_drc(pcb)}
    res["fab"] = export_fab(c, pcb, sch, outdir / "fab")
    return res


if __name__ == "__main__":
    r = build()
    print("ERC:", r["erc"] or "clean")
    print("DRC:", r["drc"] or "clean")
    print("fab:", r["fab"])
