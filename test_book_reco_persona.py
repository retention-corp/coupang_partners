"""Tests for PersonaProfile building, merging, and persona-aware scoring."""

from __future__ import annotations

import unittest
from typing import Any

from book_reco.models import BookRecord
from book_reco.persona import (
    PersonaProfile,
    build_from_analytics,
    build_from_payload,
    interest_tokens,
    merge,
)
from book_reco.services.recommendation_service import RecommendationService
from book_reco.utils import score_candidate_with_persona


class _FakeStore:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, int]] = []

    def get_recent_queries_for_client(self, client_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        self.calls.append((client_id, limit))
        return list(self.rows)


class BuildFromPayloadTests(unittest.TestCase):
    def test_explicit_fields_are_captured(self) -> None:
        profile = build_from_payload(
            {
                "persona": {
                    "interests": ["솔로 오퍼레이터", "엔지니어링"],
                    "categories": ["자기계발"],
                    "authors": ["자청"],
                    "avoid_categories": ["육아"],
                    "korean_preference": 0.7,
                    "engineering_weight": 1.0,
                }
            }
        )
        self.assertEqual(profile.interests, ["솔로 오퍼레이터", "엔지니어링"])
        self.assertEqual(profile.categories, ["자기계발"])
        self.assertEqual(profile.authors, ["자청"])
        self.assertEqual(profile.avoid_categories, ["육아"])
        self.assertAlmostEqual(profile.korean_preference, 0.7)
        self.assertAlmostEqual(profile.engineering_weight, 1.0)
        self.assertEqual(profile.source_trace, ["explicit"])

    def test_missing_persona_returns_empty_profile(self) -> None:
        profile = build_from_payload({"query": "hello"})
        self.assertTrue(profile.is_empty())

    def test_scalar_normalization_clamps_and_ignores_garbage(self) -> None:
        profile = build_from_payload(
            {"persona": {"korean_preference": "nope", "engineering_weight": 1.5}}
        )
        self.assertEqual(profile.korean_preference, 0.0)
        self.assertEqual(profile.engineering_weight, 1.0)

    def test_list_fields_deduplicate(self) -> None:
        profile = build_from_payload(
            {"persona": {"interests": ["엔지니어링", "엔지니어링", "프로덕트"]}}
        )
        self.assertEqual(profile.interests, ["엔지니어링", "프로덕트"])


class BuildFromAnalyticsTests(unittest.TestCase):
    def test_infers_categories_from_cue_tokens(self) -> None:
        store = _FakeStore(
            [
                {"query_text": "도메인 주도 설계 백엔드 공부"},
                {"query_text": "팀 관리와 스타트업 운영 책"},
            ]
        )
        profile = build_from_analytics(store, "cli-1")
        self.assertIn("자연과학", profile.categories)
        self.assertIn("자기계발", profile.categories)
        self.assertIn("백엔드", profile.interests)
        self.assertIn("스타트업", profile.interests)
        self.assertTrue(any(trace.startswith("analytics:client_id=cli-1") for trace in profile.source_trace))

    def test_empty_store_returns_empty_profile(self) -> None:
        profile = build_from_analytics(_FakeStore([]), "cli-1")
        self.assertTrue(profile.is_empty())

    def test_missing_client_id_is_tolerated(self) -> None:
        profile = build_from_analytics(_FakeStore([{"query_text": "엔지니어링"}]), None)
        self.assertTrue(profile.is_empty())

    def test_store_failure_degrades_to_empty(self) -> None:
        class _Broken:
            def get_recent_queries_for_client(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
                raise RuntimeError("db down")

        profile = build_from_analytics(_Broken(), "cli-1")
        self.assertTrue(profile.is_empty())


class MergeTests(unittest.TestCase):
    def test_explicit_wins_on_scalars_and_lists_union(self) -> None:
        explicit = PersonaProfile(
            interests=["엔지니어링"], korean_preference=0.2, source_trace=["explicit"]
        )
        implicit = PersonaProfile(
            interests=["스타트업"], categories=["자기계발"],
            korean_preference=0.9, engineering_weight=1.0, source_trace=["analytics"],
        )
        merged = merge(explicit, implicit)
        self.assertEqual(merged.interests, ["엔지니어링", "스타트업"])
        self.assertEqual(merged.categories, ["자기계발"])
        self.assertAlmostEqual(merged.korean_preference, 0.2)  # explicit wins
        self.assertAlmostEqual(merged.engineering_weight, 1.0)
        self.assertEqual(merged.source_trace, ["explicit", "analytics"])

    def test_avoid_category_overrides_implicit_interest(self) -> None:
        explicit = PersonaProfile(avoid_categories=["육아"], source_trace=["explicit"])
        implicit = PersonaProfile(categories=["육아", "자기계발"], source_trace=["analytics"])
        merged = merge(explicit, implicit)
        self.assertIn("자기계발", merged.categories)
        self.assertNotIn("육아", merged.categories)
        self.assertIn("육아", merged.avoid_categories)


class ScoreCandidateWithPersonaTests(unittest.TestCase):
    def _candidate(self, **kwargs: Any) -> BookRecord:
        defaults = dict(title="x", author="", category="", description="")
        defaults.update(kwargs)
        return BookRecord(**defaults)

    def test_category_match_boosts_score_and_records_signal(self) -> None:
        profile = PersonaProfile(categories=["자기계발"])
        score, signals = score_candidate_with_persona(
            self._candidate(title="역행자", category="자기계발"), profile,
        )
        self.assertGreater(score, 0)
        self.assertTrue(any(s["signal"] == "category:자기계발" for s in signals))

    def test_avoid_category_produces_negative_score(self) -> None:
        profile = PersonaProfile(avoid_categories=["육아"])
        score, signals = score_candidate_with_persona(
            self._candidate(title="엄마라서 다행이다", category="육아"), profile,
        )
        self.assertLess(score, 0)
        self.assertTrue(any(s["signal"] == "avoid_category:육아" for s in signals))

    def test_interest_overlap_counts_each_token(self) -> None:
        profile = PersonaProfile(interests=["엔지니어링 아키텍처"])
        score, signals = score_candidate_with_persona(
            self._candidate(title="소프트웨어 아키텍처", description="엔지니어링 현장 사례"), profile,
        )
        overlap_signal = next((s for s in signals if s["signal"].startswith("interest_overlap:")), None)
        self.assertIsNotNone(overlap_signal)
        # 2 tokens overlapping ("엔지니어링", "아키텍처") → weight 4.0
        self.assertAlmostEqual(overlap_signal["weight"], 4.0)

    def test_interest_tokens_helper_lowercases(self) -> None:
        profile = PersonaProfile(interests=["솔로 오퍼레이터"])
        tokens = interest_tokens(profile)
        self.assertIn("솔로", tokens)
        self.assertIn("오퍼레이터", tokens)


class RankWithProfileTests(unittest.TestCase):
    def test_avoid_category_is_dropped_entirely(self) -> None:
        svc = RecommendationService(None, None, None, None, None)
        books = [
            BookRecord(title="엄마라서 다행이다", category="육아"),
            BookRecord(title="역행자", author="자청", category="자기계발"),
        ]
        profile = PersonaProfile(avoid_categories=["육아"], categories=["자기계발"])
        ordered, signals = svc.rank_with_profile(books, profile)
        self.assertEqual([b.title for b in ordered], ["역행자"])
        self.assertIn("역행자", signals)

    def test_empty_profile_is_noop(self) -> None:
        svc = RecommendationService(None, None, None, None, None)
        books = [BookRecord(title="x"), BookRecord(title="y")]
        ordered, signals = svc.rank_with_profile(books, PersonaProfile())
        self.assertEqual([b.title for b in ordered], ["x", "y"])
        self.assertEqual(signals, {})

    def test_engineering_weight_prefers_tech_titles(self) -> None:
        svc = RecommendationService(None, None, None, None, None)
        books = [
            BookRecord(title="자기계발의 기술", category="자기계발"),
            BookRecord(title="소프트웨어 아키텍처의 길", category="자연과학", description="엔지니어링"),
        ]
        profile = PersonaProfile(engineering_weight=1.0)
        ordered, _ = svc.rank_with_profile(books, profile)
        self.assertEqual(ordered[0].title, "소프트웨어 아키텍처의 길")


if __name__ == "__main__":
    unittest.main()
