<!--
  ManageIO Terminal — repository overview (Markdown).
  Describes what lives in each folder and how main vs develop branches are used.
-->
# ManageIO Terminal

Raspberry Pi RFID time client (`RFID_Pi/`) and PHP backend files for the web server (`Website/`).

## Contents

| Path | Description |
|------|-------------|
| `RFID_Pi/` | Python app: scanning, auth, kiosk GUI, boot checks |
| `Website/rfid_api.php` | API, punch log, dashboard, JWT auth |
| `Website/employees.json` | Optional badge UID → display name |
| `Website/terminal_serials.example.json` | Example one-time terminal serial registry |

## Branches

- **`main`** — release-aligned snapshot; deployable Pi + server files only.
- **`develop`** — integrate new features here, then merge to `main` when ready.

## Quick links

- Pi setup: see `RFID_Pi/README.md` and `RFID_Pi/.env.example`.
- Server: production uses `https://api.zk-digital.at/backend-api` with the database-backed terminal tables. `Website/rfid_api.php` remains a local/mock backend; for local testing, copy `Website/terminal_serials.example.json` to `Website/terminal_serials.json` and set the Pi `TERMINAL_SERIAL` to the pending serial.

Repository: [gitlab.com/zk-digital-group/manageio-terminal](https://gitlab.com/zk-digital-group/manageio-terminal).
