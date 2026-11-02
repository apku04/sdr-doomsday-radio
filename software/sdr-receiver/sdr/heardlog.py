"""Passive log of what the receiver heard: every squelch opening on an analogue channel.

A watcher thread samples the receiver 5x a second. A transmission starts when the squelch
opens (Receiver.activity(): running, squelch set, open; monitor excluded) and ends after
GAP_S without activity or when the frequency changes (scanner hop, manual tuning).
Openings shorter than min_s are noise blips and are not logged. Entries are appended to
a JSON-lines file and the newest max_entries are kept in memory.
"""
import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

GAP_S = 1.5             # silences shorter than this belong to the same transmission
POLL_S = 0.2


class HeardLog:
    def __init__(self, path, min_s=1.0, max_entries=500):
        self.path = Path(path)
        self.min_s, self.max_entries = float(min_s), int(max_entries)
        self.entries = []
        self.cur = None         # open transmission
        self._lock = threading.Lock()
        self._load()

    # -- persistence ------------------------------------------------------------

    def _load(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        for ln in lines[-self.max_entries:]:
            try:
                self.entries.append(json.loads(ln))
            except ValueError:
                continue
        if len(lines) > 2 * self.max_entries:      # compact now and then
            self._rewrite()

    def _rewrite(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.entries), encoding="utf-8")
        tmp.replace(self.path)

    def _append(self, e):
        self.entries.append(e)
        del self.entries[:-self.max_entries]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError as ex:
            log.warning("heard log: cannot write %s: %s", self.path, ex)

    # -- detection (pure: driven by update) --------------------------------------

    def update(self, now, wall, active, where, level_db):
        """now: monotonic s, wall: epoch s, active: squelch open on a logged channel,
        where: {"band","freq_mhz","name"} of the current channel. Returns a closed entry or None."""
        closed = None
        with self._lock:
            c = self.cur
            if c and (where is None or where["freq_mhz"] != c["freq_mhz"] or now - c["last"] > GAP_S):
                closed = self._close(c)
                c = self.cur = None
            if active and where:
                if c is None:
                    c = self.cur = {"t": wall, "start": now, "last": now, "peak_db": level_db,
                                    **{k: where[k] for k in ("band", "freq_mhz", "name")}}
                c["last"] = now
                if level_db is not None and (c["peak_db"] is None or level_db > c["peak_db"]):
                    c["peak_db"] = level_db
        return closed

    def _close(self, c):
        dur = c["last"] - c["start"]
        if dur < self.min_s:
            return None
        e = {"t": round(c["t"], 1), "band": c["band"], "freq_mhz": c["freq_mhz"], "name": c["name"],
             "dur_s": round(dur, 1), "peak_db": None if c["peak_db"] is None else round(c["peak_db"], 1)}
        self._append(e)
        return e

    # -- reading ----------------------------------------------------------------

    def recent(self, n=100):
        """Newest first. The transmission in progress (if any) is included with live=True."""
        with self._lock:
            out = list(reversed(self.entries[-n:]))
            c = self.cur
            if c and c["last"] - c["start"] >= self.min_s:
                out.insert(0, {"t": round(c["t"], 1), "band": c["band"], "freq_mhz": c["freq_mhz"],
                               "name": c["name"], "dur_s": round(c["last"] - c["start"], 1),
                               "peak_db": c["peak_db"], "live": True})
        return out[:n]

    def summary(self, wall):
        today = time.localtime(wall)[:3]
        with self._lock:
            n = sum(1 for e in self.entries if time.localtime(e["t"])[:3] == today)
        last = self.recent(1)
        return {"today": n, "last": last[0] if last else None}

    # -- watcher ------------------------------------------------------------------

    def watch(self, ctl):
        def where():
            st = ctl.rx
            if not st.running:
                return None
            p = None
            if ctl.preset_id:
                try:
                    p = ctl.presets.get(ctl.preset_id)
                except Exception:  # noqa: BLE001 - preset deleted meanwhile
                    p = None
            return {"band": ctl.band, "freq_mhz": ctl.freq_hz / 1e6,
                    "name": p["name"] if p else f"{ctl.freq_hz / 1e6:.4f} MHz"}

        def run():
            while True:
                try:
                    w = where()
                    active = bool(w) and ctl.rx.activity() and not ctl.monitor
                    e = self.update(time.monotonic(), time.time(), active, w, ctl.rx.level_db)
                    if e:
                        log.info("heard %s %.4f MHz for %.0f s", e["name"], e["freq_mhz"], e["dur_s"])
                except Exception:  # noqa: BLE001 - the log must never take the receiver down
                    log.exception("heard log watcher")
                time.sleep(POLL_S)
        threading.Thread(target=run, name="heardlog", daemon=True).start()
