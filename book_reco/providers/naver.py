"""Naver book search provider (stdlib HTTP)."""

from __future__ import annotations

from ..config import get_settings
from ..models import BookRecord
from ..utils import clean_text, extract_isbn13, http_get_json
from .base import BookMetadataProvider, BookSearchProvider


class NaverBookProvider(BookSearchProvider, BookMetadataProvider):
    """Naver Search API adapter for Korean books."""

    base_url = "https://openapi.naver.com/v1/search/book.json"

    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        if not (self.settings.naver_client_id and self.settings.naver_client_secret):
            raise RuntimeError("Naver credentials are not configured")
        return {
            "X-Naver-Client-Id": self.settings.naver_client_id,
            "X-Naver-Client-Secret": self.settings.naver_client_secret,
            "User-Agent": self.settings.kbook_user_agent,
        }

    def search(self, query: str, limit: int = 10) -> list[BookRecord]:
        payload = http_get_json(
            self.base_url,
            params={"query": query, "display": min(limit, 100), "sort": "sim"},
            headers=self._headers(),
            timeout=self.settings.kbook_timeout_seconds,
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [self._normalize(item) for item in items]

    def describe(self, isbn13: str) -> BookRecord | None:
        matches = self.search(isbn13, limit=5)
        for book in matches:
            if book.isbn13 == isbn13:
                return book
        return matches[0] if matches else None

    def _normalize(self, item: dict) -> BookRecord:
        isbn13 = extract_isbn13(item.get("isbn", ""))
        return BookRecord(
            title=clean_text(item.get("title", "")),
            author=clean_text(item.get("author", "")).replace("^", ", "),
            publisher=clean_text(item.get("publisher", "")),
            isbn13=isbn13,
            pub_date=clean_text(item.get("pubdate", "")),
            description=clean_text(item.get("description", "")),
            source="naver",
            thumbnail=item.get("image", ""),
            category="",
            popularity_score=None,
        )
