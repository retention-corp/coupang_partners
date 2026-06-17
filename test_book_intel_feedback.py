"""Tests for book_intel feedback loop: Wilson bound, signal store, rollup, and cluster_label."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta

from analytics import AnalyticsStore
from book_intel.feedback.events import (
    BOOK_CLICK,
    BOOK_CONVERSION,
    BOOK_IMPRESSION,
    POST_READ,
    emit,
)
from book_intel.feedback.rollup import rollup_day
from book_intel.feedback.signal_store import DailyRow, SignalStore
from book_reco.models import BookRecord
from book_reco.persona import (
    A_TIER_CLUSTERS,
    PersonaProfile,
    cluster_label,
)
from book_reco.utils import _wilson_lower_bound, learned_boost


class WilsonLowerBoundTests(unittest.TestCase):
    def test_zero_denominator_returns_zero(self) -> None:
        self.assertEqual(_wilson_lower_bound(0, 0), 0.0)

    def test_small_n_is_penalised(self) -> None:
        # 1/10 raw CTR 10%, but Wilson LB is significantly lower
        lb = _wilson_lower_bound(1, 10)
        self.assertLess(lb, 0.05)
        self.assertGreater(lb, 0.0)

    def test_big_n_stays_close_to_raw_rate(self) -> None:
        lb = _wilson_lower_bound(100, 1000)
        self.assertGreater(lb, 0.07)
        self.assertLess(lb, 0.11)

    def test_all_positive_returns_conservative(self) -> None:
        lb = _wilson_lower_bound(10, 10)
        # Raw 100%, Wilson LB should be ~0.72 for n=10 at z=1.96
        self.assertLess(lb, 1.0)
        self.assertGreater(lb, 0.5)

    def test_invalid_inputs_clamped_to_zero(self) -> None:
        self.assertEqual(_wilson_lower_bound(-1, 10), 0.0)
        self.assertEqual(_wilson_lower_bound(5, 3), 0.0)  # positive > total


class ClusterLabelTests(unittest.TestCase):
    def test_engineer_cluster_wins_on_engineering_interests(self) -> None:
        profile = PersonaProfile(interests=["엔지니어링", "백엔드"], source_trace=["t"])
        self.assertEqual(cluster_label(profile), "openclaw_engineer")

    def test_operator_cluster_wins_on_operator_interests(self) -> None:
        profile = PersonaProfile(interests=["솔로 오퍼레이터", "수익화"], source_trace=["t"])
        self.assertEqual(cluster_label(profile), "openclaw_operator")

    def test_literature_cluster_on_novel_interests(self) -> None:
        profile = PersonaProfile(categories=["소설"], interests=["문학"], source_trace=["t"])
        self.assertEqual(cluster_label(profile), "literature")

    def test_parent_cluster_on_lifestyle_interests(self) -> None:
        profile = PersonaProfile(categories=["육아"], source_trace=["t"])
        self.assertEqual(cluster_label(profile), "parent_lifestyle")

    def test_self_dev_when_no_openclaw_signal(self) -> None:
        profile = PersonaProfile(interests=["재무", "마케팅"], source_trace=["t"])
        self.assertEqual(cluster_label(profile), "general_self_dev")

    def test_empty_profile_is_other(self) -> None:
        self.assertEqual(cluster_label(PersonaProfile()), "other")
        self.assertEqual(cluster_label(None), "other")

    def test_a_tier_set(self) -> None:
        self.assertIn("openclaw_engineer", A_TIER_CLUSTERS)
        self.assertIn("openclaw_operator", A_TIER_CLUSTERS)
        self.assertNotIn("literature", A_TIER_CLUSTERS)


class SignalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SignalStore(os.path.join(self._tmp.name, "signals.db"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_upsert_then_recent_rates(self) -> None:
        today = date.today().isoformat()
        self.store.upsert(DailyRow(isbn="9780000000001", cluster="openclaw_engineer", date=today, impressions=1000, clicks=100, conversions=5))
        ctr_lb, cvr_lb = self.store.recent_rates("9780000000001", "openclaw_engineer")
        self.assertGreater(ctr_lb, 0.07)
        self.assertGreater(cvr_lb, 0.01)

    def test_cold_start_returns_zero_rates(self) -> None:
        ctr_lb, cvr_lb = self.store.recent_rates("9780000000999", "openclaw_engineer")
        self.assertEqual(ctr_lb, 0.0)
        self.assertEqual(cvr_lb, 0.0)

    def test_category_demand_heatmap(self) -> None:
        today = date.today().isoformat()
        self.store.upsert(DailyRow(isbn="isbn-1", cluster="openclaw_engineer", date=today, impressions=500, clicks=50))
        self.store.upsert(DailyRow(isbn="isbn-2", cluster="literature", date=today, impressions=200, clicks=10))
        heatmap = self.store.category_demand_heatmap(window_days=1)
        self.assertGreater(heatmap.get("openclaw_engineer", 0), heatmap.get("literature", 0))

    def test_upsert_is_idempotent(self) -> None:
        today = date.today().isoformat()
        row = DailyRow(isbn="isbn-x", cluster="literature", date=today, clicks=5)
        self.store.upsert(row)
        self.store.upsert(row)  # same PK → replaces
        rows = self.store.debug_dump()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["clicks"], 5)


class LearnedBoostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SignalStore(os.path.join(self._tmp.name, "signals.db"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_store_gives_zero_boost(self) -> None:
        book = BookRecord(title="x", isbn13="9780000000001")
        boost, signals = learned_boost(book, "openclaw_engineer", self.store)
        self.assertEqual(boost, 0.0)
        self.assertEqual(signals, [])

    def test_missing_store_gives_zero_boost(self) -> None:
        book = BookRecord(title="x", isbn13="9780000000001")
        boost, signals = learned_boost(book, "openclaw_engineer", None)
        self.assertEqual(boost, 0.0)

    def test_other_cluster_skips(self) -> None:
        book = BookRecord(title="x", isbn13="9780000000001")
        boost, signals = learned_boost(book, "other", self.store)
        self.assertEqual(boost, 0.0)

    def test_high_ctr_book_gets_positive_boost(self) -> None:
        today = date.today().isoformat()
        self.store.upsert(DailyRow(
            isbn="9780000000001", cluster="openclaw_engineer", date=today,
            impressions=500, clicks=60,  # 12% raw, Wilson LB ~9.5%
        ))
        book = BookRecord(title="Test", isbn13="9780000000001")
        boost, signals = learned_boost(book, "openclaw_engineer", self.store)
        self.assertGreater(boost, 0.5)
        self.assertTrue(any(s["signal"].startswith("learned_ctr:openclaw_engineer") for s in signals))


class RollupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.analytics_path = os.path.join(self._tmp.name, "analytics.db")
        self.signal_path = os.path.join(self._tmp.name, "signals.db")
        self.analytics = AnalyticsStore(self.analytics_path)
        self.signal_store = SignalStore(self.signal_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _emit(self, event_type: str, **metadata) -> None:
        emit(self.analytics, event_type, **metadata)

    def test_rollup_joins_click_to_impression_by_slug(self) -> None:
        # Seed impression for yesterday, click for today; rollup target = today.
        # To keep test deterministic we let both go under today's timestamps; rollup
        # will look at yesterday-window impressions too (default lookback 30 days).
        self._emit(BOOK_IMPRESSION,
                   isbn13="9780000000001", persona_cluster="openclaw_engineer",
                   slug="abc123", position=0)
        self._emit(BOOK_CLICK, slug="abc123", source="blog")
        self._emit(BOOK_IMPRESSION,
                   isbn13="9780000000002", persona_cluster="literature",
                   slug="def456", position=1)
        self._emit(POST_READ, isbn13="9780000000001", persona_cluster="openclaw_engineer")

        rows_written = rollup_day(self.analytics_path, self.signal_store, date.today())
        self.assertGreater(rows_written, 0)

        dump = {(r["isbn"], r["cluster"]): r for r in self.signal_store.debug_dump()}
        engineer_row = dump[("9780000000001", "openclaw_engineer")]
        self.assertEqual(engineer_row["impressions"], 1)
        self.assertEqual(engineer_row["clicks"], 1)
        self.assertEqual(engineer_row["post_reads"], 1)
        # Literature book had an impression but no click
        literature_row = dump[("9780000000002", "literature")]
        self.assertEqual(literature_row["impressions"], 1)
        self.assertEqual(literature_row["clicks"], 0)

    def test_click_without_impression_falls_back_to_other(self) -> None:
        self._emit(BOOK_CLICK, slug="orphan-slug", source="api")
        rollup_day(self.analytics_path, self.signal_store, date.today())
        # Click with no resolvable isbn is dropped (no isbn13 → no row)
        self.assertEqual(self.signal_store.debug_dump(), [])

    def test_rollup_is_idempotent_on_rerun(self) -> None:
        self._emit(BOOK_IMPRESSION, isbn13="isbn-1", persona_cluster="openclaw_engineer", slug="s1")
        self._emit(BOOK_CLICK, slug="s1")
        rollup_day(self.analytics_path, self.signal_store, date.today())
        rollup_day(self.analytics_path, self.signal_store, date.today())
        rows = [r for r in self.signal_store.debug_dump() if r["isbn"] == "isbn-1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["clicks"], 1)


class EmitTests(unittest.TestCase):
    def test_none_store_is_safe(self) -> None:
        self.assertIsNone(emit(None, BOOK_IMPRESSION, isbn13="x"))

    def test_metadata_strips_nones(self) -> None:
        recorded: list[dict] = []

        class _Cap:
            def record_event(self, *, event_type, query_id=None, recommendation_id=None, metadata=None):
                recorded.append({"type": event_type, "meta": metadata})
                return "event-id"

        emit(_Cap(), BOOK_IMPRESSION, isbn13="x", dropped=None, kept="v")
        self.assertEqual(recorded[0]["meta"], {"isbn13": "x", "kept": "v"})


if __name__ == "__main__":
    unittest.main()
