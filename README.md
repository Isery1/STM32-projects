# ManageIO Terminal

RFID time terminal stack: **Raspberry Pi** client (headless or kiosk GUI), **PHP** API and dashboard, and optional **systemd** service.

## Repository layout

| Path | Purpose |
|------|---------|
| `RFID_Pi/` | Python client: scans, auth, heartbeat, boot checks, Tkinter kiosk |
| `Website/rfid_api.php` | Backend: JWT auth, punch log, worked-time, dashboard API |
| `Website/employees.json` | Optional UID → display name map |
| `handover_report.md` | Architecture / operations notes |

## Quick start (Pi)

1. Copy `RFID_Pi/.env.example` to `RFID_Pi/.env` and set `SERVER_URL`, `DEVICE_ID`, and `DEVICE_SECRET`.
2. Create a venv, install `RFID_Pi/requirements.txt`, run `main.py` or `gui_poc.py` (see `RFID_Pi/README.md`).
3. Deploy `Website/rfid_api.php`, `employees.json`, and supporting web files to your PHP host; set timezone via `RFID_APP_TZ` if needed.

## Git remotes

This clone may track both **GitHub** (`origin`) and **GitLab** (`gitlab`) for historical STM32 work and the ManageIO terminal product respectively.

## License

Specify license in this repository per your organisation’s policy.
