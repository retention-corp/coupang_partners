"""사서추천도서 (National Library of Korea librarian recommendations) provider.

Wraps `https://www.nl.go.kr/NL/search/openApi/saseoApi.do`. Uses the same nl.go.kr cert_key
as the NLK metadata provider; reads from `SASEO_API_KEY` first, falling back to `NLK_API_KEY`.

The endpoint returns XML (no JSON mode as of 2026-04). Schema, confirmed live:

    <channel>
      <totalCount>...</totalCount>
      <list>
        <item>
          <recomNo>...</recomNo>
          <drCodeName>인문과학</drCodeName>          ← subject category
          <recomtitle>...</recomtitle>
          <recomauthor>...</recomauthor>
          <recompublisher>...</recompublisher>
          <recomisbn>...</recomisbn>
          <recomcontens>... librarian commentary (HTML) ...</recomcontens>
          <mokchFilePath>... thumbnail URL ...</mokchFilePath>
          <publishYear>...</publishYear>
          <recomYear>...</recomYear>
          <recomMonth>...</recomMonth>
        </item>
        ...
      </list>
    </channel>
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

from ..config import get_settings
from ..models import BookRecord
from ..utils import LOGGER, clean_text, http_get_text
from .base import BookRecommendationProvider, TrendingBooksProvider

_CACHE_TTL_SECONDS = 60 * 60 * 6  # 6h: librarian lists move slowly, stay friendly to quota


class SaseoRecommendationProvider(BookRecommendationProvider, TrendingBooksProvider):
    """Adapter for the 사서추천도서 Open API."""

    base_url = "https://www.nl.go.kr/NL/search/openApi/saseoApi.do"

    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.saseo_api_key:
            raise RuntimeError("Saseo (nl.go.kr cert_key) is not configured")
        self._cache: dict[str, tuple[float, list[BookRecord]]] = {}

    def trending(self, limit: int = 10) -> list[BookRecord]:
        books = self._fetch(page_size=min(max(limit, 10), 100))
        return books[:limit]

    def recommend(self, seed: BookRecord, limit: int = 5) -> list[BookRecord]:
        pool = self._fetch(page_size=min(max(limit * 4, 20), 100))
        if not pool:
            return []
        seed_cat = (seed.category or "").strip()
        seed_author = (seed.author or "").strip()
        scored: list[tuple[float, BookRecord]] = []
        for book in pool:
            if book.isbn13 and seed.isbn13 and book.isbn13 == seed.isbn13:
                continue
            score = 0.0
            if seed_cat and book.category and seed_cat in book.category:
                score += 3.0
            if seed_author and book.author and seed_author in book.author:
                score += 2.0
            if book.popularity_score:
                score += book.popularity_score / 100.0
            scored.append((score, book))
        scored.sort(key=lambda item: (-item[0], item[1].title))
        chosen = [book for _, book in scored[:limit]]
        return chosen or pool[:limit]

    def _fetch(self, page_size: int) -> list[BookRecord]:
        cache_key = f"size={page_size}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

        # saseoApi.do has historically ignored page_size; we cap locally and keep the param
        # so if NLK adds support later we benefit without code change.
        params = {
            "key": self.settings.saseo_api_key,
            "page_no": 1,
            "page_size": page_size,
        }
        try:
            raw = http_get_text(
                self.base_url,
                params=params,
                headers={"User-Agent": self.settings.kbook_user_agent},
                timeout=self.settings.kbook_timeout_seconds,
            )
        except Exception as exc:
            LOGGER.warning("saseo fetch failed: %s", exc)
            self._cache[cache_key] = (now, [])
            return []

        books = self._parse_xml(raw, total=page_size)
        self._cache[cache_key] = (now, books)
        return books

    def _parse_xml(self, raw: str, total: int) -> list[BookRecord]:
        if not raw:
            return []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            LOGGER.warning("saseo XML parse failed: %s", exc)
            return []

        if root.tag == "error":
            msg = (root.findtext("msg") or "").strip()
            code = (root.findtext("error_code") or "").strip()
            LOGGER.warning("saseo API error %s: %s", code, msg)
            return []

        items = root.findall("./list/item")
        books: list[BookRecord] = []
        for rank, item in enumerate(items, start=1):
            title = clean_text(_text(item, "recomtitle"))
            if not title:
                continue
            reason = clean_text(_text(item, "recomcontens"))
            if reason:
                reason = _truncate(reason, 220)
            popularity = float(max(0, total - rank + 1) * 10) if total else None
            books.append(
                BookRecord(
                    title=title,
                    author=clean_text(_text(item, "recomauthor")),
                    publisher=clean_text(_text(item, "recompublisher")),
                    isbn13=clean_text(_text(item, "recomisbn")),
                    pub_date=clean_text(_text(item, "publishYear")),
                    description=reason,
                    source="saseo",
                    thumbnail=clean_text(_text(item, "mokchFilePath")),
                    category=clean_text(_text(item, "drCodeName")),
                    popularity_score=popularity,
                    recommendation_reason=reason or "librarian recommendation",
                )
            )
        return books


def _text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    if child is None:
        return ""
    return child.text or ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
