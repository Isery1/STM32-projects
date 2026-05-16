"""
Central configuration for the Raspberry Pi RFID terminal.

Loads settings from environment variables (optionally via a local ``.env`` file), builds full HTTP URLs
for auth, scans, heartbeat, and status queries, and exposes a small validator used at process start.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Server Configuration
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:5000").rstrip('/')
AUTH_ENDPOINT = os.getenv("AUTH_ENDPOINT", "/api/auth/login").lstrip('/')
SCAN_ENDPOINT = os.getenv("SCAN_ENDPOINT", "/api/scans").lstrip('/')
HEARTBEAT_ENDPOINT = os.getenv("HEARTBEAT_ENDPOINT", "heartbeat").lstrip('/')
QUERY_ENDPOINT = os.getenv("QUERY_ENDPOINT", "query_punch_status").lstrip('/')
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

# Derived absolute URLs
# Handle query parameter base URLs (e.g., URL ends with '=' or '?') without prepending extraneous slashes
if SERVER_URL.endswith(('=', '?', '&')):
    AUTH_URL = f"{SERVER_URL}{AUTH_ENDPOINT}"
    SCAN_URL = f"{SERVER_URL}{SCAN_ENDPOINT}"
    HEARTBEAT_URL = f"{SERVER_URL}{HEARTBEAT_ENDPOINT}"
    QUERY_URL = f"{SERVER_URL}{QUERY_ENDPOINT}"
else:
    AUTH_URL = f"{SERVER_URL}/{AUTH_ENDPOINT}"
    SCAN_URL = f"{SERVER_URL}/{SCAN_ENDPOINT}"
    HEARTBEAT_URL = f"{SERVER_URL}/{HEARTBEAT_ENDPOINT}"
    QUERY_URL = f"{SERVER_URL}/{QUERY_ENDPOINT}"


# Device Credentials
DEVICE_ID = os.getenv("DEVICE_ID", "default-pi-device")
DEVICE_SECRET = os.getenv("DEVICE_SECRET", "dev-secret")
ADMIN_BADGE_UIDS = {
    uid.strip()
    for uid in os.getenv("ADMIN_BADGE_UIDS", "250256679402").split(",")
    if uid.strip()
}

# Behavioral Configuration
SCAN_COOLDOWN_SECONDS = float(os.getenv("SCAN_COOLDOWN_SECONDS", "2.0"))
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "45.0"))
OFFLINE_QUEUE_DB = os.getenv("OFFLINE_QUEUE_DB", "offline_queue.sqlite3")
OFFLINE_SYNC_BATCH_SIZE = int(os.getenv("OFFLINE_SYNC_BATCH_SIZE", "25"))
BOOT_TIME_SYNC_MAX_DRIFT_SECONDS = int(os.getenv("BOOT_TIME_SYNC_MAX_DRIFT_SECONDS", "60"))
KIOSK_FULLSCREEN = os.getenv("KIOSK_FULLSCREEN", "true").strip().lower() in ("1", "true", "yes", "on")


def validate_config() -> None:
    """
    Ensure ``SERVER_URL``, ``DEVICE_ID``, and ``DEVICE_SECRET`` are non-empty before the app runs.

    Raises:
        ValueError: With a short checklist message if anything required is missing.

    Side effect:
        Prints a friendly summary of endpoints to stdout when validation passes.
    """
    critical_vars = {
        "SERVER_URL": SERVER_URL,
        "DEVICE_ID": DEVICE_ID,
        "DEVICE_SECRET": DEVICE_SECRET
    }

    missing = [key for key, value in critical_vars.items() if not value]
    if missing:
        raise ValueError(f"Missing required configuration values: {', '.join(missing)}. "
                         f"Please check your environment variables or .env file.")

    print("--- Configuration Loaded Successfully ---")
    print(f"Server: {SERVER_URL}")
    print(f"Auth Endpoint: {AUTH_URL}")
    print(f"Scan Endpoint: {SCAN_URL}")
    print(f"Heartbeat Endpoint: {HEARTBEAT_URL}")
    print(f"Query Endpoint: {QUERY_URL}")
    print(f"Device ID: {DEVICE_ID}")
    print(f"App Version: {APP_VERSION}")
    print("----------------------------------------")
