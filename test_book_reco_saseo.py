"""Tests for the 사서추천도서 provider (no network, XML fixtures)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from book_reco import config as book_config
from book_reco.models import BookRecord
from book_reco.providers import saseo as saseo_module


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <totalCount>1444</totalCount>
  <list>
    <item>
      <recomNo>1267</recomNo>
      <drCodeName>한국소설</drCodeName>
      <recomtitle>불편한 편의점</recomtitle>
      <recomauthor>김호연</recomauthor>
      <recompublisher>나무옆의자</recompublisher>
      <recomisbn>9791161571188</recomisbn>
      <recomcontens>일상 공감 소설로 사서가 추천합니다.</recomcontens>
      <mokchFilePath>http://example.com/1.jpg</mokchFilePath>
      <publishYear>2021</publishYear>
    </item>
    <item>
      <recomNo>1259</recomNo>
      <drCodeName>자기계발</drCodeName>
      <recomtitle>역행자</recomtitle>
      <recomauthor>자청</recomauthor>
      <recompublisher>웅진지식하우스</recompublisher>
      <recomisbn>9788901260718</recomisbn>
      <recomcontens>실용 자기계발 사서 추천</recomcontens>
      <publishYear>2022</publishYear>
    </item>
  </list>
</channel>
""".strip()


ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<error>
  <msg>NO KEY VALUE</msg>
  <error_code>010</error_code>
</error>
""".strip()


class SaseoProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["SASEO_API_KEY"] = "dummy-cert-key"
        book_config.reset_settings_cache()

    def tearDown(self) -> None:
        os.environ.pop("SASEO_API_KEY", None)
        book_config.reset_settings_cache()

    def test_trending_parses_xml(self) -> None:
        provider = saseo_module.SaseoRecommendationProvider()
        with mock.patch.object(saseo_module, "http_get_text", return_value=SAMPLE_XML) as patched:
            books = provider.trending(limit=5)
        self.assertEqual(len(books), 2)
        self.assertEqual(books[0].title, "불편한 편의점")
        self.assertEqual(books[0].author, "김호연")
        self.assertEqual(books[0].isbn13, "9791161571188")
        self.assertEqual(books[0].category, "한국소설")
        self.assertEqual(books[0].source, "saseo")
        self.assertIn("사서", books[0].recommendation_reason)
        patched.assert_called_once()

    def test_trending_uses_cache_on_second_call(self) -> None:
        provider = saseo_module.SaseoRecommendationProvider()
        with mock.patch.object(saseo_module, "http_get_text", return_value=SAMPLE_XML) as patched:
            provider.trending(limit=5)
            provider.trending(limit=5)
        self.assertEqual(patched.call_count, 1)

    def test_fetch_failure_caches_empty_briefly(self) -> None:
        provider = saseo_module.SaseoRecommendationProvider()
        with mock.patch.object(saseo_module, "http_get_text", side_effect=RuntimeError("boom")) as patched:
            self.assertEqual(provider.trending(limit=5), [])
            self.assertEqual(provider.trending(limit=5), [])
        self.assertEqual(patched.call_count, 1)

    def test_api_error_response_returns_empty(self) -> None:
        provider = saseo_module.SaseoRecommendationProvider()
        with mock.patch.object(saseo_module, "http_get_text", return_value=ERROR_XML):
            self.assertEqual(provider.trending(limit=5), [])

    def test_recommend_prefers_same_category(self) -> None:
        provider = saseo_module.SaseoRecommendationProvider()
        seed = BookRecord(title="자기계발입문", author="저자A", category="자기계발")
        with mock.patch.object(saseo_module, "http_get_text", return_value=SAMPLE_XML):
            books = provider.recommend(seed, limit=2)
        self.assertTrue(books)
        self.assertEqual(books[0].title, "역행자")

    def test_recommend_drops_same_isbn_as_seed(self) -> None:
        provider = saseo_module.SaseoRecommendationProvider()
        seed = BookRecord(title="불편한 편의점", author="김호연", isbn13="9791161571188", category="한국소설")
        with mock.patch.object(saseo_module, "http_get_text", return_value=SAMPLE_XML):
            books = provider.recommend(seed, limit=5)
        self.assertFalse(any(book.isbn13 == seed.isbn13 for book in books))


class SaseoCacheTTLTests(unittest.TestCase):
    def test_cache_refreshes_after_ttl(self) -> None:
        os.environ["SASEO_API_KEY"] = "dummy"
        book_config.reset_settings_cache()
        try:
            provider = saseo_module.SaseoRecommendationProvider()
            fake_time = [1000.0]

            def _now() -> float:
                return fake_time[0]

            with mock.patch.object(saseo_module.time, "monotonic", _now), \
                 mock.patch.object(saseo_module, "http_get_text", return_value=SAMPLE_XML) as patched:
                provider.trending(limit=5)
                fake_time[0] += saseo_module._CACHE_TTL_SECONDS + 1
                provider.trending(limit=5)
            self.assertEqual(patched.call_count, 2)
        finally:
            os.environ.pop("SASEO_API_KEY", None)
            book_config.reset_settings_cache()


if __name__ == "__main__":
    unittest.main()
