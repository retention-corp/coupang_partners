"""End-to-end tests for vertical=book routing through ShoppingBackend (no network)."""

from __future__ import annotations

import unittest
from typing import Any

from book_reco.backend_integration import book_assist
from book_reco.models import BookRecord
from book_reco.providers.fallback import FallbackProvider
from book_reco.services.recommendation_service import RecommendationService


class _FakeCurated:
    def __init__(self, books: list[BookRecord]) -> None:
        self._books = books

    def trending(self, limit: int = 10) -> list[BookRecord]:
        return list(self._books[:limit])

    def recommend(self, seed: BookRecord, limit: int = 5) -> list[BookRecord]:
        return list(self._books[:limit])


def _fake_service(curated: _FakeCurated) -> RecommendationService:
    fb = FallbackProvider(search_provider=None)
    return RecommendationService(
        search_provider=None,
        recommendation_provider=None,
        fallback_recommendation_provider=fb,
        trending_provider=None,
        fallback_trending_provider=fb,
        curated_provider=curated,
    )


def _coupang_payload(products: list[dict]) -> dict:
    return {"data": {"products": products}}


class BookAssistFlowTests(unittest.TestCase):
    def test_trending_flow_matches_books_to_coupang(self) -> None:
        curated = _FakeCurated([
            BookRecord(title="불편한 편의점", author="김호연", publisher="나무옆의자",
                        isbn13="9791161571188", source="saseo",
                        recommendation_reason="librarian pick"),
            BookRecord(title="역행자", author="자청", publisher="웅진지식하우스",
                        isbn13="9788901260718", source="saseo"),
        ])

        def fake_search(**kwargs: Any) -> Any:
            keyword = kwargs.get("keyword", "")
            if "불편한 편의점" in keyword:
                return _coupang_payload([
                    {"productName": "불편한 편의점 김호연 소설", "productUrl": "https://link.coupang.com/a/A"},
                ])
            if "역행자" in keyword:
                return _coupang_payload([
                    {"productName": "역행자 자청 자기계발", "productUrl": "https://link.coupang.com/a/B"},
                ])
            return _coupang_payload([])

        shorten_calls: list[str] = []

        def shorten(url: str) -> str:
            shorten_calls.append(url)
            return f"https://a.retn.kr/s/{len(shorten_calls)}"

        def validate_host(url: str) -> bool:
            return "link.coupang.com" in url

        result = book_assist(
            {"vertical": "book", "limit": 2},
            search_products_fn=fake_search,
            shorten_fn=shorten,
            validate_host_fn=validate_host,
            service_factory=lambda: _fake_service(curated),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["vertical"], "book")
        # Over-fetch pulls fallback curated titles in too, so the books list is longer than
        # just the curator's contribution; what matters is that the matched slots fill up.
        self.assertGreaterEqual(result["counts"]["books"], 2)
        self.assertEqual(result["counts"]["matched"], 2)
        self.assertEqual(len(result["recommendations"]), 2)
        for rec in result["recommendations"]:
            self.assertTrue(rec["product"]["short_deeplink"].startswith("https://a.retn.kr/s/"))
        self.assertEqual(len(shorten_calls), 2)
        self.assertIn("파트너스", result["disclosure"])

    def test_query_flow_drops_unmatched_books(self) -> None:
        curated = _FakeCurated([
            BookRecord(title="소실된 책", author="저자Z", source="saseo"),
            BookRecord(title="불편한 편의점", author="김호연", source="saseo"),
        ])

        def fake_search(**kwargs: Any) -> Any:
            if "불편한 편의점" in kwargs.get("keyword", ""):
                return _coupang_payload([
                    {"productName": "불편한 편의점 김호연 소설", "productUrl": "https://link.coupang.com/a/OK"},
                ])
            return _coupang_payload([])

        result = book_assist(
            {"vertical": "book", "limit": 2},
            search_products_fn=fake_search,
            service_factory=lambda: _fake_service(curated),
        )

        self.assertGreaterEqual(result["counts"]["books"], 2)
        self.assertGreaterEqual(result["counts"]["matched"], 1)
        titles = [rec["book"]["title"] for rec in result["recommendations"]]
        self.assertIn("불편한 편의점", titles)

    def test_shortener_missing_keeps_raw_deeplink(self) -> None:
        curated = _FakeCurated([
            BookRecord(title="불편한 편의점", author="김호연", source="saseo"),
        ])

        def fake_search(**kwargs: Any) -> Any:
            return _coupang_payload([
                {"productName": "불편한 편의점 김호연 소설", "productUrl": "https://link.coupang.com/a/RAW"},
            ])

        result = book_assist(
            {"vertical": "book", "limit": 1},
            search_products_fn=fake_search,
            shorten_fn=None,
            validate_host_fn=None,
            service_factory=lambda: _fake_service(curated),
        )
        product = result["recommendations"][0]["product"]
        self.assertEqual(product["deeplink"], "https://link.coupang.com/a/RAW")
        self.assertEqual(product["short_deeplink"], "https://link.coupang.com/a/RAW")


class ShoppingBackendVerticalRouteTests(unittest.TestCase):
    def test_vertical_book_routes_to_book_assist(self) -> None:
        import backend as backend_module

        class FakeAdapter:
            def search_products(self, **kwargs: Any) -> Any:
                return _coupang_payload([
                    {"productName": "불편한 편의점 김호연 소설", "productUrl": "https://link.coupang.com/a/OK"},
                ])

        class FakeAnalytics:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def record_assist(self, **kwargs: Any) -> str:
                self.calls.append(kwargs)
                return "query-id-1"

        class FakeShortener:
            def shorten(self, url: str) -> str:
                return "https://a.retn.kr/s/shortened"

        analytics = FakeAnalytics()
        backend = backend_module.ShoppingBackend(
            adapter=FakeAdapter(),
            analytics_store=analytics,
            shortener=FakeShortener(),
        )

        curated = _FakeCurated([
            BookRecord(title="불편한 편의점", author="김호연", source="saseo"),
        ])

        # Patch the lazy factory so we don't hit env-dependent providers.
        import book_reco.backend_integration as integration
        original_factory = integration._build_service
        integration._build_service = lambda: _fake_service(curated)
        try:
            response = backend.assist({"vertical": "book", "limit": 1})
        finally:
            integration._build_service = original_factory

        self.assertTrue(response["ok"])
        self.assertEqual(response["vertical"], "book")
        self.assertEqual(response["counts"]["matched"], 1)
        self.assertEqual(analytics.calls[0]["category"], "book")


class PersonaAwareRoutingTests(unittest.TestCase):
    def test_avoid_category_drops_unwanted_books_and_ranks_tech_first(self) -> None:
        curated = _FakeCurated([
            BookRecord(title="엄마라서 다행이다", author="잭 캔필드", category="육아", source="saseo"),
            BookRecord(title="데이터 중심 애플리케이션 설계", author="마틴 클레프만",
                        category="자연과학", description="엔지니어링 교양서",
                        source="saseo", popularity_score=80.0),
            BookRecord(title="역행자", author="자청", category="자기계발", source="saseo"),
        ])

        def fake_search(**kwargs: Any) -> Any:
            return _coupang_payload([
                {"productName": kwargs.get("keyword", "") + " 매치", "productUrl": "https://link.coupang.com/a/OK"},
            ])

        payload = {
            "vertical": "book",
            "limit": 3,
            "persona": {
                "interests": ["엔지니어링"],
                "categories": ["자연과학", "자기계발"],
                "avoid_categories": ["육아"],
                "engineering_weight": 1.0,
            },
        }

        result = book_assist(
            payload,
            search_products_fn=fake_search,
            service_factory=lambda: _fake_service(curated),
        )

        matched_titles = [rec["book"]["title"] for rec in result["recommendations"]]
        # 육아 dropped entirely; tech book ranks first.
        self.assertNotIn("엄마라서 다행이다", matched_titles)
        self.assertEqual(matched_titles[0], "데이터 중심 애플리케이션 설계")
        self.assertGreater(result["counts"]["persona_signals_used"], 0)
        self.assertIsNotNone(result.get("persona"))
        # Every top rec should carry its signal breakdown.
        self.assertTrue(all(rec.get("persona_signals") for rec in result["recommendations"]))

    def test_implicit_persona_from_analytics_replay(self) -> None:
        curated = _FakeCurated([
            BookRecord(title="엄마라서 다행이다", author="잭 캔필드", category="육아", source="saseo"),
            BookRecord(title="데이터 중심 애플리케이션 설계", author="마틴 클레프만",
                        category="자연과학", source="saseo"),
            BookRecord(title="역행자", author="자청", category="자기계발", source="saseo"),
        ])

        class _ReplayStore:
            def get_recent_queries_for_client(self, client_id: str, *, limit: int = 20):
                if client_id == "cli-engineer":
                    return [
                        {"query_text": "엔지니어링 아키텍처 공부"},
                        {"query_text": "백엔드 개발자 커리어"},
                    ]
                return []

        def fake_search(**kwargs: Any) -> Any:
            return _coupang_payload([
                {"productName": kwargs.get("keyword", "") + " 매치", "productUrl": "https://link.coupang.com/a/OK"},
            ])

        result = book_assist(
            {"vertical": "book", "limit": 3},
            search_products_fn=fake_search,
            service_factory=lambda: _fake_service(curated),
            client_id="cli-engineer",
            analytics_store=_ReplayStore(),
        )

        matched_titles = [rec["book"]["title"] for rec in result["recommendations"]]
        self.assertEqual(matched_titles[0], "데이터 중심 애플리케이션 설계")
        self.assertIn("analytics:client_id=cli-engineer", " ".join(result["persona"]["source_trace"]))


class BookAssistFromAnalyticsRoundTripTests(unittest.TestCase):
    def test_header_client_id_reaches_analytics(self) -> None:
        import backend as backend_module

        class FakeAdapter:
            def search_products(self, **kwargs: Any) -> Any:
                return _coupang_payload([
                    {"productName": "엔지니어링 책 매치", "productUrl": "https://link.coupang.com/a/A"},
                ])

        class FakeAnalytics:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def record_assist(self, **kwargs: Any) -> str:
                self.calls.append(kwargs)
                return "query-id-1"

            def get_recent_queries_for_client(self, client_id: str, *, limit: int = 20):
                return []

        analytics = FakeAnalytics()
        backend = backend_module.ShoppingBackend(
            adapter=FakeAdapter(), analytics_store=analytics, shortener=None,
        )

        curated = _FakeCurated([
            BookRecord(title="엔지니어링 책", author="저자A", category="자연과학", source="saseo"),
        ])

        import book_reco.backend_integration as integration
        original = integration._build_service
        integration._build_service = lambda: _fake_service(curated)
        try:
            response = backend.assist(
                {"vertical": "book", "limit": 1},
                client_id="cli-header-test",
            )
        finally:
            integration._build_service = original

        self.assertTrue(response["ok"])
        self.assertEqual(analytics.calls[0]["client_id"], "cli-header-test")


if __name__ == "__main__":
    unittest.main()
