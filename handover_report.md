# 📚 RFID Time Recording System: AI Handover & Architecture Report

This document outlines the exact operational state of the RFID Client-Server ecosystem. Read this document to instantly comprehend the current architecture, file dependencies, security models, and planned roadmaps.

---

## 🎯 1. Project Mission
A professional, secure, and scalable RFID-powered time recording terminal system.
- **Client:** Python running on Raspberry Pi (using RC522 scanner hardware) or PC (using local simulation mode).
- **Server:** Zero-dependency PHP API acting as the data ingestion gatekeeper and a real-time HTML/JS management portal.

---

## 🏛️ 2. System Architecture

### 📁 File Map & Directory Index

| Component | File Path | Purpose |
| :--- | :--- | :--- |
| **Backend** | `[rfid_api.php](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/Website/rfid_api.php)` | Core routing, JWT Signing, Sandbox Registry, Real-time Dashboard UI. |
| **Backend DB** | `[scans.log](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/Website/scans.log)` | Flat CSV log tracking: `TIMESTAMP, DEVICE_ID, RFID_UID`. |
| **Backend DB** | `[devices.json](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/Website/devices.json)` | Persistent device authentication registry (Pending vs. Approved status). |
| **Client** | `[main.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/main.py)` | Core hardware loop & Self-Healing connection manager. |
| **Client** | `[auth.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/auth.py)` | `requests`-backed HTTP engine handling Handshakes and JWT retries. |
| **Client** | `[config.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/config.py)` | Environment variable parser and URL integrity builder. |
| **Client** | `[mock_rfid.py](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/mock_rfid.py)` | PC simulation fallback with terminal manual keyboard input emulator. |
| **Client Config** | `[.env](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/.env)` | Client secrets, Server endpoint configs, and flags. |
| **Deploy Config** | `[rfid_scanner.service](file:///C:/Users/hette/STM32CubeIDE/workspace_1.19.0/RFID_Pi/rfid_scanner.service)` | Linux `systemd` configuration to auto-start daemon on Pi boot. |

---

## 🔐 3. Security Model & Lifecycle

We use a **"Zero-Touch Sandbox Registration"** model to eliminate administrative overhead while preserving 100% physical hardware control.

### 🔄 The Operational Flow (Step-by-Step)

1. **Master Handshake (`?action=login`):**
   - Client sends its `DEVICE_ID` and a `DEVICE_SECRET`.
   - Server verifies the secret matches the `$GLOBAL_SHARED_SECRET` (Stateless check).
2. **The "Hardware Gatekeeper" Sandbox:**
   - If the `DEVICE_ID` is new, the server auto-creates a entry inside `devices.json` marked as **`pending`** and rejects login with an **HTTP 403 Forbidden**.
   - The client `main.py` intercepts the 403, locks the RFID scanner, and enters an interactive "Waiting Room" loop, polling the server every 5 seconds.
3. **Admin Elevation:**
   - The Admin opens the Browser Dashboard and clicks **`✅ Approve`** on the pulsing pending device record.
   - Upon the client's next 5-second poll, the server issues a signed **HS256 JWT Token** (valid for 1 hour).
   - The client breaks out of the waiting room and unlocks the RFID scanning hardware!
4. **Instant Revocation (`?action=scan`):**
   - When scanning a card, the client sends the JWT in the `Authorization` / `X-Authorization` header.
   - The server decrypts the JWT, reads `devices.json`, and checks if status is still `approved`.
   - If the Admin clicked **`🔒 Revoke`** on the dashboard, the server rejects the scan with an **HTTP 403 Forbidden**.
   - The client instantly wipes its JWT memory, triggers a security lockdown alert, and bounces back into the 5-second Waiting Room loop!

---

## 🖥️ 4. The Real-time HTML Dashboard

The user hosts the dashboard on an Apache server (`framegeist.at`). 
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
