# ManageIO Terminal

Raspberry Pi RFID time client (`RFID_Pi/`) and PHP backend files for the web server (`Website/`).

## Contents

| Path | Description |
|------|-------------|
| `RFID_Pi/` | Python app: scanning, auth, kiosk GUI, boot checks, systemd unit |
| `Website/rfid_api.php` | API, punch log, dashboard, JWT auth |
| `Website/employees.json` | Optional badge UID → display name |

## Branches

- **`main`** — release-aligned snapshot; deployable Pi + server files only.
- **`develop`** — integrate new features here, then merge to `main` when ready.

## Quick links

- Pi setup: see `RFID_Pi/README.md` and `RFID_Pi/.env.example`.
- Server: upload `rfid_api.php` and `employees.json`; set env `RFID_APP_TZ` if needed.

Repository: [gitlab.com/zk-digital-group/manageio-terminal](https://gitlab.com/zk-digital-group/manageio-terminal).
