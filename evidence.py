import re
from typing import Any, Dict, Iterable, List, Sequence, Set


TOKEN_RE = re.compile(r"[\w가-힣]+")


def tokenize(text: str) -> List[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 1]


def _normalize_snippets(snippets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for snippet in snippets or []:
        text = (snippet.get("text") or "").strip()
        if not text:
            continue
        normalized.append({"text": text, "source": snippet.get("source", "snippet")})
    return normalized


def build_evidence(product: Dict[str, Any], query: str, snippets: Iterable[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    normalized_snippets = _normalize_snippets(snippets or [])
    query_tokens: Set[str] = set(tokenize(query))
    title = product.get("title") or product.get("productName") or ""
    vendor = product.get("vendor") or product.get("brand") or ""
    description = product.get("description") or ""
    text_haystack = " ".join([title, vendor, description] + [item["text"] for item in normalized_snippets])
    haystack_tokens = set(tokenize(text_haystack))
    matched_terms = sorted(query_tokens & haystack_tokens)

    rating = float(product.get("rating") or product.get("ratingAverage") or 0.0)
    review_count = int(product.get("review_count") or product.get("reviewCount") or 0)
    snippet_bonus = min(len(normalized_snippets), 3)
    keyword_score = len(matched_terms) * 4
    review_score = min(review_count / 25.0, 8.0)
    rating_score = rating * 2.0
    evidence_score = round(keyword_score + review_score + rating_score + snippet_bonus, 2)

    risks: List[str] = []
    if not normalized_snippets:
        risks.append("No external evidence snippet supplied; rationale relies on product metadata.")
    if review_count < 20:
        risks.append("Limited review history reduces confidence.")
    if rating and rating < 4.0:
        risks.append("Average rating is below the preferred threshold.")
    if not product.get("deeplink") and not product.get("productUrl"):
        risks.append("Missing deeplink requires operator review before publishing.")

    facts: List[str] = []
    if matched_terms:
        facts.append(f"Matched query terms: {', '.join(matched_terms)}")
    if rating:
        facts.append(f"Rating {rating:.1f}")
    if review_count:
        facts.append(f"{review_count} reviews")
    if normalized_snippets:
        facts.append(f"{len(normalized_snippets)} user-supplied evidence snippet(s)")

    return {
        "matched_terms": matched_terms,
        "score": evidence_score,
        "risks": risks,
        "facts": facts,
        "snippets": normalized_snippets,
    }
