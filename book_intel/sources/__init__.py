"""Enrichment source adapters.

Each module exposes a small, synchronous interface returning normalized dicts. The
orchestrator fans out per-book, caches results, and hands the merged payload to the
OpenClaw composer. All modules fail soft: network/auth errors yield an empty result,
not an exception, so the composer can still produce a post from partial signals.
"""

from .aladin_ttb import AladinTTBClient, normalize_book
from .coupang_reviews import fetch_reviews
from .data4library import Data4LibraryClient
from .gather import gather_book_intel
from .naver_book import NaverBookClient
from .youtube_search import YouTubeSearchClient

__all__ = [
    "AladinTTBClient",
    "Data4LibraryClient",
    "NaverBookClient",
    "YouTubeSearchClient",
    "fetch_reviews",
    "gather_book_intel",
    "normalize_book",
]
