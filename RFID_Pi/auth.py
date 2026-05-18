"""
HTTP session for the ManageIO RFID terminal.

Handles device enrollment/login (terminal serial → API key → JWT), automatic re-auth when tokens
expire, and all POSTs the Pi needs: punches, read-only status queries, and lightweight heartbeats.
"""

import logging
import datetime
import json
import os
from typing import Any, Dict, Optional

import requests
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AuthenticatedSession:
    """Small wrapper around ``requests.Session`` with JWT lifecycle for the terminal API."""

    PERMANENT_SCAN_STATUSES = {400, 403, 404, 409}

    def __init__(self) -> None:
        """Create a fresh session with JSON headers; no token until :meth:`authenticate` succeeds."""
        self.session = requests.Session()
        self.jwt_token = None
        self.is_approved = False

        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    @staticmethod
    def _terminal_time() -> str:
        """Return the terminal clock with timezone for server diagnostics."""
        return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def _load_api_key(self) -> Optional[str]:
        """Read the stored API key, preferring an explicit environment override."""
        if config.TERMINAL_API_KEY:
            return config.TERMINAL_API_KEY

        key_file = config.TERMINAL_API_KEY_PATH
        try:
            if key_file.exists():
                key = key_file.read_text(encoding="utf-8").strip()
                if key:
                    return key
        except OSError as exc:
            logger.warning("Could not read terminal API key file '%s': %s", key_file, exc)
        return None

    def _store_api_key(self, api_key: str) -> bool:
        """Persist the one-time enrollment result so future boots do not need the serial."""
        key_file = config.TERMINAL_API_KEY_PATH
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(api_key.strip() + "\n", encoding="utf-8")
            if os.name == "posix":
                os.chmod(key_file, 0o600)
            return True
        except OSError as exc:
            logger.error("Could not store terminal API key in '%s': %s", key_file, exc)
            return False

    def has_api_key(self) -> bool:
        """Return ``True`` when login can use an existing API key instead of first enrollment."""
        return self._load_api_key() is not None

    def build_enroll_payload(self) -> Dict[str, Any]:
        """Build the exact JSON body used for first terminal enrollment."""
        return {
            "serial": config.TERMINAL_SERIAL,
            "device_id": config.DEVICE_ID,
            "app_version": config.APP_VERSION,
            "terminal_time": self._terminal_time(),
        }

    def enroll(self) -> Optional[str]:
        """
        Exchange the server-generated terminal serial for a unique API key.

        This is only expected to succeed once per pending enrollment serial. The received API key is stored
        locally and used for all future logins.
        """
        if not config.TERMINAL_SERIAL:
            logger.error(
                "Cannot enroll terminal: TERMINAL_SERIAL is missing and no stored API key was found."
            )
            return None

        payload = self.build_enroll_payload()
        logger.info("Enrolling terminal '%s' with server at %s...", config.DEVICE_ID, config.ENROLL_URL)
        logger.warning("Enrollment payload being sent: %s", json.dumps(payload, ensure_ascii=False))

        try:
            response = self.session.post(config.ENROLL_URL, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                data = response.json()
                api_key = str(data.get("api_key") or "").strip()
                if not api_key:
                    logger.error("Enrollment succeeded, but response did not include an api_key.")
                    return None
                if not self._store_api_key(api_key):
                    return None
                logger.info("✅ [SUCCESS] Terminal enrolled and API key stored locally.")
                return api_key

            try:
                data = response.json()
                detail = data.get("message") or data.get("error") or response.text
            except ValueError:
                detail = response.text
            logger.error("Terminal enrollment failed with HTTP %s: %s", response.status_code, detail)
            return None
        except requests.exceptions.RequestException as exc:
            logger.error("Connection error during terminal enrollment: %s", exc)
            return None

    def authenticate(self, silent: bool = False) -> bool:
        """
        Exchange ``DEVICE_ID`` / stored API key for a JWT and attach it to outgoing requests.

        Returns:
            ``True`` if the server returned HTTP 200 and a token body field; ``False`` on denial or error.

        Args:
            silent: If ``True``, skip the usual “connecting…” log line (used during token refresh bursts).
        """
        if not silent:
            logger.info(
                "Authenticating device '%s' with server at %s...",
                config.DEVICE_ID,
                config.AUTH_URL,
            )

        api_key = self._load_api_key()
        if not api_key:
            api_key = self.enroll()
            if not api_key:
                self.is_approved = False
                self.jwt_token = None
                return False

        payload = {
            "device_id": config.DEVICE_ID,
            "api_key": api_key,
            "app_version": config.APP_VERSION,
            "terminal_time": self._terminal_time(),
        }

        try:
            response = self.session.post(config.AUTH_URL, json=payload, timeout=10.0)

            if response.status_code == 200:
                data = response.json()
                self.jwt_token = data.get("token") or data.get("access_token")

                if not self.jwt_token:
                    logger.error("Authentication successful, but no token found in JSON response.")
                    return False

                self.session.headers.update(
                    {
                        "Authorization": f"Bearer {self.jwt_token}",
                        "X-Authorization": f"Bearer {self.jwt_token}",
                    }
                )

                self.is_approved = True
                logger.info("✅ [SUCCESS] Device fully authenticated and connected.")
                return True

            if response.status_code == 401:
                self.is_approved = False
                self.jwt_token = None
                logger.error(
                    "❌ [DENIED] Unauthorized! Verify this terminal's API key and server terminal record."
                )
                return False

            self.is_approved = False
            self.jwt_token = None
            logger.error("Authentication failed with HTTP %s: %s", response.status_code, response.text)
            return False

        except requests.exceptions.RequestException as e:
            logger.error("Connection error during authentication: %s", e)
            return False

    def _post_scan_with_token_retry(self, payload: Dict[str, Any]) -> requests.Response:
        """POST a punch; if the server answers 401, try a fresh login once and retry the same payload."""
        response = self.session.post(config.SCAN_URL, json=payload, timeout=10.0)
        if response.status_code == 401:
            logger.warning("Received 401 Unauthorized. Token may be expired. Attempting re-authentication...")
            if self.authenticate():
                response = self.session.post(config.SCAN_URL, json=payload, timeout=10.0)
        return response

    def _post_query_with_token_retry(self, payload: Dict[str, Any]) -> requests.Response:
        """POST a status query with the same 401 → re-auth → retry behaviour as scans."""
        response = self.session.post(config.QUERY_URL, json=payload, timeout=10.0)
        if response.status_code == 401:
            logger.warning("Query: 401. Re-authenticating...")
            if self.authenticate():
                response = self.session.post(config.QUERY_URL, json=payload, timeout=10.0)
        return response

    @staticmethod
    def _error_result(response: requests.Response, *, permanent: bool = False) -> Dict[str, Any]:
        """Convert a backend error response into a structured result the UI can display."""
        try:
            data = response.json()
        except ValueError:
            data = {}
        message = data.get("message") or data.get("error") or response.text or "Server rejected the request."
        error_code = data.get("error_code") or data.get("error") or "request_error"
        return {
            "success": False,
            "http_status": response.status_code,
            "error_code": error_code,
            "message": message,
            "terminal_message": message,
            "permanent": permanent,
        }

    def send_scan(
        self, card_uid: str, terminal_time_iso: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Send one badge read to the server; the API decides check-in vs check-out from history.

        Args:
            card_uid: Raw UID from the reader (always sent as a string in JSON).
            terminal_time_iso: Optional terminal clock string for ``d_stamps.terminal_time``.

        Returns:
            Parsed JSON dict on success, or ``None`` if auth or transport failed.
        """
        uid = str(card_uid).strip()
        payload: Dict[str, Any] = {"device_id": config.DEVICE_ID, "uid": uid}
        payload["terminal_time"] = terminal_time_iso or self._terminal_time()
        return self.send_scan_payload(payload)

    def send_scan_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Send a prepared scan payload to the server.

        Used by the offline queue so retried scans keep the original ``request_id`` and terminal time.
        """
        if not self.jwt_token:
            if not self.authenticate():
                logger.error("Cannot send scan: Authentication failed.")
                return None

        try:
            logger.warning("Stamp payload being sent: %s", json.dumps(payload, ensure_ascii=False))
            response = self._post_scan_with_token_retry(payload)

            if response.status_code in (200, 201, 202):
                try:
                    return response.json()
                except ValueError:
                    logger.info("Scan accepted but response was not JSON.")
                    return {"success": True, "uid": payload.get("uid"), "request_id": payload.get("request_id")}

            logger.error("Server rejected scan. Status: %s. Body: %s", response.status_code, response.text)
            permanent = response.status_code in self.PERMANENT_SCAN_STATUSES
            result = self._error_result(response, permanent=permanent)
            result["uid"] = payload.get("uid")
            result["request_id"] = payload.get("request_id")
            return result

        except requests.exceptions.RequestException as e:
            logger.error("Network connection error during scan transmission: %s", e)
            return None

    def send_status_query(self, card_uid: str, query_kind: str = "status") -> Optional[Dict[str, Any]]:
        """
        Ask the server for status text (and optional HR placeholders) **without** recording a punch.

        Args:
            card_uid: Badge UID.
            query_kind: ``status`` | ``flextime`` | ``holiday`` — only the wording changes server-side.

        Returns:
            JSON dict on success, ``None`` on failure.
        """
        uid = str(card_uid).strip()
        payload: Dict[str, Any] = {
            "device_id": config.DEVICE_ID,
            "uid": uid,
            "query_kind": query_kind,
        }
        if not self.jwt_token or not self.is_approved:
            if not self.authenticate():
                logger.error("Cannot query: Authentication failed.")
                return None
        try:
            response = self._post_query_with_token_retry(payload)
            if response.status_code in (200, 201, 202):
                try:
                    return response.json()
                except ValueError:
                    return {"success": False}
            logger.error("Status query failed: HTTP %s %s", response.status_code, response.text)
            result = self._error_result(response, permanent=response.status_code in (400, 403, 404))
            result["query_kind"] = query_kind
            result["query_failed"] = True
            return result
        except requests.exceptions.RequestException as e:
            logger.error("Status query connection error: %s", e)
            return None

    def send_heartbeat(self, pending_offline_stamps: Optional[int] = None) -> bool:
        """
        Ping the server so ``last_seen`` updates and the dashboard can show the terminal as online.

        Returns:
            ``True`` if any accepted HTTP status was returned after optional re-auth.
        """
        if not self.jwt_token:
            if not self.authenticate():
                return False
        try:
            payload = {
                "device_id": config.DEVICE_ID,
                "app_version": config.APP_VERSION,
                "terminal_time": self._terminal_time(),
            }
            if pending_offline_stamps is not None:
                payload["pending_offline_stamps"] = int(pending_offline_stamps)
            response = self.session.post(config.HEARTBEAT_URL, json=payload, timeout=10.0)
            if response.status_code == 401 and self.authenticate():
                response = self.session.post(config.HEARTBEAT_URL, json=payload, timeout=10.0)
            if response.status_code in (200, 201, 202):
                return True
            logger.warning("Heartbeat failed: HTTP %s %s", response.status_code, response.text)
            return False
        except requests.exceptions.RequestException as e:
            logger.warning("Heartbeat connection error: %s", e)
            return False

    def send_boot_check(self, hardware: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Report a successful local boot to the backend and receive server clock/version status.

        The backend stores this in ``d_terminal_events`` with event_kind ``boot``. Failure is
        non-fatal because the terminal must still work offline.
        """
        if not self.jwt_token:
            if not self.authenticate():
                return None
        payload: Dict[str, Any] = {
            "device_id": config.DEVICE_ID,
            "app_version": config.APP_VERSION,
            "terminal_time": self._terminal_time(),
            "hardware": hardware or {},
        }
        try:
            response = self.session.post(config.BOOT_CHECK_URL, json=payload, timeout=10.0)
            if response.status_code == 401 and self.authenticate():
                response = self.session.post(config.BOOT_CHECK_URL, json=payload, timeout=10.0)
            if response.status_code in (200, 201, 202):
                try:
                    return response.json()
                except ValueError:
                    return {"success": True}
            logger.warning("Boot check failed: HTTP %s %s", response.status_code, response.text)
            return None
        except requests.exceptions.RequestException as e:
            logger.warning("Boot check connection error: %s", e)
            return None
