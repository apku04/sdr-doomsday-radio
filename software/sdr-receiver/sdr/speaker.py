"""Local audio out: plays the receiver audio on the Pi itself (USB speaker, else the 3.5 mm jack).

A listener like a browser client, fed into `aplay` (raw S16_LE mono; ALSA plughw resamples
16 kHz NFM / 32 kHz WFM to whatever the device wants). Volume / mute = Controller.volume,
the same curve as the web UI. Silence is written while the squelch is closed so the device
never underruns. Devices are tried in settings order; when a preferred device appears
(USB speaker plugged in) playback moves to it, when it vanishes the next one takes over.
"""
import logging
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

CHUNK_S = 0.1           # silence written per empty poll
CHECK_S = 3.0           # how often to look for a better device / retry a failed one
CARD_RE = re.compile(r"^\s*\d+\s+\[(\S+)\s*\]", re.M)


def volume_gain(v):
    """Same curve as static/app.js volumeToGain: 0..100 -> 0..2."""
    return (max(0, min(100, v)) / 100) ** 2 * 2


def apply_volume(pcm, gain):
    if gain == 1:
        return pcm
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * gain
    return np.clip(a, -32768, 32767).astype("<i2").tobytes()


def present_cards(path="/proc/asound/cards"):
    try:
        return set(CARD_RE.findall(Path(path).read_text()))
    except OSError:
        return set()


def card_of(device):
    m = re.search(r"CARD=([^,]+)", device)
    return m.group(1) if m else None


def pick_device(devices, cards):
    """First configured device whose card is present (devices without CARD= always count)."""
    for d in devices:
        c = card_of(d)
        if c is None or c in cards:
            return d
    return None


class Speaker:
    def __init__(self, ctl, cfg):
        self.ctl, self.rx = ctl, ctl.rx
        self.devices = list(cfg["devices"])
        self.mixer_percent = cfg.get("mixer_percent")
        self.buffer_us = int(cfg.get("buffer_ms", 300) * 1000)
        self.device = None
        self.proc = None
        self.rate = None
        self.status = "starting"
        self._listener = None
        self._stop = threading.Event()

    def start(self):
        self._listener = self.rx.subscribe(10 ** 6)     # not counted against web clients
        threading.Thread(target=self._run, name="speaker", daemon=True).start()

    def stop(self):
        self._stop.set()

    def info(self):
        return {"device": self.device, "status": self.status}

    # -- aplay ------------------------------------------------------------------

    def _open(self, device, rate):
        self._close()
        c = card_of(device)
        if c and self.mixer_percent is not None:
            subprocess.run(["amixer", "-q", "-c", c, "sset", "PCM", f"{self.mixer_percent}%"],
                           capture_output=True, timeout=5)
        self.proc = subprocess.Popen(
            ["aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", str(rate),
             f"--buffer-time={self.buffer_us}"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.device, self.rate = device, rate
        self.status = f"playing on {device}"
        log.info("speaker: %s at %d Hz", device, rate)

    def _close(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()

    def _write(self, data):
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            log.warning("speaker: %s stopped (unplugged or busy); retrying every %.0f s", self.device, CHECK_S)
            self._close()
            self.device = None
            return False

    # -- loop -------------------------------------------------------------------

    def _run(self):
        next_check, warned = 0.0, False
        while not self._stop.is_set():
            try:
                rate, pcm = self._listener.q.get(timeout=CHUNK_S)
            except queue.Empty:
                rate, pcm = self.rate or self.rx.sample_rate, None
            now = time.monotonic()

            if now >= next_check:
                next_check = now + CHECK_S
                want = pick_device(self.devices, present_cards())
                if want is None:
                    if not warned:
                        log.warning("speaker: none of %s present", self.devices)
                        warned = True
                    self.status = "no audio device"
                    self._close()
                    self.device = None
                    continue
                warned = False
                if want != self.device or self.proc is None or self.proc.poll() is not None:
                    try:
                        self._open(want, rate)
                    except OSError as e:
                        log.warning("speaker: cannot start aplay: %s", e)
                        self.status = f"error: {e}"
                        continue
            if self.proc is None:
                continue                           # waiting for the next device check
            if rate != self.rate:
                self._open(self.device, rate)      # NFM <-> WFM

            if pcm is None:
                pcm = bytes(int(rate * CHUNK_S) * 2)
            else:
                pcm = apply_volume(pcm, volume_gain(self.ctl.volume))
            self._write(pcm)
        self._close()
        self.rx.unsubscribe(self._listener)
