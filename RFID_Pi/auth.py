"""
HTTP session for the ManageIO RFID terminal.

Handles device login (shared secret → JWT), automatic re-auth when tokens expire, and all POSTs the
Pi needs: punches, read-only status queries, and lightweight heartbeats so the dashboard stays “live”.
"""

import logging
from typing import Any, Dict, Optional

import requests
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AuthenticatedSession:
    """Small wrapper around ``requests.Session`` with JWT lifecycle for the terminal API."""

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

    def authenticate(self, silent: bool = False) -> bool:
        """
        Exchange ``DEVICE_ID`` / ``DEVICE_SECRET`` for a JWT and attach it to outgoing requests.

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

        payload = {"device_id": config.DEVICE_ID, "secret": config.DEVICE_SECRET}

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
                    "❌ [DENIED] Unauthorized! Verify that your 'DEVICE_SECRET' in .env matches your Server's key."
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

    def send_scan(
        self, card_uid: str, client_local_time_iso: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Send one badge read to the server; the API decides check-in vs check-out from history.

        Args:
            card_uid: Raw UID from the reader (always sent as a string in JSON).
            client_local_time_iso: Optional terminal clock string for the server log’s optional field.

        Returns:
            Parsed JSON dict on success, or ``None`` if auth or transport failed.
        """
        uid = str(card_uid).strip()
        payload: Dict[str, Any] = {"device_id": config.DEVICE_ID, "uid": uid}
        if client_local_time_iso:
            payload["client_local_time"] = client_local_time_iso

        if not self.jwt_token:
            if not self.authenticate():
                logger.error("Cannot send scan: Authentication failed.")
                return None

        try:
            response = self._post_scan_with_token_retry(payload)

            if response.status_code in (200, 201, 202):
                try:
                    return response.json()
                except ValueError:
                    logger.info("Scan accepted but response was not JSON.")
                    return {"success": True, "uid": uid}

            logger.error("Server rejected scan. Status: %s. Body: %s", response.status_code, response.text)
            return None

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
        if not self.jwt_token:
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
            return None
        except requests.exceptions.RequestException as e:
            logger.error("Status query connection error: %s", e)
            return None

    def send_heartbeat(self) -> bool:
        """
        Ping the server so ``last_seen`` updates and the dashboard can show the terminal as online.

        Returns:
            ``True`` if any accepted HTTP status was returned after optional re-auth.
        """
        if not self.jwt_token:
            if not self.authenticate():
                return False
        try:
            response = self.session.post(config.HEARTBEAT_URL, json={}, timeout=10.0)
            if response.status_code == 401 and self.authenticate():
                response = self.session.post(config.HEARTBEAT_URL, json={}, timeout=10.0)
            if response.status_code in (200, 201, 202):
                return True
            logger.warning("Heartbeat failed: HTTP %s %s", response.status_code, response.text)
            return False
        except requests.exceptions.RequestException as e:
            logger.warning("Heartbeat connection error: %s", e)
            return False
