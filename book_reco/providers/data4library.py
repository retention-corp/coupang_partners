"""Data4Library provider (stdlib HTTP)."""

from __future__ import annotations

from ..config import get_settings
from ..models import BookRecord
from ..utils import clean_text, http_get_json
from .base import BookRecommendationProvider, TrendingBooksProvider


class Data4LibraryProvider(BookRecommendationProvider, TrendingBooksProvider):
    """Adapter for Data4Library recommendation and trending endpoints."""

    base_url = "http://data4library.kr/api"

    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.data4library_api_key:
            raise RuntimeError("Data4Library API key is not configured")

    def recommend(self, seed: BookRecord, limit: int = 5) -> list[BookRecord]:
        params = {
            "authKey": self.settings.data4library_api_key,
            "isbn13": seed.isbn13,
            "pageNo": 1,
            "pageSize": min(limit, 10),
            "format": "json",
        }
        data = self._get_json("/recommandList", params)
        docs = data.get("response", {}).get("docs", []) if isinstance(data, dict) else []
        books = [self._normalize_doc(doc.get("book", {}), reason="data4library recommendation") for doc in docs]
        return books[:limit]

    def trending(self, limit: int = 10) -> list[BookRecord]:
        params = {
            "authKey": self.settings.data4library_api_key,
            "pageNo": 1,
            "pageSize": min(limit, 20),
            "format": "json",
        }
        data = self._get_json("/loanItemSrch", params)
        docs = data.get("response", {}).get("docs", []) if isinstance(data, dict) else []
        books: list[BookRecord] = []
        for rank, doc in enumerate(docs, start=1):
            books.append(
                self._normalize_doc(
                    doc.get("doc", {}),
                    popularity=float(max(0, limit - rank + 1) * 10),
                    reason="popular loan trend",
                )
            )
        return books[:limit]

    def _get_json(self, path: str, params: dict) -> dict:
        return http_get_json(
            f"{self.base_url}{path}",
            params=params,
            headers={"User-Agent": self.settings.kbook_user_agent},
            timeout=self.settings.kbook_timeout_seconds,
        )

    def _normalize_doc(self, item: dict, popularity: float | None = None, reason: str = "") -> BookRecord:
        return BookRecord(
            title=clean_text(item.get("bookname", "") or item.get("bookImageURL", "")),
            author=clean_text(item.get("authors", "")),
            publisher=clean_text(item.get("publisher", "")),
            isbn13=clean_text(item.get("isbn13", "")),
            pub_date=clean_text(item.get("publication_year", "") or item.get("pubYear", "")),
            description=clean_text(item.get("description", "")),
            source="data4library",
            thumbnail=item.get("bookImageURL", ""),
            category=clean_text(item.get("class_nm", "") or item.get("category", "")),
            popularity_score=popularity,
            recommendation_reason=reason,
        )
