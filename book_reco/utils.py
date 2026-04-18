"""Utilities for normalization, logging, and ranking."""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from .config import get_settings
from .models import BookRecord

LOGGER = logging.getLogger("book_reco")


def configure_logging() -> None:
    """Configure package logging once."""

    settings = get_settings()
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, settings.kbook_log_level.upper(), logging.INFO),
            format="%(levelname)s %(name)s: %(message)s",
        )


def clean_text(value: str) -> str:
    """Normalize API text fields."""

    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_isbn13(raw: str) -> str:
    """Extract ISBN13 from provider fields."""

    if not raw:
        return ""
    tokens = re.split(r"\s+", raw.strip())
    for token in tokens:
        digits = re.sub(r"[^0-9Xx]", "", token)
        if len(digits) == 13 and digits.isdigit():
            return digits
    digits_only = re.sub(r"[^0-9]", "", raw)
    return digits_only[:13] if len(digits_only) >= 13 else ""


def korean_text_score(text: str) -> float:
    """Return a simple Korean text preference score."""

    return 1.0 if re.search(r"[가-힣]", text or "") else 0.0


def tokenize_koreanish(text: str) -> set[str]:
    """Create simple lowercase tokens for ranking."""

    cleaned = re.sub(r"[^0-9A-Za-z가-힣 ]", " ", (text or "").lower())
    return {token for token in cleaned.split() if len(token) >= 2}


def score_candidate(seed: BookRecord, candidate: BookRecord) -> float:
    """Deterministic fallback ranking score."""

    score = 0.0
    if seed.category and candidate.category and seed.category == candidate.category:
        score += 4.0
    overlap = tokenize_koreanish(seed.title) & tokenize_koreanish(candidate.title)
    score += 1.5 * len(overlap)
    if seed.author and candidate.author and seed.author == candidate.author:
        score += 3.0
    if seed.publisher and candidate.publisher and seed.publisher == candidate.publisher:
        score += 1.5
    score += korean_text_score(candidate.title + " " + candidate.description)
    if candidate.popularity_score is not None:
        score += min(candidate.popularity_score, 100.0) / 100.0
    return score


def dump_json(data: Any) -> str:
    """Pretty JSON dump for CLI output."""

    return json.dumps(data, ensure_ascii=False, indent=2)


def http_get_text(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> str:
    """GET a URL with query params and return the raw body. Raises on non-2xx."""

    full_url = url
    if params:
        cleaned = {k: v for k, v in params.items() if v is not None}
        query = urllib.parse.urlencode(cleaned, doseq=True)
        separator = "&" if "?" in full_url else "?"
        full_url = f"{full_url}{separator}{query}"
    request = urllib.request.Request(full_url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def http_get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    """GET a URL with query params and parse JSON. Raises urllib.error.HTTPError on non-2xx."""

    raw = http_get_text(url, params=params, headers=headers, timeout=timeout)
    return json.loads(raw) if raw else {}
