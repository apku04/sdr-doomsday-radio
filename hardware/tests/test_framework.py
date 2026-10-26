from lib.circuit import Circuit, Part
from lib.kicad_sch import write_schematic, sch_netlist, run_erc
from lib.kicad_pcb import build_pcb, pcb_pad_nets
from lib.route import autoroute
from lib.checks import run_drc


def small_circuit():
    c = Circuit("t")
    c.add(Part("R1", "Device:R_Small", "Resistor_SMD:R_0805_2012Metric", "10k", {"1": "A", "2": "B"}))
    c.add(Part("C1", "Device:C_Small", "Capacitor_SMD:C_0805_2012Metric", "100n", {"1": "B", "2": "GND"}))
    c.add(Part("J1", "Connector_Generic:Conn_01x03", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
               "IN", {"1": "A", "2": "B", "3": "GND"}))
    return c


PLACE = {"R1": (10, 8, 0, "F"), "C1": (20, 12, 90, "F"), "J1": (5, 15, 0, "F")}


def test_netlist_roundtrip(tmp_path):
    c = small_circuit()
    sch = tmp_path / "t.kicad_sch"
    write_schematic(c, sch)
    assert sch_netlist(sch) == c.nets()


def test_erc_clean(tmp_path):
    c = small_circuit()
    sch = tmp_path / "t.kicad_sch"
    write_schematic(c, sch)
    assert run_erc(sch) == []


def test_pcb_nets_match(tmp_path):
    c = small_circuit()
    pcb = tmp_path / "t.kicad_pcb"
    build_pcb(c, (30, 25), PLACE, [], pcb)
    got = pcb_pad_nets(pcb)
    for net, pins in c.nets().items():
        for ref, pin in pins:
            assert got[(ref, pin)] == net


def test_router_connects_small_board(tmp_path):
    c = small_circuit()
    pcb = tmp_path / "t.kicad_pcb"
    build_pcb(c, (30, 25), PLACE, [], pcb)
    autoroute(pcb, c)
    assert run_drc(pcb) == []


def test_drc_reports_unrouted_board(tmp_path):
    c = small_circuit()
    pcb = tmp_path / "t.kicad_pcb"
    build_pcb(c, (30, 25), PLACE, [], pcb)
    probs = run_drc(pcb)
    assert any("unconnected" in p for p in probs)


def test_export_fab_makes_jlc_zip_and_bom(tmp_path):
    import zipfile
    from lib.fab import add_gnd_pours, export_fab
    c = small_circuit()
    pcb = tmp_path / "t.kicad_pcb"
    sch = tmp_path / "t.kicad_sch"
    write_schematic(c, sch)
    build_pcb(c, (30, 25), PLACE, [], pcb)
    autoroute(pcb, c)
    add_gnd_pours(pcb, "GND")
    assert run_drc(pcb) == []
    out = export_fab(c, pcb, sch, tmp_path / "fab")
    names = zipfile.ZipFile(out["gerbers_zip"]).namelist()
    for suffix in ("F_Cu.gtl", "B_Cu.gbl", "Edge_Cuts.gm1", ".drl"):
        assert any(n.endswith(suffix) for n in names), suffix
    bom = out["bom_csv"].read_text()
    assert "R1" in bom and "10k" in bom
    assert out["schematic_pdf"].stat().st_size > 1000
