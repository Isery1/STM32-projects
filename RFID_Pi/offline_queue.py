"""
Local durable scan queue for offline-first RFID operation.

Each badge scan is written to SQLite before any network upload is attempted. The server still decides
the official in/out event; the queue only preserves scan facts and retry state.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with timezone."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def local_now_iso() -> str:
    """Return an ISO-8601 local timestamp with timezone if the OS knows it."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class OfflineScanQueue:
    """Small SQLite-backed queue for scans that still need server confirmation."""

    def __init__(self, db_path: str = config.OFFLINE_QUEUE_DB) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS local_scan_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    client_local_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    server_response TEXT,
                    server_stamp_id TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_sync_error TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    synced_at_utc TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_scan_queue_status_id
                ON local_scan_queue (status, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )

    def enqueue_scan(self, uid: str, client_local_time: Optional[str] = None) -> Dict[str, Any]:
        """Insert a pending scan and return the row as a plain dict."""
        now = utc_now_iso()
        local_time = client_local_time or local_now_iso()
        request_id = f"{config.DEVICE_ID}-{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO local_scan_queue (
                    request_id, device_id, uid, client_local_time, status, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (request_id, config.DEVICE_ID, str(uid).strip(), local_time, now, now),
            )
        return self.get_by_request_id(request_id)

    def get_by_request_id(self, request_id: str) -> Dict[str, Any]:
        """Return one queued scan by request id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM local_scan_queue WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return dict(row)

    def pending_count(self) -> int:
        """Number of rows still waiting for a successful server response."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM local_scan_queue WHERE status IN ('pending', 'failed')"
            ).fetchone()
        return int(row["n"])

    def status_counts(self) -> Dict[str, int]:
        """Return queue counts grouped by status for the admin page."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM local_scan_queue
                GROUP BY status
                """
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def recent_scans(self, limit: int = 8) -> List[Dict[str, Any]]:
        """Newest local queue rows for admin inspection."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, request_id, uid, client_local_time, status, retry_count, last_sync_error, synced_at_utc
                FROM local_scan_queue
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def issue_scans(self, limit: int = 8) -> List[Dict[str, Any]]:
        """Rows likely needing attention: failed retries first, then pending uploads."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, request_id, uid, client_local_time, status, retry_count, last_sync_error, synced_at_utc
                FROM local_scan_queue
                WHERE status IN ('failed', 'pending')
                ORDER BY
                    CASE status WHEN 'failed' THEN 0 ELSE 1 END,
                    id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_scans(self, limit: int = config.OFFLINE_SYNC_BATCH_SIZE) -> List[Dict[str, Any]]:
        """Oldest pending/failed scans for retry."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM local_scan_queue
                WHERE status IN ('pending', 'failed')
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_synced(self, request_id: str, response: Dict[str, Any]) -> None:
        """Mark a scan as accepted by the server."""
        now = utc_now_iso()
        stamp_id = response.get("server_stamp_id") or response.get("stamp_id") or ""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE local_scan_queue
                SET status = 'synced',
                    server_response = ?,
                    server_stamp_id = ?,
                    last_sync_error = NULL,
                    updated_at_utc = ?,
                    synced_at_utc = ?
                WHERE request_id = ?
                """,
                (json.dumps(response, ensure_ascii=False), str(stamp_id), now, now, request_id),
            )

    def mark_failed(self, request_id: str, error: str) -> None:
        """Keep a scan for later retry and record the latest error."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE local_scan_queue
                SET status = 'failed',
                    retry_count = retry_count + 1,
                    last_sync_error = ?,
                    updated_at_utc = ?
                WHERE request_id = ?
                """,
                (error[:500], utc_now_iso(), request_id),
            )

    def set_state(self, key: str, value: Any) -> None:
        """Persist a small sync/status value."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (key, value, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at_utc = excluded.updated_at_utc
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now_iso()),
            )

    def sync_pending(self, api_session, limit: int = config.OFFLINE_SYNC_BATCH_SIZE) -> Dict[str, Any]:
        """
        Try to upload queued scans one by one.

        Stops at the first network/auth failure to avoid blocking the UI with repeated timeouts.
        """
        uploaded = 0
        failed = 0
        last_error = ""
        for row in self.pending_scans(limit=limit):
            payload = {
                "device_id": row["device_id"],
                "uid": row["uid"],
                "client_local_time": row["client_local_time"],
                "request_id": row["request_id"],
            }
            result = api_session.send_scan_payload(payload)
            if result and result.get("success"):
                self.mark_synced(row["request_id"], result)
                uploaded += 1
                continue
            failed += 1
            last_error = "Server unavailable or rejected scan"
            self.mark_failed(row["request_id"], last_error)
            if not getattr(api_session, "is_approved", False):
                break
        pending = self.pending_count()
        self.set_state(
            "last_sync",
            {
                "uploaded": uploaded,
                "failed": failed,
                "pending": pending,
                "last_error": last_error,
                "time": utc_now_iso(),
            },
        )
        return {"uploaded": uploaded, "failed": failed, "pending": pending, "last_error": last_error}
