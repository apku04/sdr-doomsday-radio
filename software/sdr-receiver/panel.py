#!/usr/bin/env python3
"""Front-panel driver: knobs, buttons and LEDs of the hand-wired RX-1 panel.

Runs as its own process (sdr-panel.service) next to app.py and talks to it over the local
HTTP API, so a panel fault cannot stop the receiver and GPIO polling does not compete with
the DSP for the GIL. Pins and the control mapping are in config/settings.json "panel".

Inputs are polled (one lgpio group read per millisecond): lgpio edge callbacks are not
reliable on this Pi + trixie (see the panel tester). Needs python3-lgpio.
"""
import json
import logging
import signal
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("panel")

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "config" / "settings.json"

# Quadrature state = (A << 1) | B with raw pin levels (pull-ups: contact closed = 0).
# Clockwise = A changes first: 3 -> 1 -> 0 -> 2 -> 3.
_QSTEP = {(3, 1): 1, (1, 0): 1, (0, 2): 1, (2, 3): 1,
          (3, 2): -1, (2, 0): -1, (0, 1): -1, (1, 3): -1}


class Encoder:
    """Counts one click per detent. rest = the A/B states the knob sits in at a detent:
    (0, 3) for KY-040 / EC11 types that rest alternately at 00 and 11, (3,) or (0,) for
    full-cycle-per-detent types. Bounce gives +1/-1 pairs that cancel; invalid (skipped)
    transitions are ignored."""

    def __init__(self, rest=(0, 3), invert=False):
        self.rest = tuple(rest)
        self.sign = -1 if invert else 1
        self.state = None
        self.acc = 0

    def update(self, a, b):
        """Feed current levels; returns +1 (CW), -1 (CCW) or 0."""
        s = (a << 1) | b
        if self.state is None:
            self.state = s
            return 0
        if s == self.state:
            return 0
        self.acc += _QSTEP.get((self.state, s), 0)
        self.state = s
        if s in self.rest:
            out = 1 if self.acc >= 2 else -1 if self.acc <= -2 else 0
            self.acc = 0
            return out * self.sign
        return 0


class Button:
    """Active-low push contact. Reports 'press' once the level has been stable low for
    debounce_s, and 'long' once it has been held for long_s (then no further events)."""

    def __init__(self, debounce_s=0.02, long_s=None):
        self.debounce_s, self.long_s = debounce_s, long_s
        self.raw, self.since = 1, 0.0
        self.down, self.down_at, self.long_sent = False, 0.0, False

    def update(self, level, now):
        events = []
        if level != self.raw:
            self.raw, self.since = level, now
        stable = now - self.since >= self.debounce_s
        if stable and (self.raw == 0) != self.down:
            self.down = self.raw == 0
            if self.down:
                self.down_at, self.long_sent = now, False
                events.append("press")
            else:
                events.append("release")
        if self.down and self.long_s and not self.long_sent and now - self.down_at >= self.long_s:
            self.long_sent = True
            events.append("long")
        return events


class Api:
    """Minimal client for app.py's JSON API on localhost."""

    def __init__(self, base, timeout=2.0):
        self.base, self.timeout = base.rstrip("/"), timeout

    def _req(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", str(e))
            except ValueError:
                msg = str(e)
            raise ApiError(msg) from None

    def get(self, path):
        return self._req("GET", path)

    def action(self, action, value=None):
        body = {"action": action}
        if value is not None:
            body["value"] = value
        return self._req("POST", "/api/action", body)


class ApiError(Exception):
    pass


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Panel:
    """Turns knob clicks and button presses into receiver actions; drives the LEDs and OLED."""

    MSG_S = 3.0

    def __init__(self, cfg, api, oled=None, info=lambda: [], state_file=None):
        self.cfg, self.api, self.oled, self.info = cfg, api, oled, info
        self.state_file = state_file
        self.config = None          # /api/config
        self.state = None           # last /api/state
        self.ui = None
        self.lock = threading.Lock()
        self.events = []            # ordered: ("turn", knob, clicks) / ("button", name, event)
        self.wake = threading.Event()
        self.muted_volume = None
        self.message, self.message_t = None, -1e9
        self.local = {"contrast": 8}
        if state_file and state_file.exists():
            try:
                self.local.update(json.loads(state_file.read_text(encoding="utf-8")))
            except ValueError:
                pass
        self._last_frame = None

    # -- inputs (called from the GPIO thread: only queue work) --------------

    def on_turn(self, knob, clicks):
        with self.lock:
            last = self.events[-1] if self.events else None
            if last and last[0] == "turn" and last[1] == knob:
                self.events[-1] = ("turn", knob, last[2] + clicks)
            else:
                self.events.append(("turn", knob, clicks))
        self.wake.set()

    def on_button(self, name, event):
        with self.lock:
            self.events.append(("button", name, event))
        self.wake.set()

    # -- worker: sends actions and draws, never blocks the GPIO thread -------

    def take(self):
        with self.lock:
            work, self.events = self.events, []
        return work

    def run_actions(self, stop):
        while not stop.is_set():
            self.wake.wait(0.2)
            self.wake.clear()
            for kind, name, arg in self.take():
                self.handle(kind, name, arg)
            try:
                self.refresh_display(time.monotonic())
            except Exception:  # noqa: BLE001 - a drawing bug must not stop the knobs
                log.exception("display refresh failed")

    def handle(self, kind, name, arg):
        try:
            if kind == "button":
                self.button(name, arg)
            elif arg:
                self.turn(name, arg)
        except ApiError as e:
            log.info("%s: %s", name, e)
            self.flash(str(e))
        except (OSError, ValueError) as e:
            log.warning("%s: receiver not reachable (%s)", name, e)
            self.flash("RECEIVER NOT REACHABLE")

    def flash(self, text):
        self.message, self.message_t = text, time.monotonic()

    def ensure_ui(self):
        if self.config is None:
            self.config = self.api.get("/api/config")
        if self.ui is None:
            from ui import Ui
            self.ui = Ui(self.config, contrast=self.local["contrast"], info=self.info)
        return self.ui

    def _st(self):
        if self.state is None:
            self.state = self.api.get("/api/state")
        return self.state

    def _do(self, action, value=None):
        if action == "volume":
            self.muted_volume = None
        self.state = self.api.action(action, value)
        return self.state

    def run(self, actions):
        for a in actions:
            if a[0] == "local":
                _, key, value = a
                self.local[key] = value
                if key == "contrast" and self.oled:
                    self.oled.contrast(value)
                self.save_local()
            else:
                self._do(*a)

    def save_local(self):
        if self.state_file:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.local), encoding="utf-8")
            tmp.replace(self.state_file)

    def turn(self, knob, n):
        ui = self.ensure_ui()
        st = self._st()
        k = self.cfg["knob_steps"]
        if knob == "tune":
            band = self.config["bands"][st["band"]]
            mhz = st["freq_mhz"] + n * st["step_khz"] / 1000
            mhz = clamp(round(mhz, 5), band["min_mhz"], band["max_mhz"])
            if mhz != st["freq_mhz"]:
                self._do("freq", mhz)
            else:
                self.flash("BAND EDGE")
        elif knob == "volume":
            base = self.muted_volume if self.muted_volume is not None else st["volume"]
            self._do("volume", clamp(base + n * k["volume"], 0, 100))
        elif knob == "squelch":
            lo, hi = self.config["squelch_min_db"], self.config["squelch_max_db"]
            cur = lo if st["squelch_db"] is None else st["squelch_db"]
            self._do("squelch", clamp(cur + n * k["squelch_db"], lo, hi))
        elif knob == "menu":
            step = 1 if n > 0 else -1
            for _ in range(abs(n)):
                self.run(ui.turn(step, self._st(), time.monotonic()))

    def button(self, name, event):
        action = self.cfg["buttons"].get(name, {}).get(event)
        if not action:
            return
        st = self._st()
        if action == "ui:push":
            ui = self.ensure_ui()
            if ui.screen == "main" and ui.MAIN[ui.sel] == "PRESETS":
                ui.presets = self.api.get("/api/presets")["presets"]
            self.run(ui.push(st, time.monotonic()))
        elif action == "ui:back":
            self.run(self.ensure_ui().back(st, time.monotonic()))
        elif action == "mute":
            if self.muted_volume is None:
                v = st["volume"] or 60
                self._do("volume", 0)
                self.muted_volume = v
            else:
                self._do("volume", self.muted_volume)
        elif action == "scan":
            # Like a scanner: start; stopped on a signal -> skip to the next channel; else stop.
            state = st["scan"]["state"]
            if state == "off":
                self._do("scan_start")
            elif state in ("activity", "holding", "paused"):
                self._do("scan_resume")
            else:
                self._do("scan_stop")
        elif action == "manual":
            if st["scan"]["state"] != "off":
                self._do("scan_stop")
            if not self.state["want_running"]:
                self._do("start")
        elif action.startswith("preset:"):
            self._do("preset", action.split(":", 1)[1])
            if not self.state["want_running"]:
                self._do("start")
        else:
            self._do(action)

    # -- outputs --------------------------------------------------------------

    def refresh_display(self, now):
        if self.oled is None:
            return
        try:
            ui = self.ensure_ui()
        except (OSError, ValueError, ApiError):
            ui = None
        msg = self.message if now - self.message_t < self.MSG_S else None
        if ui is None or self.state is None:
            from ui import Ui
            frame = Ui({}).render(None, now)
        else:
            ui.tick(now)
            frame = ui.render(self.state, now, muted=self.muted_volume is not None, message=msg)
        if frame.buf != self._last_frame:
            self._last_frame = bytes(frame.buf)
            self.oled.submit(frame)

    def leds(self, st, blink):
        """Logical LED states from a /api/state snapshot (None = receiver unreachable)."""
        out = {name: False for name in self.cfg["leds"]}
        if st is None:
            out["scan"] = blink    # app down: SCAN blinks alone
            return out
        scanning = st["scan"]["state"] != "off"
        error = bool(st["error"])
        out["scan"] = scanning and (st["scan"]["state"] != "holding" or blink)
        out["sig"] = bool(st["squelch_open"])
        out["man"] = blink if error else (st["want_running"] and not scanning)
        band_led = {"vhf": "2m", "uhf": "70cm", "fm": "fm"}.get(st["band"])
        if band_led:
            out[band_led] = st["want_running"] or blink
        return out


def system_info():
    """Lines for the INFO page."""
    import socket
    import subprocess
    lines = [socket.gethostname()[:21]]
    try:
        ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout.split()
    except (OSError, subprocess.SubprocessError):
        ips = []
    lines += [ip for ip in ips if ":" not in ip][:3] or ["NO NETWORK"]
    lines.append("WEB UI PORT 8080")
    try:
        up = float(Path("/proc/uptime").read_text().split()[0])
        lines.append(f"UP {int(up // 3600)}H {int(up % 3600 // 60)}M")
    except (OSError, ValueError):
        pass
    try:
        t = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        lines.append(f"CPU {t:.0f} C")
    except (OSError, ValueError):
        pass
    return lines


class Gpio:
    """lgpio: inputs claimed as one pulled-up group (one read per poll), outputs one by one."""

    def __init__(self, chip, inputs, outputs):
        import lgpio
        self.lg = lgpio
        self.h = lgpio.gpiochip_open(chip)
        self.inputs = list(inputs)
        lgpio.group_claim_input(self.h, self.inputs, lgpio.SET_PULL_UP)
        self.outputs = list(outputs)
        for g in self.outputs:
            lgpio.gpio_claim_output(self.h, g, 0)

    def read_bits(self):
        """All inputs as one int, bit i = self.inputs[i]."""
        return self.lg.group_read(self.h, self.inputs[0])[1]

    def levels(self, bits):
        return {g: (bits >> i) & 1 for i, g in enumerate(self.inputs)}

    def write(self, gpio, level):
        self.lg.gpio_write(self.h, gpio, int(level))

    def close(self):
        for g in self.outputs:
            try:
                self.lg.gpio_write(self.h, g, 0)
            except Exception:  # noqa: BLE001
                pass
        self.lg.gpiochip_close(self.h)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    cfg = settings["panel"]
    api = Api(f"http://127.0.0.1:{settings['server']['port']}")
    oled = None
    if cfg.get("oled"):
        from oled import Oled
        o = cfg["oled"]
        oled = Oled(o.get("i2c_bus", 1), int(o.get("addr", "0x3C"), 16), o.get("rotate", 0))
    info_cache = {"t": -1e9, "lines": []}

    def info():
        if time.monotonic() - info_cache["t"] > 5:
            info_cache.update(t=time.monotonic(), lines=system_info())
        return info_cache["lines"]

    panel = Panel(cfg, api, oled, info, HERE / "config" / "panel_state.json")
    if oled:
        oled.contrast(panel.local["contrast"])

    encs = {k: (e["a"], e["b"], Encoder(e.get("rest", (0, 3)), e.get("invert", False)))
            for k, e in cfg["encoders"].items()}
    btns = {k: (b["pin"], Button(cfg["debounce_s"], cfg["long_press_s"] if "long" in b else None))
            for k, b in cfg["buttons"].items()}
    leds = cfg["leds"]
    inputs = sorted({p for a, b, _ in encs.values() for p in (a, b)} | {p for p, _ in btns.values()})
    gpio = Gpio(cfg.get("gpiochip", 0), inputs, leds.values())

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threading.Thread(target=panel.run_actions, args=(stop,), name="actions", daemon=True).start()

    def poll_state():
        while not stop.is_set():
            try:
                panel.state = api.get("/api/state")
            except (OSError, ValueError, ApiError):
                panel.state = None
            stop.wait(cfg["state_poll_s"])
    threading.Thread(target=poll_state, name="state", daemon=True).start()

    log.info("panel running: %d inputs, %d LEDs", len(inputs), len(leds))
    period, led_period, next_led = cfg["poll_s"], 0.05, 0.0
    lit, last_bits, btn_check = {}, None, 0.0
    try:
        while not stop.is_set():
            now = time.monotonic()
            bits = gpio.read_bits()
            # Idle fast path: decode only when a pin changed; buttons also need the clock
            # (debounce, long press), which a 10 ms check covers.
            if bits != last_bits or now >= btn_check:
                if bits != last_bits:
                    lv = gpio.levels(bits)
                    for knob, (a, b, enc) in encs.items():
                        d = enc.update(lv[a], lv[b])
                        if d:
                            panel.on_turn(knob, d)
                last_bits, btn_check = bits, now + 0.01
                for name, (pin, btn) in btns.items():
                    for ev in btn.update(lv[pin], now):
                        panel.on_button(name, ev)
            if now >= next_led:
                next_led = now + led_period
                want = panel.leds(panel.state, int(now * 2.5) % 2 == 0)
                for name, on in want.items():
                    if lit.get(name) != on:
                        gpio.write(leds[name], on)
                        lit[name] = on
            time.sleep(period)
    finally:
        gpio.close()
        if oled:
            oled.close()
            time.sleep(0.3)
        log.info("panel stopped")


if __name__ == "__main__":
    main()
