import pcbnew
import pytest

from lib.contract import BCM_TO_HEADER, HEADER_POWER, PANEL_RIBBON, PI_BCM


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from hat import hat
    return hat.build(tmp_path_factory.mktemp("hat")), hat


def test_hat_contract():
    from hat import hat
    c = hat.circuit()
    j2 = c.part("J2").pins
    assert {int(k): v for k, v in j2.items()} == PANEL_RIBBON
    j1 = c.part("J1").pins
    for net, bcm in PI_BCM.items():
        assert j1[str(BCM_TO_HEADER[bcm])] == net, net
    for pin, net in HEADER_POWER.items():
        assert j1[str(pin)] == net


def test_hat_header_matches_pi_pinout(built):
    res, _ = built
    board = pcbnew.LoadBoard(str(res["pcb"]))
    j1 = board.FindFootprintByReference("J1")
    assert j1.IsFlipped()  # socket on the underside
    pos = {p.GetNumber(): (round(pcbnew.ToMM(p.GetPosition().x), 2), round(pcbnew.ToMM(p.GetPosition().y), 2))
           for p in j1.Pads()}
    assert pos["1"] == (8.37, 4.77)
    assert pos["2"] == (8.37, 2.23)
    assert pos["3"] == (10.91, 4.77)
    assert pos["40"] == (56.63, 2.23)


def test_hat_mounting_holes(built):
    res, _ = built
    board = pcbnew.LoadBoard(str(res["pcb"]))
    holes = sorted((round(pcbnew.ToMM(f.GetPosition().x), 2), round(pcbnew.ToMM(f.GetPosition().y), 2))
                   for f in board.GetFootprints() if f.GetReference().startswith("H"))
    assert holes == [(3.5, 3.5), (3.5, 52.5), (61.5, 3.5), (61.5, 52.5)]


def test_hat_erc_drc_clean(built):
    res, _ = built
    assert res["erc"] == []
    assert res["drc"] == []
