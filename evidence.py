import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
RISK_PATTERNS = {
    "out_of_stock": ("품절", "out of stock", "sold out"),
    "shipping_delay": ("배송 지연", "delivery delay", "예약판매", "preorder"),
    "refurbished": ("리퍼", "refurbished", "used", "중고"),
    "policy_sensitive": ("병행수입", "parallel import", "overseas direct purchase"),
}


def tokenize(text: str) -> List[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def matched_terms(query: str, texts: Sequence[str]) -> List[str]:
    query_terms = {
        token for token in tokenize(query)
        if len(token) > 1 and token not in {"for", "with", "the", "and"}
    }
    corpus = " ".join(texts).lower()
    return sorted(term for term in query_terms if term in corpus)


def keyword_overlap_score(query: str, texts: Sequence[str]) -> float:
    query_terms = {
        token for token in tokenize(query)
        if len(token) > 1 and token not in {"for", "with", "the", "and"}
    }
    if not query_terms:
        return 0.0
    hits = matched_terms(query, texts)
    return round(len(hits) / len(query_terms), 4)


def detect_risks(texts: Iterable[str], product: Optional[Dict[str, Any]] = None) -> List[str]:
    corpus = " ".join(texts).lower()
    risks: Set[str] = set()
    for risk, patterns in RISK_PATTERNS.items():
        if any(pattern.lower() in corpus for pattern in patterns):
            risks.add(risk)

    if product is not None:
        price = safe_float(product.get("productPrice") or product.get("price"))
        if price is None:
            risks.add("missing_price")
        if safe_float(product.get("rating")) is None and safe_float(product.get("productRating")) is None:
            risks.add("weak_rating_signal")

    return sorted(risks)


def safe_float(value: Any) -> Optional[float]:
    if value in (None, "", False):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def product_texts(product: Dict[str, Any], snippets: Optional[Iterable[Any]] = None) -> List[str]:
    texts = [
        str(product.get("productName") or product.get("name") or ""),
        str(product.get("vendorItemName") or ""),
        str(product.get("brand") or ""),
        str(product.get("vendor") or ""),
        str(product.get("categoryName") or ""),
        str(product.get("productUrl") or product.get("url") or ""),
    ]
    for snippet in snippets or []:
        if isinstance(snippet, str):
            texts.append(snippet)
            continue
        if isinstance(snippet, dict):
            texts.append(str(snippet.get("snippet") or snippet.get("text") or ""))
    return [text for text in texts if text]


def extract_evidence(
    query: str,
    product: Dict[str, Any],
    snippets: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    texts = product_texts(product, snippets)
    overlap = keyword_overlap_score(query, texts)
    risks = detect_risks(texts, product=product)

    rating = safe_float(product.get("rating") or product.get("productRating")) or 0.0
    review_count = safe_float(product.get("reviewCount") or product.get("ratingCount")) or 0.0
    rating_signal = min(rating / 5.0, 1.0)
    review_signal = min(math.log10(review_count + 1) / 3.0, 1.0)
    snippet_signal = min(sum(1 for text in texts if len(text) > 20) / 5.0, 1.0)
    penalty = 0.15 * len(risks)

    score = max(overlap * 2.5 + rating_signal + review_signal + snippet_signal - penalty, 0.0)
    strength = "strong" if score >= 3 else "medium" if score >= 1.75 else "weak"

    evidence_snippets = []
    for snippet in snippets or []:
        if isinstance(snippet, str):
            evidence_snippets.append({"source": "user_supplied", "snippet": snippet})
        elif isinstance(snippet, dict):
            evidence_snippets.append(
                {
                    "source": snippet.get("source", "user_supplied"),
                    "snippet": snippet.get("snippet") or snippet.get("text") or "",
                }
            )

    return {
        "score": round(score, 4),
        "strength": strength,
        "matched_terms": matched_terms(query, texts),
        "risks": risks,
        "snippets": evidence_snippets[:3],
        "rating": rating or None,
        "review_count": int(review_count) if review_count else 0,
    }
