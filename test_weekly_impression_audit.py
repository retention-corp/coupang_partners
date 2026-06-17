import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "scripts" / "weekly_impression_audit.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import weekly_impression_audit as audit  # noqa: E402

KST = timezone(timedelta(hours=9))


def _make_recommendations_db(path: Path, created_at_isos: list[str]) -> None:
    with sqlite3.connect(str(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS queries (
                id TEXT PRIMARY KEY,
                query_text TEXT NOT NULL,
                budget INTEGER,
                category TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY,
                query_id TEXT NOT NULL,
                rank_index INTEGER NOT NULL,
                product_id TEXT,
                title TEXT NOT NULL,
                score REAL NOT NULL,
                deeplink TEXT,
                rationale TEXT NOT NULL,
                risks_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        query_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO queries (id, query_text, budget, category, created_at) VALUES (?, ?, ?, ?, ?)",
            (query_id, "test query", None, None, created_at_isos[0] if created_at_isos else "2026-01-01T00:00:00+00:00"),
        )
        for index, created_at in enumerate(created_at_isos, start=1):
            connection.execute(
                "INSERT INTO recommendations (id, query_id, rank_index, product_id, title, score, deeplink, rationale, risks_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    query_id,
                    index,
                    f"prod-{index}",
                    f"Title {index}",
                    1.0,
                    None,
                    "ok",
                    "[]",
                    created_at,
                ),
            )


class WeeklyImpressionAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "analytics.sqlite3"
        # 2026-04-20 is a Monday in KST (verified manually).
        self.week_start = date(2026, 4, 20)
        self.start_utc, self.end_utc = audit._week_window_utc(self.week_start)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_previous_monday_kst_returns_monday(self) -> None:
        # Wednesday 2026-04-29 → previous completed week starts Monday 2026-04-20.
        result = audit._previous_monday_kst(date(2026, 4, 29))
        self.assertEqual(result, date(2026, 4, 20))
        self.assertEqual(result.weekday(), 0)

    def test_week_window_spans_seven_days_in_utc(self) -> None:
        start_utc, end_utc = audit._week_window_utc(self.week_start)
        self.assertEqual((end_utc - start_utc), timedelta(days=7))
        # KST Monday 00:00 = Sunday 15:00 UTC.
        self.assertEqual(start_utc, datetime(2026, 4, 19, 15, 0, tzinfo=timezone.utc))

    def test_count_recommendations_only_inside_window(self) -> None:
        in_window = (self.start_utc + timedelta(hours=1)).isoformat()
        also_in = (self.start_utc + timedelta(days=3)).isoformat()
        before = (self.start_utc - timedelta(hours=1)).isoformat()
        after = self.end_utc.isoformat()  # exclusive upper bound
        _make_recommendations_db(self.db_path, [in_window, also_in, before, after])

        count = audit.count_recommendations_in_window(
            str(self.db_path), start_utc=self.start_utc, end_utc=self.end_utc
        )
        self.assertEqual(count, 2)

    def test_count_returns_zero_when_db_missing(self) -> None:
        missing = self.db_path.with_name("does-not-exist.sqlite3")
        self.assertFalse(missing.exists())
        count = audit.count_recommendations_in_window(
            str(missing), start_utc=self.start_utc, end_utc=self.end_utc
        )
        self.assertEqual(count, 0)

    def test_compute_ratio_handles_division(self) -> None:
        self.assertAlmostEqual(audit.compute_ratio(80, 100), 0.8, places=6)
        self.assertIsNone(audit.compute_ratio(None, 100))
        self.assertIsNone(audit.compute_ratio(80, 0))

    def test_run_audit_with_explicit_override_below_threshold_flags(self) -> None:
        in_window = (self.start_utc + timedelta(hours=2)).isoformat()
        _make_recommendations_db(self.db_path, [in_window] * 100)

        result = audit.run_audit(
            db_path=str(self.db_path),
            week_start_kst=self.week_start,
            coupang_impressions_override=70,
            threshold=0.8,
        )
        self.assertEqual(result["our_recommendations"], 100)
        self.assertEqual(result["coupang_impressions"], 70)
        self.assertAlmostEqual(result["ratio"], 0.7, places=6)
        self.assertTrue(result["flag"])

    def test_run_audit_with_override_above_threshold_does_not_flag(self) -> None:
        in_window = (self.start_utc + timedelta(hours=2)).isoformat()
        _make_recommendations_db(self.db_path, [in_window] * 100)

        result = audit.run_audit(
            db_path=str(self.db_path),
            week_start_kst=self.week_start,
            coupang_impressions_override=90,
            threshold=0.8,
        )
        self.assertAlmostEqual(result["ratio"], 0.9, places=6)
        self.assertFalse(result["flag"])

    def test_run_audit_reads_economics_table_when_no_override(self) -> None:
        in_window = (self.start_utc + timedelta(hours=2)).isoformat()
        _make_recommendations_db(self.db_path, [in_window] * 50)
        with sqlite3.connect(str(self.db_path)) as connection:
            connection.execute(
                "CREATE TABLE economics (week_start TEXT PRIMARY KEY, reported_impressions INTEGER)"
            )
            connection.execute(
                "INSERT INTO economics (week_start, reported_impressions) VALUES (?, ?)",
                (self.week_start.isoformat(), 30),
            )

        result = audit.run_audit(
            db_path=str(self.db_path),
            week_start_kst=self.week_start,
        )
        self.assertEqual(result["coupang_impressions"], 30)
        self.assertAlmostEqual(result["ratio"], 0.6, places=6)
        self.assertTrue(result["flag"])

    def test_run_audit_returns_null_ratio_when_no_data(self) -> None:
        result = audit.run_audit(
            db_path=str(self.db_path),
            week_start_kst=self.week_start,
        )
        self.assertEqual(result["our_recommendations"], 0)
        self.assertIsNone(result["coupang_impressions"])
        self.assertIsNone(result["ratio"])
        self.assertFalse(result["flag"])

    def test_cli_prints_json(self) -> None:
        in_window = (self.start_utc + timedelta(hours=2)).isoformat()
        _make_recommendations_db(self.db_path, [in_window] * 4)
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                str(self.db_path),
                "--week-start",
                self.week_start.isoformat(),
                "--coupang-impressions",
                "5",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout.strip())
        self.assertEqual(payload["our_recommendations"], 4)
        self.assertEqual(payload["coupang_impressions"], 5)
        self.assertGreater(payload["ratio"], 1.0)
        self.assertFalse(payload["flag"])

    def test_cli_rejects_non_monday_week_start(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                str(self.db_path),
                "--week-start",
                "2026-04-21",  # Tuesday
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Monday", completed.stderr)


if __name__ == "__main__":
    unittest.main()
