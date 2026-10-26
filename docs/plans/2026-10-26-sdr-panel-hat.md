# SDR Panel + HAT + Box Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Order-ready HAT + panel PCBs, printable panel/box, and panel software so the receiver works by knobs, buttons and OLED with no network.

**Architecture:** One Python "circuit definition" per board drives both a KiCad schematic (symbols + net labels) and a KiCad PCB (footprints + nets, placed by script, routed by Freerouting, checked by `kicad-cli` DRC/ERC). Part positions are exported as JSON and consumed by a FreeCAD script that cuts the panel holes, so panel and PCB come from one set of coordinates. The panel software is a pure state machine (events → controller actions, state → screen model) with thin hardware drivers, added to the existing app.

**Tech Stack:** KiCad 9.0.7 (pcbnew Python, kicad-cli), Freerouting 2.x (portable JRE 21), FreeCAD 1.0 (FreeCADCmd), Python 3.13 on the Pi (numpy, Flask, smbus2, gpiozero/lgpio, Pillow — all apt), pytest.

**Spec:** `docs/2026-10-26-sdr-panel-hat-design.md`

## Global Constraints

- Hand-soldered bare boards: 0805+ passives/LEDs, SOIC ICs, THT encoders/switches/headers, 2-layer, JLCPCB.
- 3.3 V logic on every GPIO. Panel powered from Pi 3V3.
- Front panel 160 × 90 mm; every printed part ≤ 256 mm (Bambu P1S).
- HAT 65 × 56.5 mm, Pi HAT hole pattern (58 × 49 mm, M2.5, 3.5 mm from edges).
- Receive only. No pip on the Pi: apt packages only (or vendored code).
- Avoid GPIO 0/1 (ID EEPROM), 2/3 except I2C, 4, 7–11 (SPI enabled), 14/15 (UART), 18–21 (I2S).
- SD writes only on change (debounced ≥ 60 s) and at shutdown.

## Interface contract (both boards and the software use exactly this)

Ribbon J_PANEL, 2×10 IDC, pin: 1 3V3 · 2 3V3 · 3 GND · 4 GND · 5 SDA · 6 SCL · 7 GND · 8 PANEL_INT ·
9 ENC_VOL_A · 10 ENC_VOL_B · 11 ENC_SQL_A · 12 ENC_SQL_B · 13 GND · 14 GND · 15 ENC_TUNE_A ·
16 ENC_TUNE_B · 17 ENC_MENU_A · 18 ENC_MENU_B · 19 GND · 20 GND.

Pi BCM: ENC_VOL A/B 5/6 · ENC_SQL A/B 12/13 · ENC_TUNE A/B 22/23 · ENC_MENU A/B 24/25 ·
PANEL_INT 17 (active low, open-drain, 10 k pull-up on panel) · LNA_EN 27 (high = on) · spare 16, 26.

I2C bus 1: MCP23017 0x20 (panel) · SH1107 OLED 0x3C (panel) · INA219 0x40 (HAT) · DS3231 0x68 (HAT).

MCP23017 (GPA7/GPB7 are output-only on current silicon):
GPA0 BTN_SCAN · GPA1 BTN_MANUAL · GPA2 BTN_Q1 · GPA3 BTN_Q2 · GPA4 BTN_BACK · GPA5 SW_VOL · GPA6 SW_SQL ·
GPA7 LED_PWR(out) · GPB0 SW_TUNE · GPB1 SW_MENU · GPB2 LED_SCAN · GPB3 LED_SIG · GPB4 LED_MAN ·
GPB5 LED_2M · GPB6 LED_70CM · GPB7 LED_FM. Inputs active-low with internal pull-ups; LEDs active-high.

## Review Focus

1. Fast encoder spins (5+ detents in 100 ms) must not lose or reverse steps → test in Task 6 (`test_fast_spin_counts_all_steps`).
2. Button held at boot / bouncing contacts must not fire repeated actions → Task 7 (`test_bounce_yields_single_press`, `test_long_press_not_also_short`).
3. Panel absent or I2C error (ribbon unplugged) must not crash the app or the web UI → Task 7 (`test_panel_missing_degrades`).
4. Power-monitor reads garbage or chip missing → OLED shows "--" not a crash → Task 8 (`test_ina_missing_shows_dashes`).
5. Persisted state file corrupt/missing → boot uses defaults → Task 9 (`test_corrupt_state_uses_defaults`).

---

## Part 1 — Electronics

### Task 1: Toolchain + circuit-definition framework

**Files:** Create `hardware/lib/circuit.py`, `hardware/lib/kicad_sch.py`, `hardware/lib/kicad_pcb.py`, `hardware/lib/route.py`, `hardware/tests/test_framework.py`; tools in `tools/` (git-ignored).

**Interfaces – Produces:**
- `Part(ref:str, symbol:str, footprint:str, value:str, pins:dict[str,str], lcsc:str="", side:str="F")` (pins: pin number → net name)
- `Circuit(name).add(part) ; .nets() -> dict[str, list[(ref,pin)]]`
- `write_schematic(circuit, path)` — symbols from KiCad libs, one net label per connected pin, grid layout by part group.
- `build_pcb(circuit, outline_mm:(w,h), placements:dict[ref,(x,y,rot,side)], holes:list, path)`
- `autoroute(pcb_path) -> routed_path` (DSN export → Freerouting → SES import), `run_drc(path) -> list[str]`, `run_erc(path) -> list[str]`, `export_fab(pcb_path, outdir)` (Gerbers+drill zip, BOM csv, positions csv, PDF schematic).

- [ ] Step 1: download Temurin JRE 21 (zip) and Freerouting 2.x jar into `tools/`; verify `java -version` and `--help`.
- [ ] Step 2: test `test_netlist_roundtrip`: a 3-part circuit → schematic → `kicad-cli sch export netlist` → nets equal `circuit.nets()`.
- [ ] Step 3: test `test_pcb_nets_match`: same circuit → PCB → every pad's net equals the circuit net.
- [ ] Step 4: implement; run both tests; commit.

### Task 2: HAT board

**Files:** Create `hardware/hat/hat.py` (circuit + placement), outputs in `hardware/hat/out/`.

Circuit (values pinned): J1 2×20 socket on B side; J2 2×10 IDC box header; J3 2-pin screw terminal 5.00 mm (5 V in); F1 polyfuse 1812 3 A hold; Q1 AO4407A P-FET reverse-protection (gate 10 k to GND, 5.6 V zener G-S); R_SHUNT 0.02 Ω 2512 1 %; U1 INA219AID (SOIC-8, A0=A1=GND → 0x40); U2 DS3231SN (SOIC-16W) + CR2032 holder, 100 nF; LNA switch: Q2 2N7002 (gate ← GPIO27 via 1 k, 100 k pull-down) driving Q3 AO3401A high side (10 k G-S), F2 PTC 0.5 A 1206, J4 JST-XH 2-pin LNA out; LED_5V (green 0805 + 1 k) on the 5 V rail; decoupling 100 nF + 10 µF 0805 on 5 V and 3V3; 5 V net feeds J1 pins 2 and 4.

- [ ] Step 1: test `test_hat_contract`: J2 pin→net and J1 BCM pins equal the Interface contract table.
- [ ] Step 2: build schematic + PCB (65 × 56.5, HAT holes, J1 at the HAT-standard position), autoroute (power nets ≥ 1.0 mm), add GND pours both sides.
- [ ] Step 3: ERC = 0 errors, DRC = 0 errors with JLCPCB rules (0.15 mm track/space, 0.3 mm drill); export fab files; render PNG of both sides; commit.

### Task 3: Panel board

**Files:** Create `hardware/panel/panel.py`, `hardware/panel/layout.json` (front-panel coordinates, mm, origin = panel top-left); outputs in `hardware/panel/out/`.

Circuit: U1 MCP23017-E/SO (A0–A2 GND, RESET 10 k to 3V3 + 100 nF), 4 × EC11E encoder with switch (THT), 5 × 12 mm tactile THT, 7 × 0805 LED + 0805 resistor (1 k for red/green/amber, 150 Ω for white/blue), encoder A/B: 10 k pull-up + 10 nF to GND each, INT 10 k pull-up, OLED 1×4 2.54 mm socket (GND, VCC, SCL, SDA), J1 2×10 IDC box header, 4 × M3 holes, decoupling 100 nF + 10 µF.

layout.json (from the approved v4 mockup; panel 160 × 90): Volume (19,24) Ø20 · Squelch (19,57) Ø20 · Tune (104,32) Ø30 · Menu (141,24) Ø20 · OLED window centre (57,30) 30×30 · LEDs PWR/SCAN/SIG/MAN (45/53/61/69, 52), 2M/70/FM (49/57/65, 62) · Scan (46,78) · Manual (62,78) · Q1 (96,70) · Q2 (114,70) · Back (141,52) · phones jack (10,81) (panel-mounted, not on PCB).

- [ ] Step 1: test `test_panel_contract` (J1 pinout, MCP pin map = contract) and `test_parts_at_layout` (every control's footprint centre = layout.json + board offset, ±0.05 mm).
- [ ] Step 2: build, autoroute, pours, ERC/DRC = 0, export fab + `out/positions.json`; commit.

**OLED module dimensions must be measured by the owner before ordering** (module outline, hole positions, header position relative to the glass). Until then the footprint uses the common 1.5" SH1107 module assumption and is marked on silkscreen.

## Part 2 — Panel software (in `software/sdr-receiver`)

### Task 4: Controller additions

**Files:** Modify `sdr/controller.py`; Test `tests/test_controller_panel.py`.

Produces actions: `channel_next`, `channel_prev` (non-digital presets, band order then freq, wraps), `toggle_manual` (state `manual: bool`), `quick` (value 1|2 → preset id from `settings.panel.quick`), `mute` (toggle `muted: bool`), `lna` (value bool, calls an injected `lna_setter`), `volume_step` (±1, clamp 0–100 by 5), `squelch_step` (±1 dB, clamp to min/max), `stay` (stop scan, keep channel). State adds `manual`, `muted`, `lna`, `channel_index`, `channel_count`.

- [ ] Tests: `test_channel_next_wraps_and_skips_digital`, `test_quick_unknown_preset_errors`, `test_squelch_step_clamps`, `test_stay_stops_scan_keeps_freq`; implement; pass; commit.

### Task 5: Hardware drivers with fakes

**Files:** Create `sdr/panel/__init__.py`, `sdr/panel/mcp23017.py`, `sdr/panel/sh1107.py`, `sdr/panel/ina219.py`, `sdr/panel/encoders.py`, `sdr/panel/fakes.py`; Test `tests/test_drivers.py`.

Produces: `Mcp23017(bus, addr=0x20)` `.configure()`, `.read_inputs() -> int (14-bit, bit set = pressed)`, `.set_leds(dict[name,bool])`; `Sh1107(bus, addr=0x3C)` `.show(image: PIL.Image 128×128 mode "1")`, `.contrast(v)`; `Ina219(bus, addr=0x40, shunt_ohm=0.02)` `.read() -> (volts, amps) | None`; `Encoders(pins: dict[name,(a,b)], on_step: Callable[[str,int],None])`. Bus = smbus2-compatible; fakes record writes.

- [ ] Tests: `test_mcp_config_bytes` (IODIRA=0x7F, IODIRB=0x03, GPPU A=0x7F B=0x03, IOCON mirror+odr), `test_mcp_led_bits` (LED_FM → GPB7), `test_sh1107_page_writes` (128 cols × 16 pages), `test_ina219_scaling` (raw shunt 0x0FA0 @ 0.02 Ω → 2.0 A); implement; commit.

### Task 6: Encoder decoding

**Files:** `sdr/panel/encoders.py`; Test `tests/test_encoders.py`.

Quadrature state table decoder (full-step, 4 transitions per detent) fed by edge callbacks; `feed(name, a, b)` testable without GPIO.

- [ ] Tests: `test_cw_detent_is_plus_one`, `test_ccw_detent_is_minus_one`, `test_bounce_on_one_line_no_step`, `test_fast_spin_counts_all_steps`; implement; commit.

### Task 7: Panel UI state machine

**Files:** `sdr/panel/ui.py`; Test `tests/test_ui.py`.

`PanelUI(ctl, clock)` `.on_encoder(name, steps)`, `.on_buttons(mask, now)`, `.tick(now)`, `.screen() -> Screen` (dataclass: kind main|scan|hit|manual|menu|message, lines, big, meter, squelch_mark), `.leds() -> dict[name,bool]`. Mapping exactly as spec §6. Debounce 30 ms; long press 800 ms; menu timeout 10 s; menu items: Band, Gain, LNA, Step, Brightness, Wi-Fi/IP, Shut down.

- [ ] Tests: one per row of spec §6 table, plus `test_bounce_yields_single_press`, `test_long_press_not_also_short`, `test_menu_times_out`, `test_back_closes_menu`, `test_panel_missing_degrades`; implement; commit.

### Task 8: Rendering + power + panel service loop

**Files:** `sdr/panel/render.py`, `sdr/panel/power.py`, `sdr/panel/service.py`; modify `app.py` (start panel thread if `settings.panel.enabled`); Test `tests/test_render.py`, `tests/test_power.py`.

`render(screen) -> PIL.Image`; `PowerMeter(ina, capacity_wh|None)` `.update(now)`, `.text() -> "5.0V 0.6A 1.2Wh"`; service polls INT/encoders, ticks UI at 20 Hz, redraws OLED on change (max 10 fps), drives LEDs and LNA GPIO.

- [ ] Tests: `test_render_main_has_frequency_text` (golden PNG diff ≤ 1 %), `test_energy_integrates`, `test_ina_missing_shows_dashes`; implement; commit.

### Task 9: Local audio, persistence, boot

**Files:** `sdr/local_audio.py`, `sdr/persist.py`, `sdr-receiver.service`, `sdr-splash.service`; Test `tests/test_persist.py`, `tests/test_local_audio.py`.

Local audio: listener → numpy gain (volume², mute) → `aplay -D <dev>` per configured device (`settings.audio.devices`, default `["default"]`), restart on failure. Persist `{band, freq, preset_id, volume, squelch, gain, manual}` to `config/state.json`, atomic, debounced 60 s + on shutdown; boot restores and auto-starts receiver.

- [ ] Tests: `test_corrupt_state_uses_defaults`, `test_state_written_once_per_debounce`, `test_gain_applied_and_mute`; deploy to Pi; breadboard test with KY-040 + OLED (owner); commit.

## Part 3 — Mechanical

### Task 10: Front panel + box (FreeCAD script)

**Files:** `mechanical/panel_box.py` (FreeCADCmd), outputs `mechanical/out/*.step|*.stl|*.3mf`.

Consumes `hardware/panel/out/positions.json`. Panel 160 × 90 × 3 mm, holes: encoder bushings Ø7.5, buttons Ø per cap, OLED window 30 × 30, LED light-pipe holes Ø2.5, phones jack Ø6.2; raised 0.6 mm labels (text from layout.json); back bosses M3 at PCB holes, standoff = encoder body height. Box: tray with Pi standoffs (HAT pattern), back wall cutouts: USB-C module, panel-mount USB-A extension, LNA 2-pin socket, vents. Every part ≤ 250 mm.

- [ ] Step 1: script check `test_holes_match_positions` (hole centres vs positions.json ±0.05 mm).
- [ ] Step 2: export; render previews; commit.
