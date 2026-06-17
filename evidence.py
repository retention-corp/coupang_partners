import re
from typing import Any, Dict, Iterable, List, Optional, Set


TOKEN_RE = re.compile(r"[\w가-힣]+")
LISTING_SIGNAL_TOKENS = ("저소음", "원룸", "자취방", "초경량", "경량", "핸디", "미니", "소형", "스틱")


def tokenize(text: str) -> List[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 1]


def _normalize_snippets(snippets: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for snippet in snippets or []:
        if isinstance(snippet, str):
            text = snippet.strip()
            source = "snippet"
        else:
            text = (snippet.get("text") or "").strip()
            source = snippet.get("source", "snippet")
        if not text:
            continue
        normalized.append({"text": text, "source": source})
    return normalized


def build_evidence(product: Dict[str, Any], query: str, snippets: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    landing_page_snippets = [
        {"text": snippet, "source": "landing_page"}
        for snippet in product.get("page_snippets", []) or []
        if str(snippet).strip()
    ]
    normalized_snippets = _normalize_snippets(list(snippets or []) + landing_page_snippets)
    query_tokens: Set[str] = set(tokenize(query))
    title = product.get("title") or product.get("productName") or ""
    landing_page_title = product.get("page_title") or ""
    vendor = product.get("vendor") or product.get("brand") or ""
    description = product.get("description") or ""
    text_haystack = " ".join([title, landing_page_title, vendor, description] + [item["text"] for item in normalized_snippets])
    haystack_tokens = set(tokenize(text_haystack))
    matched_terms = sorted(query_tokens & haystack_tokens)

    rating = float(product.get("rating") or product.get("ratingAverage") or 0.0)
    review_count = int(product.get("review_count") or product.get("reviewCount") or 0)
    snippet_bonus = min(len(normalized_snippets), 3)
    landing_page_bonus = 2.0 if product.get("page_title") or product.get("page_description") or product.get("page_snippets") else 0.0
    listing_signals = [token for token in LISTING_SIGNAL_TOKENS if token in title]
    keyword_score = len(matched_terms) * 4
    listing_signal_score = len(listing_signals) * 1.5
    review_score = min(review_count / 25.0, 8.0)
    rating_score = rating * 2.0
    evidence_score = round(keyword_score + landing_page_bonus + listing_signal_score + review_score + rating_score + snippet_bonus, 2)
    confidence = _classify_confidence(
        matched_term_count=len(matched_terms),
        has_snippets=bool(normalized_snippets),
        has_landing_page_evidence=bool(product.get("page_title") or product.get("page_description") or product.get("page_snippets")),
        review_count=review_count,
        listing_signal_count=len(listing_signals),
    )

    risks: List[str] = []
    if not normalized_snippets and not (product.get("page_title") or product.get("page_description") or product.get("page_snippets")):
        risks.append("No external evidence snippet or landing-page evidence supplied; rationale relies on product metadata.")
    if confidence == "low":
        risks.append("Metadata-only evidence makes product fit uncertain.")
    elif confidence == "medium":
        risks.append("Evidence is partial, so final fit should be validated before publishing.")
    if review_count < 20:
        risks.append("Limited review history reduces confidence.")
    if rating and rating < 4.0:
        risks.append("Average rating is below the preferred threshold.")
    if not product.get("deeplink") and not product.get("productUrl"):
        risks.append("Missing deeplink requires operator review before publishing.")

    facts: List[str] = []
    if matched_terms:
        facts.append(f"Matched query terms: {', '.join(matched_terms)}")
    if listing_signals:
        facts.append(f"Listing signals: {', '.join(listing_signals)}")
    if rating:
        facts.append(f"Rating {rating:.1f}")
    if review_count:
        facts.append(f"{review_count} reviews")
    if normalized_snippets:
        facts.append(f"{len(normalized_snippets)} user-supplied evidence snippet(s)")
    page_facts = product.get("page_facts") or []
    facts.extend(str(item) for item in page_facts if str(item).strip())

    return {
        "matched_terms": matched_terms,
        "listing_signals": listing_signals,
        "confidence": confidence,
        "score": evidence_score,
        "risks": risks,
        "facts": facts,
        "snippets": normalized_snippets,
    }


def _classify_confidence(
    *,
    matched_term_count: int,
    has_snippets: bool,
    has_landing_page_evidence: bool,
    review_count: int,
    listing_signal_count: int,
) -> str:
    if (has_snippets or has_landing_page_evidence) and (matched_term_count >= 2 or listing_signal_count >= 1):
        return "high"
    if has_landing_page_evidence and matched_term_count >= 1:
        return "medium"
    if matched_term_count >= 2 and review_count >= 20:
        return "medium"
    if matched_term_count >= 1 and (review_count >= 20 or listing_signal_count >= 1):
        return "medium"
    return "low"
