import logging
from typing import Any, Dict, Optional

import requests
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AuthenticatedSession:
    def __init__(self):
        self.session = requests.Session()
        self.jwt_token = None
        self.is_approved = False

        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def authenticate(self, silent=False) -> bool:
        """Attempts to authenticate against the server using configured credentials."""
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
        response = self.session.post(config.SCAN_URL, json=payload, timeout=10.0)
        if response.status_code == 401:
            logger.warning("Received 401 Unauthorized. Token may be expired. Attempting re-authentication...")
            if self.authenticate():
                response = self.session.post(config.SCAN_URL, json=payload, timeout=10.0)
        return response

    def _post_query_with_token_retry(self, payload: Dict[str, Any]) -> requests.Response:
        response = self.session.post(config.QUERY_URL, json=payload, timeout=10.0)
        if response.status_code == 401:
            logger.warning("Query: 401. Re-authenticating...")
            if self.authenticate():
                response = self.session.post(config.QUERY_URL, json=payload, timeout=10.0)
        return response

    def send_scan(
        self, card_uid: str, client_local_time_iso: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Posts a punch scan. Server assigns check-in vs check-out from history."""
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
        """Read-only: status / flextime / holiday UI — does not record a punch."""
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
        """Lightweight POST so the server can mark the device as recently connected."""
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
