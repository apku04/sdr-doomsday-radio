"""Load and sanity-check config/settings.json."""
import json
import math
from pathlib import Path

from .dsp import MODES

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"


class ConfigError(Exception):
    pass


def mhz_to_hz(mhz):
    return int(round(float(mhz) * 1e6))


def valid_gain(g, gains):
    return g == "auto" or any(math.isclose(float(g), x, abs_tol=0.05) for x in gains)


def load_settings(path=CONFIG_DIR / "settings.json"):
    with open(path, encoding="utf-8") as f:
        s = json.load(f)

    for name in s["modes"]:
        if name not in MODES:
            raise ConfigError(f"mode {name}: not implemented (have {list(MODES)})")

    sq_lo, sq_hi = float(s["squelch_min_db"]), float(s["squelch_max_db"])
    gains = s["receiver"]["gains"]
    for name, b in s["bands"].items():
        lo, hi = float(b["min_mhz"]), float(b["max_mhz"])
        if not (24 <= lo < hi <= 1766):
            raise ConfigError(f"band {name}: range outside RTL-SDR tuning range")
        if b["mode"] not in s["modes"]:
            raise ConfigError(f"band {name}: unknown mode {b['mode']}")
        if not lo <= float(b["default_mhz"]) <= hi:
            raise ConfigError(f"band {name}: default_mhz outside band")
        if not b["steps_khz"] or not all(0 < float(k) <= 10000 for k in b["steps_khz"]):
            raise ConfigError(f"band {name}: bad steps_khz")
        if b["squelch_db"] is not None and not sq_lo < float(b["squelch_db"]) <= sq_hi:
            raise ConfigError(f"band {name}: squelch_db must be null (off) or {sq_lo}..{sq_hi}")
        if not valid_gain(b["gain"], gains):
            raise ConfigError(f"band {name}: gain must be 'auto' or one of receiver.gains")
        b["min_hz"], b["max_hz"] = mhz_to_hz(lo), mhz_to_hz(hi)

    if set(s["band_order"]) != set(s["bands"]) or s["initial_band"] not in s["bands"]:
        raise ConfigError("band_order/initial_band do not match bands")
    cmd = s["receiver"].get("usb_power_cycle")
    if cmd is not None and not (isinstance(cmd, list) and all(isinstance(a, str) for a in cmd)):
        raise ConfigError("receiver.usb_power_cycle must be a list of strings or null")
    for k in ("settle_s", "dwell_s", "hang_s", "hold_s"):
        if not 0 <= float(s["scan"][k]) <= 600:
            raise ConfigError(f"scan.{k} out of range")
    return s
