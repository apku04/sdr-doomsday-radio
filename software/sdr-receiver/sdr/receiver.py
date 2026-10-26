"""The receiver: one RTL-SDR kept open for the life of the app, demodulated in numpy.

Why not rtl_fm: rtl_fm cannot retune, so every change re-opened the dongle, and on this
Pi + dongle roughly one re-open in a few hundred timed out and wedged the USB device
(needing a power cycle). Here the device is opened once; frequency, gain and mode changes
are register writes on the open device. 500 retunes in a row: 0 errors.

Threads:
  usb    rtlsdr_read_async -> raw I/Q blocks into a small queue (librtlsdr buffers the USB side)
  dsp    demodulate, measure channel power (dBFS), squelch, push PCM to listeners
  watch  stall detection: no samples for STALL_S while running -> close, power-cycle USB, reopen
"""
import collections
import logging
import queue
import subprocess
import threading
import time

from .dsp import MODES, Demodulator
from .rtlsdr import RtlSdr, RtlSdrError

log = logging.getLogger(__name__)

STALL_S = 3.0
SQUELCH_HYST_DB = 2.0
SQUELCH_HANG_S = 0.4   # keep the squelch open this long after the level drops (no choppy speech)


class TooManyListeners(Exception):
    pass


class Listener:
    """Bounded per-client queue; drops the oldest chunk rather than buffering forever."""

    def __init__(self, maxlen=8):
        self.q = queue.Queue(maxsize=maxlen)
        self.dropped = 0

    def put(self, item):
        while True:
            try:
                self.q.put_nowait(item)
                return
            except queue.Full:
                try:
                    self.q.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass


class Receiver:
    def __init__(self, rx_cfg, on_message=None):
        self.cfg = rx_cfg
        self.on_message = on_message or (lambda text, level: None)
        self._lock = threading.RLock()
        self._dev = None
        self._dev_mode = None
        self._usb_thread = None
        self._raw = queue.Queue(maxsize=6)
        self._listeners = set()
        self._llock = threading.Lock()
        self._settings_gen = 0      # bumped on every retune; blocks from older settings are dropped
        self._skip = 0
        self._dm = None
        self.tuning = None          # {"freq_hz", "mode", "squelch_db" (None = off), "gain"}
        self.running = False
        self.sample_rate = MODES["nfm"]["fs"] // (MODES["nfm"]["decim1"] * MODES["nfm"]["decim2"])
        self.level_db = None
        self.squelch_open = False
        self._open_until = 0.0
        self.last_block = 0.0
        self.retune_at = 0.0
        self.error = None
        self.recoveries = 0
        self.blocks = 0
        self.overruns = 0
        self.log = collections.deque(maxlen=20)
        threading.Thread(target=self._dsp_loop, name="dsp", daemon=True).start()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()

    # -- device lifecycle -------------------------------------------------------

    def _note(self, text, level="info"):
        self.log.append(f"{time.strftime('%H:%M:%S')} {text}")
        getattr(log, "warning" if level == "warn" else level)(text)
        self.on_message(text, level)

    def _open(self):
        if self._dev is None:
            self._dev = RtlSdr(int(self.cfg["device_index"]))
            if int(self.cfg["ppm"]):
                self._dev.set_ppm(self.cfg["ppm"])
            self._dev_mode = None
            self._note("RTL-SDR opened")

    def _close(self):
        dev, t = self._dev, self._usb_thread
        if dev is not None:
            dev.cancel_async()
            if t:
                t.join(timeout=3)
            if t and t.is_alive():
                log.error("USB reader did not stop; leaving device handle open")
            else:
                dev.close()
        self._dev, self._usb_thread, self._dev_mode = None, None, None

    def _usb_loop(self, dev, buf_len):
        def cb(data):
            self.last_block = time.monotonic()
            try:
                self._raw.put_nowait((self._settings_gen, data))
            except queue.Full:
                self.overruns += 1
        r = dev.read_async(cb, buf_len)
        log.info("read_async returned %s", r)

    def _start_stream(self, mode):
        """(Re)start async reading for this mode's sample rate and block size. No re-open."""
        p = MODES[mode]
        if self._dev_mode == mode and self._usb_thread and self._usb_thread.is_alive():
            return
        if self._usb_thread and self._usb_thread.is_alive():
            self._dev.cancel_async()
            self._usb_thread.join(timeout=3)
            if self._usb_thread.is_alive():
                raise RtlSdrError("USB reader did not stop")
        self._dev.set_sample_rate(p["fs"])
        self._dev.reset_buffer()
        self._dev_mode = mode
        self.last_block = time.monotonic()
        self._usb_thread = threading.Thread(target=self._usb_loop, args=(self._dev, 2 * p["block"]),
                                            name="usb", daemon=True)
        self._usb_thread.start()

    # -- public control ---------------------------------------------------------

    def start(self, freq_hz, mode, squelch_db, gain):
        """Start, or retune in place if already running."""
        with self._lock:
            try:
                self._open()
                if self._dm is None or self._dm.mode != mode:
                    self._dm = Demodulator(mode)
                self._settings_gen += 1
                self._dev.set_gain(gain)
                self._dev.set_center_freq(int(freq_hz) + self._dm.offset_hz)
                self._start_stream(mode)
            except RtlSdrError as e:
                self.error = str(e)
                self._close()
                self.running = False
                raise
            self._skip = 1  # the next block may still hold pre-retune samples
            self.tuning = {"freq_hz": int(freq_hz), "mode": mode, "squelch_db": squelch_db, "gain": gain}
            self.sample_rate = self._dm.audio_rate
            self.squelch_open = squelch_db is None
            self._open_until = 0.0
            self.level_db = None
            self.retune_at = time.monotonic()
            self.running = True
            self.error = None

    def set_squelch(self, squelch_db):
        with self._lock:
            if self.tuning:
                self.tuning["squelch_db"] = squelch_db
                if squelch_db is None:
                    self.squelch_open = True

    def stop(self):
        """Stop streaming audio. The dongle stays open (idle) so the next start is a retune."""
        with self._lock:
            self.running = False
            self.tuning = None
            self.squelch_open = False
            self.level_db = None
            if self._dev is not None and self._usb_thread and self._usb_thread.is_alive():
                self._dev.cancel_async()
                self._usb_thread.join(timeout=3)
            self._dev_mode = None

    def shutdown(self):
        with self._lock:
            self.running = False
            self._close()

    # -- DSP --------------------------------------------------------------------

    def _dsp_loop(self):
        while True:
            gen, data = self._raw.get()
            if gen != self._settings_gen or not self.running:
                continue
            dm, tuning = self._dm, self.tuning
            if dm is None or tuning is None or len(data) != dm.block_bytes:
                continue
            if self._skip > 0:
                self._skip -= 1
                continue
            try:
                pcm, db = dm.process(data)
            except Exception:
                log.exception("DSP failed")
                continue
            if gen != self._settings_gen:
                continue
            self.blocks += 1
            self.level_db = db
            sq = tuning["squelch_db"]
            now = time.monotonic()
            if sq is None:
                self.squelch_open = True
            else:
                thresh = sq - SQUELCH_HYST_DB if self.squelch_open else sq
                if db >= thresh:
                    self._open_until = now + SQUELCH_HANG_S
                self.squelch_open = now < self._open_until
            if self.squelch_open:
                with self._llock:
                    listeners = list(self._listeners)
                for li in listeners:
                    li.put((dm.audio_rate, pcm))

    # -- stall recovery ---------------------------------------------------------

    def _watchdog(self):
        while True:
            time.sleep(1)
            if not self.running or time.monotonic() - self.last_block < STALL_S:
                continue
            with self._lock:
                if not self.running or time.monotonic() - self.last_block < STALL_S:
                    continue
                tuning = dict(self.tuning)
                self._note("No samples from the RTL-SDR for %.0f s: recovering" % STALL_S, "warn")
                self._close()
                self._power_cycle()
                try:
                    self.start(tuning["freq_hz"], tuning["mode"], tuning["squelch_db"], tuning["gain"])
                    self.recoveries += 1
                    self._note("RTL-SDR recovered")
                except RtlSdrError as e:
                    self.running = False
                    self.error = f"RTL-SDR lost and recovery failed: {e}. Check USB / power."
                    self._note(self.error, "error")

    def _power_cycle(self):
        cmd = self.cfg.get("usb_power_cycle")
        if not cmd:
            return
        self._note("Power-cycling USB: " + " ".join(cmd), "warn")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode:
                self._note(f"USB power cycle failed: {r.stderr.strip()[:200]}", "error")
        except (OSError, subprocess.TimeoutExpired) as e:
            self._note(f"USB power cycle failed: {e}", "error")
        time.sleep(float(self.cfg.get("usb_settle_s", 5)))

    # -- listeners / status -----------------------------------------------------

    def subscribe(self, max_clients):
        with self._llock:
            if len(self._listeners) >= max_clients:
                raise TooManyListeners()
            li = Listener()
            self._listeners.add(li)
            return li

    def unsubscribe(self, li):
        with self._llock:
            self._listeners.discard(li)

    def listener_count(self):
        with self._llock:
            return len(self._listeners)

    def activity(self):
        """True while the squelch is open (channel power above the threshold)."""
        return self.running and self.squelch_open and bool(self.tuning) and self.tuning["squelch_db"] is not None

    @property
    def device_open(self):
        return self._dev is not None
