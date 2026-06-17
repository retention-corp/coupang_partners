"""Tests for the AnalyticsStore client_id column + get_recent_queries_for_client."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from analytics import AnalyticsStore


class AnalyticsClientIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "a.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip_by_client(self) -> None:
        store = AnalyticsStore(self.path)
        store.record_assist(
            query_text="엔지니어링 책 추천",
            budget=None, category="book",
            evidence_snippets=[], recommendations=[],
            client_id="cli-1",
        )
        store.record_assist(
            query_text="스타트업 책",
            budget=None, category="book",
            evidence_snippets=[], recommendations=[],
            client_id="cli-1",
        )
        store.record_assist(
            query_text="전혀 다른 손님",
            budget=None, category="book",
            evidence_snippets=[], recommendations=[],
            client_id="cli-2",
        )

        rows = store.get_recent_queries_for_client("cli-1", limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["query_text"] for row in rows}, {"엔지니어링 책 추천", "스타트업 책"})
        self.assertEqual(rows[0]["category"], "book")

    def test_missing_client_id_returns_empty(self) -> None:
        store = AnalyticsStore(self.path)
        self.assertEqual(store.get_recent_queries_for_client(""), [])
        self.assertEqual(store.get_recent_queries_for_client(None), [])

    def test_record_assist_without_client_id_is_null(self) -> None:
        store = AnalyticsStore(self.path)
        store.record_assist(
            query_text="anonymous query",
            budget=None, category="book",
            evidence_snippets=[], recommendations=[],
        )
        # No client_id → no entries under any client filter.
        self.assertEqual(store.get_recent_queries_for_client("anyone"), [])

    def test_migration_idempotent_on_second_open(self) -> None:
        # Simulate an upgrade: create the legacy schema without client_id, then open via
        # AnalyticsStore and confirm the migration runs exactly once.
        with sqlite3.connect(self.path) as cx:
            cx.executescript(
                """
                CREATE TABLE queries (
                    id TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    budget INTEGER,
                    category TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE recommendations (id TEXT PRIMARY KEY, query_id TEXT, rank_index INTEGER, product_id TEXT, title TEXT, score REAL, deeplink TEXT, rationale TEXT, risks_json TEXT, created_at TEXT);
                CREATE TABLE events (id TEXT PRIMARY KEY, query_id TEXT, recommendation_id TEXT, event_type TEXT, metadata_json TEXT, created_at TEXT);
                CREATE TABLE evidence_snippets (id TEXT PRIMARY KEY, query_id TEXT, snippet_text TEXT, source TEXT, created_at TEXT);
                """
            )

        AnalyticsStore(self.path)  # runs migration
        AnalyticsStore(self.path)  # second open must not raise

        with sqlite3.connect(self.path) as cx:
            cols = {row[1] for row in cx.execute("PRAGMA table_info(queries)").fetchall()}
        self.assertIn("client_id", cols)


if __name__ == "__main__":
    unittest.main()
