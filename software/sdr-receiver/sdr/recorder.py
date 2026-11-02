"""Audio recordings of logged transmissions (driven by HeardLog).

One 16-bit mono WAV per transmission in rec_dir, raw receiver audio (before the volume
knob). Retention: files older than keep_days are deleted, then the oldest until the total
is at most max_mb. Names are 'YYYYmmdd-HHMMSS_<kHz>.wav' (safe to serve as-is).
"""
import logging
import re
import time
import wave
from pathlib import Path

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^\d{8}-\d{6}_\d{4,7}(-\d+)?\.wav$")


class Recorder:
    def __init__(self, rec_dir, keep_days=30, max_mb=1024):
        self.dir = Path(rec_dir)
        self.keep_s = float(keep_days) * 86400
        self.max_bytes = int(float(max_mb) * 1024 * 1024)
        self._w = None          # open wave writer
        self._path = None
        self._rate = None

    # -- one transmission ----------------------------------------------------------

    def start(self, wall, freq_mhz):
        """Begin a new recording; audio arrives through write()."""
        self.abort()
        base = time.strftime("%Y%m%d-%H%M%S", time.localtime(wall)) + f"_{round(freq_mhz * 1000)}"
        p, i = self.dir / f"{base}.wav", 1
        while p.exists():
            p, i = self.dir / f"{base}-{i}.wav", i + 1
        self._path, self._w, self._rate = p, None, None

    def write(self, rate, pcm):
        if self._path is None:
            return
        try:
            if self._w is None:
                self.dir.mkdir(parents=True, exist_ok=True)
                self._w = wave.open(str(self._path), "wb")
                self._w.setnchannels(1)
                self._w.setsampwidth(2)
                self._w.setframerate(rate)
                self._rate = rate
            elif rate != self._rate:
                return              # mode changed mid-file (the log closes the entry anyway)
            self._w.writeframes(pcm)
        except OSError as e:
            log.warning("recorder: cannot write %s: %s", self._path, e)
            self.abort()

    def finish(self, keep):
        """Close the file. keep=False deletes it. Returns the file name or None."""
        p, w = self._path, self._w
        self._path, self._w = None, None
        if w is not None:
            try:
                w.close()
            except OSError:
                pass
        if p is None or w is None:
            return None
        if not keep:
            p.unlink(missing_ok=True)
            return None
        self.prune()
        return p.name

    def abort(self):
        self.finish(keep=False)

    @property
    def active(self):
        return self._path is not None

    # -- retention / lookup ---------------------------------------------------------

    def files(self):
        try:
            return sorted((p for p in self.dir.iterdir() if NAME_RE.match(p.name)), key=lambda p: p.name)
        except FileNotFoundError:
            return []

    def prune(self, now=None):
        now = time.time() if now is None else now
        files = self.files()
        keep = []
        for p in files:
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            if now - st.st_mtime > self.keep_s:
                p.unlink(missing_ok=True)
            else:
                keep.append((p, st.st_size))
        total = sum(s for _, s in keep)
        for p, s in keep:                   # oldest first (names sort by time)
            if total <= self.max_bytes:
                break
            if p == self._path:
                continue
            p.unlink(missing_ok=True)
            total -= s

    def path_of(self, name):
        """Validated path of a stored recording, or None."""
        if not NAME_RE.match(name or ""):
            return None
        p = self.dir / name
        return p if p.is_file() else None
