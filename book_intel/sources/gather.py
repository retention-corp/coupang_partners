"""Fan-out: single entry that collects every source's slice for one book.

Each source module stays independent; `gather_book_intel` just dispatches in order
and merges results. Each source is best-effort — a failure in Naver doesn't stop
Aladin. The output is the shape the OpenClaw composer prompt templates consume.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from book_intel.cache import EnrichmentCache
from book_intel.sources.aladin_ttb import AladinTTBClient, normalize_book
from book_intel.sources.coupang_reviews import fetch_reviews
from book_intel.sources.data4library import Data4LibraryClient
from book_intel.sources.naver_book import NaverBookClient
from book_intel.sources.youtube_search import YouTubeSearchClient

LOGGER = logging.getLogger("book_intel.sources.gather")


def gather_book_intel(
    *,
    isbn13: str = "",
    title: str = "",
    author: str = "",
    coupang_product: dict[str, Any] | None = None,
    cache: Optional[EnrichmentCache] = None,
    aladin: Optional[AladinTTBClient] = None,
    d4l: Optional[Data4LibraryClient] = None,
    naver: Optional[NaverBookClient] = None,
    youtube: Optional[YouTubeSearchClient] = None,
) -> dict[str, Any]:
    """Collect raw material from every configured source. Returns a merged dict."""

    isbn13 = (isbn13 or "").strip()
    title = (title or "").strip()
    author = (author or "").strip()
    identifier = isbn13 or f"{title}|{author}"

    aladin_block = _load(cache, "aladin", identifier, lambda: _fetch_aladin(aladin, isbn13, title, author))
    d4l_block = _load(cache, "data4library", identifier, lambda: _fetch_d4l(d4l, isbn13))
    naver_block = _load(cache, "naver", identifier, lambda: _fetch_naver(naver, isbn13, title))
    coupang_block = _load(cache, "coupang_reviews", identifier, lambda: _fetch_coupang(coupang_product))
    youtube_block = _load(cache, "youtube", identifier, lambda: _fetch_youtube(youtube, title, author))

    return {
        "aladin": aladin_block,
        "data4library": d4l_block,
        "naver": naver_block,
        "coupang": coupang_block,
        "youtube": youtube_block,
    }


def _load(cache: Optional[EnrichmentCache], source: str, identifier: str, fetch_fn) -> Any:
    if not identifier:
        return _empty(source)
    if cache is not None:
        cached = cache.get(source, identifier)
        if cached is not None:
            return cached
    try:
        result = fetch_fn() or _empty(source)
    except Exception as exc:
        LOGGER.warning("%s gather failed for %s: %s", source, identifier, exc)
        result = _empty(source)
    if cache is not None:
        cache.set(source, identifier, result)
    return result


def _empty(source: str) -> Any:
    if source == "aladin":
        return {"detail": {}, "bestseller_rank": None}
    if source == "data4library":
        return {"monthly_loans": 0, "similar_books": []}
    if source == "naver":
        return {"description": "", "price": None, "rating_avg": None}
    if source == "coupang":
        return {"title": "", "description": "", "top_reviews": []}
    if source == "youtube":
        return []
    return {}


def _fetch_aladin(client: Optional[AladinTTBClient], isbn13: str, title: str, author: str) -> dict[str, Any]:
    if client is None:
        try:
            client = AladinTTBClient()
        except Exception:
            return _empty("aladin")
    detail_raw = client.item_lookup(isbn13) if isbn13 else None
    if detail_raw is None and (title or author):
        results = client.item_search(f"{title} {author}".strip(), max_results=1)
        detail_raw = results[0] if results else None
    if not detail_raw:
        return _empty("aladin")
    detail = normalize_book(detail_raw)
    return {
        "detail": detail,
        "bestseller_rank": detail.get("bestseller_rank"),
    }


def _fetch_d4l(client: Optional[Data4LibraryClient], isbn13: str) -> dict[str, Any]:
    if client is None:
        client = Data4LibraryClient()
    if not client.configured or not isbn13:
        return _empty("data4library")
    book_info = client.book_exists(isbn13) or {}
    similar = client.similar_books(isbn13, page_size=5)
    return {
        "monthly_loans": _to_int(book_info.get("loanCnt") or book_info.get("loan_count")),
        "class_nm": (book_info.get("class_nm") or "").strip(),
        "similar_books": similar,
    }


def _fetch_naver(client: Optional[NaverBookClient], isbn13: str, title: str) -> dict[str, Any]:
    if client is None:
        client = NaverBookClient()
    if not client.configured:
        return _empty("naver")
    hit = client.lookup(isbn13) if isbn13 else None
    if not hit and title:
        results = client.search(title, limit=1)
        hit = results[0] if results else None
    if not hit:
        return _empty("naver")
    return {
        "description": hit.get("description", ""),
        "price": hit.get("price"),
        "discount": hit.get("discount"),
        "pub_date": hit.get("pub_date"),
        "link": hit.get("link"),
    }


def _fetch_coupang(product: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not product:
        return _empty("coupang")
    return fetch_reviews(product)


def _fetch_youtube(client: Optional[YouTubeSearchClient], title: str, author: str) -> list[dict[str, Any]]:
    if client is None:
        client = YouTubeSearchClient()
    if not client.configured or not title:
        return []
    query = f"{title} {author} 책 리뷰".strip()
    return client.search(query, max_results=3)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = ["gather_book_intel"]
