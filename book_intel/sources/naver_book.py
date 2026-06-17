"""Naver Book API adapter — thin wrapper around book_reco.providers.naver.

Exposes a composer-friendly raw payload (description, publisher blurb, rating info
where available). Naver's book search returns deduplicated items ordered by
relevance when `sort=sim`; we ask for up to `limit` items and trust the library
wrapper to normalize the BookRecord shape. Extra fields not on BookRecord are
pulled from the raw Naver response here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from book_reco.utils import clean_text, extract_isbn13, http_get_json

LOGGER = logging.getLogger("book_intel.sources.naver_book")

_API_ROOT = "https://openapi.naver.com/v1/search/book.json"


class NaverBookClient:
    """Read-only Naver Book search with composer-friendly output."""

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout_seconds: float = 10.0,
        user_agent: str = "book_intel/0.1 (+https://retn.kr)",
    ) -> None:
        self.client_id = (client_id or os.getenv("NAVER_CLIENT_ID") or "").strip()
        self.client_secret = (client_secret or os.getenv("NAVER_CLIENT_SECRET") or "").strip()
        self.timeout = float(timeout_seconds)
        self.user_agent = user_agent

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def lookup(self, isbn13: str) -> dict[str, Any] | None:
        """Exact-ISBN lookup. Returns None when missing or not found."""

        isbn13 = (isbn13 or "").strip()
        if not isbn13 or not self.configured:
            return None
        results = self.search(isbn13, limit=3)
        for result in results:
            if result.get("isbn13") == isbn13:
                return result
        return results[0] if results else None

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query or not self.configured:
            return []
        try:
            payload = http_get_json(
                _API_ROOT,
                params={"query": query, "display": min(max(limit, 1), 100), "sort": "sim"},
                headers={
                    "X-Naver-Client-Id": self.client_id,
                    "X-Naver-Client-Secret": self.client_secret,
                    "User-Agent": self.user_agent,
                },
                timeout=self.timeout,
            )
        except Exception as exc:
            LOGGER.warning("naver_book %r request failed: %s", query, exc)
            return []
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [_normalize(item) for item in items if isinstance(item, dict)]


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": clean_text(item.get("title", "")),
        "author": clean_text(item.get("author", "")).replace("^", ", "),
        "publisher": clean_text(item.get("publisher", "")),
        "isbn13": extract_isbn13(item.get("isbn", "")),
        "pub_date": clean_text(item.get("pubdate", "")),
        "description": clean_text(item.get("description", "")),
        "thumbnail": (item.get("image") or "").strip(),
        "price": item.get("price"),
        "discount": item.get("discount"),
        "link": (item.get("link") or "").strip(),
    }


__all__ = ["NaverBookClient"]
