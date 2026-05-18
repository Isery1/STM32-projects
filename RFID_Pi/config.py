"""
Central configuration for the Raspberry Pi RFID terminal.

Loads settings from environment variables (optionally via a local ``.env`` file), builds full HTTP URLs
for auth, scans, heartbeat, and status queries, and exposes a small validator used at process start.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from RFID_Pi/.env regardless of the process working directory.
load_dotenv(BASE_DIR / ".env")

# Server Configuration
# The production backend stores terminal data in backend_api.d_terminals,
# d_terminal_serials, d_terminal_api_keys, d_user_badges, and d_stamps.
SERVER_URL = os.getenv("SERVER_URL", "https://api.zk-digital.at/backend-api").rstrip('/')
ENROLL_ENDPOINT = os.getenv("ENROLL_ENDPOINT", "terminal/enroll").lstrip('/')
AUTH_ENDPOINT = os.getenv("AUTH_ENDPOINT", "terminal/login").lstrip('/')
SCAN_ENDPOINT = os.getenv("SCAN_ENDPOINT", "terminal/stamps").lstrip('/')
HEARTBEAT_ENDPOINT = os.getenv("HEARTBEAT_ENDPOINT", "terminal/heartbeat").lstrip('/')
BOOT_CHECK_ENDPOINT = os.getenv("BOOT_CHECK_ENDPOINT", "terminal/boot-check").lstrip('/')
QUERY_ENDPOINT = os.getenv("QUERY_ENDPOINT", "terminal/query").lstrip('/')
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

# Derived absolute URLs
# Handle query parameter base URLs (e.g., URL ends with '=' or '?') without prepending extraneous slashes
if SERVER_URL.endswith(('=', '?', '&')):
    ENROLL_URL = f"{SERVER_URL}{ENROLL_ENDPOINT}"
    AUTH_URL = f"{SERVER_URL}{AUTH_ENDPOINT}"
    SCAN_URL = f"{SERVER_URL}{SCAN_ENDPOINT}"
    HEARTBEAT_URL = f"{SERVER_URL}{HEARTBEAT_ENDPOINT}"
    BOOT_CHECK_URL = f"{SERVER_URL}{BOOT_CHECK_ENDPOINT}"
    QUERY_URL = f"{SERVER_URL}{QUERY_ENDPOINT}"
else:
    ENROLL_URL = f"{SERVER_URL}/{ENROLL_ENDPOINT}"
    AUTH_URL = f"{SERVER_URL}/{AUTH_ENDPOINT}"
    SCAN_URL = f"{SERVER_URL}/{SCAN_ENDPOINT}"
    HEARTBEAT_URL = f"{SERVER_URL}/{HEARTBEAT_ENDPOINT}"
    BOOT_CHECK_URL = f"{SERVER_URL}/{BOOT_CHECK_ENDPOINT}"
    QUERY_URL = f"{SERVER_URL}/{QUERY_ENDPOINT}"


# Device Credentials
DEVICE_ID = os.getenv("DEVICE_ID", "default-pi-device")
TERMINAL_SERIAL = os.getenv("TERMINAL_SERIAL", "").strip()
TERMINAL_API_KEY = os.getenv("TERMINAL_API_KEY", "").strip()
TERMINAL_API_KEY_FILE = os.getenv("TERMINAL_API_KEY_FILE", ".terminal_api_key")
TERMINAL_API_KEY_PATH = (
    Path(TERMINAL_API_KEY_FILE)
    if Path(TERMINAL_API_KEY_FILE).is_absolute()
    else BASE_DIR / TERMINAL_API_KEY_FILE
)
ADMIN_BADGE_UIDS = {
    uid.strip()
    for uid in os.getenv("ADMIN_BADGE_UIDS", "250256679402").split(",")
    if uid.strip()
}

# Behavioral Configuration
SCAN_COOLDOWN_SECONDS = float(os.getenv("SCAN_COOLDOWN_SECONDS", "2.0"))
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "45.0"))
_OFFLINE_QUEUE_DB_RAW = os.getenv("OFFLINE_QUEUE_DB", "offline_queue.sqlite3")
OFFLINE_QUEUE_DB = str(
    Path(_OFFLINE_QUEUE_DB_RAW)
    if Path(_OFFLINE_QUEUE_DB_RAW).is_absolute()
    else BASE_DIR / _OFFLINE_QUEUE_DB_RAW
)
OFFLINE_SYNC_BATCH_SIZE = int(os.getenv("OFFLINE_SYNC_BATCH_SIZE", "25"))
BOOT_TIME_SYNC_MAX_DRIFT_SECONDS = int(os.getenv("BOOT_TIME_SYNC_MAX_DRIFT_SECONDS", "60"))
KIOSK_FULLSCREEN = os.getenv("KIOSK_FULLSCREEN", "true").strip().lower() in ("1", "true", "yes", "on")


def validate_config() -> None:
    """
    Ensure the terminal has enough configuration for serial enrollment and API-key login.

    Raises:
        ValueError: With a short checklist message if anything required is missing.

    Side effect:
        Prints a friendly summary of endpoints to stdout when validation passes.
    """
    critical_vars = {
        "SERVER_URL": SERVER_URL,
        "DEVICE_ID": DEVICE_ID,
    }

    missing = [key for key, value in critical_vars.items() if not value]
    if missing:
        raise ValueError(f"Missing required configuration values: {', '.join(missing)}. "
                         f"Please check your environment variables or .env file.")

    print("--- Configuration Loaded Successfully ---")
    print(f"Server: {SERVER_URL}")
    print(f"Enroll Endpoint: {ENROLL_URL}")
    print(f"Auth Endpoint: {AUTH_URL}")
    print(f"Scan Endpoint: {SCAN_URL}")
    print(f"Heartbeat Endpoint: {HEARTBEAT_URL}")
    print(f"Boot Check Endpoint: {BOOT_CHECK_URL}")
    print(f"Query Endpoint: {QUERY_URL}")
    print(f"Device ID: {DEVICE_ID}")
    print(f"Terminal Serial: {'configured' if TERMINAL_SERIAL else 'missing (only OK after API-key file exists)'}")
    print(f"API Key Source: {'env' if TERMINAL_API_KEY else TERMINAL_API_KEY_PATH}")
    print(f"App Version: {APP_VERSION}")
    print("----------------------------------------")
