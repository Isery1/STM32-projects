"""
Startup health checks before the main RFID loop or kiosk UI starts.

Runs three steps in order: (1) RC522 SPI sanity via the version register, (2) Linux network link state
plus a quick internet TCP probe, (3) reachability of the configured auth URL with a deliberate bad
secret (expect HTTP 401). GUI mode can pass ``on_step`` to animate a splash screen.

Interactive hardware test (version register + live tag loop, formerly ``test_hardware.py``)::

    python boot_sequence.py --diagnose

Environment:
    USE_MOCK_RFID — skip the hardware step (development / no reader).
    SKIP_BOOT_CHECKS — skip the entire sequence (recovery or CI).
"""

from __future__ import annotations

import argparse
import datetime
import email.utils
import logging
import os
import socket
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

import requests

import config

logger = logging.getLogger(__name__)

MOCK_ENV = "USE_MOCK_RFID"
SKIP_ENV = "SKIP_BOOT_CHECKS"

PREFERRED_INTERFACES = ("eth0", "wlan0")

OnStep = Optional[Callable[[str, str, str], None]]


class BootCheckFailed(RuntimeError):
    """Raised when a boot step fails in GUI mode instead of calling ``sys.exit``."""

    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _env_truthy(name: str) -> bool:
    """Return True if ``name`` is set to a common “yes” string (1, true, yes, on)."""
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def skip_boot_checks_requested() -> bool:
    """True if ``SKIP_BOOT_CHECKS`` asks us to bypass every startup check."""
    return _env_truthy(SKIP_ENV)


def _read_sys(path: str) -> Optional[str]:
    """Read a small sysfs file (e.g. operstate); return None if missing or unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def _interface_states() -> Dict[str, Dict[str, str]]:
    """
    Collect ``operstate`` and ``carrier`` for each kernel network interface (Linux).

    Returns:
        Map ``ifname`` → ``{operstate, carrier}``. Empty dict if ``/sys/class/net`` is unavailable.
    """
    base = "/sys/class/net"
    out: Dict[str, Dict[str, str]] = {}
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if name == "lo":
            continue
        oper = _read_sys(os.path.join(base, name, "operstate")) or "unknown"
        carrier = _read_sys(os.path.join(base, name, "carrier"))
        out[name] = {"operstate": oper, "carrier": carrier or ""}
    return out


def _any_connected(states: Dict[str, Dict[str, str]]) -> Tuple[bool, List[str]]:
    """
    Decide whether at least one non-loopback interface looks suitable for routing.

    Returns:
        (ok, notes) where ``notes`` is one human-readable line per interface (for debug logs).
    """
    notes: List[str] = []
    ok_any = False
    for name, meta in sorted(states.items()):
        oper = meta.get("operstate", "").lower()
        carrier_s = meta.get("carrier", "")
        carrier_ok = carrier_s in ("", "1")
        up = oper == "up" and carrier_ok
        if oper == "up" and carrier_s == "0":
            up = False
        notes.append(f"{name}: operstate={meta.get('operstate')} carrier={carrier_s or 'n/a'}")
        if up:
            ok_any = True
    return ok_any, notes


def _abort(code: int, msg: str, *, cli_exit: bool) -> None:
    """Log the failure, then either ``sys.exit`` (CLI) or raise :class:`BootCheckFailed` (GUI)."""
    logger.error("Boot check failed: %s", msg)
    if cli_exit:
        sys.exit(code)
    raise BootCheckFailed(code, msg)


def _rc522_version_summary(version: int) -> str:
    """Short human label for the MFRC522 version register (for logs and kiosk boot line)."""
    if version == 0x91:
        return "NXP MFRC522 v1.0 — reader OK"
    if version == 0x92:
        return "NXP MFRC522 v2.0 — reader OK"
    if version == 0x88:
        return "Compatible MFRC522 — reader OK"
    if version in (0x00, 0xFF):
        return "No response (check SPI / solder / wiring)"
    return f"Version 0x{version:02X} — unexpected; check wiring"


def _rc522_comm_failure_help() -> str:
    """Multi-line help for dead SPI reads (embedded in :exc:`RuntimeError` messages)."""
    return (
        "RC522 not responding (0x00/0xFF). Quick checks:\n"
        "• Solder header pins — press-fit alone usually fails.\n"
        "• Swap check: MOSI (pin 19) vs MISO (pin 21).\n"
        "• SDA/SS → GPIO8 / CE0 (physical pin 24).\n"
        "• Close other apps using SPI (only one process at a time)."
    )


def check_rc522_version_register(emit: OnStep = None) -> None:
    """
    Talk to the MFRC522 over SPI and read the version register (0x37).

    Raises:
        RuntimeError: If the reader returns a dead pattern (0x00 / 0xFF), with troubleshooting text.

    Args:
        emit: Optional UI callback ``(phase, state, detail)`` for kiosk startup screens.
    """
    from mfrc522 import MFRC522  # pylint: disable=import-outside-toplevel
    import RPi.GPIO as GPIO  # pylint: disable=import-outside-toplevel

    if emit:
        emit("hardware", "running", "")
    reader = MFRC522()
    try:
        version = reader.Read_MFRC522(0x37)
        logger.info("RC522 VersionReg (0x37): 0x%02X — %s", version, _rc522_version_summary(version))
        if version in (0x00, 0xFF):
            raise RuntimeError(_rc522_comm_failure_help())
        if version not in (0x88, 0x91, 0x92):
            logger.warning("Unexpected VersionReg 0x%02X — SPI may be marginal; continuing.", version)
        if emit:
            emit("hardware", "ok", _rc522_version_summary(version))
    finally:
        GPIO.cleanup()


def run_interactive_hardware_diagnostics() -> None:
    """
    Console utility: print version interpretation, then loop reading tags (Ctrl+C to stop).

    Replaces the former ``test_hardware.py`` script; keep for bench bring-up only.
    """
    try:
        from mfrc522 import MFRC522
        import RPi.GPIO as GPIO
    except ImportError:
        print("❌ ERROR: RPi.GPIO or mfrc522 library not installed!")
        print("Run this on the Pi inside your project virtual environment.")
        sys.exit(1)

    print("\n" + "=" * 50)
    print("RC522 RFID — hardware check & live tag test")
    print("=" * 50)
    print("[1/3] Initializing GPIO & SPI…")

    try:
        reader = MFRC522()
        version = reader.Read_MFRC522(0x37)

        print("\n[2/3] Version register 0x37 reads: 0x%02X" % version)
        print("→", _rc522_version_summary(version))

        if version in (0x00, 0xFF):
            print("\n" + _rc522_comm_failure_help())
            return

        print("\n[3/3] Hold a card on the reader — printing UIDs (Ctrl+C to stop)…\n")

        try:
            last_scan_time = 0.0
            while True:
                (status, _tag_type) = reader.MFRC522_Request(reader.PICC_REQIDL)
                if status == reader.MI_OK:
                    now = time.time()
                    if now - last_scan_time > 1.0:
                        print("Tag detected — reading UID…")
                        (status2, uid) = reader.MFRC522_Anticoll()
                        if status2 == reader.MI_OK:
                            formatted = "-".join(f"{x:02X}" for x in uid[:4])
                            print("UID:", formatted)
                            last_scan_time = now
                        else:
                            print("Anticollision failed (try again).")
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopped by user.")

    except Exception as err:
        print("\n❌ Error:", err)
        print("If main.py or the kiosk is running, stop it first — only one process may use SPI.")
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up.\n")


def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> None:
    """Open a TCP connection to host:port; raises ``OSError`` on failure (simple connectivity litmus test)."""
    with socket.create_connection((host, port), timeout=timeout):
        pass


def check_network(emit: OnStep = None) -> None:
    """
    Require at least one up interface with carrier (when sysfs exposes it), then probe Cloudflare DNS (1.1.1.1:443).

    On non-Linux hosts without sysfs, only the TCP probe runs.

    Raises:
        RuntimeError: If nothing looks valid or the probe cannot connect.

    Args:
        emit: Optional UI callback for progress labels.
    """
    if emit:
        emit("network", "running", "")
    states = _interface_states()
    lines: List[str] = []
    if not states:
        logger.warning("No /sys/class/net data — not Linux or restricted; skipping interface enumeration.")
        try:
            _tcp_probe("1.1.1.1", 443, timeout=4.0)
            if emit:
                emit("network", "ok", "Internet reachable (no interface details on this OS)")
            logger.info("Internet reachability: OK (TCP %s:%s).", "1.1.1.1", 443)
        except OSError as e:
            raise RuntimeError(f"No interface state available and internet probe failed: {e}") from e
        return

    for iface in PREFERRED_INTERFACES:
        if iface in states:
            meta = states[iface]
            line = f"{iface}: {meta['operstate']}"
            lines.append(line)
            logger.info("Interface %s: operstate=%s carrier=%s", iface, meta["operstate"], meta["carrier"] or "n/a")

    ok, notes = _any_connected(states)
    for line in notes:
        logger.debug("%s", line)
    if not ok:
        raise RuntimeError(
            "No network interface appears up with carrier. "
            "Connect Ethernet or join Wi‑Fi, then retry."
        )

    try:
        _tcp_probe("1.1.1.1", 443, timeout=4.0)
        logger.info("Internet reachability: OK (TCP to 1.1.1.1:443).")
    except OSError as e:
        raise RuntimeError(
            f"Interfaces are up but internet probe failed ({e}). "
            "Check gateway, DNS, or firewall."
        ) from e

    if emit:
        detail = "; ".join(lines) if lines else "Interfaces up"
        emit("network", "ok", f"{detail} — internet OK")


def _parse_http_date(value: str) -> Optional[datetime.datetime]:
    """Parse an HTTP Date header as timezone-aware UTC."""
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _set_system_time_utc(server_dt: datetime.datetime) -> Tuple[bool, str]:
    """
    Best-effort system clock update.

    The desktop autostart user normally cannot change system time; non-root runs try passwordless
    ``sudo -n`` and otherwise keep the Pi clock.
    """
    if os.name != "posix":
        return False, "time set skipped (not a Linux/Pi host)"
    timestamp = server_dt.strftime("%Y-%m-%d %H:%M:%S")
    cmd = ["date", "-u", "-s", timestamp]
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() != 0:
        cmd = ["sudo", "-n", *cmd]
    try:
        subprocess.run(
            cmd,
            check=True,
            timeout=8.0,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"time set failed (needs root or passwordless sudo): {exc}"
    return True, "Pi clock updated from server"


def check_server_time(emit: OnStep = None) -> Optional[datetime.datetime]:
    """
    Read the server's HTTP Date header and align the Pi clock when drift is large.

    Returns:
        Parsed server time when available, otherwise ``None`` so callers can continue offline.
    """
    if emit:
        emit("time", "running", "")
    try:
        response = requests.get(config.SERVER_URL, timeout=8.0)
    except requests.exceptions.RequestException as exc:
        if emit:
            emit("time", "skip", "Offline — using this terminal's clock")
        logger.warning("Server time check skipped: %s", exc)
        return None

    server_dt = _parse_http_date(response.headers.get("Date", ""))
    if server_dt is None:
        if emit:
            emit("time", "skip", "Server did not provide a clock header")
        logger.warning("Server time check: no parseable Date header.")
        return None

    local_dt = datetime.datetime.now(datetime.timezone.utc)
    drift = abs((server_dt - local_dt).total_seconds())
    if drift <= config.BOOT_TIME_SYNC_MAX_DRIFT_SECONDS:
        if emit:
            emit("time", "ok", f"Clock OK (drift {int(drift)}s)")
        logger.info("Server time check: OK (drift %.1fs).", drift)
        return server_dt

    ok, detail = _set_system_time_utc(server_dt)
    if emit:
        state = "ok" if ok else "skip"
        emit("time", state, f"{detail} (drift {int(drift)}s)")
    if ok:
        logger.info("Server time check: %s (drift %.1fs).", detail, drift)
    else:
        logger.warning("Server time check: %s (drift %.1fs).", detail, drift)
    return server_dt


def check_auth_endpoint_expect_401(emit: OnStep = None) -> None:
    """
    POST invalid credentials to the login URL — success means we got a live app that rejects bad secrets.

    Raises:
        RuntimeError: On connection errors or if HTTP 200 somehow accepts the probe secret.

    Args:
        emit: Optional UI callback.
    """
    if emit:
        emit("server", "running", "")
    payload = {"device_id": config.DEVICE_ID, "secret": "__boot_probe_invalid__"}
    try:
        r = requests.post(config.AUTH_URL, json=payload, timeout=12.0)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Auth endpoint unreachable ({config.AUTH_URL}): {e}") from e
    if r.status_code == 401:
        logger.info("Server auth endpoint: OK (401 on invalid secret as expected).")
        if emit:
            emit("server", "ok", "Auth endpoint OK (server rejected bad secret)")
        return
    if r.status_code == 200:
        raise RuntimeError(
            "Auth endpoint accepted invalid boot probe secret (HTTP 200). "
            "Check SERVER_URL / AUTH_ENDPOINT and server configuration."
        )
    logger.warning(
        "Auth probe HTTP %s (expected 401). Continuing if server returned an error page.",
        r.status_code,
    )
    if emit:
        emit("server", "ok", f"Server answered (HTTP {r.status_code}); check auth config if unexpected")


def run_boot_checks(on_step: OnStep = None, *, cli_exit: bool = True) -> None:
    """
    Run the full boot sequence, or return immediately if ``SKIP_BOOT_CHECKS`` is set.

    Args:
        on_step: Optional ``callable(phase, state, detail)`` for kiosk UI updates.
        cli_exit: If ``True`` (default), fatal errors call ``sys.exit`` with codes 2–4; if ``False``,
            raise :class:`BootCheckFailed` so the GUI can show the message.

    Phases for ``on_step``:
        ``hardware``, ``network``, ``time``, ``server`` — states ``running``, ``skip``, ``ok``, ``error``.
    """
    if skip_boot_checks_requested():
        logger.info("SKIP_BOOT_CHECKS set — skipping startup checks.")
        return

    logger.info("Boot sequence: hardware (RFID) → network/time/server (offline allowed)…")

    if _env_truthy(MOCK_ENV):
        logger.info("USE_MOCK_RFID — skipping RC522 hardware probe.")
        if on_step:
            on_step("hardware", "skip", "Mock reader (no hardware probe)")
    else:
        try:
            check_rc522_version_register(on_step)
            logger.info("RC522 hardware check: OK.")
        except Exception as e:  # pragma: no cover - hardware path
            if on_step:
                on_step("hardware", "error", str(e))
            logger.error("RC522 hardware check failed: %s", e)
            _abort(2, str(e), cli_exit=cli_exit)

    try:
        check_network(on_step)
    except Exception as e:
        if on_step:
            parts = str(e).split(". ", 1)
            on_step("network", "skip", parts[0] if parts else str(e))
        logger.warning("Network check failed; continuing offline: %s", e)

    check_server_time(on_step)

    try:
        check_auth_endpoint_expect_401(on_step)
    except Exception as e:
        if on_step:
            on_step("server", "skip", "Offline — scans will be saved locally")
        logger.warning("Server check failed; continuing offline: %s", e)

    logger.info("Boot sequence completed successfully.")


def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="Run boot checks from the shell, or --diagnose for interactive RC522 testing.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Interactive RC522 test in this terminal (version register + live UID loop); for bench bring-up only.",
    )
    args = parser.parse_args()
    if args.diagnose:
        run_interactive_hardware_diagnostics()
        return
    run_boot_checks()


if __name__ == "__main__":
    _cli_main()
