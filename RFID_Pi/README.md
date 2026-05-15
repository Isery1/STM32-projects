<!--
  RFID_Pi package README: hardware wiring, .env template pointers, and how to run headless vs kiosk GUI.
-->
# Raspberry Pi RFID Tag Scanner

A lightweight Python service that interfaces with an MFRC522 RFID reader over the SPI bus, reads card UIDs, and securely transmits scan records to a remote server via HTTP POST using JWT authentication.

## Features
- **JWT Token Authentication**: Automatic token renewal and lazy-retry on `401 Unauthorized` errors.
- **Local Mock Mode**: Auto-detects if not on a Pi (e.g., Windows/Mac dev PC) and launches a simulated scanner for testing software logic without hardware.
- **Robust Failure Handling**: Recovers from brief network dropping and provides cooldown timers to prevent spamming the API.

---

## 🔌 Hardware Setup (RC522 Pinout)

Connect your MFRC522 reader to the Raspberry Pi's 40-pin header using female-to-female jumper wires:

| RC522 Pin | Color (Suggested) | Raspberry Pi Pin | Physical Pin Number |
| :--- | :--- | :--- | :--- |
| **SDA (SS)** | Green | GPIO 8 (SPI0 CE0) | Pin 24 |
| **SCK** | Yellow | GPIO 11 (SPI0 SCLK) | Pin 23 |
| **MOSI** | Orange | GPIO 10 (SPI0 MOSI) | Pin 19 |
| **MISO** | Blue | GPIO 9 (SPI0 MISO) | Pin 21 |
| **IRQ** | - | *Not Connected* | - |
| **GND** | Black | Ground (Any) | Pin 6, 9, 14, etc. |
| **RST** | White | GPIO 25 | Pin 22 |
| **3.3V** | Red | 3.3V | Pin 1 or 17 |

> [!CAUTION]
> **Do not connect the RC522 reader to 5V.** The module is rated for 3.3V, and using 5V will permanently damage the reader's IC and potentially the Raspberry Pi's GPIO pins.

---

## 🛠️ Raspberry Pi Environment Configuration

Before running the code on your Raspberry Pi, you must enable the SPI hardware interface:

1. SSH into your Raspberry Pi (or open a terminal).
2. Run the configuration tool:
   ```bash
   sudo raspi-config
   ```
3. Navigate to **3 Interface Options** -> **I4 SPI** and choose **Yes** to enable the SPI interface.
4. Select **Finish** and **Reboot** if prompted.

---

## 🚀 Software Deployment

### 1. Transfer Project Files
Copy the project directory (`RFID_Pi`) to your Raspberry Pi.

### 2. Install Dependencies
Navigate into the project folder and install the required python modules:
```bash
cd RFID_Pi
pip3 install -r requirements.txt
```
*(Optional but highly recommended: Install inside a Python virtual environment)*

### 3. Setup Environment variables
Create a `.env` file in the project directory (e.g. `nano .env`) with at least:

- `SERVER_URL` — e.g. `https://framegeist.at/rfid_api.php?action=`
- `AUTH_ENDPOINT` — `login`
- `SCAN_ENDPOINT` — `scan`
- `HEARTBEAT_ENDPOINT` — `heartbeat` (keeps the device visible as **TERMINAL LIVE** on the dashboard between scans)
- `QUERY_ENDPOINT` — `query_punch_status` (read-only: status / flex / holiday **without** recording a punch)
- `DEVICE_ID` — unique name for this terminal
- `DEVICE_SECRET` — must match the PHP `$GLOBAL_SHARED_SECRET` on the server

Each badge punch is recorded as **CHECK IN** or **CHECK OUT**: the **server** picks the next event from that badge’s history (first scan of the day is IN, then alternating). The client sends `client_local_time` for reference; the log stores server time plus the terminal’s timestamp when provided. **Do not duplicate in/out logic on the client** — it would disagree with the server.

Optional on the server: edit `Website/employees.json` to map RFID UIDs to names, e.g. `{"12345": "Jane Doe"}`, for personalized messages on the terminal and dashboard.

### 4. Start Scanner
Headless (console only, no display UI):
```bash
python3 main.py
```
With local Tkinter kiosk (layout targets a **800×480** panel; shows scan results and uploads each read):
```bash
python3 main.py --gui
# or: python3 gui_poc.py
```
On the Pi desktop, ensure a display is available (e.g. `DISPLAY=:0` if started from SSH).

### Kiosk autostart (desktop session)
If the Pi boots into a graphical desktop, the usual pattern is a **`.desktop`** file under the user’s autostart folder, for example `~/.config/autostart/manageio-kiosk.desktop`, with `Exec=` pointing at `python3 …/main.py --gui` (and `env` / `PATH` as needed). A **systemd** unit is optional and only needed if you want the app to start **without** a logged-in desktop.

### Hardware diagnostics (SSH / serial console)
Bench checks for SPI and the MFRC522 (interactive: version read, then live tag UID loop):

```bash
cd RFID_Pi
python3 boot_sequence.py --diagnose
```

For the same checks as startup **without** the GUI, run:

```bash
python3 boot_sequence.py
```

---

## 🧪 Testing & Development

### Running Locally (Windows / Mac / Linux)
This codebase includes built-in hardware emulation. If you run the code on your local developer machine, it will auto-detect the absence of `RPi.GPIO` and load the **Simulated Mock Reader** instead:
```bash
# From inside your local RFID_Pi directory:
python main.py
```
The mock system will pause and prompt you to `PRESS ENTER TO TAP A MOCK CARD`. Doing so simulates an RFID scan event, generating a mock payload and forwarding it to the server API, fully exercising your `auth.py` and network connection logic!
