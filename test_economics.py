import os
import sqlite3
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

from analytics import AnalyticsStore
from economics import (
    DEFAULT_AVG_BASKET_KRW,
    DEFAULT_CONVERSION_RATE,
    KST,
    build_economics_summary,
    compute_daily_click_reconciliation,
    compute_effective_clicks,
    compute_weekly_proxy_gmv,
    is_internal_ip,
    read_click_reconciliation_row,
    read_proxy_gmv_rows,
)


class EconomicsTests(unittest.TestCase):
    def test_build_economics_summary_uses_observed_ctr_when_signal_exists(self):
        summary = build_economics_summary(
            {
                "total_queries": 200,
                "total_short_link_clicks": 20,
                "event_breakdown": [{"event_type": "deeplink_clicked", "count": 20}],
                "category_breakdown": [{"category": "electronics", "count": 200}],
            }
        )

        funnel = summary["funnel"]
        self.assertEqual(funnel["observed_click_through_rate"], 0.1)
        self.assertFalse(funnel["short_link_clicks_are_attributed"])
        base = next(item for item in summary["scenarios"] if item["name"] == "base")
        self.assertAlmostEqual(base["inputs"]["click_through_rate"], 0.1)

    def test_category_override_changes_projection(self):
        original = os.environ.get("OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON")
        os.environ["OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON"] = (
            '{"electronics":{"base":{"commission_rate":0.05,"aov_krw":60000}}}'
        )
        try:
            summary = build_economics_summary(
                {
                    "total_queries": 200,
                    "total_short_link_clicks": 20,
                    "event_breakdown": [],
                    "category_breakdown": [{"category": "electronics", "count": 200}],
                }
            )
        finally:
            if original is None:
                os.environ.pop("OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON", None)
            else:
                os.environ["OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON"] = original

        category_projection = summary["category_scenarios"][0]
        self.assertFalse(category_projection["uses_observed_ctr"])
        base = next(item for item in category_projection["scenarios"] if item["name"] == "base")
        self.assertEqual(base["inputs"]["commission_rate"], 0.05)
        self.assertEqual(base["inputs"]["aov_krw"], 60000.0)

    def test_no_attributed_click_signal_keeps_global_projection_on_assumed_ctr(self):
        summary = build_economics_summary(
            {
                "total_queries": 500,
                "total_short_link_clicks": 50,
                "event_breakdown": [],
                "category_breakdown": [],
            }
        )

        funnel = summary["funnel"]
        self.assertIsNone(funnel["observed_click_through_rate"])
        self.assertFalse(funnel["has_meaningful_click_signal"])
        base = next(item for item in summary["scenarios"] if item["name"] == "base")
        self.assertAlmostEqual(base["inputs"]["click_through_rate"], 0.15)


class EffectiveClicksDedupTests(unittest.TestCase):
    """Spec R3.7 — collapse `(client_ip, slug)` repeats within 10s into one click."""

    def _ts(self, *, base: datetime, offset_seconds: float) -> str:
        return (base + timedelta(seconds=offset_seconds)).isoformat()

    def test_effective_clicks_dedup_within_10s(self):
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            {"timestamp": self._ts(base=base, offset_seconds=0), "slug": "xdZ9Rzh", "client_ip": "1.2.3.4"},
            {"timestamp": self._ts(base=base, offset_seconds=8), "slug": "xdZ9Rzh", "client_ip": "1.2.3.4"},
        ]
        raw, effective = compute_effective_clicks(rows)
        self.assertEqual(raw, 2)
        self.assertEqual(effective, 1)

    def test_effective_clicks_no_dedup_across_slugs(self):
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            {"timestamp": self._ts(base=base, offset_seconds=0), "slug": "A1", "client_ip": "1.2.3.4"},
            {"timestamp": self._ts(base=base, offset_seconds=0.3), "slug": "B2", "client_ip": "1.2.3.4"},
            {"timestamp": self._ts(base=base, offset_seconds=0.4), "slug": "C3", "client_ip": "1.2.3.4"},
        ]
        raw, effective = compute_effective_clicks(rows)
        self.assertEqual(raw, 3)
        self.assertEqual(effective, 3)

    def test_effective_clicks_no_dedup_beyond_window(self):
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            {"timestamp": self._ts(base=base, offset_seconds=0), "slug": "xdZ9Rzh", "client_ip": "1.2.3.4"},
            {"timestamp": self._ts(base=base, offset_seconds=15), "slug": "xdZ9Rzh", "client_ip": "1.2.3.4"},
        ]
        raw, effective = compute_effective_clicks(rows)
        self.assertEqual(raw, 2)
        self.assertEqual(effective, 2)

    def test_effective_clicks_mixes_ips_and_slugs(self):
        """Two distinct IPs on the same slug within 1s stay separate — different buckets."""

        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            {"timestamp": self._ts(base=base, offset_seconds=0), "slug": "A1", "client_ip": "1.2.3.4"},
            {"timestamp": self._ts(base=base, offset_seconds=1), "slug": "A1", "client_ip": "5.6.7.8"},
        ]
        raw, effective = compute_effective_clicks(rows)
        self.assertEqual((raw, effective), (2, 2))

    def test_effective_clicks_empty_and_none_timestamps(self):
        # Empty input: (0, 0).
        self.assertEqual(compute_effective_clicks([]), (0, 0))
        # Rows with no timestamp can't be dedup'd — each contributes its own effective click.
        rows = [
            {"timestamp": None, "slug": "A1", "client_ip": "1.2.3.4"},
            {"timestamp": None, "slug": "A1", "client_ip": "1.2.3.4"},
        ]
        raw, effective = compute_effective_clicks(rows)
        self.assertEqual(raw, 2)
        self.assertEqual(effective, 2)

    def test_is_internal_ip_covers_google_fe_proxy(self):
        # The exact IP from the 2026-04-21 incident.
        self.assertTrue(is_internal_ip("169.254.169.126"))
        self.assertTrue(is_internal_ip("127.0.0.1"))
        self.assertFalse(is_internal_ip("1.2.3.4"))
        self.assertFalse(is_internal_ip(None))
        self.assertFalse(is_internal_ip("not-an-ip"))


class ProxyGmvBatchTests(unittest.TestCase):
    """Spec R3.3 — weekly batch writes proxy_gmv rows with the hardcoded formula."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tempdir.name}/analytics.sqlite3"
        self.store = AnalyticsStore(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def _seed_click(self, *, slug: str, client_ip: str, created_at: datetime, surface: str = "cli"):
        """Low-level insert that bypasses AnalyticsStore.record_event's `_utc_now` clock.

        Needed because the weekly batch filters on stored `created_at` and we want
        deterministic per-second placement within the test window.
        """

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO events ("
                "id, event_type, metadata_json, created_at, "
                "surface, client_ip, user_agent, proxy_ip"
                ") VALUES (?, 'shortlink_redirect', ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    '{"slug": "' + slug + '"}',
                    created_at.astimezone(timezone.utc).isoformat(),
                    surface,
                    client_ip,
                    "curl/7.88.1",
                    "169.254.169.126",
                ),
            )

    def test_weekly_proxy_gmv_math(self):
        # 10 clicks total: 5 dedup'd down to 3 effective for surface=cli (2 collapsed
        # inside the 10s window), 5 separate slugs for chatgpt-gpt. Verify both
        # effective counts and the closed-form proxy_gmv_krw formula.
        week_start_kst = datetime(2026, 4, 20, 0, 0, 0, tzinfo=KST)  # Monday KST
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=KST)  # Inside the week

        # surface=cli: 5 events, collapse pairs (xdZ9Rzh x3 within 8s) → 1 effective
        # for that slug + 2 singletons = 3 effective total.
        self._seed_click(slug="xdZ9Rzh", client_ip="1.2.3.4", created_at=base + timedelta(seconds=0))
        self._seed_click(slug="xdZ9Rzh", client_ip="1.2.3.4", created_at=base + timedelta(seconds=4))
        self._seed_click(slug="xdZ9Rzh", client_ip="1.2.3.4", created_at=base + timedelta(seconds=8))
        self._seed_click(slug="aaaa", client_ip="1.2.3.4", created_at=base + timedelta(seconds=30))
        self._seed_click(slug="bbbb", client_ip="1.2.3.4", created_at=base + timedelta(seconds=60))

        # surface=chatgpt-gpt: 5 singletons (different slugs, well apart) → 5 effective.
        for i in range(5):
            self._seed_click(
                slug=f"g{i}",
                client_ip="9.9.9.9",
                created_at=base + timedelta(seconds=100 + i * 20),
                surface="chatgpt-gpt",
            )

        rows = compute_weekly_proxy_gmv(week_start_kst, db_path=self.db_path)
        by_surface = {row["surface"]: row for row in rows}
        self.assertIn("cli", by_surface)
        self.assertIn("chatgpt-gpt", by_surface)

        cli_row = by_surface["cli"]
        self.assertEqual(cli_row["raw_clicks"], 5)
        self.assertEqual(cli_row["effective_clicks"], 3)
        self.assertEqual(cli_row["estimated_basket_krw"], DEFAULT_AVG_BASKET_KRW)
        self.assertEqual(cli_row["label"], "attributed_estimate")
        expected_cli_gmv = int(round(3 * DEFAULT_AVG_BASKET_KRW * DEFAULT_CONVERSION_RATE))
        self.assertEqual(cli_row["proxy_gmv_krw"], expected_cli_gmv)

        gpt_row = by_surface["chatgpt-gpt"]
        self.assertEqual(gpt_row["raw_clicks"], 5)
        self.assertEqual(gpt_row["effective_clicks"], 5)
        expected_gpt_gmv = int(round(5 * DEFAULT_AVG_BASKET_KRW * DEFAULT_CONVERSION_RATE))
        self.assertEqual(gpt_row["proxy_gmv_krw"], expected_gpt_gmv)

        # read_proxy_gmv_rows should return the same content filtered by week.
        stored = read_proxy_gmv_rows(self.db_path, week_start="2026-04-20")
        self.assertEqual(len(stored), 2)

    def test_weekly_proxy_gmv_upserts_when_rerun(self):
        """Re-running the batch on the same week should overwrite, not duplicate."""

        week_start_kst = datetime(2026, 4, 20, 0, 0, 0, tzinfo=KST)
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=KST)
        self._seed_click(slug="a", client_ip="1.2.3.4", created_at=base)
        compute_weekly_proxy_gmv(week_start_kst, db_path=self.db_path)
        # Add another event, re-run, verify only one row per (surface, week).
        self._seed_click(slug="b", client_ip="1.2.3.4", created_at=base + timedelta(seconds=60))
        compute_weekly_proxy_gmv(week_start_kst, db_path=self.db_path)
        stored = read_proxy_gmv_rows(self.db_path, week_start="2026-04-20")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["raw_clicks"], 2)


class ClickReconciliationTests(unittest.TestCase):
    """Spec R3.8 — daily job writes click_reconciliation rows and emits warnings on large gaps."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tempdir.name}/analytics.sqlite3"
        self.store = AnalyticsStore(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def _seed_click(self, *, slug: str, client_ip: str, created_at: datetime):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO events ("
                "id, event_type, metadata_json, created_at, "
                "surface, client_ip, user_agent, proxy_ip"
                ") VALUES (?, 'shortlink_redirect', ?, ?, 'cli', ?, 'curl', '169.254.169.126')",
                (
                    str(uuid.uuid4()),
                    '{"slug": "' + slug + '"}',
                    created_at.astimezone(timezone.utc).isoformat(),
                    client_ip,
                ),
            )

    def _seed_coupang_reported(self, date_iso: str, clicks: int):
        """Stub the T4 economics table (not yet implemented) so reconciliation can compare."""

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economics (
                    date_kst TEXT PRIMARY KEY,
                    coupang_clicks INTEGER
                )
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO economics (date_kst, coupang_clicks) VALUES (?, ?)",
                (date_iso, clicks),
            )

    def test_reconciliation_warning_fires_above_half_gap(self):
        # Replicate the 2026-04-21 baseline: 6 raw, 3 effective (2 pairs collapse inside 10s,
        # plus 2 singletons), Coupang reports 1 → gap_ratio ≈ 0.67, warning fires.
        day_kst = date(2026, 4, 21)
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=KST)
        # 3 pairs inside the 10s window, each pair collapses to 1 effective.
        # So: 6 raw → 3 effective (pairs a/a, b/b, c/c). Inside the same (ip, slug).
        for slug in ("a", "b", "c"):
            self._seed_click(slug=slug, client_ip="1.2.3.4", created_at=base)
            self._seed_click(
                slug=slug,
                client_ip="1.2.3.4",
                created_at=base + timedelta(seconds=4),
            )
        self._seed_coupang_reported("2026-04-21", 1)

        result = compute_daily_click_reconciliation(
            day_kst, db_path=self.db_path, analytics_store=self.store
        )
        self.assertEqual(result["our_raw_clicks"], 6)
        self.assertEqual(result["our_effective_clicks"], 3)
        self.assertEqual(result["coupang_reported_clicks"], 1)
        self.assertIsNotNone(result["gap_ratio"])
        self.assertAlmostEqual(result["gap_ratio"], 1 - (1 / 3), places=4)
        self.assertGreater(result["gap_ratio"], 0.5)

        # Warning event must have been recorded into `events`.
        with sqlite3.connect(self.db_path) as connection:
            warnings = connection.execute(
                "SELECT metadata_json FROM events WHERE event_type = 'click_reconciliation_warning'"
            ).fetchall()
        self.assertEqual(len(warnings), 1)

        # Row is persisted in click_reconciliation and readable via the admin helper.
        stored = read_click_reconciliation_row(self.db_path, date_kst="2026-04-21")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["our_raw_clicks"], 6)
        self.assertEqual(stored["our_effective_clicks"], 3)
        self.assertEqual(stored["coupang_reported_clicks"], 1)

    def test_reconciliation_without_t4_data_leaves_gap_ratio_null(self):
        day_kst = date(2026, 4, 21)
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=KST)
        self._seed_click(slug="a", client_ip="1.2.3.4", created_at=base)

        result = compute_daily_click_reconciliation(
            day_kst, db_path=self.db_path, analytics_store=self.store
        )
        self.assertEqual(result["our_raw_clicks"], 1)
        self.assertEqual(result["our_effective_clicks"], 1)
        self.assertIsNone(result["coupang_reported_clicks"])
        self.assertIsNone(result["gap_ratio"])

        # No warning should have been emitted when there's nothing to compare against.
        with sqlite3.connect(self.db_path) as connection:
            warnings = connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'click_reconciliation_warning'"
            ).fetchone()[0]
        self.assertEqual(warnings, 0)

    def test_reconciliation_small_gap_does_not_warn(self):
        day_kst = date(2026, 4, 21)
        base = datetime(2026, 4, 21, 12, 0, 0, tzinfo=KST)
        for i in range(10):
            self._seed_click(
                slug=f"s{i}", client_ip="1.2.3.4", created_at=base + timedelta(seconds=30 * i)
            )
        self._seed_coupang_reported("2026-04-21", 8)

        result = compute_daily_click_reconciliation(
            day_kst, db_path=self.db_path, analytics_store=self.store
        )
        self.assertAlmostEqual(result["gap_ratio"], 1 - 8 / 10, places=4)
        with sqlite3.connect(self.db_path) as connection:
            warnings = connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'click_reconciliation_warning'"
            ).fetchone()[0]
        self.assertEqual(warnings, 0)


if __name__ == "__main__":
    unittest.main()
