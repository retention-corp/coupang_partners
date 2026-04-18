"""National Library of Korea metadata provider (stdlib HTTP)."""

from __future__ import annotations

from ..config import get_settings
from ..models import BookRecord
from ..utils import clean_text, http_get_json
from .base import BookMetadataProvider


class NLKMetadataProvider(BookMetadataProvider):
    """Optional NLK metadata adapter.

    Uses a narrow search-by-ISBN flow; NLK account/endpoint details can vary.
    """

    base_url = "https://www.nl.go.kr/NL/search/openApi/search.do"

    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.nlk_api_key:
            raise RuntimeError("NLK API key is not configured")

    def describe(self, isbn13: str) -> BookRecord | None:
        params = {
            "key": self.settings.nlk_api_key,
            "detailSearch": "true",
            "isbnOp": "isbn",
            "isbnCode": isbn13,
            "apiType": "json",
        }
        payload = http_get_json(
            self.base_url,
            params=params,
            headers={"User-Agent": self.settings.kbook_user_agent},
            timeout=self.settings.kbook_timeout_seconds,
        )
        result = payload.get("result", []) if isinstance(payload, dict) else []
        if not result:
            return None
        item = result[0]
        return BookRecord(
            title=clean_text(item.get("title_info", "")),
            author=clean_text(item.get("author_info", "")),
            publisher=clean_text(item.get("pub_info", "")),
            isbn13=isbn13,
            pub_date=clean_text(item.get("pub_year_info", "")),
            description=clean_text(item.get("abstracts", "")),
            source="nlk",
            thumbnail="",
            category=clean_text(item.get("kdc_name_1s", "")),
            popularity_score=None,
        )
