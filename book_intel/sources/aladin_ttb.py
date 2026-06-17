"""Aladin TTB Open API client (stdlib, JSON mode).

Scope: the three endpoints the content pipeline needs —

- ItemList.aspx     → Bestseller / ItemNewAll / ItemNewSpecial / ItemEditorChoice
- ItemLookUp.aspx   → ISBN13 → full book detail
- ItemSearch.aspx   → free-text keyword → candidate list

Rate limit: 5,000 calls/day/key (per Aladin TTB docs). We enforce a conservative
in-process limiter (default 4,000/day) so we can burst without tripping; the
caller cache layer makes this rarely relevant in practice.

Both XML and JSON response modes exist server-side; we always ask for `Output=JS`
(JSON) since it's cheaper to parse and removes an XML dependency. Aladin error
responses come back as `{"errorCode": ..., "errorMessage": ...}` in JSON mode.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from book_reco.utils import http_get_json

LOGGER = logging.getLogger("book_intel.sources.aladin_ttb")

_API_ROOT = "https://www.aladin.co.kr/ttb/api"
_DEFAULT_VERSION = "20131101"
_DEFAULT_OUTPUT = "JS"


@dataclass
class _DailyCounter:
    today: str = field(default_factory=lambda: date.today().isoformat())
    count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def check_and_incr(self, daily_cap: int) -> bool:
        with self.lock:
            today = date.today().isoformat()
            if today != self.today:
                self.today = today
                self.count = 0
            if self.count >= daily_cap:
                return False
            self.count += 1
            return True


class AladinTTBClient:
    """Thin wrapper over Aladin TTB with per-process daily rate limiting."""

    def __init__(
        self,
        *,
        ttb_key: str | None = None,
        daily_cap: int = 4000,
        timeout_seconds: float = 10.0,
        user_agent: str = "book_intel/0.1 (+https://retn.kr)",
    ) -> None:
        self.ttb_key = (ttb_key or os.getenv("ALADIN_TTB_KEY") or "").strip()
        if not self.ttb_key:
            raise RuntimeError("ALADIN_TTB_KEY is not configured")
        self.daily_cap = max(1, int(daily_cap))
        self.timeout = float(timeout_seconds)
        self.user_agent = user_agent
        self._counter = _DailyCounter()

    # --- public API -------------------------------------------------------

    def item_list(
        self,
        query_type: str = "ItemNewSpecial",
        *,
        search_target: str = "Book",
        sub_category_id: int = 0,
        max_results: int = 10,
        start: int = 1,
    ) -> list[dict[str, Any]]:
        """QueryType ∈ {ItemNewAll, ItemNewSpecial, ItemEditorChoice, Bestseller, BlogBest}."""

        payload = self._get("ItemList.aspx", {
            "QueryType": query_type,
            "SearchTarget": search_target,
            "SubCategoryId": sub_category_id,
            "MaxResults": min(max(max_results, 1), 50),
            "start": max(start, 1),
            "Cover": "Big",
        })
        return _extract_items(payload)

    def item_lookup(self, isbn13: str) -> dict[str, Any] | None:
        """ISBN13 → full detail dict. Returns None when not found or on error."""

        isbn13 = (isbn13 or "").strip()
        if not isbn13:
            return None
        payload = self._get("ItemLookUp.aspx", {
            "ItemIdType": "ISBN13",
            "ItemId": isbn13,
            "Cover": "Big",
            "OptResult": "ebookList,usedList,ratingInfo,reviewList",
        })
        items = _extract_items(payload)
        return items[0] if items else None

    def item_search(
        self,
        keyword: str,
        *,
        search_target: str = "Book",
        max_results: int = 5,
        start: int = 1,
        query_type: str = "Keyword",
    ) -> list[dict[str, Any]]:
        """Free-text search. Used by orchestrator to recover a book by title+author."""

        keyword = (keyword or "").strip()
        if not keyword:
            return []
        payload = self._get("ItemSearch.aspx", {
            "Query": keyword,
            "QueryType": query_type,
            "SearchTarget": search_target,
            "MaxResults": min(max(max_results, 1), 50),
            "start": max(start, 1),
            "Cover": "Big",
        })
        return _extract_items(payload)

    # --- internals --------------------------------------------------------

    def _get(self, path: str, extra: dict[str, Any]) -> Any:
        if not self._counter.check_and_incr(self.daily_cap):
            LOGGER.warning("aladin_ttb daily cap %d reached; returning empty", self.daily_cap)
            return {}
        params = {
            "ttbkey": self.ttb_key,
            "Version": _DEFAULT_VERSION,
            "Output": _DEFAULT_OUTPUT,
            **extra,
        }
        try:
            data = http_get_json(
                f"{_API_ROOT}/{path}",
                params=params,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except Exception as exc:
            LOGGER.warning("aladin_ttb %s request failed: %s", path, exc)
            return {}
        if isinstance(data, dict) and data.get("errorCode"):
            LOGGER.warning(
                "aladin_ttb %s returned error %s: %s",
                path, data.get("errorCode"), data.get("errorMessage"),
            )
            return {}
        return data


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("item")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def normalize_book(item: dict[str, Any]) -> dict[str, Any]:
    """Map a TTB item to a stable shape consumable by the composer."""

    if not isinstance(item, dict):
        return {}
    return {
        "title": (item.get("title") or "").strip(),
        "author": (item.get("author") or "").strip(),
        "publisher": (item.get("publisher") or "").strip(),
        "isbn13": (item.get("isbn13") or item.get("isbn") or "").strip(),
        "pub_date": (item.get("pubDate") or "").strip(),
        "description": (item.get("description") or "").strip(),
        "cover": (item.get("cover") or "").strip(),
        "category_name": (item.get("categoryName") or "").strip(),
        "price_standard": item.get("priceStandard"),
        "price_sales": item.get("priceSales"),
        "customer_review_rank": item.get("customerReviewRank"),
        "bestseller_rank": item.get("bestRank") or item.get("bestDuration"),
        "aladin_item_id": item.get("itemId"),
        "aladin_link": (item.get("link") or "").strip(),
    }


__all__ = ["AladinTTBClient", "normalize_book"]
