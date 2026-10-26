# SDR emergency receiver: front panel, HAT and box — design

Date: 2026-10-26 · Status: draft for owner review
Builds on: the working receiver app in `pi@192.168.1.23:~/sdr-receiver` (see its README).
Mockup (to scale): `.superpowers/brainstorm/1225-1793036824/content/panel-scale-v4.html`

## 1. Intent

During a **power cut at home**, the owner wants to pick up one box, plug in a **powerbank**,
and — with **no PC, no monitor, no Wi-Fi** — turn a knob, press a button, read a small
display and **hear the local repeater** (Randers / Aarhus, from Hadsten) or DR P4.

The owner is new to amateur radio, experienced in electronics (EasyEDA, JLCPCB, 3D
printing on a Bambu Lab P1S). Everything must be understandable without a manual:
every everyday action is one touch, labels say what things do.

Success = from a cold box: plug in powerbank → within about a minute it is playing the last
channel (or scanning), and every control works by feel, without the web page.

## 2. Goals

1. A printed **box** holding the front panel and the Pi 3B + HAT. Powerbank, RTL-SDR
   dongle and (later) the SPF5189Z LNA stay **outside** (owner decision, 2026-10-26).
2. A **front panel, 160 × 90 mm**, printable on the P1S, carrying the controls and display.
3. A **panel PCB** (~150 × 80 mm) behind the panel holding all controls, LEDs and the OLED,
   joined to the HAT by **one IDC ribbon cable**.
4. A **Raspberry Pi HAT** (standard 65 × 56.5 mm) with the ribbon header, a real-time clock,
   a power monitor, power input and switched power for the LNA.
5. **Back of the box**: USB-C power input, a USB socket for the external dongle, and a
   small socket with switched 5 V for the external LNA.
6. **Software**: a panel driver added to the existing receiver app, driving the OLED and
   LEDs and mapping knobs/buttons onto the existing controller actions. The web UI keeps
   working whenever a network exists.
7. **Local audio**: USB speaker and/or headphones (Pi jack, extended to a front jack).

## 3. Non-goals (v1)

- Transmitting (never — receive only).
- Decoding digital voice (DMR, D-STAR, C4FM). Digital presets stay non-scannable.
- A battery inside the box; charging electronics.
- An audio amplifier or built-in speaker on the PCBs (USB speaker / headphones instead).
- Recording, a second OLED, an LED bar-graph meter, a hardware power switch.
- The Pi acting as its own Wi-Fi hotspot when the router is down (possible later).

## 4. Anti-goals (would be failures even if it "works")

- Needing a PC, phone or network to operate in an emergency.
- Volume (or starting a scan) hidden in a menu.
- Stateful hardware switches that can disagree with the software state (e.g. a band
  switch saying FM while the receiver is on 2 m). Use momentary buttons + LEDs instead.
- Fragile wiring (loose dupont jumpers) inside the box.
- Re-opening the dongle per retune (the receiver keeps it open — see app README).
- Writing to the SD card continuously.

## 5. Constraints

- **Raspberry Pi 3 Model B**, Debian 13 trixie, existing app (Flask + numpy receiver,
  ctypes librtlsdr, `uhubctl` USB recovery). Pi is dedicated to this.
- **3.3 V logic** on everything touching GPIO.
- **P1S build volume 256 × 256 × 256 mm**; parts larger than that get split.
- PCBs designed in **EasyEDA (Pro)**, fabricated at **JLCPCB**, 2-layer, **bare boards —
  the owner hand-solders everything**. So: 0805 (or larger) passives and LEDs, SOIC (not
  QFN/fine-pitch) ICs, through-hole encoders/switches/headers, generous pads, silkscreen
  reference designators and values readable for hand assembly. Use the owner's own SMD LEDs
  and stock parts where the footprint fits (check their size before layout).
  Owner accepts panel-board cost (~$10–15 / 5 pcs) and larger if needed.
- **Display**: owner's **1.5" 128×128 OLED, SH1107, I2C, 4-pin, 3–5 V** (has 2).
- Owner has KY-040 encoder modules (EC11, 20 detents, 6 mm D-shaft) — used for
  breadboard prototyping; the panel PCB uses bare EC11-type encoders with push switch.
- Owner has SMD LEDs, IDC/box headers, JST-XH, SMA parts, through-hole LEDs, etc.
- **Powerbank outside**, 5 V USB. Receiver box takes ~0.6–1 A at 5 V today (+ LNA,
  + USB speaker).

## 6. Front panel (decided with the owner)

160 × 90 mm, to-scale layout as in the v4 mockup:

| Control | Turn | Push / press |
|---|---|---|
| **Volume** knob (Ø20 cap) | louder / quieter | mute toggle |
| **Squelch** knob (Ø20) | squelch threshold (dB) | MON: hear the channel anyway (toggle) |
| **Tune** knob (Ø30, big) | channel mode: next/prev saved channel · manual mode: frequency by step | during a scan hit: **stay** (stop scan here) · in manual mode: change step 12.5 k → 100 k → 1 MHz |
| **Menu** knob (Ø20) | move in menu | select |
| **Back** button | — | one menu level up / close menu |
| **Scan** button | — | start scan · on a hit: skip to next · long press: stop scan |
| **Manual** button | — | toggle channel mode ⇄ manual frequency mode |
| **Quick 1 / Quick 2** buttons | — | jump to a configured channel (initially DR P4 and OZ3RED Randers) |
| **Headphone jack** 3.5 mm | | front access to the Pi's audio |

LEDs: **PWR, SCAN, SIG** (squelch open), **MAN** (manual mode), **2 m, 70 cm, FM**.

Default on the Tune knob is **saved channels**; manual frequency tuning is the exception.
Menu holds the rare things: band, gain, LNA on/off, step, brightness, Wi-Fi/IP display,
quick-button assignment (optional), **Shut down** (safe power-off before unplugging).
Menu closes by itself after ~10 s.

OLED main screen shows: band + mode, channel number, volume; frequency in big digits;
channel name (or MANUAL + step); signal bar with squelch mark; clock and power
(voltage/current) on the bottom line. Further screens per the mockup: scanning,
activity hit (with time), manual, menu.

Panel printed face-down, labels by filament colour change (raised or recessed),
light pipes or thin windows over SMD LEDs, bosses on the back to fix the panel PCB so
shafts/buttons/OLED line up with the holes.

## 7. Electronics

### 7.1 Partitioning — builder's call, with this proposal

The panel has 4 encoders × 3 lines = 12 lines, 5 buttons, 7 LEDs, I2C for the OLED:
24 lines + I2C is more than the Pi has free. Proposal (my call):

- **Encoders direct to Pi GPIO** (12 lines) — needs fast, reliable edge detection.
- **Buttons + LEDs on an I2C I/O expander on the panel PCB** (e.g. MCP23017, 16 I/O,
  interrupt line to the Pi).
- **OLED** on the same I2C bus (SH1107 at 0x3C; expander 0x20; RTC 0x68; power monitor
  0x40 — no conflicts).
- Ribbon: 2×10 (20-pin) IDC carrying 12 encoder lines, SDA, SCL, expander INT, 3.3 V,
  and several GND (interleave GND with I2C/encoder lines). Builder may change pin count.
- Panel is powered from the Pi's 3.3 V (OLED ~20 mA, LEDs ~5 mA each, expander).

### 7.2 HAT

- Standard HAT outline and mounting holes; 40-pin header (stacking not required).
- **Ribbon header** to the panel.
- **RTC**: DS3231 (or similar) with CR2032; Linux `i2c-rtc` overlay. Reason: after a power
  cut there's no internet time; scan-hit times must be right.
- **Power input**: 5 V from the back-panel socket → **power monitor** (INA219-class, shunt)
  → Pi 5 V via the GPIO header (back-powering, with the protection the HAT spec asks for:
  ideal diode / reverse protection + fuse). Builder decides the exact protection.
- **Honest limit:** a powerbank holds 5 V until it suddenly stops, so voltage does *not*
  show charge. The display shows voltage, current and **energy used since power-on (Wh)**;
  if the owner enters the powerbank capacity (Wh) in settings, it estimates time left.
- **LNA power**: switched 5 V output (high-side MOSFET, GPIO-controlled) to a small
  connector for the SPF5189Z (~70–100 mA).

### 7.3 RF path

All RF stays **outside the box**: antenna → (optional FM band-stop filter) → SPF5189Z LNA
→ RTL-SDR dongle → USB extension cable → back-panel USB socket → Pi.
The LNA is powered from the HAT's switched 5 V through a small back-panel socket (my call)
and can be bypassed by recabling.
Note from measurements: 2 m has strong *external* noise here (~27 dB above 70 cm);
an LNA will not fix that and can overload the dongle near FM broadcast — test with and
without. The dongle outside, on a USB extension away from the Pi, also helps the noise.

The antenna connector type (N, SO-239/PL-259 or SMA) no longer affects the box: it only
decides the adapter at the dongle/LNA.

### 7.4 Audio

- Front 3.5 mm jack connected to the Pi's own jack (short internal cable; keep audio
  off the ribbon).
- USB speaker plugged into the Pi (outside or inside the box, builder's call).
- Software plays to both when both are present; volume knob controls local output.

## 8. Box

- Front: the 160 × 90 mm panel. Inside: panel PCB, Pi 3B + HAT. Back: **power in**
  (USB-C receptacle with 5.1 kΩ CC resistors so USB-C powerbanks deliver 5 V),
  **USB-A socket** (panel-mount extension to a Pi USB port) for the dongle,
  **LNA 5 V socket** (2-pin, keyed, not a USB connector to avoid mix-ups).
- Ventilation for the Pi.
- Printed in parts if needed; serviceable (Pi SD card reachable without dismantling the
  panel is a plus, builder's call).

## 9. Software behaviour (added to the existing app)

- New panel driver module using the controller's existing `Controller.do(action, value)`
  and `indicators()`; new actions where needed (channel next/prev, mode toggle, quick
  channel, mute, menu). Web UI and panel share one state.
- **Channel list** for the Tune knob: all non-digital presets, in band order then
  frequency (my call). Scan uses the presets with *scan* enabled (as today).
- **Boot behaviour (my call, important for emergencies):** systemd service enabled;
  on boot the receiver **starts automatically on the last used channel** with the last
  volume/squelch. State is saved only on change, debounced (minutes), and on shutdown —
  not continuously (SD card).
- OLED shows a "starting…" screen as early in boot as practical.
- Volume: the knob controls **local** output; the web page keeps its own (browser-side)
  volume (my call).
- Menu "Shut down": stops the receiver, syncs, powers off the Pi; OLED shows
  "safe to unplug".

## 10. What's left to the builder

Exact part numbers and footprints; MCP23017 vs other expander; ribbon pin count and order;
HAT power-protection circuit; light-pipe design; box layout, split lines and fasteners;
connector for the LNA; OLED driver library; local audio implementation; debounce and
long-press timings; menu text.

## 11. Open with the owner

- Antenna connector type (when the antenna arrives) — adapter choice only.
- Package size of the owner's SMD LEDs (0603 / 0805 / 1206) — measure or photo.
- Powerbank capacity in Wh (optional, for the time-left estimate).
