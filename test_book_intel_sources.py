"""Tests for book_intel source adapters — offline, fixture-based, fail-soft coverage."""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any
from unittest import mock

from book_intel.cache import EnrichmentCache
from book_intel.sources import aladin_ttb, coupang_reviews, data4library, gather, naver_book, youtube_search


_ALADIN_ITEM = {
    "title": "세이노의 가르침",
    "author": "세이노",
    "publisher": "데이원",
    "isbn13": "9791168473690",
    "pubDate": "2023-03-02",
    "description": "사서 큐레이션 본문",
    "cover": "https://image.aladin.co.kr/cover.jpg",
    "categoryName": "자기계발",
    "priceStandard": 7200,
    "priceSales": 6480,
    "customerReviewRank": 9,
    "bestRank": 5,
    "itemId": 123456,
    "link": "https://www.aladin.co.kr/item/...",
}


class AladinTTBTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ALADIN_TTB_KEY"] = "ttbtest000"

    def tearDown(self) -> None:
        os.environ.pop("ALADIN_TTB_KEY", None)

    def test_missing_key_raises(self) -> None:
        os.environ.pop("ALADIN_TTB_KEY", None)
        with self.assertRaises(RuntimeError):
            aladin_ttb.AladinTTBClient()

    def test_item_list_normalized(self) -> None:
        client = aladin_ttb.AladinTTBClient()
        payload = {"item": [_ALADIN_ITEM]}
        with mock.patch.object(aladin_ttb, "http_get_json", return_value=payload):
            items = client.item_list(query_type="Bestseller", max_results=3)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "세이노의 가르침")

    def test_error_payload_returns_empty(self) -> None:
        client = aladin_ttb.AladinTTBClient()
        payload = {"errorCode": 100, "errorMessage": "bad key"}
        with mock.patch.object(aladin_ttb, "http_get_json", return_value=payload):
            self.assertEqual(client.item_list(), [])
            self.assertIsNone(client.item_lookup("9791168473690"))

    def test_network_failure_returns_empty(self) -> None:
        client = aladin_ttb.AladinTTBClient()
        with mock.patch.object(aladin_ttb, "http_get_json", side_effect=RuntimeError("boom")):
            self.assertEqual(client.item_list(), [])

    def test_daily_rate_limit_blocks_after_cap(self) -> None:
        client = aladin_ttb.AladinTTBClient(daily_cap=2)
        with mock.patch.object(aladin_ttb, "http_get_json", return_value={"item": [_ALADIN_ITEM]}) as patched:
            client.item_list()
            client.item_list()
            client.item_list()  # third call should be blocked
        self.assertEqual(patched.call_count, 2)

    def test_normalize_book_strips_fields(self) -> None:
        out = aladin_ttb.normalize_book(_ALADIN_ITEM)
        self.assertEqual(out["price_sales"], 6480)
        self.assertEqual(out["bestseller_rank"], 5)
        self.assertEqual(out["isbn13"], "9791168473690")


class Data4LibraryTests(unittest.TestCase):
    def test_missing_key_returns_empty(self) -> None:
        os.environ.pop("DATA4LIBRARY_API_KEY", None)
        client = data4library.Data4LibraryClient()
        self.assertFalse(client.configured)
        self.assertEqual(client.loan_top(), [])
        self.assertIsNone(client.book_exists("9791168473690"))

    def test_loan_top_normalizes(self) -> None:
        os.environ["DATA4LIBRARY_API_KEY"] = "dk-test"
        try:
            client = data4library.Data4LibraryClient()
            payload = {
                "response": {
                    "docs": [
                        {"doc": {
                            "bookname": "세이노의 가르침",
                            "authors": "세이노",
                            "publisher": "데이원",
                            "isbn13": "9791168473690",
                            "loan_count": "1450",
                            "ranking": "3",
                        }}
                    ]
                }
            }
            with mock.patch.object(data4library, "http_get_json", return_value=payload):
                rows = client.loan_top()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["loan_count"], 1450)
            self.assertEqual(rows[0]["ranking"], 3)
        finally:
            os.environ.pop("DATA4LIBRARY_API_KEY", None)


class NaverBookTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["NAVER_CLIENT_ID"] = "ncid"
        os.environ["NAVER_CLIENT_SECRET"] = "nsec"

    def tearDown(self) -> None:
        os.environ.pop("NAVER_CLIENT_ID", None)
        os.environ.pop("NAVER_CLIENT_SECRET", None)

    def test_search_normalizes(self) -> None:
        client = naver_book.NaverBookClient()
        payload = {
            "items": [{
                "title": "<b>세이노</b>의 가르침",
                "author": "세이노",
                "publisher": "데이원",
                "isbn": "9791168473690 978-11-6847-369-0",
                "pubdate": "20230302",
                "description": "요약",
                "image": "https://example.com/a.jpg",
                "price": "7200",
                "discount": "6480",
                "link": "https://example.com/detail",
            }]
        }
        with mock.patch.object(naver_book, "http_get_json", return_value=payload):
            results = client.search("세이노")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["isbn13"], "9791168473690")
        self.assertEqual(results[0]["discount"], "6480")
        # HTML tags stripped
        self.assertNotIn("<b>", results[0]["title"])

    def test_missing_credentials_returns_empty(self) -> None:
        os.environ.pop("NAVER_CLIENT_ID", None)
        os.environ.pop("NAVER_CLIENT_SECRET", None)
        client = naver_book.NaverBookClient()
        self.assertEqual(client.search("x"), [])


class YouTubeSearchTests(unittest.TestCase):
    def test_missing_key_returns_empty(self) -> None:
        os.environ.pop("YOUTUBE_API_KEY", None)
        client = youtube_search.YouTubeSearchClient()
        self.assertFalse(client.configured)
        self.assertEqual(client.search("foo"), [])

    def test_search_normalizes(self) -> None:
        os.environ["YOUTUBE_API_KEY"] = "yt-test"
        try:
            client = youtube_search.YouTubeSearchClient()
            payload = {
                "items": [{
                    "id": {"videoId": "abc123"},
                    "snippet": {
                        "title": "세이노의 가르침 리뷰",
                        "channelTitle": "책방",
                        "publishedAt": "2026-01-05T00:00:00Z",
                        "thumbnails": {"medium": {"url": "https://img.youtube/t.jpg"}},
                    },
                }]
            }
            with mock.patch.object(youtube_search, "http_get_json", return_value=payload):
                results = client.search("세이노")
            self.assertEqual(results[0]["video_id"], "abc123")
            self.assertTrue(results[0]["embed_url"].endswith("abc123"))
        finally:
            os.environ.pop("YOUTUBE_API_KEY", None)


class CoupangReviewsTests(unittest.TestCase):
    def test_no_evidence_returns_empty_shell(self) -> None:
        with mock.patch.object(coupang_reviews, "fetch_product_page_evidence", return_value=None):
            result = coupang_reviews.fetch_reviews({"productUrl": "https://www.coupang.com/vp/products/1"})
        self.assertEqual(result, {"title": "", "description": "", "top_reviews": []})

    def test_review_filter_keeps_first_person_text(self) -> None:
        evidence = {
            "page_title": "책 상품 페이지",
            "page_description": "좋은 책",
            "page_snippets": [
                "실제로 읽었는데 너무 좋아요 강추합니다 진짜 한 번 사보세요",
                "가격",
                "배송",
                "선물로 구매했는데 만족하신다고 했습니다 감사드려요 정말",
            ],
        }
        with mock.patch.object(coupang_reviews, "fetch_product_page_evidence", return_value=evidence):
            result = coupang_reviews.fetch_reviews({"productUrl": "https://www.coupang.com/vp/products/1"}, max_reviews=5)
        self.assertEqual(len(result["top_reviews"]), 2)  # 2 review-like, 2 rejected (too short)


class EnrichmentCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip(self) -> None:
        cache = EnrichmentCache(os.path.join(self._tmp.name, "c.db"))
        cache.set("aladin", "isbn-x", {"detail": {"title": "T"}})
        got = cache.get("aladin", "isbn-x")
        self.assertEqual(got["detail"]["title"], "T")

    def test_expired_returns_none(self) -> None:
        cache = EnrichmentCache(os.path.join(self._tmp.name, "c.db"), ttl_seconds=60)
        cache.set("aladin", "isbn-y", {"v": 1})
        # Manually expire via direct SQL.
        import sqlite3, time
        with sqlite3.connect(cache.db_path) as cx:
            cx.execute("UPDATE book_intel_cache SET expires_at = ?", (int(time.time()) - 10,))
        self.assertIsNone(cache.get("aladin", "isbn-y"))


class GatherTests(unittest.TestCase):
    def test_empty_identifier_returns_source_shapes(self) -> None:
        # No isbn/title/author → gather returns empty-shaped dict from each source
        result = gather.gather_book_intel()
        self.assertEqual(set(result.keys()), {"aladin", "data4library", "naver", "coupang", "youtube"})

    def test_aladin_source_fails_soft_when_key_missing(self) -> None:
        os.environ.pop("ALADIN_TTB_KEY", None)
        # Should not raise; each source swallows configuration errors independently
        result = gather.gather_book_intel(isbn13="9791168473690", title="T", author="A")
        self.assertIn("aladin", result)


if __name__ == "__main__":
    unittest.main()
