"""python -m unittest discover -s tests"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdr import speaker  # noqa: E402

CARDS = """ 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 1 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi
                      vc4-hdmi
 2 [Audio          ]: USB-Audio - AB13X USB Audio
                      Generic AB13X USB Audio at usb-3f980000.usb-1.3, full speed
"""
DEVS = ["plughw:CARD=Audio,DEV=0", "plughw:CARD=Headphones,DEV=0"]


class SpeakerTest(unittest.TestCase):
    def test_volume_curve_matches_web_ui(self):
        self.assertEqual(speaker.volume_gain(0), 0)
        self.assertAlmostEqual(speaker.volume_gain(50), 0.5)
        self.assertEqual(speaker.volume_gain(100), 2)
        self.assertEqual(speaker.volume_gain(150), 2)

    def test_apply_volume_scales_and_clips(self):
        pcm = np.array([1000, -1000, 30000], dtype="<i2").tobytes()
        out = np.frombuffer(speaker.apply_volume(pcm, 2), dtype="<i2")
        self.assertEqual(out.tolist(), [2000, -2000, 32767])
        out = np.frombuffer(speaker.apply_volume(pcm, 0), dtype="<i2")
        self.assertEqual(out.tolist(), [0, 0, 0])

    def test_cards_parsed(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write(CARDS)
        self.assertEqual(speaker.present_cards(f.name), {"Headphones", "vc4hdmi", "Audio"})
        self.assertEqual(speaker.present_cards(f.name + ".missing"), set())

    def test_usb_speaker_preferred_jack_as_fallback(self):
        self.assertEqual(speaker.pick_device(DEVS, {"Headphones", "Audio"}), DEVS[0])
        self.assertEqual(speaker.pick_device(DEVS, {"Headphones"}), DEVS[1])
        self.assertIsNone(speaker.pick_device(DEVS, set()))
        self.assertEqual(speaker.pick_device(["default"], set()), "default")


if __name__ == "__main__":
    unittest.main()
