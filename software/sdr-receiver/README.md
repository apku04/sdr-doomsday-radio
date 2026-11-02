# SDR emergency listening receiver (prototype)

Receive-only. Raspberry Pi 3 Model B + RTL2832U dongle, controlled from a browser on the LAN.
Nothing in this project transmits.

**URL:** `http://192.168.1.23:8080/` (fixed IP on the router; the server binds `0.0.0.0:8080`).
No login: trusted LAN only. Do **not** port-forward 8080 on the router.

## Hardware as found (2026-10-26)

| | |
|---|---|
| Board | Raspberry Pi 3 Model B Rev 1.2, 906 MB RAM, Wi-Fi only (wlan0) |
| OS | Debian 13 trixie (Raspberry Pi OS), kernel 6.12.47, Python 3.13 |
| Dongle | USB 0bda:2838, EEPROM "Realtek / RTL2838UHIDIR / 00000001", **tuner R828D** |
| Power | 2.5 A supply (an earlier supply logged under-voltage when the dongle was replugged) |

The dongle was described as an RTL-SDR Blog V3 (R820T2) but reports an **R828D** with a
generic EEPROM. Frequency accuracy is fine (FM carriers on the 100 kHz grid within 25 kHz).

## Architecture

```
browser ──HTTP──> app.py (Flask)        ──> sdr/controller.py  Controller.do(action, value)
  │                /api/state /api/action    │  validation, band limits, presets, per-band gain/squelch
  │                /api/presets               ├─> sdr/scanner.py   sequential preset scan
  └─fetch──<────── /audio (framed PCM) <──────┴─> sdr/receiver.py  dongle open for the app's lifetime
                                                     ├─ sdr/rtlsdr.py  ctypes binding to system librtlsdr
                                                     └─ sdr/dsp.py     numpy NFM/WFM demodulators
config/settings.json  bands, gains, squelch defaults, scan timing, USB recovery command
config/presets.json   presets (editable)
static/               index.html, app.js, style.css (no external resources; works offline)
```

**Why not rtl_fm.** The first version ran `rtl_fm`, which cannot retune, so every frequency
change closed and re-opened the dongle. On this Pi + dongle about one re-open in a few
hundred timed out (`rtlsdr_write_reg failed with -7`, then librtlsdr's USB reset hung and the
dongle dropped off the bus: `device descriptor read/64, error -110`). It happened three
times, once on the good 2.5 A supply; scanning hit it within 10–20 minutes. The current
receiver opens the dongle once; frequency, gain and mode changes are register writes on the
open device.

* **Receiver** (`sdr/receiver.py`): `rtlsdr_read_async` thread → small queue → DSP thread.
  Stopping the receiver stops streaming but keeps the device open.
  **Watchdog:** no samples for 3 s while running → close, power-cycle USB with
  `sudo -n uhubctl -l 1-1 -p 2 -a cycle -d 3` (`receiver.usb_power_cycle`), reopen, retune.
  On a Pi 3B that cuts power to all four USB ports and the wired Ethernet for 3 s
  (Wi-Fi unaffected). Manual: `sudo uhubctl -l 1-1 -p 2 -a cycle -d 3`.
* **DSP** (`sdr/dsp.py`): tuned 50 kHz (NFM) / 250 kHz (WFM) off-channel so the RTL2832 DC
  spike is outside the filter; mix to 0 Hz, FIR channel filter + decimation, channel power in
  dBFS, FM discriminator, 50 µs de-emphasis (WFM), audio FIR + decimation.
  NFM 240 kS/s → 16 kHz audio, ±6 kHz channel filter. WFM 1.024 MS/s → 32 kHz mono.
* **Controller** (`sdr/controller.py`): every input — web or future GPIO — is a named action
  through `Controller.do()`; all values are validated server-side. No shell from HTTP.
* **Audio**: frames `"PCM1" + u32 rate + u32 length + s16le mono` over a plain HTTP response.
  The browser reads it with `fetch()` streaming and plays via WebAudio: 250 ms jitter buffer,
  chunks dropped if >1 s behind (bounded latency, ~0.4 s total), 300 Hz high-pass in NFM
  (removes repeater CTCSS tones), software volume. Silence is padded while squelched.
  Per-client queue ≈1 s, max 4 audio clients.

### Signal level, squelch, scanning

The meter shows **channel power in dBFS** (after the channel filter). Squelch opens when the
level reaches the threshold (2 dB hysteresis, 0.4 s hang). Slider far left = off.

Activity means *RF power above the threshold*. It does **not** mean speech was decoded:
a carrier, interference or a DMR/C4FM signal opens it too.

Measured noise floors here (empty channels, stock antenna):

| | gain 29.7 | gain 40.2 | gain 49.6 |
|---|---|---|---|
| 2 m (144–146) | −41 dBFS | **−30 dBFS** | −24 dBFS |
| 70 cm (430–440) | −60 dBFS | **−57 dBFS** | −42 dBFS |

Defaults (gain 40.2): squelch **−25 dBFS (2 m)**, **−52 dBFS (70 cm)**, off for FM broadcast.
FM broadcast gain 22.9 dB (at 40.2 the strongest station clipped the ADC).

**2 m is noisy here:** the 2 m floor is flat across the band and ~27 dB above the 70 cm
floor, rising 1:1 with gain — external broadband noise or overload entering the front end,
not receiver noise. 2 m sensitivity is therefore poor. Test: unscrew the antenna and watch
the meter; then try the dongle on a USB extension away from the Pi, a different power
supply, an FM band-stop filter.

**Scan**: enabled analogue presets in band order; each hop retunes, waits `settle_s` (0.3)
+ `dwell_s` (0.5) for the squelch — ≈0.8 s per channel. On activity it stays; quiet for
`hang_s` + `hold_s` (2 + 5 s) → resumes. `hold_s: 0` = stay until *Skip / resume*.
Manual tuning stops the scan. Scanning requires a squelch level on every scanned band.

## Install (already done on this Pi)

```bash
sudo apt-get install --no-install-recommends rtl-sdr python3-flask python3-numpy uhubctl
# Debian's rtl-sdr package does NOT blacklist the DVB-T driver; add it:
sudo tee /etc/modprobe.d/blacklist-rtlsdr.conf <<'EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832_sdr
blacklist rtl2832
blacklist r820t
EOF
sudo reboot
rtl_test -t        # must print "Found Rafael Micro R828D tuner"
```
Copy the project to `/home/pi/sdr-receiver`. No pip, no venv, nothing fetched at runtime.
The `pi` user needs passwordless `sudo` for `uhubctl` (true on this Pi), or set
`receiver.usb_power_cycle` to `null` to disable automatic USB recovery.

## Start / stop

Manual (foreground; Ctrl-C closes the dongle cleanly):
```bash
cd ~/sdr-receiver && python3 -B app.py
```
Optional systemd service (**not installed yet — waiting for approval**):
```bash
sudo cp ~/sdr-receiver/sdr-receiver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sdr-receiver     # start now and on boot
systemctl status sdr-receiver ; journalctl -u sdr-receiver -f
sudo systemctl disable --now sdr-receiver    # stop and remove from boot
```
The app starts with the receiver stopped; press **Start receiver**.

## Presets

Edit `config/presets.json` (restart the app afterwards) or use the UI: click a row to tune,
tick **Scan** to include it, **Save current** stores the tuned frequency, ✕ deletes (click
twice). The file's `_readme` explains the fields. The server enforces:

* frequency inside the preset's band, else the preset is rejected (shown in Messages);
* `digital` presets (DMR, C4FM, D-STAR) can be tuned but never scanned;
* `verification`: `unverified` · `listed` (in a published list, not heard here) ·
  `heard` (confirmed by ear at this receiver). Set via JSON or `PATCH /api/presets/<id>`.

Repeater data: oz1ln.dk "Repeatere i Danmark" (linked from edr.dk), fetched 2026-10-26.
Presets are chosen for a receiver in **Hadsten** (between Randers and Aarhus): analogue
repeaters within ~65 km, plus nearby digital ones (disabled for scan). Names carry the
straight-line distance. Nearest: OZ3RED Randers 145.725, OZ9REZ Randers 434.9875,
OZ9ARHU Aarhus 434.875. The earlier country-wide list is in
`config/presets.json.bak-before-hadsten`. FM broadcast presets come from an `rtl_power`
sweep at this receiver; station names are not identified.

The 70 cm band limit is 430–440 MHz as requested; EDR's Danish 70 cm plan is 432–438 MHz.

## Troubleshooting

| Symptom | Check |
|---|---|
| "RTL-SDR not found" | `lsusb` must show `0bda:2838`; `dmesg | tail`; `sudo uhubctl -l 1-1 -p 2 -a cycle -d 3` |
| `rtlsdr_open failed` / busy | another program holds the dongle: `pgrep -a rtl_`; `lsmod | grep dvb` must be empty |
| `usb_open error -3` | permissions: `/lib/udev/rules.d/60-librtlsdr0.rules`, user in `plugdev`, replug |
| `dmesg`: `error -110`, `unable to enumerate` | dongle wedged on USB. The watchdog power-cycles automatically; else run uhubctl as above or replug |
| ACT light always on / scan stops everywhere | squelch below the noise floor: put the squelch tick ~4–5 dB above the idle level on the meter |
| Scan never stops | squelch too high, or no traffic. *Open squelch (monitor)* plays raw noise to prove the audio path |
| No sound | press **Listen** (browsers need a click). Volume slider; *audio on* pill. NFM with squelch closed is silent by design |
| Audio stutters | Wi-Fi (link was 58 % on 2.4 GHz ch 6). Diagnostics shows overruns/underruns |
| Off-tune | set `receiver.ppm` in settings.json |
| Distorted FM broadcast | gain too high (ADC clipping): lower FM gain |

Logs: stdout (`/tmp/sdr-receiver.log` when started with nohup; journal under systemd).

## Uninstall

```bash
sudo systemctl disable --now sdr-receiver 2>/dev/null
sudo rm -f /etc/systemd/system/sdr-receiver.service && sudo systemctl daemon-reload
rm -rf ~/sdr-receiver /tmp/sdr-receiver*
sudo rm /etc/modprobe.d/blacklist-rtlsdr.conf      # only if you want the DVB driver back
sudo apt-get remove rtl-sdr python3-flask python3-numpy uhubctl && sudo apt-get autoremove
```

## Front panel (`panel.py`, `sdr-panel.service`)

The hand-wired RX-1 panel (wiring: `panel-wiring-sheet.html`, pins = `hardware/lib/contract.py`
`PI_BCM`) is driven by `panel.py`, a separate process that calls this app's HTTP API on
127.0.0.1:8080. A panel fault cannot stop the receiver. Inputs are polled with one `lgpio`
group read per ms (edge callbacks are unreliable on this Pi + trixie); ~8 % of one core.
Pins and mapping: `config/settings.json` → `panel`. OLED: `oled.py` (SH1107 driver), `ui.py` (home screen + menu: band, presets, scan, step, volume, squelch, gain, receiver on/off, contrast, info). Contrast is kept in `config/panel_state.json`. `autostart: true` starts the receiver at boot.

| Control | Action |
|---|---|
| TUNE turn / push | frequency ± step (stops a scan) / next step size |
| VOL turn / push | volume ± 5 % / mute toggle (turning unmutes) |
| SQL turn / push | squelch ± 2 dB (fully left = off) / monitor (squelch open) |
| MENU turn / push | opens the OLED menu; in it: move or change value / select, edit, confirm |
| SCAN · MANUAL | start scan / skip a held channel · stop scan and listen |
| BACK | menu: up one level (main menu → home screen). Idle 20 s → home |
| RANDERS (Q2) · DR P4 (Q1) | preset OZ3RED 145.725 · preset 95.9 MHz (not receivable here: pending the right station) |

| LED | Meaning |
|---|---|
| SCAN | scanning (blinks while holding on a hit); blinks alone = app not reachable |
| SIG | squelch open |
| MAN | listening, not scanning; blinks on receiver error |
| 2M / 70CM / FM | current band; blinks while the receiver is off |

Knob direction: `encoders.<knob>.invert`. If a knob counts once per two clicks set its
`rest` to `[0]` or `[3]` (the A/B state it rests at). Tests: `python -m unittest discover -s tests`.
Logs: `journalctl -u sdr-panel -f`. The pin tester (`~/panel-test`) needs the panel service
stopped first: `sudo systemctl stop sdr-panel`.

## Heard log (`sdr/heardlog.py`)

Every squelch opening of 1 s or more on an analogue channel (scanning or not; FM broadcast and
monitor excluded) is logged with time, channel, length and peak level to `data/heard.jsonl`
(newest 500 kept, survives reboots). View: OLED **MENU → HEARD** (push an entry to tune
there; home screen shows `LAST hh:mm CALLSIGN`), web page `http://<pi>:8080/heard`, JSON
`/api/heard?n=100`. At boot the receiver starts and scans (`autostart`, `autostart_scan`).

Recording (`sdr/recorder.py`, settings `recording`): each logged transmission is also saved
as a 16-bit mono WAV in `data/rec/` (raw audio, before the volume knob; ~2 MB per minute)
and gets a player on `/heard` (main page: **Heard log** button). Files older than 30 days
are deleted, then the oldest until the total is under 1 GB.

## Test results (2026-10-26)

**Verified**
* Dongle detected; DVB driver blacklisted; tuning accurate on FM broadcast.
* **Browser audio on the Windows PC: FM broadcast 104.0 MHz music heard by the owner.**
* API from the PC, twice in a row, 23/23 checks: WFM/NFM streams (32/16 kHz), rejects
  out-of-band / NaN / text frequencies, band edge, bad squelch/gain/step/band/action;
  digital presets cannot be scanned; scan stop 0.04–0.07 s; manual tune stops scan; stop.
* 500 retunes on one open device: 0 errors. 8-minute scan, ≈520 hops: 0 USB errors,
  0 sample overruns, 0 recoveries, CPU temp 54 °C, no under-voltage.
* CPU: WFM DSP ≈48 % of one core (≈12 % of the Pi); NFM ≈20 %; RAM ≈200 MB used of 906.
* "RTL-SDR not found" is reported when the dongle is absent (tested with the old backend).
* Software USB power cycle (`uhubctl`) revived a wedged dongle (manual run).

**Not verified**
* Hearing amateur 2 m / 70 cm speech: no transmission heard. The 8-minute scan stopped
  4× (3× 433.500, 1× 145.500) — nobody listened, so real traffic vs. noise burst is unknown.
* The watchdog's automatic recovery inside the app (no wedge has happened since).
* Phone browsers; long runs (hours); operation with the internet disconnected (the app
  makes no external requests and the UI loads no external resources, but not tested offline).
* Power consumption (needs a USB power meter).
* systemd service (written, not installed).
