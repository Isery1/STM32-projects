## Backend Base URL

Production base URL:

```text
https://api.zk-digital.at/backend-api
```

This is the same value the frontend uses as `EXPO_PUBLIC_API_URL`. The terminal config stores it as `SERVER_URL`.

## Database Mapping

- `s_terminal_version` controls minimum/latest terminal app versions during login or heartbeat.
- `d_terminals` is the authenticated terminal record; the server derives the customer from this table.
- `d_terminal_serials` stores one-time enrollment serial hashes. A `PENDING` serial can enroll once, then becomes `ENROLLED`.
- `d_terminal_api_keys` stores the hashed per-terminal API key used for future logins.
- `d_user_badges` maps the raw scanned UID, after server-side hashing, to the user.
- `d_stamps` stores accepted punches with `request_id`, `terminal_time`, and server-generated `server_time`.

The terminal never sends `customer_id`, `company_id`, `user_id`, or `terminal_id`; the backend derives those from the authenticated terminal and badge.

Most service validation errors from the Spring backend use:

```json
{
  "error": "request_error",
  "message": "Human-readable failure reason."
}
```

Authentication filter failures use `success: false` with `error_code` for missing or invalid bearer tokens.

## Authentication Model

1. A terminal is shipped with a server-generated serial.
2. On first boot, the terminal enrolls with that serial.
3. The server validates the serial and returns a unique API key once.
4. The terminal stores the API key locally.
5. Future logins use `device_id` + `api_key` to get a short-lived bearer token.
6. Normal scan, query, heartbeat, and sync requests use the bearer token.

## 1. Enroll Terminal

Used once before the terminal has an API key.

```http
POST /terminal/enroll
Content-Type: application/json
```

Request:

```json
{
  "serial": "MIO1-COMPANY01-H8K4-2P9Q-X7VD-93LA",
  "device_id": "terminal-raspi-12345678",
  "app_version": "0.1.0",
  "terminal_time": "2026-05-17T20:45:00+02:00"
}
```

Success `201`:

```json
{
  "success": true,
  "device_id": "terminal-raspi-12345678",
  "company_id": 42,
  "terminal_id": 7,
  "terminal_name": "Workshop terminal",
  "api_key": "mio_live_xxxxxxxxxxxxxxxxx",
  "message": "Terminal enrolled."
}
```

Invalid or missing payload `400`:

```json
{
  "success": false,
  "error_code": "bad_request",
  "message": "serial and device_id are required."
}
```

Unknown serial `404`:

```json
{
  "success": false,
  "error_code": "unknown_serial",
  "message": "This terminal serial is not registered."
}
```

Invalid serial hash `401`:

```json
{
  "error": "request_error",
  "message": "Invalid terminal credentials."
}
```

Serial expired `410`:

```json
{
  "success": false,
  "error_code": "serial_expired",
  "message": "This enrollment serial has expired."
}
```

Serial already used `409`:

```json
{
  "success": false,
  "error_code": "serial_already_enrolled",
  "message": "This terminal serial has already been used."
}
```

Serial revoked `403`:

```json
{
  "success": false,
  "error_code": "serial_revoked",
  "message": "This terminal serial has been revoked."
}
```

Server error `500`:

```json
{
  "success": false,
  "error_code": "server_error",
  "message": "Could not enroll terminal."
}
```

## 2. Login With API Key

Used after enrollment.

```http
POST /terminal/login
Content-Type: application/json
```

Request:

```json
{
  "device_id": "terminal-raspi-12345678",
  "api_key": "mio_live_xxxxxxxxxxxxxxxxx",
  "app_version": "0.1.0",
  "terminal_time": "2026-05-17T20:45:00+02:00"
}
```

Success `200`:

```json
{
  "success": true,
  "access_token": "jwt...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "terminal_id": 7,
  "company_id": 42
}
```

Missing credentials `400`:

```json
{
  "success": false,
  "error_code": "bad_request",
  "message": "device_id and api_key are required."
}
```

Invalid API key `401`:

```json
{
  "success": false,
  "error_code": "invalid_credentials",
  "message": "Invalid terminal credentials."
}
```

Terminal disabled `403`:

```json
{
  "success": false,
  "error_code": "terminal_disabled",
  "message": "This terminal has been disabled."
}
```

API key revoked `403`:

```json
{
  "success": false,
  "error_code": "api_key_revoked",
  "message": "This API key has been revoked."
}
```

App version blocked `426`:

```json
{
  "success": false,
  "error_code": "upgrade_required",
  "message": "This terminal software version is no longer supported.",
  "minimum_version": "1.2.0"
}
```

## 3. Heartbeat

Used while logged in.

```http
POST /terminal/heartbeat
Authorization: Bearer <access_token>
Content-Type: application/json
```

Request:

```json
{
  "device_id": "terminal-raspi-12345678",
  "app_version": "0.1.0",
  "terminal_time": "2026-05-17T20:45:00+02:00",
  "pending_offline_stamps": 3
}
```

Success `200`:

```json
{
  "success": true,
  "server_time": "2026-05-17T20:45:01+02:00",
  "terminal_status": "active",
  "version_status": "ok"
}
```

Missing token `401`:

```json
{
  "success": false,
  "error_code": "missing_token",
  "message": "Missing authentication token."
}
```

Invalid or expired token `401`:

```json
{
  "success": false,
  "error_code": "invalid_token",
  "message": "Invalid or expired authentication token."
}
```

Terminal disabled `403`:

```json
{
  "success": false,
  "error_code": "terminal_disabled",
  "message": "This terminal has been disabled."
}
```

Version warning `200`:

```json
{
  "success": true,
  "server_time": "2026-05-17T20:45:01+02:00",
  "terminal_status": "active",
  "version_status": "update_available",
  "latest_version": "1.3.0",
  "message": "A newer terminal version is available."
}
```

## 4. Upload One Stamp

Used for online scans and offline queue retries.

```http
POST /terminal/stamps
Authorization: Bearer <access_token>
Content-Type: application/json
```

Request:

```json
{
  "request_id": "terminal-raspi-12345678-20260517T204500-a1b2c3d4",
  "device_id": "terminal-raspi-12345678",
  "uid": "250256679402",
  "terminal_time": "2026-05-17T20:45:00+02:00"
}
```

Success `201`:

```json
{
  "success": true,
  "request_id": "terminal-raspi-12345678-20260517T204500-a1b2c3d4",
  "stamp_id": 987,
  "event": "in",
  "server_time": "2026-05-17T20:45:01+02:00",
  "display_name": "Matthias",
  "terminal_message": "You are checked in."
}
```

Duplicate retry, already processed `200`:

```json
{
  "success": true,
  "duplicate": true,
  "request_id": "terminal-raspi-12345678-20260517T204500-a1b2c3d4",
  "stamp_id": 987,
  "event": "in",
  "server_time": "2026-05-17T20:45:01+02:00",
  "display_name": "Matthias",
  "terminal_message": "You are checked in."
}
```

Missing fields `400`:

```json
{
  "success": false,
  "error_code": "bad_request",
  "message": "request_id, uid and terminal_time are required."
}
```

Unknown badge `404`:

```json
{
  "success": false,
  "error_code": "unknown_badge",
  "message": "This badge is not assigned to a user."
}
```

Inactive user or badge `403`:

```json
{
  "success": false,
  "error_code": "inactive_badge",
  "message": "This badge is not active."
}
```

Terminal disabled `403`:

```json
{
  "success": false,
  "error_code": "terminal_disabled",
  "message": "This terminal has been disabled."
}
```

Conflicting duplicate `409`:

```json
{
  "success": false,
  "error_code": "request_id_conflict",
  "message": "This request_id was already used with different stamp data."
}
```

Needs admin review `202`:

```json
{
  "success": true,
  "needs_review": true,
  "request_id": "terminal-raspi-12345678-20260517T204500-a1b2c3d4",
  "terminal_message": "Saved, but an admin may need to review the final order."
}
```

## 5. Batch Offline Sync

Recommended later instead of uploading one stamp at a time.

```http
POST /terminal/stamps/sync
Authorization: Bearer <access_token>
Content-Type: application/json
```

Request:

```json
{
  "device_id": "terminal-raspi-12345678",
  "scans": [
    {
      "request_id": "terminal-raspi-12345678-20260517T204500-a1b2c3d4",
      "uid": "250256679402",
      "terminal_time": "2026-05-17T20:45:00+02:00",
      "sequence": 101
    },
    {
      "request_id": "terminal-raspi-12345678-20260517T205000-b2c3d4e5",
      "uid": "250256679402",
      "terminal_time": "2026-05-17T20:50:00+02:00",
      "sequence": 102
    }
  ]
}
```

Success or mixed result `207` or `200`:

```json
{
  "success": true,
  "results": [
    {
      "request_id": "terminal-raspi-12345678-20260517T204500-a1b2c3d4",
      "status": "accepted",
      "stamp_id": 987,
      "event": "in",
      "terminal_message": "Synced: checked in."
    },
    {
      "request_id": "terminal-raspi-12345678-20260517T205000-b2c3d4e5",
      "status": "duplicate",
      "stamp_id": 988,
      "event": "out",
      "terminal_message": "Already synced."
    }
  ]
}
```

Partial failures:

```json
{
  "success": true,
  "results": [
    {
      "request_id": "terminal-raspi-12345678-20260517T204500-a1b2c3d4",
      "status": "accepted",
      "stamp_id": 987,
      "event": "in"
    },
    {
      "request_id": "terminal-raspi-12345678-20260517T205000-b2c3d4e5",
      "status": "rejected",
      "error_code": "unknown_badge",
      "message": "This badge is not assigned to a user."
    }
  ]
}
```

Bad batch `400`:

```json
{
  "success": false,
  "error_code": "bad_request",
  "message": "scans must be a non-empty array."
}
```

Too many scans `413`:

```json
{
  "success": false,
  "error_code": "batch_too_large",
  "message": "Maximum batch size is 100 scans."
}
```

## 6. Query Current Status

Used by "Check my current status".

```http
POST /terminal/query
Authorization: Bearer <access_token>
Content-Type: application/json
```

Request:

```json
{
  "device_id": "terminal-raspi-12345678",
  "uid": "250256679402",
  "query_kind": "status"
}
```

Success `200`:

```json
{
  "success": true,
  "query_kind": "status",
  "display_name": "Matthias",
  "state": "in",
  "since": "2026-05-17T08:00:00+02:00",
  "worked_seconds_today": 14400,
  "worked_today_hm": "4:00",
  "terminal_message": "You are clocked in. Worked today: 4:00."
}
```

Unknown badge `404`:

```json
{
  "success": false,
  "error_code": "unknown_badge",
  "message": "This badge is not assigned to a user."
}
```

Invalid query kind `400`:

```json
{
  "success": false,
  "error_code": "invalid_query_kind",
  "message": "query_kind must be status, flextime or holiday."
}
```

## 7. Query Flextime

```http
POST /terminal/query
Authorization: Bearer <access_token>
Content-Type: application/json
```

Request:

```json
{
  "device_id": "terminal-raspi-12345678",
  "uid": "250256679402",
  "query_kind": "flextime"
}
```

Success `200`:

```json
{
  "success": true,
  "query_kind": "flextime",
  "display_name": "Matthias",
  "state": "in",
  "worked_today_hm": "4:00",
  "flextime_balance_hm": "12:30",
  "terminal_message": "Flextime balance: 12:30."
}
```

If the HR/flextime module is not connected yet `200`:

```json
{
  "success": true,
  "query_kind": "flextime",
  "display_name": "Matthias",
  "state": "in",
  "worked_today_hm": "4:00",
  "flextime_balance_hm": null,
  "terminal_message": "Flextime details are not available yet."
}
```

## 8. Query Holidays

```http
POST /terminal/query
Authorization: Bearer <access_token>
Content-Type: application/json
```

Request:

```json
{
  "device_id": "terminal-raspi-12345678",
  "uid": "250256679402",
  "query_kind": "holiday"
}
```

Success `200`:

```json
{
  "success": true,
  "query_kind": "holiday",
  "display_name": "Matthias",
  "state": "in",
  "holiday_days_remaining": 18,
  "holiday_days_planned": 4,
  "terminal_message": "You have 18 holiday days remaining."
}
```

If the HR/holiday module is not connected yet `200`:

```json
{
  "success": true,
  "query_kind": "holiday",
  "display_name": "Matthias",
  "state": "in",
  "holiday_days_remaining": null,
  "holiday_days_planned": null,
  "terminal_message": "Holiday balance is not available yet."
}
```

## 9. Server Time / Boot Version Check

Useful on boot.

```http
POST /terminal/boot-check
Content-Type: application/json
Authorization: Bearer <access_token>
```

Request:

```json
{
  "device_id": "terminal-raspi-12345678",
  "app_version": "0.1.0",
  "terminal_time": "2026-05-17T20:45:00+02:00",
  "hardware": {
    "rfid_reader": "ok"
  }
}
```

Success `200`:

```json
{
  "success": true,
  "server_time": "2026-05-17T20:45:01+02:00",
  "version_status": "ok",
  "terminal_status": "active"
}
```

Update available `200`:

```json
{
  "success": true,
  "server_time": "2026-05-17T20:45:01+02:00",
  "version_status": "update_available",
  "latest_version": "1.3.0",
  "message": "A newer terminal version is available."
}
```

Upgrade required `426`:

```json
{
  "success": false,
  "error_code": "upgrade_required",
  "message": "This terminal software version is no longer supported.",
  "minimum_version": "1.2.0"
}
```

## Why `request_id` Is Needed

`request_id` is the idempotency key for offline sync.

Offline sync can retry the same scan after a network failure. Example:

1. User scans badge.
2. Terminal saves the scan locally.
3. Terminal sends the scan to the server.
4. Server records it as check-in.
5. Wi-Fi drops before the terminal receives the success response.
6. Terminal still thinks the scan is unsynced.
7. Terminal retries later.

Without `request_id`, the retry looks like a new scan and could become a check-out.

With `request_id`, the server can detect:

```text
This exact scan was already processed.
```

Then it returns the original result instead of creating another stamp.