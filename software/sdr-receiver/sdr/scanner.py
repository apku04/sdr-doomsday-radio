"""Sequential scan of enabled presets.

Each hop retunes the open receiver to the next preset. Activity means the squelch
opened, i.e. channel power above the dBFS threshold; it says nothing about whether
the signal is intelligible speech.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)


class Scanner:
    def __init__(self, ctl, cfg):
        self.ctl = ctl
        self.cfg = cfg
        self._thread = None
        self._stop = threading.Event()
        self._resume = threading.Event()
        self.state = "off"   # off | scanning | activity | holding | paused
        self.preset = None
        self.index = 0
        self.total = 0
        self.hits = 0
        self.last_hit = None

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.active:
            if not self._stop.is_set():
                return
            self.join()
        self._stop.clear()
        self._resume.clear()
        self.hits = 0
        self.state = "scanning"
        self._thread = threading.Thread(target=self._run, name="scanner", daemon=True)
        self._thread.start()

    @property
    def stopping(self):
        return self._stop.is_set()

    def request_stop(self):
        """Non-blocking; safe to call while holding the controller lock."""
        self._stop.set()
        self._resume.set()

    def join(self, timeout=3):
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout)

    def resume(self):
        """Leave the current channel (skip) and continue scanning."""
        self._resume.set()

    def status(self):
        return {
            "active": self.active,
            "state": self.state if self.active else "off",
            "preset": self.preset,
            "index": self.index,
            "total": self.total,
            "hits": self.hits,
            "last_hit": self.last_hit,
            "dwell_s": self.cfg["dwell_s"],
            "hold_s": self.cfg["hold_s"],
        }

    def _wait(self, seconds, until):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop.is_set():
                return False
            if until():
                return True
            time.sleep(0.05)
        return False

    def _run(self):
        ctl, rx = self.ctl, self.ctl.rx
        settle, dwell = float(self.cfg["settle_s"]), float(self.cfg["dwell_s"])
        hang, hold = float(self.cfg["hang_s"]), float(self.cfg["hold_s"])
        ctl.msg("Scan started")
        i = 0
        try:
            while not self._stop.is_set():
                plist = ctl.presets.scan_list()
                if not plist:
                    ctl.msg("Scan stopped: no enabled presets", "warn")
                    break
                p = plist[i % len(plist)]
                self.index, self.total = i % len(plist) + 1, len(plist)
                i += 1
                self.preset = {"id": p["id"], "name": p["name"], "freq_mhz": p["freq_mhz"], "band": p["band"]}
                self.state = "scanning"
                started = ctl.scan_tune(p)
                if started is None:
                    break  # stop requested while waiting for the controller
                if not started:
                    ctl.msg("Receiver failed to start", "error")
                    break
                if not self._wait(settle + dwell, rx.activity):
                    continue

                self.hits += 1
                self.last_hit = {"name": p["name"], "freq_mhz": p["freq_mhz"], "t": time.time()}
                ctl.msg(f"Squelch opened on {p['freq_mhz']:.4f} MHz ({p['name']})")
                self._resume.clear()
                last_active = time.monotonic()
                while not self._stop.is_set() and not self._resume.is_set():
                    if not rx.running:
                        break
                    now = time.monotonic()
                    if rx.activity():
                        last_active = now
                    if now - last_active < hang:
                        self.state = "activity"
                    else:
                        if hold > 0 and now - last_active >= hang + hold:
                            break
                        self.state = "holding" if hold > 0 else "paused"
                    time.sleep(0.1)
                self._resume.clear()
        except Exception:
            log.exception("scanner crashed")
            ctl.msg("Scan stopped: internal error (see journal)", "error")
        finally:
            self.state = "off"
            ctl.msg("Scan stopped")
