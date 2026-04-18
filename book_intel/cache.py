"""Per-book enrichment cache.

Source responses rarely change meaningfully within a week — bestseller rank moves
slowly, editor commentary almost never changes, YouTube review videos accrete but
the top ones stay stable. A 7-day TTL keeps the composer cheap and removes most
noise from rate-limited partners.

SQLite is the canonical store (mirrors the analytics DB layout so operators don't
have to think about multiple DB paths). Firestore mirror can be added later
without breaking the interface; everything goes through `get/set` here.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

_DEFAULT_TTL_SECONDS = 7 * 24 * 3600


class EnrichmentCache:
    """Cache keyed on (source_name, identifier), values JSON-encoded."""

    def __init__(self, db_path: str, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self.db_path = str(Path(db_path))
        self.ttl_seconds = max(60, int(ttl_seconds))
        parent = os.path.dirname(self.db_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as cx:
            cx.execute("PRAGMA journal_mode=WAL")
            cx.execute(
                """
                CREATE TABLE IF NOT EXISTS book_intel_cache (
                    source       TEXT NOT NULL,
                    identifier   TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    expires_at   INTEGER NOT NULL,
                    PRIMARY KEY (source, identifier)
                )
                """
            )

    def get(self, source: str, identifier: str) -> Any:
        if not source or not identifier:
            return None
        now = int(time.time())
        with sqlite3.connect(self.db_path) as cx:
            row = cx.execute(
                "SELECT payload_json, expires_at FROM book_intel_cache WHERE source = ? AND identifier = ?",
                (source, str(identifier)),
            ).fetchone()
        if not row:
            return None
        payload_json, expires_at = row
        if int(expires_at) < now:
            return None
        try:
            return json.loads(payload_json)
        except Exception:
            return None

    def set(self, source: str, identifier: str, payload: Any) -> None:
        if not source or not identifier:
            return
        expires_at = int(time.time()) + self.ttl_seconds
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
        except Exception:
            return
        with sqlite3.connect(self.db_path) as cx:
            cx.execute(
                """
                INSERT INTO book_intel_cache (source, identifier, payload_json, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source, identifier) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    expires_at   = excluded.expires_at
                """,
                (source, str(identifier), payload_json, expires_at),
            )

    def purge_expired(self) -> int:
        now = int(time.time())
        with sqlite3.connect(self.db_path) as cx:
            cur = cx.execute("DELETE FROM book_intel_cache WHERE expires_at < ?", (now,))
            return cur.rowcount or 0


__all__ = ["EnrichmentCache"]
