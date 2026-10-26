"use strict";
const $ = (id) => document.getElementById(id);
let cfg = null, st = null, presetList = [];
let dragging = { squelch: false, volume: false };

// ---------- HTTP helpers ----------
async function api(method, url, body) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 6000);
  try {
    const r = await fetch(url, {
      method, signal: ctl.signal, cache: "no-store",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  } finally { clearTimeout(timer); }
}
function flash(text) { const e = $("error"); e.textContent = text; e.hidden = false; setTimeout(() => render(), 4000); }
async function act(action, value) {
  try { st = await api("POST", "/api/action", { action, value }); render(); }
  catch (e) { flash(e.message); }
}

// ---------- audio: framed PCM over fetch -> WebAudio ----------
const audio = { ctx: null, gain: null, hp: null, abort: null, next: 0, on: false, under: 0 };
function volumeToGain(v) { return Math.pow(v / 100, 2) * 2; }

async function startAudio() {
  if (!audio.ctx) {
    audio.ctx = new AudioContext();
    audio.hp = audio.ctx.createBiquadFilter(); audio.hp.type = "highpass";
    audio.gain = audio.ctx.createGain();
    audio.hp.connect(audio.gain); audio.gain.connect(audio.ctx.destination);
  }
  await audio.ctx.resume();
  applyAudioSettings();
  audio.on = true; audio.next = 0;
  audio.abort = new AbortController();
  setAudioState("connecting");
  try {
    const r = await fetch("/audio", { signal: audio.abort.signal, cache: "no-store" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    setAudioState("audio on", true);
    const reader = r.body.getReader();
    let buf = new Uint8Array(0);
    for (;;) {
      const { value, done } = await reader.read();
      if (done) throw new Error("stream ended");
      const merged = new Uint8Array(buf.length + value.length);
      merged.set(buf); merged.set(value, buf.length); buf = merged;
      let off = 0;
      while (buf.length - off >= 12) {
        const dv = new DataView(buf.buffer, buf.byteOffset + off, 12);
        if (dv.getUint32(0, false) !== 0x50434d31) throw new Error("bad audio frame");
        const rate = dv.getUint32(4, true), n = dv.getUint32(8, true);
        if (buf.length - off - 12 < n) break;
        playChunk(buf.subarray(off + 12, off + 12 + n), rate);
        off += 12 + n;
      }
      buf = buf.slice(off);
    }
  } catch (e) {
    if (audio.on) {
      setAudioState("audio lost: " + e.message, false, true);
      setTimeout(() => { if (audio.on) startAudio(); }, 2000);
    }
  }
}
function stopAudio() {
  audio.on = false;
  if (audio.abort) audio.abort.abort();
  setAudioState("audio off");
}
function playChunk(bytes, rate) {
  const n = bytes.length >> 1;
  if (!n) return;
  const ab = audio.ctx.createBuffer(1, n, rate);
  const ch = ab.getChannelData(0);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.length);
  for (let i = 0; i < n; i++) ch[i] = dv.getInt16(2 * i, true) / 32768;
  const now = audio.ctx.currentTime;
  if (audio.next < now + 0.02) { audio.next = now + 0.25; audio.under++; } // (re)buffer 250 ms
  if (audio.next - now > 1.0) return; // too far behind: drop to keep latency bounded
  const src = audio.ctx.createBufferSource();
  src.buffer = ab; src.connect(audio.hp); src.start(audio.next);
  audio.next += ab.duration;
}
function applyAudioSettings() {
  if (!audio.ctx || !st) return;
  audio.gain.gain.value = volumeToGain(st.volume);
  // NFM: cut sub-audible CTCSS tones (67-254 Hz) that repeaters send
  audio.hp.frequency.value = st.mode === "nfm" ? 300 : 20;
}
function setAudioState(text, on, err) {
  const e = $("audioState");
  e.textContent = text; e.className = "pill " + (err ? "err" : on ? "run" : "off");
  $("btnListen").textContent = audio.on ? "Mute (stop audio)" : "Listen";
  $("btnListen").classList.toggle("on", !!audio.on);
}

// ---------- rendering ----------
function fmtMHz(f) { return f.toFixed(4); }
function render() {
  if (!st || !cfg) return;
  const scan = st.scan;
  const pill = $("rxState");
  if (st.error) { pill.textContent = "ERROR"; pill.className = "pill err"; }
  else if (scan.active) { pill.textContent = "SCAN " + scan.state.toUpperCase(); pill.className = "pill scan"; }
  else if (st.running) { pill.textContent = "RECEIVING"; pill.className = "pill run"; }
  else { pill.textContent = st.want_running ? "STARTING" : "STOPPED"; pill.className = "pill off"; }
  $("actLed").className = "led" + (st.squelch_open ? " on" : "");
  $("scanLed").className = "led" + (scan.active ? " amber" : "");

  $("bandLabel").textContent = `${st.band_label} · ${cfg.bands[st.band].min_mhz}–${cfg.bands[st.band].max_mhz} MHz`;
  $("freq").textContent = fmtMHz(st.freq_mhz);
  $("mode").textContent = cfg.modes[st.mode] || st.mode;
  const p = st.preset;
  $("presetName").textContent = p ? p.name : "manual frequency";
  const v = $("verif");
  if (p) {
    v.hidden = false;
    v.textContent = p.digital ? `${p.digital} digital` : p.verification;
    v.className = "tag " + (p.digital ? "digital" : p.verification);
  } else v.hidden = true;
  const err = $("error");
  err.hidden = !st.error; err.textContent = st.error || "";

  $("btnPower").textContent = st.want_running ? "Stop receiver" : "Start receiver";
  $("btnPower").classList.toggle("on", st.want_running);

  for (const b of $("bands").children) b.classList.toggle("sel", b.dataset.band === st.band);
  const steps = cfg.bands[st.band].steps_khz;
  const stepSel = $("step");
  if (stepSel.dataset.band !== st.band) {
    stepSel.innerHTML = steps.map((s) => `<option value="${s}">${s} kHz</option>`).join("");
    stepSel.dataset.band = st.band;
  }
  stepSel.value = String(st.step_khz);
  $("bandRange").textContent = `Allowed: ${cfg.bands[st.band].min_mhz.toFixed(3)} – ${cfg.bands[st.band].max_mhz.toFixed(3)} MHz`;

  $("btnScan").hidden = scan.active;
  $("btnResume").disabled = !scan.active;
  $("btnStopScan").hidden = !scan.active;
  let info = scan.active
    ? `${scan.state} · ${scan.index}/${scan.total} · ${scan.preset ? fmtMHz(scan.preset.freq_mhz) + " " + scan.preset.name : ""} · hits ${scan.hits}`
    : "off";
  if (scan.last_hit) info += ` · last: ${fmtMHz(scan.last_hit.freq_mhz)} at ${new Date(scan.last_hit.t * 1000).toLocaleTimeString()}`;
  $("scanInfo").textContent = info + ` · dwell ${scan.dwell_s}s, hold ${scan.hold_s}s`;

  const sqTxt = st.squelch_db === null ? "off" : `${st.squelch_db} dBFS`;
  if (!dragging.squelch) $("squelch").value = st.squelch_db === null ? cfg.squelch_min_db : st.squelch_db;
  $("sqVal").textContent = st.monitor ? `${sqTxt} (OPEN – monitor)` : sqTxt;
  renderMeter();
  $("btnMonitor").classList.toggle("on", st.monitor);
  $("btnMonitor").textContent = st.monitor ? "Close squelch" : "Open squelch (monitor)";
  if (!dragging.volume) $("volume").value = st.volume;
  $("volVal").textContent = st.volume + "%";
  if (document.activeElement !== $("gain")) $("gain").value = String(st.gain);
  applyAudioSettings();

  for (const tr of $("presets").tBodies[0].rows) tr.classList.toggle("cur", p && tr.dataset.id === p.id);
  $("messages").innerHTML = st.messages.slice().reverse().map((m) =>
    `<li class="${m.level}">${new Date(m.t * 1000).toLocaleTimeString()} ${esc(m.text)}</li>`).join("");
  const d = st.diag;
  $("diag").textContent = `device open: ${st.device_open}   blocks: ${d.blocks}   overruns: ${d.overruns}   USB recoveries: ${d.recoveries}\n` +
    `audio listeners: ${st.listeners}   browser underruns: ${audio.under}\nindicators: ${JSON.stringify(st.indicators)}\n${d.log.join("\n")}`;
}
// Signal meter: channel power in dBFS on a -90..0 scale, with the squelch threshold marked.
function renderMeter() {
  const lo = cfg.squelch_min_db, hi = cfg.squelch_max_db, pct = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
  const lv = st.running ? st.level_db : null;
  $("meterBar").style.width = lv === null ? "0%" : pct(lv) + "%";
  $("meterBar").className = st.squelch_open ? "open" : "";
  $("meterSq").hidden = st.squelch_db === null;
  if (st.squelch_db !== null) $("meterSq").style.left = pct(st.squelch_db) + "%";
  $("levelVal").textContent = lv === null ? "—" : `${lv.toFixed(1)} dBFS`;
}
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

async function loadPresets() {
  const data = await api("GET", "/api/presets");
  presetList = data.presets;
  const order = cfg.band_order;
  presetList.sort((a, b) => order.indexOf(a.band) - order.indexOf(b.band) || a.freq_mhz - b.freq_mhz);
  $("presets").tBodies[0].innerHTML = presetList.map((p) => `
    <tr data-id="${esc(p.id)}" title="${esc(p.source + (p.notes ? " — " + p.notes : ""))}">
      <td class="f">${fmtMHz(p.freq_mhz)}</td>
      <td class="n">${esc(p.name)}</td>
      <td><span class="tag ${p.digital ? "digital" : p.verification}">${esc(p.digital ? p.digital : p.verification)}</span></td>
      <td><input type="checkbox" ${p.enabled ? "checked" : ""} ${p.digital ? "disabled" : ""}></td>
      <td><button class="del" title="Delete preset">✕</button></td>
    </tr>`).join("");
  render();
}

// ---------- wiring ----------
function wire() {
  $("btnPower").onclick = () => act(st && st.want_running ? "stop" : "start");
  $("btnListen").onclick = () => (audio.on ? stopAudio() : startAudio());
  $("bands").innerHTML = cfg.band_order.map((b) => `<button data-band="${b}">${esc(cfg.bands[b].label)}</button>`).join("");
  $("bands").onclick = (e) => { const b = e.target.dataset.band; if (b) act("band", b); };
  $("tuneUp").onclick = () => act("tune_up");
  $("tuneDown").onclick = () => act("tune_down");
  $("step").onchange = (e) => act("step", parseFloat(e.target.value));
  $("freqForm").onsubmit = (e) => {
    e.preventDefault();
    const v = $("freqIn").value.trim().replace(",", ".");
    if (!/^\d{2,4}(\.\d{0,6})?$/.test(v)) return flash("Enter a frequency in MHz, e.g. 145.6375");
    act("freq", v); $("freqIn").value = "";
  };
  $("btnScan").onclick = () => act("scan_start");
  $("btnStopScan").onclick = () => act("scan_stop");
  $("btnResume").onclick = () => act("scan_resume");
  $("btnMonitor").onclick = () => act("monitor");
  for (const k of ["squelch", "volume"]) {
    const el = $(k);
    el.oninput = () => { dragging[k] = true;
      (k === "squelch" ? $("sqVal") : $("volVal")).textContent = k === "volume" ? el.value + "%" : (+el.value <= cfg.squelch_min_db ? "off" : el.value + " dBFS");
      if (k === "volume" && st) { st.volume = +el.value; applyAudioSettings(); } };
    el.onchange = () => { dragging[k] = false; act(k, parseInt(el.value, 10)); };
  }
  $("squelch").min = cfg.squelch_min_db; $("squelch").max = cfg.squelch_max_db;
  $("gain").innerHTML = cfg.gains.map((g) => `<option value="${g}">${g === "auto" ? "auto (AGC)" : g + " dB"}</option>`).join("");
  $("gain").onchange = (e) => act("gain", e.target.value === "auto" ? "auto" : parseFloat(e.target.value));

  $("presets").onclick = async (e) => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return;
    const id = tr.dataset.id;
    if (e.target.matches("input[type=checkbox]")) {
      try { await api("PATCH", `/api/presets/${encodeURIComponent(id)}`, { enabled: e.target.checked }); }
      catch (err) { flash(err.message); }
      return loadPresets();
    }
    if (e.target.matches("button.del")) {
      const p = presetList.find((x) => x.id === id);
      if (!e.target.dataset.armed) { e.target.dataset.armed = "1"; e.target.textContent = "sure?"; return; }
      try { await api("DELETE", `/api/presets/${encodeURIComponent(id)}`); } catch (err) { flash(err.message); }
      return loadPresets();
    }
    act("preset", id);
  };
  $("addForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("POST", "/api/presets", { name: $("addName").value }); $("addName").value = ""; }
    catch (err) { flash(err.message); }
    loadPresets();
  };
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select")) return;
    if (e.key === "ArrowUp") { e.preventDefault(); act("tune_up"); }
    if (e.key === "ArrowDown") { e.preventDefault(); act("tune_down"); }
  });
}

async function poll() {
  try { st = await api("GET", "/api/state"); render(); }
  catch (e) { $("rxState").textContent = "NO CONNECTION"; $("rxState").className = "pill err"; }
  setTimeout(poll, st && st.scan.active ? 400 : 800);
}

(async function init() {
  for (;;) {
    try { cfg = await api("GET", "/api/config"); break; }
    catch { $("rxState").textContent = "NO CONNECTION"; await new Promise((r) => setTimeout(r, 2000)); }
  }
  wire();
  await loadPresets();
  poll();
})();
