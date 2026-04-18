"""User persona primitives for the book vertical.

A PersonaProfile is the minimum serializable representation of "who is asking" for a
book recommendation. It is built from up to three signal sources — explicit (payload
hints), implicit (recent analytics history for the same `client_id`), and Notion
(v2 stub) — then merged with an explicit-wins precedence.

Everything is stdlib-only. Profile objects are ephemeral by design: we do not store
the merged profile, only the raw query history (which is already persisted by
`analytics.AnalyticsStore.record_assist`). This keeps the privacy surface minimal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from .utils import tokenize_koreanish

# Seeds that nudge implicit persona inference from free-text query history. Each
# keyword → (category, interest_token, engineering_weight, korean_weight).
# Keep intentionally small; a richer ontology is a v2 concern.
_INTEREST_CUES: dict[str, tuple[str, str, float, float]] = {
    "엔지니어": ("자연과학", "엔지니어링", 1.0, 0.0),
    "엔지니어링": ("자연과학", "엔지니어링", 1.0, 0.0),
    "개발자": ("자연과학", "엔지니어링", 1.0, 0.0),
    "개발": ("자연과학", "엔지니어링", 1.0, 0.0),
    "백엔드": ("자연과학", "백엔드", 1.0, 0.0),
    "프론트엔드": ("자연과학", "프론트엔드", 1.0, 0.0),
    "인프라": ("자연과학", "인프라", 1.0, 0.0),
    "아키텍처": ("자연과학", "아키텍처", 1.0, 0.0),
    "데이터": ("자연과학", "데이터", 0.8, 0.0),
    "알고리즘": ("자연과학", "알고리즘", 1.0, 0.0),
    "보안": ("자연과학", "보안", 1.0, 0.0),
    "스타트업": ("자기계발", "스타트업", 0.6, 0.4),
    "창업": ("자기계발", "창업", 0.3, 0.5),
    "프로덕트": ("자기계발", "프로덕트", 0.5, 0.3),
    "pm": ("자기계발", "프로덕트", 0.5, 0.3),
    "경영": ("자기계발", "경영", 0.0, 0.5),
    "재무": ("자기계발", "재무", 0.0, 0.5),
    "돈": ("자기계발", "재무", 0.0, 0.5),
    "수익화": ("자기계발", "수익화", 0.3, 0.4),
    "마케팅": ("자기계발", "마케팅", 0.0, 0.4),
    "자기계발": ("자기계발", "자기계발", 0.0, 0.5),
    "소설": ("한국소설", "문학", 0.0, 0.8),
    "문학": ("한국소설", "문학", 0.0, 0.8),
    "철학": ("인문과학", "철학", 0.0, 0.5),
    "역사": ("인문과학", "역사", 0.0, 0.5),
    "육아": ("사회과학", "육아", 0.0, 0.0),
    "요리": ("자연과학", "요리", 0.0, 0.0),
    "건강": ("자연과학", "건강", 0.0, 0.0),
    "운동": ("자연과학", "운동", 0.0, 0.0),
}


@dataclass
class PersonaProfile:
    """What we know about the caller at recommendation time."""

    interests: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    avoid_categories: list[str] = field(default_factory=list)
    korean_preference: float = 0.0
    engineering_weight: float = 0.0
    source_trace: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.interests
            or self.categories
            or self.authors
            or self.avoid_categories
            or self.korean_preference
            or self.engineering_weight
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interests": list(self.interests),
            "categories": list(self.categories),
            "authors": list(self.authors),
            "avoid_categories": list(self.avoid_categories),
            "korean_preference": self.korean_preference,
            "engineering_weight": self.engineering_weight,
            "source_trace": list(self.source_trace),
        }


class _QueryHistoryStore(Protocol):
    def get_recent_queries_for_client(
        self, client_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]: ...


def build_from_payload(payload: dict[str, Any] | None) -> PersonaProfile:
    """Pull explicit persona hints from the assist request payload.

    Expected shape (all optional, all list-like fields tolerate scalars too):

        {
          "persona": {
            "interests":        ["솔로 오퍼레이터", "엔지니어링"],
            "categories":       ["자기계발", "자연과학"],
            "authors":          ["자청"],
            "avoid_categories": ["육아"],
            "korean_preference": 0.7,
            "engineering_weight": 1.0
          }
        }
    """

    persona = ((payload or {}).get("persona") or {}) if payload else {}
    if not isinstance(persona, dict) or not persona:
        return PersonaProfile()

    profile = PersonaProfile(
        interests=_as_str_list(persona.get("interests")),
        categories=_as_str_list(persona.get("categories")),
        authors=_as_str_list(persona.get("authors")),
        avoid_categories=_as_str_list(persona.get("avoid_categories")),
        korean_preference=_as_float(persona.get("korean_preference"), 0.0),
        engineering_weight=_as_float(persona.get("engineering_weight"), 0.0),
        source_trace=["explicit"],
    )
    return profile


def build_from_analytics(
    store: _QueryHistoryStore | None,
    client_id: str | None,
    *,
    limit: int = 20,
) -> PersonaProfile:
    """Infer a profile from the caller's most recent queries.

    Low-noise strategy: scan each prior query text for cue tokens
    (see `_INTEREST_CUES`), collect category/interest votes, then return the
    categories that earned more than one vote and every interest we saw.
    Authors are not inferred from free text.
    """

    if not store or not client_id:
        return PersonaProfile()
    try:
        rows = store.get_recent_queries_for_client(client_id, limit=limit)
    except Exception:
        return PersonaProfile()
    if not rows:
        return PersonaProfile()

    category_votes: dict[str, int] = {}
    interests: list[str] = []
    seen_interests: set[str] = set()
    korean = 0.0
    engineering = 0.0
    for row in rows:
        text = (row.get("query_text") or "").lower()
        if not text:
            continue
        for cue, (category, interest, eng, kor) in _INTEREST_CUES.items():
            if cue in text:
                category_votes[category] = category_votes.get(category, 0) + 1
                if interest not in seen_interests:
                    seen_interests.add(interest)
                    interests.append(interest)
                engineering = max(engineering, eng)
                korean = max(korean, kor)

    categories = [cat for cat, votes in category_votes.items() if votes >= 1]
    profile = PersonaProfile(
        interests=interests,
        categories=sorted(categories, key=lambda c: -category_votes[c]),
        korean_preference=korean,
        engineering_weight=engineering,
        source_trace=[f"analytics:client_id={client_id}:n={len(rows)}"],
    )
    return profile


def build_from_notion() -> PersonaProfile | None:
    """Stub: full Notion Books DB integration is v2."""

    return None


def merge(*profiles: PersonaProfile | None) -> PersonaProfile:
    """Merge profiles with left-wins precedence for scalars, union for lists.

    Call order matters: `merge(explicit, analytics, notion)` means explicit overrides
    everything on scalar fields, while list fields (interests/categories/authors/
    avoid_categories) accumulate from every source so no signal is silently dropped.
    """

    active = [p for p in profiles if p is not None]
    if not active:
        return PersonaProfile()

    interests: list[str] = []
    categories: list[str] = []
    authors: list[str] = []
    avoid: list[str] = []
    trace: list[str] = []
    korean = 0.0
    engineering = 0.0
    korean_set = False
    engineering_set = False

    for profile in active:
        _extend_unique(interests, profile.interests)
        _extend_unique(categories, profile.categories)
        _extend_unique(authors, profile.authors)
        _extend_unique(avoid, profile.avoid_categories)
        _extend_unique(trace, profile.source_trace)
        if not korean_set and profile.korean_preference:
            korean = profile.korean_preference
            korean_set = True
        if not engineering_set and profile.engineering_weight:
            engineering = profile.engineering_weight
            engineering_set = True

    # A category that's both interested and explicitly avoided is treated as avoided
    # (user override via avoid_categories wins on the same token).
    if avoid:
        avoided = set(avoid)
        categories = [c for c in categories if c not in avoided]

    return PersonaProfile(
        interests=interests,
        categories=categories,
        authors=authors,
        avoid_categories=avoid,
        korean_preference=korean,
        engineering_weight=engineering,
        source_trace=trace,
    )


def interest_tokens(profile: PersonaProfile) -> set[str]:
    """Flatten profile interests into lowercase match tokens for scoring."""

    tokens: set[str] = set()
    for interest in profile.interests:
        tokens |= tokenize_koreanish(interest)
    return tokens


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Iterable):
        out: list[str] = []
        for item in value:
            text = str(item).strip() if item is not None else ""
            if text and text not in out:
                out.append(text)
        return out
    return []


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    # Clamp to [0.0, 1.0] — unbounded weights would break score comparisons.
    if result < 0.0:
        return 0.0
    if result > 1.0:
        return 1.0
    return result


def _extend_unique(sink: list[str], source: Iterable[str]) -> None:
    for item in source:
        if item and item not in sink:
            sink.append(item)


__all__ = [
    "PersonaProfile",
    "build_from_payload",
    "build_from_analytics",
    "build_from_notion",
    "merge",
    "interest_tokens",
]
