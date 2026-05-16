# ManageIO RFID Terminal Deployment Hardware

This guide turns the Raspberry Pi RFID client into a repeatable field terminal. The goal is that an installer connects only power and network at the wall; the RFID reader wiring stays fixed inside the enclosure.

## Terminal Stack

- Raspberry Pi 4 Model B, 2 GB or 4 GB.
- Official Raspberry Pi 7-inch touchscreen.
- AZ-Delivery RFID Kit RC522 / MFRC522 reader module, powered from 3.3 V only.
- 5 V USB-C power input, 3 A minimum.
- Ethernet as the preferred network path; Wi-Fi remains configured as fallback.
- Parametric enclosure source: `rfid_terminal_enclosure.scad`.

## Recommended Bill Of Materials

| Area | Part | Recommendation |
| --- | --- | --- |
| Compute | Raspberry Pi 4 Model B | Use the same RAM size for every terminal image. 2 GB is enough for the kiosk, 4 GB is fine if supply is easier. |
| Display | Official Raspberry Pi 7-inch touchscreen | Use the official DSI ribbon and display power jumpers supplied with the display kit. |
| RFID | AZ-Delivery RFID Kit RC522 / MFRC522 reader module | This is a 40 x 60 mm, 13.56 MHz SPI board. Use the same module for the printed mounting pattern. Do not use press-fit headers. |
| Power | USB-C 5.1 V / 3 A supply | Prefer the official Raspberry Pi USB-C supply or an industrial 5 V DIN-rail supply feeding short USB-C pigtails. |
| Power cable | Right-angle USB-C cable or panel-mount USB-C pigtail | Use a short cable inside the case if the Pi port is not directly reachable. Avoid long thin USB cables. |
| Network | Cat 6 Ethernet patch lead | Use Ethernet for installed units. Keep Wi-Fi credentials provisioned for fallback only. |
| Network pass-through | Short flat Ethernet lead or panel-mount RJ45 coupler | Pick one style and standardize it for all terminals. A panel coupler is easiest to service. |
| RFID harness | 7-wire keyed harness | Pi side should be a GPIO plug/ribbon breakout; RC522 side should be keyed JST or soldered pigtail. |
| GPIO connector | 40-pin GPIO screw-terminal breakout | Recommended product style: 52Pi GPIO Screw Terminal Block Breakout Board HAT, SKU EP-0129, or a 40-pin ribbon-cable terminal block breakout. This gives labeled screw terminals instead of loose jumper wires. |
| Cooling | Raspberry Pi 4 heatsink, optional 30 mm 5 V fan | Use a passive heatsink by default. Add the fan only for warm areas, direct sun, or enclosed rooms with poor airflow. |
| Fasteners | M2.5 screws/standoffs for Pi/display, M2 or M2.5 for RC522 | Use stainless or zinc-plated machine screws. Avoid metal directly behind the RFID antenna. |
| Inserts | M2.5 heat-set threaded inserts | Use for the enclosure service cover and repeated maintenance points. |
| Print material | PETG for indoor use, ASA/ABS for warm or sunlit areas | PLA is acceptable for a prototype fit check only. |
| Labeling | Printed terminal ID label and cable tags | Label both the enclosure and the wall-side Ethernet/power service point. |

## Internal RFID Harness

Do not deploy terminals with loose female-to-female jumper wires. Build one keyed harness per terminal and secure it to the printed cable anchors.

Recommended harness:

- Pi side: short 40-pin GPIO ribbon to a small screw-terminal breakout board, or a 52Pi GPIO Screw Terminal Block Breakout Board HAT if the enclosure clearance is sufficient.
- RC522 side: 7-pin keyed JST-XH/JST-PH plug if the reader is modified for a socket, or soldered pigtail with heat shrink.
- Wire length: 120-180 mm inside the enclosure.
- Wire gauge: 26-28 AWG stranded.
- Keep the RFID harness away from the Ethernet and USB-C cable bundle.

Practical connector option:

- Use a Raspberry Pi 40-pin GPIO screw-terminal breakout, for example the 52Pi GPIO Screw Terminal Block Breakout Board HAT, SKU `EP-0129`.
- For the cleanest enclosure, use the same type of screw-terminal board through a short 40-pin ribbon cable and mount the breakout inside the case. That keeps the Pi low and gives serviceable terminals for the RC522 wires.
- Land only these seven wires on the breakout: `3.3 V`, `GND`, `GPIO8`, `GPIO11`, `GPIO10`, `GPIO9`, and `GPIO25`.
- On the RC522 side, solder the wires directly to the AZ-Delivery board or fit a keyed JST plug. The RC522 kit includes pin headers, but a keyed plug or soldered pigtail is better for field deployment.

| RC522 Signal | Wire Color | Raspberry Pi Signal | Physical Pin |
| --- | --- | --- | --- |
| SDA / SS | Green | GPIO 8 / SPI0 CE0 | 24 |
| SCK | Yellow | GPIO 11 / SPI0 SCLK | 23 |
| MOSI | Orange | GPIO 10 / SPI0 MOSI | 19 |
| MISO | Blue | GPIO 9 / SPI0 MISO | 21 |
| GND | Black | Ground | 6, 9, or 14 |
| RST | White | GPIO 25 | 22 |
| 3.3 V | Red | 3.3 V | 1 or 17 |
| IRQ | Not fitted | Not connected | Not connected |

Important: the RC522 is a 3.3 V module. Never connect it to 5 V.

## Enclosure Model

Open `rfid_terminal_enclosure.scad` in OpenSCAD.

Set `part` before exporting:

```scad
part = "fit_check"; // first print
part = "rear";      // rear shell
part = "front";     // front bezel
part = "preview";   // visual assembly only
```

Print order:

1. Print `fit_check` at low height and confirm the Pi standoffs, RC522 standoffs, wall keyholes, and cable exits.
2. Adjust top-level dimensions in `rfid_terminal_enclosure.scad` for the exact display frame, RC522 module, cable pigtails, and printer tolerances.
3. Print the rear shell.
4. Print the front bezel.

Suggested print settings:

- Material: PETG for normal indoor deployment.
- Layer height: 0.20 mm.
- Walls: 4 perimeters.
- Infill: 25-35%.
- Heat-set inserts: install after printing, before electronics assembly.
- Orientation: print both shell pieces with the visible outside face on the bed when possible.

RFID placement rules:

- Mount the AZ-Delivery RC522 directly behind the front-right tap area beside the screen.
- The OpenSCAD model raises the RC522 on tall internal standoffs so the antenna sits close to the front face, not on the rear shell floor.
- Keep at least 3 mm of plastic between the badge and the reader.
- Keep metal screws, inserts, Ethernet couplers, and bundled cables away from the reader antenna.
- Test read range with real employee badges before printing a batch.

Thermal placement rules:

- Fit a heatsink to the Raspberry Pi 4 CPU before closing the case.
- Keep the top exhaust slots and bottom intake slots open after wall mounting.
- Leave a small standoff gap behind the enclosure where possible so rear ventilation is not fully blocked by the wall.
- Use the optional 30 mm fan opening only if temperature testing shows throttling or high sustained temperatures.
- Do not route the USB-C, Ethernet, or RFID harness across the Pi heatsink.

## Assembly

1. Print and deburr the enclosure parts.
2. Install heat-set inserts in service-cover and shell mounting points.
3. Mount the official touchscreen into the front bezel.
4. Mount the Raspberry Pi 4 to the rear shell standoffs.
5. Connect the official DSI display ribbon and display power jumpers.
6. Mount the RC522 behind the front-right tap area using plastic spacers if possible.
7. Connect the fixed RFID harness using the pinout above.
8. Route the RFID harness through the printed cable anchors.
9. Fit the Raspberry Pi 4 heatsink and check that no cable touches it.
10. If the install area is warm, fit a 30 mm 5 V fan at the grille and power it from the Pi 5 V/GND header or a controlled fan HAT.
11. Fit the USB-C power pigtail or route the USB-C supply lead through the strain relief.
12. Fit the Ethernet coupler or route a short Ethernet lead through the RJ45 strain relief.
13. Close the enclosure and verify that the service cover can be removed without disturbing the RC522 harness.
14. Label the terminal with its `DEVICE_ID`.

## Site Deployment

For each location:

1. Install wall anchors using the rear shell keyhole spacing from the printed part.
2. Provide one nearby 5 V USB-C power feed.
3. Provide one Ethernet drop where possible.
4. Mount the terminal.
5. Connect USB-C power.
6. Connect Ethernet.
7. Boot the terminal and confirm the kiosk starts.

Only two external cables should be handled in the field: USB-C power and Ethernet. Wi-Fi is a fallback path for places where an Ethernet drop is unavailable or temporarily disconnected.

## Scalable Naming And Configuration

Use one unique `DEVICE_ID` per physical terminal. Keep the name stable when the SD card is replaced.

Recommended pattern:

```text
terminal-<area>-<number>
terminal-lobby-01
terminal-workshop-02
terminal-warehouse-03
```

For each terminal, copy `.env.example` to `.env` and set:

```text
DEVICE_ID=terminal-lobby-01
DEVICE_SECRET=<shared-secret-from-server>
SERVER_URL=https://your-server.example/rfid_api.php?action=
```

Keep an asset list with:

- `DEVICE_ID`.
- Physical location.
- Pi serial number.
- Ethernet port or switch location.
- Wi-Fi fallback SSID.
- Enclosure print revision.
- Date installed.

## Software Bring-Up

On every Pi:

1. Enable SPI with `sudo raspi-config`.
2. Install dependencies from `requirements.txt`.
3. Configure `.env`.
4. Run hardware diagnostics:

```bash
python3 boot_sequence.py --diagnose
```

5. Start the kiosk:

```bash
python3 main.py --gui
```

6. Configure desktop autostart as described in `README.md`.

## Validation Checklist

Bench validation before wall mounting:

- Fit-check print verified against the exact Pi, official touchscreen, RC522 module, USB-C lead, and Ethernet lead.
- RC522 reads real badges through the front-right printed tap area.
- RC522 VersionReg check passes in `boot_sequence.py --diagnose`.
- Kiosk starts with `python3 main.py --gui`.
- Ethernet reaches the server.
- Wi-Fi fallback connects when Ethernet is unplugged.
- Device appears as live on the dashboard after heartbeat.
- A test badge scan records correctly.
- Pi reports no thermal throttling after at least 30 minutes on the kiosk screen.
- Top, bottom, and side vents are not blocked by wall brackets, cables, labels, or sealant.
- Power cable strain relief prevents movement at the Pi USB-C port.
- Ethernet strain relief prevents movement at the Pi RJ45 port or panel coupler.
- Service cover can be opened without disconnecting the RFID harness.

Field validation after mounting:

- Terminal is firmly mounted and does not flex during badge taps.
- USB-C and Ethernet cables are not exposed to pulling or foot traffic.
- `DEVICE_ID` label matches the `.env` file and asset list.
- Screen is readable at normal standing height.
- Front-right tap target is clear and badges read reliably.
- Warm air can escape from the top vents after the enclosure is mounted.
- A final test scan reaches the server from the installed location.

## Batch Rollout Notes

- Build one golden SD card image after the first terminal is validated.
- Clone the image for new terminals, then change only `DEVICE_ID`, Wi-Fi fallback, and local hostname.
- Keep the same enclosure revision for a full batch.
- When changing the enclosure model, increment the printed revision label and re-run the fit-check print.
- Keep spare prebuilt RFID harnesses and one spare complete terminal for fast replacement.
