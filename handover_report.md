# 📚 RFID Time Recording System: AI Handover & Architecture Report

This document outlines the exact operational state of the RFID Client-Server ecosystem. Read this document to instantly comprehend the current architecture, file dependencies, security models, and planned roadmaps.

**Maintenance policy:** Whenever behavior, deployment, or file responsibilities change, append a row to the **change log** below (same PR or commit) so the next person or agent sees a dated trail.

---

## 📝 Change log

| Date | Area | Summary |
| :--- | :--- | :--- |
| 2026-05 | `Website/rfid_api.php` | Matching `DEVICE_SECRET` / `$GLOBAL_SHARED_SECRET` ⇒ immediate JWT on login; device rows in `devices.json` are created/updated as **approved**. Manual **Approve** / **Revoke** removed. **Scan** accepts any request with a valid JWT (no separate per-scan registry rejection). |
| 2026-05 | Dashboard | Device table is informational + **Remove** only; last-login and token-activity hint unchanged in spirit. |
| 2026-05 | `RFID_Pi` | Removed `.env.example`; `README.md` documents `.env` keys. Canonical test host: **framegeist.at**. |
| 2026-05 | `RFID_Pi/auth.py` / `main.py` | Dropped pending/revoke client logic; auth retry is generic (wrong secret or unreachable server). |
| 2026-05 | `RFID_Pi/gui_poc.py` | **Integrated kiosk:** RFID runs on a **background thread**; each read shows **Sending…** then success/failure on screen and calls **`send_scan`** (same API as headless client). Footer buttons still show **placeholder** HR copy; the backend still only logs UID scans. |
| 2026-05 | `RFID_Pi/main.py` | Added `--gui` → runs `gui_poc.run_kiosk()`. Headless default unchanged. |
| 2026-05 | `RFID_Pi/README.md` | Documented `main.py --gui` and `gui_poc.py` entry points. |
| 2026-05 | Punch / attendance | `scans.log` lines: `server_time,device_id,uid,in\|out[,client_local_time]`. Server picks the next event from **per-UID** history (first punch **in**, then alternates). Dashboard: **daypart** banner, **Clocked in now**, punch column on log. |
| 2026-05 | `?action=heartbeat` | POST + JWT updates device **`last_seen`**; clients call on an interval so **Registered devices** shows **TERMINAL LIVE** (within ~10 min). Punches also refresh **`last_seen`**. |
| 2026-05 | `RFID_Pi/config.py` | `HEARTBEAT_URL` / `HEARTBEAT_INTERVAL_SECONDS` (.env optional). |
| 2026-05 | Read-only query | `?action=query_punch_status` + `QUERY_URL` on client — **footer actions** use this so viewing status does **not** create a punch. Check-in/out alternation stays **server-side only**. |
| 2026-05 | `Website/employees.json` | Maps RFID UID → display name for greetings and dashboard (**not** deleted by Clear punch log). |
| 2026-05 | Terminal & API messages | Scan + query responses include **`terminal_message`** (time-of-day + name + in/out or status text). |
| 2026-05 | Worked today | Server computes **seconds worked on local calendar day** (running if still IN); dashboard **Worked today** table + `today_summary` in `get_scans`. **Clear punch log** wipes `scans.log` only → totals reset; names file kept. |
| 2026-05 | `RFID_Pi/auth.py` | `send_scan` returns parsed JSON; `send_heartbeat()` added. |

## 🎯 1. Project Mission
A professional, secure, and scalable RFID-powered time recording terminal system.
- **Client:** Python running on Raspberry Pi (using RC522 scanner hardware) or PC (using local simulation mode).
- **Server:** Zero-dependency PHP API acting as the data ingestion gatekeeper and a real-time HTML/JS management portal.

---

## 🏛️ 2. System Architecture

### 📁 File Map & Directory Index

| Component | File Path | Purpose |
| :--- | :--- | :--- |
| **Backend DB** | `[employees.json](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/Website/employees.json)` | Optional UID → display name map (not wiped by Clear punch log). |
| **Backend** | `[rfid_api.php](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/Website/rfid_api.php)` | Core routing, JWT, `scan`, `heartbeat`, **`query_punch_status`** (no punch), attendance math, dashboard UI. |
| **Backend DB** | `[scans.log](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/Website/scans.log)` | Append-only punch log: `TIMESTAMP, DEVICE_ID, UID, in\|out [, client_local_time]`. |
| **Backend DB** | `[devices.json](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/Website/devices.json)` | Device registry (first/last seen, status; auto-approved when shared secret matches). |
| **Client** | `[main.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/main.py)` | Headless RFID loop + self-healing auth; **`--gui`** launches the kiosk (`gui_poc.run_kiosk`). |
| **Client** | `[auth.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/auth.py)` | `requests`: login JWT, **`send_scan`**, **`send_status_query`** (read-only), **`send_heartbeat`**, 401 re-login. |
| **Client** | `[config.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/config.py)` | Env URLs including **`QUERY_URL`**. |
| **Client** | `[mock_rfid.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/mock_rfid.py)` | PC simulation fallback with terminal manual keyboard input emulator. |
| **Kiosk (integrated)** | `[gui_poc.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/gui_poc.py)` | Home tap → **`send_scan`**; footer buttons → **`send_status_query`** (no punch). |
| **Client Config** | `[.env](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/.env)` | Client secrets, Server endpoint configs, and flags. |
| **Deploy Config** | `[rfid_scanner.service](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/rfid_scanner.service)` | Linux `systemd` configuration to auto-start daemon on Pi boot. |

---

## 🔐 3. Security Model & Lifecycle *(testing deployment)*

**Model:** Single **shared device secret** gates enrollment. There is **no** dashboard approval step and **no** revoke path; treat the PHP script URL and secrets as sensitive for testing.

### 🔄 Operational flow

1. **Login (`?action=login`, POST JSON):** Client sends `device_id` + `secret`. If `secret === $GLOBAL_SHARED_SECRET`, the server upserts `devices.json` (marks device **approved**, updates `last_seen`), returns **HS256 JWT** (~1 h).
2. **Wrong or missing secret:** **401**; client must fix `.env` / server config.
3. **Scan (`?action=scan`, POST JSON + `Authorization: Bearer <jwt>`):** Server verifies JWT (signature + expiry). If valid, appends one CSV line to `scans.log`. Invalid/expired JWT → **401** (client re-authenticates and retries in `auth.py`).
4. **Dashboard:** Lists devices and scans; **Remove** deletes a device record from `devices.json` (does not block future logins with a new ID unless you rotate secrets).

**Production note:** Dashboard actions and secrets are still wide open—acceptable for lab use only; lock down before any real deployment.

---

## 🖥️ 4. The Real-time HTML Dashboard

The user hosts the dashboard on an Apache server (**framegeist.at**). 
- **Engine:** High-performance JavaScript polling (`fetch`) every 2,000ms against `?action=get_scans`.
- **Unified Output:** Returns unified object `{"scans": [...], "devices": {...}, "server_time": X}`.
- **Dynamic DOM Optimization:** Caches result hashes to conditionally redraw the DOM ONLY when database changes occur, preventing UI flickering.
- **Visuals:** Emerald pulsing active beacons, sleek glassmorphism cards, and instant `Wipe Logs` action buttons backed by prompt protections.

---

## 🚀 5. Future Roadmap & Development Directives

If a future agent is assigned to scale this system, focus on these planned milestones:

### 1. Edge Buffering (Offline-Immunity)
Currently, if internet drops, scans fail.
- **Task:** Upgrade `auth.py` to integrate an `sqlite3` local cache file.
- **Task:** On network failure, append scan locally. 
- **Task:** Add a background thread to "drain" cached scans once connection is restored.

### 2. 3.5" TFT Local LCD Integration
Turn the Pi into a visual terminal kiosk.
- **Task:** Add local status prints via Python (`PyGame` or `tkinter` or SPI library) showing "Scan Confirmed" or "Access Denied" to the physical employee.

### 3. Database Upgrade
Migrate the PHP flat logs (`devices.json` & `scans.log`) into a real MySQL/PostgreSQL engine to support high performance analytics as device counts approach 1,000+.

### 4. Smartphone NFC Integration
Deploy smart mobile credentials.
- **Android Apps:** Access native HCE Service APIs to stream card emulation signals.
- **iOS (Universal Tap):** Deploy passive $0.50 NTAG213 NFC stickers directly on the enclosure face. Tapping will redirect iPhone Safari instances to trigger server API validation events.

---

## 📐 6. Hardware Enclosure: 3D AI Model Prompt

Use the following engineered prompt in 3D generative platforms (like Meshy, Spline AI, Luma Genie, or Kaedim) to generate the print-ready chassis geometry:

> **Prompt:** Industrial wall-mount smart IoT terminal enclosure, sleek minimalist engineering, matte dark charcoal polycarbonate casing. The faceplate features a precisely beveled recessed cavity designed for a 7-inch capacitive touchscreen LCD display. The lower section houses a smooth flat panel with a debossed circular "Tap NFC Badge Here" icon signifying the internal RFID reader node. Internal mechanics include standard Pi 4/5 chassis standoff mounts, passive side-vent grills for thermal management, and specialized rear cable-management routing ducts tailored to conceal a PoE Ethernet splitter and RJ45 plug assembly. Watertight manifold design, highly detailed CAD-accurate geometry, ready for 3D manufacturing.

---

## 🖥️ 7. Interactive Kiosk GUI (integrated with backend)

**Run:** `python3 main.py --gui` or `python3 gui_poc.py` (needs a display; on Pi e.g. `DISPLAY=:0` if launched via SSH).

### Behavior

- **Reader thread** blocks on `read_id()` (real **RC522** or **`mock_rfid`** when not on a Pi / `USE_MOCK_RFID`).
- Until login succeeds, the thread retries **`authenticate()`** every 5 seconds; the header beacon shows **SERVER OFFLINE / AUTH** vs **SERVER ONLINE**.
- Each successful read: UI shows **Sending…**, then **✓ SCAN RECORDED** or **✗ UPLOAD FAILED**; the same **`?action=scan`** POST as **`main.py`** records the UID on the server.
- **Cooldown** after each read: `SCAN_COOLDOWN_SECONDS` from `.env` (same as headless client).
- **Footer buttons** (State / Flextime / Holidays): still **placeholder** HR copy after a scan, but the **scan is always uploaded**—there are no separate HR API routes yet.
- **Dev shortcut:** Click the home-screen instruction line to fire a mock UID (`MOCK_CLICK_001`) through the same upload path.

### 🎨 Design

Targeting **800×480**; dark space-cadet (`#121826`) `tkinter` theme. Fullscreen can be enabled later via `attributes('-fullscreen', True)`.

### 🔮 Future

When an external HR API exists, footer flows can swap placeholder text for real responses; scan ingestion stays on `rfid_api.php` or moves behind a unified gateway as designed later.
