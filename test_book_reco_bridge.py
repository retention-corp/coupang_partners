"""Tests for the book_reco → Coupang monetization bridge."""

from __future__ import annotations

import unittest

from book_reco.coupang_bridge import (
    _matches,
    attach_coupang_products,
    find_coupang_product,
)
from book_reco.models import BookRecord


def _coupang_payload(products: list[dict]) -> dict:
    return {"data": {"products": products}}


class CoupangBridgeMatchTests(unittest.TestCase):
    def test_accepts_title_overlap_with_author_signal(self) -> None:
        book = BookRecord(title="불편한 편의점", author="김호연", isbn13="9791161571188")
        product = {"productName": "불편한 편의점 김호연 장편소설", "productUrl": "https://link.coupang.com/a/X"}
        self.assertTrue(_matches(book, product))

    def test_rejects_missing_author_signal_with_weak_overlap(self) -> None:
        book = BookRecord(title="자기계발 책", author="저자A")
        product = {"productName": "자기계발 기본서 저자Z"}
        # "자기계발" overlaps but author doesn't match and overlap isn't overwhelming.
        self.assertFalse(_matches(book, product))

    def test_accepts_exact_isbn_match(self) -> None:
        book = BookRecord(title="완전히 다른 제목", isbn13="9791161571188")
        product = {"productName": "Who cares", "isbn13": "9791161571188"}
        self.assertTrue(_matches(book, product))

    def test_rejects_unrelated_product(self) -> None:
        book = BookRecord(title="불편한 편의점", author="김호연")
        product = {"productName": "전혀 다른 상품"}
        self.assertFalse(_matches(book, product))


class CoupangBridgeFlowTests(unittest.TestCase):
    def test_find_returns_first_good_match(self) -> None:
        book = BookRecord(title="불편한 편의점", author="김호연", publisher="나무옆의자")
        calls: list[str] = []

        def fake_search(**kwargs):
            calls.append(kwargs.get("keyword", ""))
            return _coupang_payload([
                {"productName": "불편한 편의점 김호연 소설", "productUrl": "https://link.coupang.com/a/OK"},
                {"productName": "noise", "productUrl": "https://link.coupang.com/a/NOISE"},
            ])

        match = find_coupang_product(book, search_fn=fake_search)
        self.assertIsNotNone(match)
        self.assertEqual(match["deeplink"], "https://link.coupang.com/a/OK")
        self.assertEqual(len(calls), 1)
        self.assertIn("불편한 편의점", calls[0])
        self.assertIn("김호연", calls[0])

    def test_find_tries_fallback_queries_on_empty(self) -> None:
        book = BookRecord(title="작별인사", author="김영하", publisher="복복서가")
        calls: list[str] = []

        def fake_search(**kwargs):
            calls.append(kwargs.get("keyword", ""))
            if "복복서가" in calls[-1]:
                return _coupang_payload([
                    {"productName": "작별인사 김영하 장편소설", "productUrl": "https://link.coupang.com/a/FB"},
                ])
            return _coupang_payload([])

        match = find_coupang_product(book, search_fn=fake_search)
        self.assertIsNotNone(match)
        self.assertEqual(match["deeplink"], "https://link.coupang.com/a/FB")
        # 3 queries because author exists: "title author", "title 도서", "title publisher"
        self.assertEqual(len(calls), 3)

    def test_find_drops_when_no_match_survives_filter(self) -> None:
        book = BookRecord(title="희귀한 책", author="저자A", publisher="출판사")

        def fake_search(**kwargs):
            return _coupang_payload([
                {"productName": "전혀 상관 없는 제품"},
            ])

        self.assertIsNone(find_coupang_product(book, search_fn=fake_search))

    def test_attach_skips_unmatched_books(self) -> None:
        books = [
            BookRecord(title="불편한 편의점", author="김호연"),
            BookRecord(title="소실된 책", author="저자Z"),
        ]

        def fake_search(**kwargs):
            if "불편한 편의점" in kwargs.get("keyword", ""):
                return _coupang_payload([{"productName": "불편한 편의점 김호연 소설", "productUrl": "https://link.coupang.com/a/OK"}])
            return _coupang_payload([])

        matched = attach_coupang_products(books, search_fn=fake_search)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["book"]["title"], "불편한 편의점")


if __name__ == "__main__":
    unittest.main()
