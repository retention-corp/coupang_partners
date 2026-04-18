"""YouTube Data API v3 search (read-only).

Used by the composer to embed 1-3 high-view review videos per book. Quota cost is
100 units per search.list call (free tier 10,000 units/day), so we can afford ~100
compose runs per day from this source alone; cache at the orchestrator layer makes
this non-binding in practice.

Fail-soft: missing YOUTUBE_API_KEY → empty result. The composer never sees an
exception from here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from book_reco.utils import http_get_json

LOGGER = logging.getLogger("book_intel.sources.youtube_search")

_API_ROOT = "https://www.googleapis.com/youtube/v3/search"


class YouTubeSearchClient:
    """Thin read-only wrapper over the YouTube Data v3 search endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 8.0,
        user_agent: str = "book_intel/0.1 (+https://retn.kr)",
    ) -> None:
        self.api_key = (api_key or os.getenv("YOUTUBE_API_KEY") or "").strip()
        self.timeout = float(timeout_seconds)
        self.user_agent = user_agent

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        query: str,
        *,
        max_results: int = 3,
        region_code: str = "KR",
        relevance_language: str = "ko",
        order: str = "viewCount",
    ) -> list[dict[str, Any]]:
        """Return up to max_results Korean-locale video matches ordered by view count."""

        query = (query or "").strip()
        if not query or not self.configured:
            return []
        params = {
            "key": self.api_key,
            "q": query,
            "part": "snippet",
            "type": "video",
            "maxResults": min(max(max_results, 1), 10),
            "order": order,
            "regionCode": region_code,
            "relevanceLanguage": relevance_language,
            "safeSearch": "none",
        }
        try:
            payload = http_get_json(
                _API_ROOT,
                params=params,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except Exception as exc:
            LOGGER.warning("youtube_search %r failed: %s", query, exc)
            return []

        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = ((item.get("id") or {}).get("videoId") or "").strip()
            snippet = item.get("snippet") or {}
            if not video_id or not isinstance(snippet, dict):
                continue
            thumbs = snippet.get("thumbnails") or {}
            medium = thumbs.get("medium") or thumbs.get("default") or {}
            out.append({
                "video_id": video_id,
                "title": (snippet.get("title") or "").strip(),
                "channel": (snippet.get("channelTitle") or "").strip(),
                "published_at": (snippet.get("publishedAt") or "").strip(),
                "thumbnail_url": (medium.get("url") or "").strip(),
                "embed_url": f"https://www.youtube.com/embed/{video_id}",
                "watch_url": f"https://www.youtube.com/watch?v={video_id}",
            })
        return out


__all__ = ["YouTubeSearchClient"]
