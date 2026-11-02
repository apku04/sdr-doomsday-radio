"""python -m unittest discover -s tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdr.heardlog import GAP_S, HeardLog  # noqa: E402

CH = {"band": "vhf", "freq_mhz": 145.725, "name": "OZ3RED Randers (16 km)"}
CH2 = {"band": "uhf", "freq_mhz": 434.9875, "name": "OZ9REZ Randers (16 km)"}


def run(log, steps, t0=0.0):
    """steps: list of (seconds, active, where, level). Samples every 0.2 s."""
    out, t = [], t0
    for secs, active, where, lvl in steps:
        for _ in range(int(round(secs / 0.2))):
            e = log.update(t, 1_000_000 + t, active, where, lvl)
            if e:
                out.append(e)
            t += 0.2
    return out


class HeardLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "heard.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_logs_a_transmission_with_duration_and_peak(self):
        log = HeardLog(self.path)
        out = run(log, [(1, False, CH, -40), (5, True, CH, -20), (0.6, True, CH, -12), (3, False, CH, -40)])
        self.assertEqual(len(out), 1)
        e = out[0]
        self.assertEqual((e["name"], e["freq_mhz"], e["peak_db"]), (CH["name"], 145.725, -12))
        self.assertAlmostEqual(e["dur_s"], 5.4, delta=0.3)
        self.assertEqual(json.loads(self.path.read_text().splitlines()[0])["name"], CH["name"])

    def test_short_blips_are_ignored(self):
        log = HeardLog(self.path, min_s=1.0)
        self.assertEqual(run(log, [(0.4, True, CH, -20), (3, False, CH, -40)]), [])

    def test_short_gap_is_one_transmission(self):
        log = HeardLog(self.path)
        out = run(log, [(2, True, CH, -20), (GAP_S - 0.5, False, CH, -40), (2, True, CH, -20), (3, False, CH, -40)])
        self.assertEqual(len(out), 1)

    def test_retune_closes_the_entry(self):
        log = HeardLog(self.path)
        out = run(log, [(2, True, CH, -20), (2, True, CH2, -25), (3, False, CH2, -40)])
        self.assertEqual([e["band"] for e in out], ["vhf", "uhf"])

    def test_live_entry_newest_first_and_survives_restart(self):
        log = HeardLog(self.path)
        run(log, [(2, True, CH, -20), (3, False, CH, -40), (2, True, CH2, -25)])
        rec = log.recent()
        self.assertTrue(rec[0]["live"])
        self.assertEqual(rec[1]["name"], CH["name"])
        again = HeardLog(self.path)
        self.assertEqual([e["name"] for e in again.recent()], [CH["name"]])

    def test_keeps_only_newest_entries(self):
        log = HeardLog(self.path, max_entries=3)
        for i in range(5):
            run(log, [(2, True, dict(CH, freq_mhz=145.0 + i), -20), (3, False, CH, -40)], t0=i * 10)
        self.assertEqual([e["freq_mhz"] for e in log.recent()], [149.0, 148.0, 147.0])


if __name__ == "__main__":
    unittest.main()
