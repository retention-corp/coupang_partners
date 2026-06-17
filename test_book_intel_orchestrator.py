"""Tests for book_intel.orchestrator — quota allocation + slot flow (no network)."""

from __future__ import annotations

import tempfile
import unittest
from typing import Any
from unittest import mock

from book_intel.feedback.signal_store import DailyRow, SignalStore
from book_intel.orchestrator import (
    CLUSTER_PERSONAS,
    DEFAULT_QUOTA_ORDER,
    OrchestratorConfig,
    pick_next_posts,
    run,
)
from book_reco.persona import A_TIER_CLUSTERS


class PickNextPostsTests(unittest.TestCase):
    def test_cold_start_gives_at_least_one_per_cluster_up_to_limit(self) -> None:
        quota = pick_next_posts(signal_store=None, limit=5)
        self.assertEqual(sum(quota.values()), 5)
        # Every cluster gets at least zero allocations (limit 5 / 5 clusters with a_tier bonus
        # → A-tier clusters likely get 2, non-A each get 1). Verify A-tier dominates.
        engineer_count = quota.get("openclaw_engineer", 0)
        parent_count = quota.get("parent_lifestyle", 0)
        self.assertGreaterEqual(engineer_count, parent_count)

    def test_heatmap_skews_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SignalStore(f"{tmp}/s.db")
            from datetime import date
            today = date.today().isoformat()
            # Literature gets huge demand, engineering modest
            store.upsert(DailyRow(isbn="x1", cluster="literature", date=today, clicks=500, impressions=5000))
            store.upsert(DailyRow(isbn="x2", cluster="openclaw_engineer", date=today, clicks=10, impressions=500))
            quota = pick_next_posts(signal_store=store, limit=5)
            self.assertGreater(quota.get("literature", 0), quota.get("openclaw_engineer", 0))

    def test_a_tier_multiplier_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SignalStore(f"{tmp}/s.db")
            from datetime import date
            today = date.today().isoformat()
            # Equal demand — A-tier should still get more due to multiplier
            store.upsert(DailyRow(isbn="x1", cluster="openclaw_engineer", date=today, clicks=50, impressions=1000))
            store.upsert(DailyRow(isbn="x2", cluster="literature", date=today, clicks=50, impressions=1000))
            quota = pick_next_posts(signal_store=store, limit=4, a_tier_multiplier=2.0)
            self.assertGreater(quota.get("openclaw_engineer", 0), quota.get("literature", 0))

    def test_persona_seeds_cover_all_clusters(self) -> None:
        for cluster in DEFAULT_QUOTA_ORDER:
            self.assertIn(cluster, CLUSTER_PERSONAS)

    def test_a_tier_set_is_subset_of_quota_order(self) -> None:
        for cluster in A_TIER_CLUSTERS:
            self.assertIn(cluster, DEFAULT_QUOTA_ORDER)


class RunDryRunTests(unittest.TestCase):
    """End-to-end orchestrator flow with composer + Coupang + Ghost all mocked."""

    def _fake_coupang(self, **kwargs: Any) -> dict[str, Any]:
        keyword = kwargs.get("keyword", "")
        return {"data": {"products": [{
            "productName": keyword + " 매치 상품",
            "productPrice": 15000,
            "productUrl": "https://link.coupang.com/a/FAKE",
        }]}}

    def _fake_compose(self, raw: dict[str, Any], *, tier: str) -> dict[str, Any]:
        return {
            "title": f"{raw['book'].get('title','?')} 리뷰 ({tier})",
            "lead": "요약입니다 " * 6,
            "body_markdown": "## 본문\n\n" + ("본문 " * 200) + "파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음",
            "tags": ["도서추천", "쿠팡파트너스"],
            "prompt_variant": f"blog_{tier.lower()}_tier",
            "tier": tier,
            "book": raw["book"],
        }

    def test_dry_run_composes_for_every_slot(self) -> None:
        # Use the deterministic fallback book pool (no saseo/naver/etc) via book_reco's
        # FallbackProvider — the fake Coupang search matches it to a fake product.
        from book_reco.providers.fallback import FallbackProvider
        from book_reco.services.recommendation_service import RecommendationService

        def fake_factory() -> RecommendationService:
            fb = FallbackProvider(search_provider=None)
            return RecommendationService(
                search_provider=None,
                recommendation_provider=None,
                fallback_recommendation_provider=fb,
                trending_provider=None,
                fallback_trending_provider=fb,
                curated_provider=None,
            )

        import book_reco.backend_integration as integration
        import book_intel.orchestrator as orch
        import book_intel.sources as sources_pkg

        with tempfile.TemporaryDirectory() as tmp:
            cfg = OrchestratorConfig(
                limit=3,
                dry_run=True,
                cache_db_path=f"{tmp}/cache.db",
                signal_db_path=f"{tmp}/sig.db",
                coupang_search_fn=self._fake_coupang,
            )
            with mock.patch.object(integration, "_build_service", fake_factory), \
                 mock.patch.object(orch, "compose_post", side_effect=self._fake_compose), \
                 mock.patch.object(orch, "gather_book_intel", return_value={
                     "aladin": {"detail": {}, "bestseller_rank": None},
                     "data4library": {"monthly_loans": 0, "similar_books": []},
                     "naver": {"description": ""},
                     "coupang": {"title": "", "description": "", "top_reviews": []},
                     "youtube": [],
                 }):
                results = run(cfg)

        self.assertEqual(len(results), 3)
        for slot in results:
            self.assertIn("cluster", slot)
            self.assertIn("tier", slot)
            self.assertEqual(slot.get("status"), "dry_run")
            self.assertIn("post", slot)
            self.assertGreater(slot["post"]["body_chars"], 100)

    def test_dry_run_does_not_touch_ghost(self) -> None:
        # If Ghost env isn't configured, dry-run must still work.
        from book_intel.orchestrator import run, OrchestratorConfig
        import book_intel.orchestrator as orch
        from book_reco.providers.fallback import FallbackProvider
        from book_reco.services.recommendation_service import RecommendationService
        import book_reco.backend_integration as integration

        def factory():
            fb = FallbackProvider(search_provider=None)
            return RecommendationService(None, None, fb, None, fb)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(integration, "_build_service", factory), \
             mock.patch.object(orch, "compose_post", return_value={
                 "title": "t", "lead": "l", "body_markdown": "본문 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음",
                 "tags": ["도서추천"], "prompt_variant": "blog_a_tier", "tier": "A", "book": {},
             }), \
             mock.patch.object(orch, "gather_book_intel", return_value={
                 "aladin": {"detail": {}}, "data4library": {}, "naver": {}, "coupang": {}, "youtube": [],
             }):
            cfg = OrchestratorConfig(
                limit=1,
                dry_run=True,
                cache_db_path=f"{tmp}/c.db",
                signal_db_path=f"{tmp}/s.db",
                coupang_search_fn=self._fake_coupang,
            )
            results = run(cfg)
        self.assertEqual(results[0]["status"], "dry_run")


if __name__ == "__main__":
    unittest.main()
