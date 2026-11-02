"""OLED screens and menu for the front panel. Pure logic: no I/O.

MENU turn / push opens the menu from the home screen. In the menu: MENU turn moves (or
changes the value being edited), MENU push selects / starts / ends an edit, BACK goes up one
level (and from the main menu home). After IDLE_S without input the home screen returns.

Every input method returns a list of actions: (api_action, value) tuples that panel.py
sends to app.py, or ("local", name, value) for panel-only settings (contrast).
"""
from oled import Frame, text_width

IDLE_S = 20.0
ROW_H = 10
VISIBLE = 11            # menu rows under the title bar

BAND_SHORT = {"vhf": "2M", "uhf": "70CM", "fm": "FM"}
SCAN_TEXT = {"scanning": "SCANNING", "activity": "ON SIGNAL", "holding": "HOLD", "paused": "PAUSED"}


def fmt_freq(mhz):
    s = f"{mhz:.5f}".rstrip("0")
    whole, frac = s.split(".")
    return f"{whole}.{frac.ljust(3, '0')}"


def wrap(text, width, max_lines):
    """Word-wrap to at most max_lines lines (long words are cut)."""
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}" if cur else word
        if len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = word[:width]
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def fmt_step(khz):
    return f"{khz:g}K" if khz < 1000 else f"{khz / 1000:g}M"


def fmt_sq(db):
    return "OFF" if db is None else f"{db:g}"


def fmt_gain(g):
    return "AUTO" if g == "auto" else f"{g:g}"


class Ui:
    MAIN = ["BAND", "PRESETS", "SCAN", "STEP", "VOLUME", "SQUELCH", "GAIN", "RECEIVER", "CONTRAST", "INFO"]

    def __init__(self, config, contrast=8, info=lambda: []):
        self.cfg = config                 # /api/config
        self.contrast = contrast
        self.info = info                  # () -> list of text lines for the INFO page
        self.screen = "home"              # home | main | list | edit | info
        self.sel = 0                      # main menu cursor
        self.top = 0
        self.list = None                  # {"title", "items": [(label, payload)], "sel", "top", "apply"}
        self.edit = None                  # {"item", "value"}
        self.last_input = 0.0
        self.presets = []                 # filled by panel.py before PRESETS opens

    # -- helpers --------------------------------------------------------------

    def _touch(self, now):
        self.last_input = now

    def tick(self, now):
        if self.screen != "home" and now - self.last_input > IDLE_S:
            self.screen, self.edit, self.list = "home", None, None

    def value(self, item, st, muted=False):
        if item == "BAND":
            return BAND_SHORT.get(st["band"], st["band"].upper())
        if item == "SCAN":
            return "ON" if st["scan"]["state"] != "off" else "OFF"
        if item == "STEP":
            return fmt_step(st["step_khz"])
        if item == "VOLUME":
            return "MUTE" if muted else f"{st['volume']}%"
        if item == "SQUELCH":
            return fmt_sq(st["squelch_db"])
        if item == "GAIN":
            return fmt_gain(st["gain"])
        if item == "RECEIVER":
            return "ON" if st["want_running"] else "OFF"
        if item == "CONTRAST":
            return str(self.contrast)
        return ">"

    def _open_list(self, title, items, current, apply):
        sel = next((i for i, (_, p) in enumerate(items) if p == current), 0)
        self.list = {"title": title, "items": items, "sel": sel, "top": max(0, sel - VISIBLE + 1),
                     "apply": apply}
        self.screen = "list"

    @staticmethod
    def _scroll(sel, top, n):
        if sel < top:
            top = sel
        elif sel >= top + n:
            top = sel - n + 1
        return top

    # -- inputs ---------------------------------------------------------------

    def turn(self, d, st, now):
        self._touch(now)
        if self.screen == "home":
            self.screen = "main"
            return []
        if self.screen == "main":
            self.sel = (self.sel + d) % len(self.MAIN)
            self.top = self._scroll(self.sel, self.top, VISIBLE)
        elif self.screen == "list":
            L = self.list
            L["sel"] = max(0, min(len(L["items"]) - 1, L["sel"] + d))
            L["top"] = self._scroll(L["sel"], L["top"], VISIBLE)
        elif self.screen == "edit":
            return self._adjust(d, st)
        return []

    def _adjust(self, d, st):
        item = self.edit["item"]
        if item == "VOLUME":
            v = max(0, min(100, self.edit["value"] + 5 * d))
            self.edit["value"] = v
            return [("volume", v)]
        if item == "SQUELCH":
            lo, hi = self.cfg["squelch_min_db"], self.cfg["squelch_max_db"]
            cur = lo if self.edit["value"] is None else self.edit["value"]
            v = max(lo, min(hi, cur + 2 * d))
            self.edit["value"] = None if v <= lo else v
            return [("squelch", v)]
        if item == "CONTRAST":
            v = max(1, min(15, self.contrast + d))
            self.contrast = self.edit["value"] = v
            return [("local", "contrast", v)]
        return []

    def push(self, st, now):
        self._touch(now)
        if self.screen == "home":
            self.screen = "main"
            return []
        if self.screen == "edit":
            self.screen, self.edit = "main", None
            return []
        if self.screen == "list":
            L = self.list
            label, payload = L["items"][L["sel"]]
            self.screen, self.list = "main", None
            return L["apply"](payload)
        if self.screen == "info":
            self.screen = "main"
            return []
        item = self.MAIN[self.sel]
        if item == "BAND":
            order = self.cfg["band_order"]
            self._open_list("BAND", [(BAND_SHORT.get(b, b.upper()) + "  " + self.cfg["bands"][b]["label"], b)
                                     for b in order], st["band"], lambda b: [("band", b)])
        elif item == "PRESETS":
            items = [(p["name"], p["id"]) for p in self.presets if p["band"] == st["band"]]
            items += [(BAND_SHORT.get(p["band"], "") + " " + p["name"], p["id"])
                      for p in self.presets if p["band"] != st["band"]]
            if not items:
                items = [("(NO PRESETS)", None)]
            cur = (st.get("preset") or {}).get("id")
            self._open_list("PRESETS", items, cur,
                            lambda pid: [("preset", pid)] + ([] if st["want_running"] else [("start", None)])
                            if pid else [])
        elif item == "STEP":
            steps = self.cfg["bands"][st["band"]]["steps_khz"]
            self._open_list("STEP", [(fmt_step(k), k) for k in steps], st["step_khz"], lambda k: [("step", k)])
        elif item == "GAIN":
            gains = self.cfg["gains"]
            self._open_list("GAIN", [(fmt_gain(g), g) for g in gains], st["gain"], lambda g: [("gain", g)])
        elif item == "SCAN":
            return [("scan_toggle", None)]
        elif item == "RECEIVER":
            return [("toggle_power", None)]
        elif item in ("VOLUME", "SQUELCH", "CONTRAST"):
            cur = {"VOLUME": st["volume"], "SQUELCH": st["squelch_db"], "CONTRAST": self.contrast}[item]
            self.edit = {"item": item, "value": cur}
            self.screen = "edit"
        elif item == "INFO":
            self.screen = "info"
        return []

    def back(self, st, now):
        self._touch(now)
        if self.screen in ("list", "edit", "info"):
            self.screen, self.list, self.edit = "main", None, None
        elif self.screen == "main":
            self.screen = "home"
        return []

    # -- drawing --------------------------------------------------------------

    def render(self, st, now, muted=False, message=None):
        f = Frame()
        if st is None:
            f.text(4, 50, "RECEIVER APP")
            f.text(4, 62, "NOT RUNNING")
            f.text(4, 80, "STARTING...")
            return f
        if self.screen == "home":
            self._home(f, st, muted)
        elif self.screen == "info":
            self._title(f, "INFO")
            for i, line in enumerate(self.info()[:VISIBLE]):
                f.text(2, 14 + i * ROW_H, line[:21])
        elif self.screen == "list":
            L = self.list
            self._title(f, L["title"])
            rows = L["items"][L["top"]:L["top"] + VISIBLE]
            for i, (label, _) in enumerate(rows):
                self._row(f, 14 + i * ROW_H, label, "", L["top"] + i == L["sel"])
        else:
            self._title(f, "MENU")
            for i, item in enumerate(self.MAIN[self.top:self.top + VISIBLE]):
                idx = self.top + i
                v = self.value(item, st, muted)
                if self.screen == "edit" and item == self.edit["item"]:
                    v = "<" + v + ">"
                self._row(f, 14 + i * ROW_H, item, v, idx == self.sel)
        if message:
            f.rect(0, 116, 128, 12, False)
            f.hline(0, 116, 128)
            f.text(2, 119, message[:21])
        return f

    def _title(self, f, title):
        f.text(0, 0, title)
        f.hline(0, 9, 128)

    def _row(self, f, y, label, value, selected):
        if selected:
            f.rect(0, y - 1, 128, ROW_H)
        room = 21 - (len(value) + 1 if value else 0)
        f.text(2, y + 1, label[:room], on=not selected)
        if value:
            f.text(126 - text_width(value), y + 1, value, on=not selected)

    def _home(self, f, st, muted):
        scan = st["scan"]["state"]
        band = BAND_SHORT.get(st["band"], st["band"].upper())
        f.text(0, 0, f"{band} {st['mode'].upper()}")
        status = ("ERROR" if st["error"] else SCAN_TEXT.get(scan, "SCANNING") if scan != "off"
                  else "RX" if st["running"] else "OFF")
        f.text(128 - text_width(status[:12]), 0, status[:12])
        f.hline(0, 9, 128)

        freq = fmt_freq(st["freq_mhz"])
        scale = 2 if text_width(freq, 2) <= 128 else 1
        f.text((128 - text_width(freq, scale)) // 2, 16, freq, scale)
        sub = f"MHZ  STEP {fmt_step(st['step_khz'])}"
        f.text((128 - text_width(sub)) // 2, 34, sub)

        p = st.get("preset")
        if p:
            for i, line in enumerate(wrap(p["name"], 21, 2)):
                f.text(0, 48 + 9 * i, line)

        # Signal meter: -90..0 dBFS, squelch threshold as a tick, SIG when open.
        lo, hi = self.cfg["squelch_min_db"], self.cfg["squelch_max_db"]
        x0, w, y = 20, 106, 70
        f.text(0, y + 1, "SIG" if st["squelch_open"] else "LVL")
        f.frame_rect(x0, y, w, 9)
        lvl = st.get("level_db")
        if lvl is not None:
            fill = int((max(lo, min(hi, lvl)) - lo) / (hi - lo) * (w - 4))
            f.rect(x0 + 2, y + 2, fill, 5)
        sq = st["squelch_db"]
        if sq is not None and not st["monitor"]:
            sx = x0 + 2 + int((sq - lo) / (hi - lo) * (w - 4))
            f.rect(sx, y - 2, 1, 13)
            f.rect(sx - 1, y - 3, 3, 1)

        vol = "MUTE" if muted else f"{st['volume']}%"
        f.text(0, 86, f"VOL {vol}")
        sqtxt = "MON" if st["monitor"] else fmt_sq(st["squelch_db"])
        right = f"SQL {sqtxt}"
        f.text(128 - text_width(right), 86, right)
        if scan != "off":
            s = st["scan"]
            f.text(0, 98, f"SCAN {s['index']}/{s['total']}  HITS {s['hits']}")
            hint = "SCAN=SKIP MANUAL=STOP" if scan in ("activity", "holding", "paused") else "MANUAL=STOP"
            f.text(0, 107, hint)
        f.hline(0, 116, 128)
        f.text(2, 119, "MENU: OPTIONS")
