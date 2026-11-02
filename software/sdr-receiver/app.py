#!/usr/bin/env python3
"""HTTP front end: static UI, JSON control API and the PCM audio stream."""
import atexit
import logging
import queue
import signal
import struct
import sys

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import WSGIRequestHandler

from sdr.config import BASE_DIR, load_settings
from sdr.controller import ControlError, Controller
from sdr.presets import PresetError, PresetStore
from sdr.receiver import TooManyListeners

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


class _QuietPanelPolls(logging.Filter):
    """panel.py polls /api/state 10x a second from localhost: keep those out of the journal."""
    def filter(self, record):
        msg = record.getMessage()
        return not (msg.startswith("127.0.0.1 ") and "GET /api/state " in msg)


logging.getLogger("werkzeug").addFilter(_QuietPanelPolls())

settings = load_settings()
presets = PresetStore(settings)
ctl = Controller(settings, presets)

app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 4096

FRAME_MAGIC = b"PCM1"
GAP_S = 0.25  # pad this much silence when no audio arrives (squelch closed / stopped)


def bad(msg, code=400):
    return jsonify({"error": msg}), code


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def api_config():
    return jsonify({
        "bands": {k: {kk: b[kk] for kk in ("label", "min_mhz", "max_mhz", "mode", "steps_khz")}
                  for k, b in settings["bands"].items()},
        "band_order": settings["band_order"],
        "modes": {k: m["label"] for k, m in settings["modes"].items()},
        "gains": ["auto"] + settings["receiver"]["gains"],
        "squelch_min_db": settings["squelch_min_db"],
        "squelch_max_db": settings["squelch_max_db"],
    })


@app.get("/api/state")
def api_state():
    return jsonify(ctl.state())


@app.post("/api/action")
def api_action():
    b = body()
    action = b.get("action")
    if not isinstance(action, str):
        return bad("missing action")
    value = b.get("value")
    if value is not None and not isinstance(value, (str, int, float)):
        return bad("value must be a string or number")
    try:
        ctl.do(action, value)
    except ControlError as e:
        return bad(str(e))
    return jsonify(ctl.state())


@app.get("/api/presets")
def api_presets():
    return jsonify({"presets": presets.all(), "problems": presets.problems})


@app.post("/api/presets")
def api_preset_add():
    b = body()
    band = b.get("band", ctl.band)
    if band not in settings["bands"]:
        return bad("unknown band")
    name = b.get("name")
    if name is not None and not isinstance(name, str):
        return bad("name must be text")
    try:
        p = presets.add(band, b.get("freq_mhz", ctl.freq_hz / 1e6), (name or "").strip() or None)
    except (PresetError, TypeError, ValueError) as e:
        return bad(str(e))
    ctl.msg(f"Preset added: {p['name']}")
    return jsonify(p)


@app.patch("/api/presets/<pid>")
def api_preset_update(pid):
    b = body()
    enabled = b.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        return bad("enabled must be true/false")
    try:
        return jsonify(presets.update(pid, enabled=enabled, verification=b.get("verification")))
    except PresetError as e:
        return bad(str(e))


@app.delete("/api/presets/<pid>")
def api_preset_delete(pid):
    try:
        presets.remove(pid)
    except PresetError as e:
        return bad(str(e), 404)
    ctl.msg(f"Preset removed: {pid}")
    return jsonify({"ok": True})


@app.get("/audio")
def audio():
    """Endless stream of frames: 'PCM1' + u32 sample_rate + u32 byte_count + s16le mono PCM."""
    try:
        listener = ctl.rx.subscribe(settings["server"]["max_audio_clients"])
    except TooManyListeners:
        return bad("too many audio listeners", 503)

    def frames():
        try:
            while True:
                try:
                    rate, data = listener.q.get(timeout=GAP_S)
                except queue.Empty:
                    rate = ctl.rx.sample_rate
                    data = bytes(int(rate * GAP_S) * 2)
                yield struct.pack("<4sII", FRAME_MAGIC, rate, len(data)) + data
        finally:
            ctl.rx.unsubscribe(listener)

    return Response(frames(), mimetype="application/octet-stream",
                    headers={"Cache-Control": "no-store"})


def shutdown(*_):
    log.info("shutting down")
    ctl.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    atexit.register(ctl.shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    WSGIRequestHandler.timeout = 15  # drop stalled clients instead of tying up threads
    if settings.get("autostart"):
        ctl.start()  # receiver errors are reported in the state, not raised
    srv = settings["server"]
    log.info("listening on http://%s:%d/", srv["host"], srv["port"])
    app.run(host=srv["host"], port=int(srv["port"]), threaded=True, use_reloader=False)
