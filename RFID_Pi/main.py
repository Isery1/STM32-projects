"""
Headless Raspberry Pi entrypoint for the ManageIO RFID terminal.

After configuration and optional startup checks, opens the MFRC522 reader (or a mock on non-Pi machines),
keeps a JWT session alive, sends periodic heartbeats, and posts every successful tag read to the server.
Use ``python main.py --gui`` to launch the kiosk UI defined in :mod:`gui_poc` instead of this loop.
"""

import sys
import os
import time
import logging
import json
import config
from auth import AuthenticatedSession
from offline_queue import OfflineScanQueue, local_now_iso

# Setup Logging (Outputs to both Console and a local log file for remote debugging)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("app_debug.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

# Dynamic Hardware Loading (Supports Local Mocking)
IS_MOCK = False
try:
    # Allow forcing mock behavior via environment flag
    if os.getenv("USE_MOCK_RFID", "False").lower() in ("true", "1", "yes"):
        raise ImportError("Forced local mock via environment setting")

    import RPi.GPIO as GPIO
    from mfrc522 import SimpleMFRC522
    logger.info("Detected Raspberry Pi environment. Loaded RPi.GPIO and SimpleMFRC522 hardware drivers.")
except ImportError as e:
    from mock_rfid import MockSimpleMFRC522 as SimpleMFRC522
    IS_MOCK = True
    logger.warning("Could not load hardware libraries. Falling back to Simulated Mock Reader.")
    logger.warning(f"Root cause: {e}")


def main(skip_boot_checks: bool = False) -> None:
    """
    Run the blocking scan loop: authenticate, heartbeat, read tag IDs, post punches.

    Args:
        skip_boot_checks: If ``True``, skip RFID/network/server verification (``--no-boot-check``).
    """
    logger.info("Starting Raspberry Pi RFID Scanner Application...")

    # 1. Config validation
    try:
        config.validate_config()
    except ValueError as err:
        logger.critical(f"Configuration Error: {err}")
        sys.exit(1)

    if not skip_boot_checks:
        from boot_sequence import run_boot_checks

        run_boot_checks()

    # 2. Instancing drivers and web session
    reader = SimpleMFRC522()
    api_session = AuthenticatedSession()
    scan_queue = OfflineScanQueue()

    # 3. Self-Healing Operational Loop
    last_hb = 0.0
    try:
        active_status_logged = False
        while True:
            if not active_status_logged:
                logger.info("RFID reader active: scans are saved locally first, then synced when online.")
                active_status_logged = True

            now_m = time.monotonic()
            if now_m - last_hb >= config.HEARTBEAT_INTERVAL_SECONDS:
                if api_session.send_heartbeat():
                    logger.debug("Heartbeat OK.")
                last_hb = now_m
                sync_summary = scan_queue.sync_pending(api_session)
                if sync_summary["uploaded"]:
                    logger.info(
                        "Synced %s queued scan(s); %s still pending.",
                        sync_summary["uploaded"],
                        sync_summary["pending"],
                    )

            try:
                card_id = reader.read_id()

                if card_id:
                    card_uid_str = str(card_id).strip()
                    logger.info("*** RFID TAG SCANNED -> ID: %s ***", card_uid_str)
                    local_ts = local_now_iso()
                    queued = scan_queue.enqueue_scan(card_uid_str, client_local_time=local_ts)
                    logger.info(
                        "Scan saved locally: request_id=%s pending=%s",
                        queued["request_id"],
                        scan_queue.pending_count(),
                    )

                    if api_session.is_approved:
                        sync_summary = scan_queue.sync_pending(api_session, limit=config.OFFLINE_SYNC_BATCH_SIZE)
                    else:
                        sync_summary = {"uploaded": 0, "failed": 0, "pending": scan_queue.pending_count()}
                    result = scan_queue.get_by_request_id(queued["request_id"])
                    if result.get("status") == "synced":
                        logger.info(
                            "Scan synced: request_id=%s pending=%s",
                            queued["request_id"],
                            sync_summary["pending"],
                        )
                    else:
                        logger.warning(
                            "Scan kept offline; %s scan(s) waiting to sync.",
                            sync_summary["pending"],
                        )

                    response_text = (result or {}).get("server_response") or ""
                    response = {}
                    if response_text:
                        try:
                            response = json.loads(response_text)
                        except ValueError:
                            response = {}
                    if response.get("success"):
                        tm = response.get("terminal_message")
                        if tm:
                            logger.info("Terminal message: %s", tm)
                        logger.info(
                            "Punch recorded: %s | UID %s | server_time=%s | client_time=%s",
                            response.get("event"),
                            card_uid_str,
                            response.get("server_time"),
                            local_ts,
                        )
                    time.sleep(config.SCAN_COOLDOWN_SECONDS)

            except KeyboardInterrupt:
                raise
            except Exception as ex:
                logger.error("Loop error occurred: %s", ex)
                time.sleep(2.0)

    except KeyboardInterrupt:
        logger.info("User terminated application. Cleaning up GPIO...")
    finally:
        if not IS_MOCK:
            try:
                GPIO.cleanup()
                logger.info("GPIO successfully cleaned up.")
            except Exception as cleanup_err:
                logger.error(f"Failed to clean up GPIO: {cleanup_err}")
        logger.info("Application shutdown complete.")


if __name__ == "__main__":
    # CLI: headless loop by default, or ``--gui`` for the kiosk (see :mod:`gui_poc`).
    import argparse

    parser = argparse.ArgumentParser(description="RFID Pi client")
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Run Tkinter kiosk (local display + uploads to configured server)",
    )
    parser.add_argument(
        "--no-boot-check",
        action="store_true",
        help="Skip startup RFID/network/server checks (dev or recovery)",
    )
    args = parser.parse_args()
    if args.gui:
        from gui_poc import run_kiosk

        run_kiosk(skip_boot_checks=args.no_boot_check)
    else:
        main(skip_boot_checks=args.no_boot_check)
