"""Utilities for normalization, logging, and ranking."""

from __future__ import annotations

import html
import json
import logging
import math
import re
import urllib.parse
import urllib.request
from typing import Any, Protocol

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


def score_candidate_with_persona(
    candidate: BookRecord,
    profile: Any,
) -> tuple[float, list[dict[str, Any]]]:
    """Score a candidate against a persona profile, returning (score, signal breakdown).

    Designed to re-rank an over-fetched pool inside the book vertical. Takes `Any` for
    profile to avoid a circular import between `utils` and `persona`; the real type is
    `book_reco.persona.PersonaProfile` (duck-typed on `interests`, `categories`,
    `authors`, `avoid_categories`, `korean_preference`, `engineering_weight`).

    Signals:
      • category match (profile.categories):         +5.0
      • author match (profile.authors):              +4.0
      • interest token overlap (title+description):  +2.0 per overlapping token
      • avoid-category match:                        ‑8.0 (caller drops <0 candidates)
      • popularity bump (existing BookRecord score): +0.0‑1.0
      • korean_preference × Korean-text presence:    0.0‑1.0
      • engineering_weight × keyword heuristic:      0.0‑1.0
    """

    score = 0.0
    signals: list[dict[str, Any]] = []

    profile_categories = [c for c in getattr(profile, "categories", None) or [] if c]
    profile_authors = [a for a in getattr(profile, "authors", None) or [] if a]
    profile_avoid = [c for c in getattr(profile, "avoid_categories", None) or [] if c]
    profile_interests = [i for i in getattr(profile, "interests", None) or [] if i]
    korean_pref = float(getattr(profile, "korean_preference", 0.0) or 0.0)
    engineering = float(getattr(profile, "engineering_weight", 0.0) or 0.0)

    cand_cat = (candidate.category or "").strip()
    if cand_cat:
        for target in profile_categories:
            if target and (target == cand_cat or target in cand_cat or cand_cat in target):
                score += 5.0
                signals.append({"signal": f"category:{target}", "weight": 5.0})
                break
        for avoid in profile_avoid:
            if avoid and (avoid == cand_cat or avoid in cand_cat or cand_cat in avoid):
                score -= 8.0
                signals.append({"signal": f"avoid_category:{avoid}", "weight": -8.0})
                break

    cand_author = (candidate.author or "").strip()
    if cand_author:
        for target in profile_authors:
            if target and (target in cand_author or cand_author in target):
                score += 4.0
                signals.append({"signal": f"author:{target}", "weight": 4.0})
                break

    if profile_interests:
        haystack_tokens = tokenize_koreanish((candidate.title or "") + " " + (candidate.description or ""))
        profile_tokens: set[str] = set()
        for interest in profile_interests:
            profile_tokens |= tokenize_koreanish(interest)
        overlap = haystack_tokens & profile_tokens
        if overlap:
            weight = 2.0 * len(overlap)
            score += weight
            signals.append({"signal": f"interest_overlap:{','.join(sorted(overlap))}", "weight": weight})

    popularity = getattr(candidate, "popularity_score", None)
    if popularity is not None:
        bump = min(float(popularity), 100.0) / 100.0
        if bump:
            score += bump
            signals.append({"signal": "popularity", "weight": round(bump, 3)})

    if korean_pref and re.search(r"[가-힣]", candidate.title or ""):
        weight = korean_pref
        score += weight
        signals.append({"signal": "korean_preference", "weight": weight})

    if engineering:
        haystack = (candidate.title or "") + " " + (candidate.description or "")
        if re.search(r"(소프트웨어|프로그래밍|엔지니어|아키텍처|시스템|데이터|개발)", haystack):
            weight = engineering
            score += weight
            signals.append({"signal": "engineering_weight", "weight": weight})

    return score, signals


def _wilson_lower_bound(positive: int, total: int, z: float = 1.96) -> float:
    """95% CI lower bound for a binomial proportion (Wilson score interval).

    Prevents small-N rate estimates from dominating the learned ranking: a book with
    1 click / 10 impressions (raw CTR 10%) gets a Wilson LB of ~1.8%, not 10%. A book
    with 100 / 1000 gets ~8.3% — the big-N estimate stays close to the raw rate.
    """

    if total <= 0 or positive < 0 or positive > total:
        return 0.0
    p = positive / total
    denom = 1.0 + (z * z) / total
    centre = p + (z * z) / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)
    return max(0.0, (centre - margin) / denom)


class SignalStoreProtocol(Protocol):
    """Minimum surface the learned-ranking scorer needs from a signal store."""

    def recent_rates(
        self, isbn: str, cluster: str, window_days: int = 28
    ) -> tuple[float, float]: ...


def learned_boost(
    candidate: BookRecord,
    cluster: str | None,
    signal_store: SignalStoreProtocol | None,
    *,
    window_days: int = 28,
) -> tuple[float, list[dict[str, Any]]]:
    """Add a behavior-learned boost on top of the persona-aware base score.

    Only fires when we have *both* a meaningful cluster and a signal store that
    returns a Wilson-safe CTR/CVR lower bound above gentle thresholds (4% / 2%).
    Cold-start is a hard 0.0 boost + empty signal list, so missing infrastructure
    never reorders results.
    """

    if signal_store is None or not cluster or cluster == "other":
        return 0.0, []
    isbn = (candidate.isbn13 or "").strip()
    if not isbn:
        return 0.0, []
    try:
        ctr_lb, cvr_lb = signal_store.recent_rates(isbn, cluster, window_days=window_days)
    except Exception as exc:
        LOGGER.debug("learned_boost signal lookup failed: %s", exc)
        return 0.0, []

    boost = 0.0
    signals: list[dict[str, Any]] = []
    if ctr_lb > 0.04:
        weight = min(1.5 * ctr_lb * 10.0, 2.0)
        boost += weight
        signals.append({"signal": f"learned_ctr:{cluster}:{ctr_lb:.3f}", "weight": round(weight, 3)})
    if cvr_lb > 0.02:
        weight = min(3.0 * cvr_lb * 10.0, 3.0)
        boost += weight
        signals.append({"signal": f"learned_cvr:{cluster}:{cvr_lb:.3f}", "weight": round(weight, 3)})
    return min(boost, 3.0), signals


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
