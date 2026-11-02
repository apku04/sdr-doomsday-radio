"""python -m unittest discover -s tests"""
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdr.heardlog import HeardLog  # noqa: E402
from sdr.recorder import Recorder  # noqa: E402

CH = {"band": "vhf", "freq_mhz": 145.725, "name": "OZ3RED Randers (16 km)"}
CHUNK = bytes(16000 // 5 * 2)           # 0.2 s of 16 kHz silence


def transmit(log, seconds, t0=0.0, where=CH):
    """Squelch open for `seconds` with audio, then closed long enough to end the entry."""
    out, t = [], t0
    for i in range(int(seconds / 0.2) + 15):
        active = i < seconds / 0.2
        e = log.update(t, 1_793_650_000 + t, active, where, -20)
        if active:
            log.feed(16000, CHUNK)
        out += [e] if e else []
        t += 0.2
    return out


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_logged_transmission_gets_a_wav(self):
        rec = Recorder(self.d / "rec")
        log = HeardLog(self.d / "heard.jsonl", recorder=rec)
        [e] = transmit(log, 3)
        p = rec.path_of(e["audio"])
        self.assertIsNotNone(p)
        with wave.open(str(p)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 16000))
            self.assertAlmostEqual(w.getnframes() / 16000, 3.0, delta=0.25)
        self.assertTrue(e["audio"].endswith("_145725.wav"))

    def test_short_blip_leaves_no_file(self):
        rec = Recorder(self.d / "rec")
        log = HeardLog(self.d / "heard.jsonl", recorder=rec)
        self.assertEqual(transmit(log, 0.4), [])
        self.assertEqual(rec.files(), [])

    def test_age_limit(self):
        rec = Recorder(self.d / "rec", keep_days=30)
        log = HeardLog(self.d / "heard.jsonl", recorder=rec)
        [old] = transmit(log, 2)
        [new] = transmit(log, 2, t0=100)
        p = rec.path_of(old["audio"])
        past = time.time() - 31 * 86400
        os.utime(p, (past, past))
        rec.prune()
        self.assertIsNone(rec.path_of(old["audio"]))
        self.assertIsNotNone(rec.path_of(new["audio"]))

    def test_size_limit_deletes_oldest_first(self):
        rec = Recorder(self.d / "rec", max_mb=0.15)      # ~2 x 2 s of 16 kHz audio
        log = HeardLog(self.d / "heard.jsonl", recorder=rec)
        names = [transmit(log, 2, t0=i * 100)[0]["audio"] for i in range(4)]
        kept = [n for n in names if rec.path_of(n)]
        self.assertEqual(kept, names[-len(kept):])
        self.assertLess(len(kept), 4)
        self.assertGreater(len(kept), 0)

    def test_path_of_rejects_other_names(self):
        rec = Recorder(self.d / "rec")
        for bad in ("../heard.jsonl", "x.wav", "", None, "20261102-185600_145725.wav/../../a"):
            self.assertIsNone(rec.path_of(bad))


if __name__ == "__main__":
    unittest.main()
