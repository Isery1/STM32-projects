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

# Behavioral Configuration
SCAN_COOLDOWN_SECONDS = float(os.getenv("SCAN_COOLDOWN_SECONDS", "2.0"))
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "45.0"))


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
    print("----------------------------------------")
