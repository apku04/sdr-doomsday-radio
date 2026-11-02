"""python -m unittest discover -s tests   (no hardware needed)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panel  # noqa: E402

CW = [(0, 1), (0, 0), (1, 0), (1, 1)]     # A leads, from rest 11
CCW = [(1, 0), (0, 0), (0, 1), (1, 1)]


def feed(enc, seq):
    return [d for a, b in seq if (d := enc.update(a, b))]


class EncoderTest(unittest.TestCase):
    def test_half_step_knob_counts_every_detent(self):
        e = panel.Encoder(rest=(0, 3))
        e.update(1, 1)
        self.assertEqual(feed(e, CW), [1, 1])
        self.assertEqual(feed(e, CCW), [-1, -1])

    def test_full_step_knob_counts_one_per_cycle(self):
        e = panel.Encoder(rest=(3,))
        e.update(1, 1)
        self.assertEqual(feed(e, CW * 3), [1, 1, 1])
        self.assertEqual(feed(e, CCW), [-1])

    def test_contact_bounce_cancels(self):
        e = panel.Encoder()
        e.update(1, 1)
        bouncy = [(0, 1), (1, 1), (0, 1), (1, 1), (0, 1), (0, 0)]
        self.assertEqual(feed(e, bouncy), [1])

    def test_turning_back_before_detent_counts_nothing(self):
        e = panel.Encoder()
        e.update(1, 1)
        self.assertEqual(feed(e, [(0, 1), (1, 1)]), [])

    def test_invert(self):
        e = panel.Encoder(invert=True)
        e.update(1, 1)
        self.assertEqual(feed(e, CW[:2]), [-1])


class ButtonTest(unittest.TestCase):
    def test_debounced_press_and_release(self):
        b = panel.Button(debounce_s=0.02)
        self.assertEqual(b.update(0, 0.000), [])
        self.assertEqual(b.update(1, 0.005), [])      # bounce
        self.assertEqual(b.update(0, 0.010), [])
        self.assertEqual(b.update(0, 0.031), ["press"])
        self.assertEqual(b.update(0, 0.100), [])
        self.assertEqual(b.update(1, 0.200), [])
        self.assertEqual(b.update(1, 0.221), ["release"])

    def test_long_press(self):
        b = panel.Button(debounce_s=0.02, long_s=1.0)
        b.update(0, 0.0)
        self.assertEqual(b.update(0, 0.03), ["press"])
        self.assertEqual(b.update(0, 1.04), ["long"])
        self.assertEqual(b.update(0, 2.00), [])


CONFIG = {"bands": {"vhf": {"min_mhz": 144.0, "max_mhz": 146.0}, "uhf": {"min_mhz": 430.0, "max_mhz": 440.0},
                    "fm": {"min_mhz": 87.5, "max_mhz": 108.0}},
          "band_order": ["vhf", "uhf", "fm"], "squelch_min_db": -90, "squelch_max_db": 0,
          "gains": ["auto", 22.9, 40.2]}
for _k, _b in CONFIG["bands"].items():
    _b.update(label=_k.upper() + " band", steps_khz=[12.5, 25] if _k != "fm" else [100, 1000])

PRESETS = [{"id": "v-1", "band": "vhf", "name": "V one"}, {"id": "u-1", "band": "uhf", "name": "U one"}]


def state(**kw):
    s = {"band": "vhf", "freq_mhz": 145.5, "step_khz": 12.5, "volume": 60, "squelch_db": -25,
         "want_running": True, "running": True, "squelch_open": False, "error": None, "mode": "nfm",
         "monitor": False, "gain": 40.2, "level_db": -60, "preset": None,
         "scan": {"state": "off", "index": 0, "total": 0, "hits": 0}}
    s.update(kw)
    return s


class FakeApi:
    def __init__(self, st):
        self.st, self.calls = st, []

    def get(self, path):
        return {"/api/config": CONFIG, "/api/presets": {"presets": PRESETS}}.get(path, self.st)

    def action(self, action, value=None):
        self.calls.append((action, value))
        if action == "volume":
            self.st = dict(self.st, volume=value)
        return self.st


PANEL_CFG = {"knob_steps": {"volume": 5, "squelch_db": 2},
             "buttons": {"vol_push": {"press": "mute"}, "manual": {"press": "manual"},
                         "menu_push": {"press": "ui:push"}, "back_ui": {"press": "ui:back"},
                         "q2": {"press": "preset:v-145725"}, "back": {"press": None}},
             "leds": {"scan": 14, "sig": 15, "man": 8, "2m": 9, "70cm": 10, "fm": 11}}


class PanelTest(unittest.TestCase):
    def mk(self, **kw):
        api = FakeApi(state(**kw))
        return panel.Panel(PANEL_CFG, api), api

    def test_tune_moves_by_step_and_stops_at_band_edge(self):
        p, api = self.mk()
        p.turn("tune", 2)
        self.assertEqual(api.calls, [("freq", 145.525)])
        p, api = self.mk(freq_mhz=146.0)
        p.turn("tune", 1)
        self.assertEqual(api.calls, [])

    def test_volume_clamped(self):
        p, api = self.mk(volume=95)
        p.turn("volume", 3)
        self.assertEqual(api.calls, [("volume", 100)])

    def test_squelch_from_off(self):
        p, api = self.mk(squelch_db=None)
        p.turn("squelch", 1)
        self.assertEqual(api.calls, [("squelch", -88)])

    def test_menu_knob_drives_the_menu(self):
        p, api = self.mk(band="fm")
        p.turn("menu", 1)                       # opens the menu, cursor on BAND
        self.assertEqual(p.ui.screen, "main")
        p.button("menu_push", "press")          # BAND list, cursor on current band (FM)
        self.assertEqual(p.ui.screen, "list")
        p.turn("menu", -2)                      # FM -> 70CM -> 2M
        p.button("menu_push", "press")
        self.assertEqual(api.calls, [("band", "vhf")])
        self.assertEqual(p.ui.screen, "main")
        p.button("back_ui", "press")
        self.assertEqual(p.ui.screen, "home")

    def test_menu_edit_volume_and_contrast(self):
        p, api = self.mk(volume=40)
        p.turn("menu", 1)
        p.turn("menu", 4)                       # BAND PRESETS SCAN STEP -> VOLUME
        p.button("menu_push", "press")
        self.assertEqual(p.ui.screen, "edit")
        p.turn("menu", 2)
        self.assertEqual(api.calls, [("volume", 45), ("volume", 50)])
        p.button("menu_push", "press")
        self.assertEqual(p.ui.screen, "main")
        p.turn("menu", 4)                       # SQUELCH GAIN RECEIVER -> CONTRAST
        p.button("menu_push", "press")
        p.turn("menu", -3)
        self.assertEqual(p.local["contrast"], 5)
        self.assertEqual(len(api.calls), 2)     # contrast is local, not sent to the app

    def test_menu_presets_current_band_first(self):
        p, api = self.mk(band="uhf", preset=None)
        p.turn("menu", 1)
        p.turn("menu", 1)                       # PRESETS
        p.button("menu_push", "press")
        labels = [label for label, _ in p.ui.list["items"]]
        self.assertEqual(labels[0], "U one")
        p.button("menu_push", "press")
        self.assertEqual(api.calls, [("preset", "u-1")])

    def test_menu_toggles_and_scrolls_with_wrap(self):
        p, api = self.mk()
        p.turn("menu", 1)
        p.turn("menu", -3)                      # wraps: INFO, CONTRAST, RECEIVER
        p.button("menu_push", "press")
        self.assertEqual(api.calls, [("toggle_power", None)])

    def test_menu_returns_home_when_idle(self):
        p, _ = self.mk()
        p.turn("menu", 1)
        p.ui.tick(p.ui.last_input + 25)
        self.assertEqual(p.ui.screen, "home")

    def test_every_screen_renders(self):
        import ui
        u = ui.Ui(CONFIG, info=lambda: ["HOST", "192.168.1.169"])
        u.presets = PRESETS
        sts = [state(), state(band="fm", freq_mhz=104.0, step_khz=100, squelch_db=None, level_db=-40),
               state(level_db=-20, squelch_open=True, preset={"id": "v-1", "name": "OZ3RED Randers (16 km)"}),
               state(scan={"state": "holding", "index": 3, "total": 12, "hits": 2}, error="boom")]
        for st in sts:
            u.screen = "home"
            self.assertTrue(any(u.render(st, 0).buf))
            for sel in range(len(u.MAIN)):
                u.screen, u.sel, u.top = "main", sel, max(0, sel - ui.VISIBLE + 1)
                u.push(st, 0)
                self.assertTrue(any(u.render(st, 0, message="TEST").buf))
        self.assertTrue(any(u.render(None, 0).buf))

    def test_mute_toggles_and_turning_unmutes(self):
        p, api = self.mk(volume=40)
        p.button("vol_push", "press")
        p.button("vol_push", "press")
        self.assertEqual(api.calls, [("volume", 0), ("volume", 40)])
        p.button("vol_push", "press")
        p.turn("volume", 1)
        self.assertEqual(api.calls[-1], ("volume", 45))

    def test_manual_stops_scan_and_starts(self):
        p, api = self.mk(want_running=False, scan={"state": "scanning"})
        p.button("manual", "press")
        self.assertEqual(api.calls, [("scan_stop", None), ("start", None)])

    def test_scan_button_starts_skips_and_stops(self):
        PANEL_CFG["buttons"]["scan"] = {"press": "scan"}
        for scan_state, expect in (("off", "scan_start"), ("activity", "scan_resume"),
                                   ("holding", "scan_resume"), ("scanning", "scan_stop")):
            p, api = self.mk(scan={"state": scan_state})
            p.button("scan", "press")
            self.assertEqual(api.calls, [(expect, None)], scan_state)

    def test_preset_button_and_unassigned_button(self):
        p, api = self.mk()
        p.button("q2", "press")
        p.button("back", "press")
        p.button("q2", "release")
        self.assertEqual(api.calls, [("preset", "v-145725")])

    def test_leds(self):
        p, _ = self.mk()
        on = {k for k, v in p.leds(state(squelch_open=True), blink=False).items() if v}
        self.assertEqual(on, {"sig", "man", "2m"})
        on = {k for k, v in p.leds(state(band="fm", scan={"state": "scanning"}), blink=False).items() if v}
        self.assertEqual(on, {"scan", "fm"})
        on = {k for k, v in p.leds(None, blink=True).items() if v}
        self.assertEqual(on, {"scan"})


if __name__ == "__main__":
    unittest.main()
