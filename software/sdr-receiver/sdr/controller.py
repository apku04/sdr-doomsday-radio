"""Receiver state and the logical actions shared by the web UI and future GPIO controls.

Every input (HTTP request, rotary encoder, button) goes through Controller.do(action, value).
All values are validated here before they reach the receiver.
"""
import collections
import logging
import math
import threading
import time

from .config import mhz_to_hz, valid_gain
from .presets import PresetError
from .receiver import Receiver
from .rtlsdr import RtlSdrError
from .scanner import Scanner

log = logging.getLogger(__name__)


class ControlError(ValueError):
    """A request that is invalid or not allowed in the current state."""


class Controller:
    def __init__(self, settings, presets):
        self.s = settings
        self.presets = presets
        self.bands = settings["bands"]
        self.band_order = settings["band_order"]
        self._lock = threading.RLock()
        self.messages = collections.deque(maxlen=40)
        self.rx = Receiver(settings["receiver"], on_message=self.msg)
        self.scanner = Scanner(self, settings["scan"])

        self.band = settings["initial_band"]
        self.band_freq = {b: mhz_to_hz(c["default_mhz"]) for b, c in self.bands.items()}
        self.step_hz = {b: int(c["steps_khz"][0] * 1000) for b, c in self.bands.items()}
        self.squelch = {b: c["squelch_db"] for b, c in self.bands.items()}  # None = off
        self.gain = {b: c["gain"] for b, c in self.bands.items()}
        self.volume = 60
        self.monitor = False          # squelch temporarily forced open
        self.want_running = False
        self.preset_id = None
        self.error = None
        for problem in presets.problems:
            self.msg(problem, "warn")

    # -- helpers -----------------------------------------------------------

    @property
    def freq_hz(self):
        return self.band_freq[self.band]

    @property
    def mode(self):
        return self.bands[self.band]["mode"]

    def msg(self, text, level="info"):
        self.messages.append({"t": time.time(), "level": level, "text": text})
        if level != "info":
            getattr(log, "warning" if level == "warn" else level)(text)

    def _effective_squelch(self):
        return None if self.monitor else self.squelch[self.band]

    def _apply(self):
        """Push the current settings to the receiver (a retune on the open device)."""
        with self._lock:
            if not self.want_running:
                self.rx.stop()
                return
            try:
                self.rx.start(self.freq_hz, self.mode, self._effective_squelch(), self.gain[self.band])
                self.error = None
            except RtlSdrError as e:
                self.want_running = False
                self.error = f"{e}"
                self.msg(f"Receiver error: {e}", "error")
                self.scanner.request_stop()

    def _check_freq(self, band, hz):
        b = self.bands[band]
        if not b["min_hz"] <= hz <= b["max_hz"]:
            raise ControlError(f"{hz / 1e6:.4f} MHz is outside {b['label']} "
                               f"({b['min_mhz']:.3f}-{b['max_mhz']:.3f} MHz). Not tuned.")

    def _manual(self):
        """A manual tuning action takes the receiver away from the scanner."""
        if self.scanner.active and not self.scanner.stopping:
            self.scanner.request_stop()
            self.msg("Scan stopped by manual tuning")

    def _match_preset(self):
        self.preset_id = None
        for p in self.presets.all():
            if p["band"] == self.band and mhz_to_hz(p["freq_mhz"]) == self.freq_hz:
                self.preset_id = p["id"]
                break

    # -- actions -----------------------------------------------------------

    def start(self):
        with self._lock:
            self.want_running = True
            self._apply()

    def stop(self):
        self.scanner.request_stop()
        self.scanner.join()
        with self._lock:
            self.want_running = False
            self.monitor = False
            self.error = None
            self._apply()

    def set_band(self, band):
        if band not in self.bands:
            raise ControlError(f"unknown band {band!r}")
        with self._lock:
            self._manual()
            self.band = band
            self.monitor = False
            self._match_preset()
            self._apply()

    def band_next(self):
        i = self.band_order.index(self.band)
        self.set_band(self.band_order[(i + 1) % len(self.band_order)])

    def set_freq(self, mhz):
        try:
            mhz = float(mhz)
        except (TypeError, ValueError):
            raise ControlError("frequency must be a number in MHz")
        if not math.isfinite(mhz):
            raise ControlError("frequency must be a number in MHz")
        hz = int(round(mhz * 1e5)) * 10  # 10 Hz resolution
        with self._lock:
            self._check_freq(self.band, hz)
            self._manual()
            self.band_freq[self.band] = hz
            self._match_preset()
            self._apply()

    def tune(self, direction):
        if direction not in (1, -1):
            raise ControlError("tune direction must be 1 or -1")
        with self._lock:
            hz = self.freq_hz + direction * self.step_hz[self.band]
            b = self.bands[self.band]
            if not b["min_hz"] <= hz <= b["max_hz"]:
                raise ControlError(f"band edge reached ({b['min_mhz']:.3f}-{b['max_mhz']:.3f} MHz)")
            self._manual()
            self.band_freq[self.band] = hz
            self._match_preset()
            self._apply()

    def set_step(self, khz):
        try:
            khz = float(khz)
        except (TypeError, ValueError):
            raise ControlError("step must be a number in kHz")
        steps = self.bands[self.band]["steps_khz"]
        if not any(math.isclose(khz, s) for s in steps):
            raise ControlError(f"step must be one of {steps} kHz for this band")
        with self._lock:
            self.step_hz[self.band] = int(round(khz * 1000))

    def step_next(self):
        steps = [int(round(k * 1000)) for k in self.bands[self.band]["steps_khz"]]
        cur = self.step_hz[self.band]
        with self._lock:
            self.step_hz[self.band] = steps[(steps.index(cur) + 1) % len(steps)] if cur in steps else steps[0]

    def select_preset(self, pid):
        try:
            p = self.presets.get(str(pid))
        except PresetError as e:
            raise ControlError(str(e))
        with self._lock:
            self._manual()
            self._tune_preset(p)
            if p["digital"]:
                self.msg(f"{p['name']} is {p['digital']} digital voice: FM demodulation gives noise, not speech", "warn")
            self._apply()

    def _tune_preset(self, p):
        hz = mhz_to_hz(p["freq_mhz"])
        self._check_freq(p["band"], hz)
        if p["band"] != self.band:
            self.monitor = False
        self.band = p["band"]
        self.band_freq[self.band] = hz
        self.preset_id = p["id"]

    def scan_tune(self, p):
        """Used by the scanner. Returns None if the scan is being stopped, else whether rx runs."""
        with self._lock:
            if self.scanner.stopping:
                return None
            self._tune_preset(p)
            self.monitor = False
            self.want_running = True
            self._apply()
            return self.rx.running

    def set_squelch(self, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ControlError("squelch must be a number in dBFS")
        lo, hi = float(self.s["squelch_min_db"]), float(self.s["squelch_max_db"])
        if not (math.isfinite(v) and lo <= v <= hi):
            raise ControlError(f"squelch must be {lo:.0f}..{hi:.0f} dBFS ({lo:.0f} = off)")
        with self._lock:
            if v <= lo and self.scanner.active:
                raise ControlError("squelch cannot be off while scanning")
            self.squelch[self.band] = None if v <= lo else round(v, 1)
            self.monitor = False
            self.rx.set_squelch(self._effective_squelch())

    def toggle_monitor(self):
        with self._lock:
            if not self.monitor and self.scanner.active:
                raise ControlError("stop the scan before opening the squelch")
            self.monitor = not self.monitor
            self.rx.set_squelch(self._effective_squelch())

    def set_volume(self, value):
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ControlError("volume must be an integer 0-100")
        if not 0 <= v <= 100:
            raise ControlError("volume must be 0-100")
        self.volume = v

    def set_gain(self, value):
        if value != "auto":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ControlError("gain must be 'auto' or a supported value in dB")
        gains = self.s["receiver"]["gains"]
        if not valid_gain(value, gains):
            raise ControlError("unsupported gain value")
        if value != "auto":
            value = min(gains, key=lambda g: abs(g - value))
        with self._lock:
            self.gain[self.band] = value
            self._apply()

    def scan_start(self):
        with self._lock:
            plist = self.presets.scan_list()
            if not plist:
                raise ControlError("no enabled analogue presets to scan")
            for band in {p["band"] for p in plist}:
                if self.squelch[band] is None:
                    raise ControlError(f"set a squelch level for {self.bands[band]['label']}: "
                                       "scanning stops when the squelch opens")
            self.monitor = False
        self.scanner.start()

    def scan_stop(self):
        self.scanner.request_stop()
        self.scanner.join()

    # Dispatch table: the one entry point for web and future GPIO inputs.
    def do(self, action, value=None):
        table = {
            "start": lambda: self.start(),
            "stop": lambda: self.stop(),
            "toggle_power": lambda: self.stop() if self.want_running else self.start(),
            "band": lambda: self.set_band(value),
            "band_next": lambda: self.band_next(),
            "freq": lambda: self.set_freq(value),
            "tune_up": lambda: self.tune(1),
            "tune_down": lambda: self.tune(-1),
            "step": lambda: self.set_step(value),
            "step_next": lambda: self.step_next(),
            "preset": lambda: self.select_preset(value),
            "squelch": lambda: self.set_squelch(value),
            "monitor": lambda: self.toggle_monitor(),
            "volume": lambda: self.set_volume(value),
            "gain": lambda: self.set_gain(value),
            "scan_start": lambda: self.scan_start(),
            "scan_stop": lambda: self.scan_stop(),
            "scan_toggle": lambda: self.scan_stop() if self.scanner.active else self.scan_start(),
            "scan_resume": lambda: self.scanner.resume(),
            # Physical controls (future GPIO) map onto the same actions:
            "encoder_cw": lambda: self.tune(1),
            "encoder_ccw": lambda: self.tune(-1),
            "encoder_push": lambda: self.step_next(),
        }
        if action not in table:
            raise ControlError(f"unknown action {action!r}")
        table[action]()

    # -- state -------------------------------------------------------------

    def indicators(self):
        """Logical LED states for a future GPIO driver."""
        err = self.error or self.rx.error
        return {
            "power": "error" if err else ("on" if self.want_running else "off"),
            "scan": self.scanner.active,
            "activity": self.rx.activity(),
        }

    def state(self):
        with self._lock:
            preset = None
            if self.preset_id:
                try:
                    preset = self.presets.get(self.preset_id)
                except PresetError:
                    self.preset_id = None
            rx = self.rx
            return {
                "running": rx.running,
                "want_running": self.want_running,
                "device_open": rx.device_open,
                "band": self.band,
                "band_label": self.bands[self.band]["label"],
                "freq_mhz": self.freq_hz / 1e6,
                "mode": self.mode,
                "step_khz": self.step_hz[self.band] / 1000,
                "squelch_db": self.squelch[self.band],
                "monitor": self.monitor,
                "squelch_open": rx.running and rx.squelch_open,
                "activity": rx.activity(),
                "level_db": None if rx.level_db is None else round(rx.level_db, 1),
                "gain": self.gain[self.band],
                "volume": self.volume,
                "preset": preset,
                "error": self.error or rx.error,
                "scan": self.scanner.status(),
                "listeners": rx.listener_count(),
                "indicators": self.indicators(),
                "messages": list(self.messages)[-15:],
                "diag": {"blocks": rx.blocks, "overruns": rx.overruns, "recoveries": rx.recoveries,
                         "log": list(rx.log)[-6:]},
            }

    def shutdown(self):
        self.scanner.request_stop()
        self.scanner.join()
        with self._lock:
            self.want_running = False
            self.rx.shutdown()
