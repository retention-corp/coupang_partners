from typing import Any, Dict, Iterable, List, Optional

try:
    from .evidence import extract_evidence, safe_float
except ImportError:  # pragma: no cover - direct script fallback
    from evidence import extract_evidence, safe_float


def normalize_product(product: Dict[str, Any]) -> Dict[str, Any]:
    product_id = str(
        product.get("productId")
        or product.get("itemId")
        or product.get("id")
        or product.get("productUrl")
        or product.get("url")
        or ""
    )
    return {
        **product,
        "product_id": product_id,
        "product_name": product.get("productName") or product.get("name") or "Unknown product",
        "price": safe_float(product.get("productPrice") or product.get("price")),
        "currency": product.get("currency") or "KRW",
        "rating": safe_float(product.get("rating") or product.get("productRating")),
        "review_count": int(safe_float(product.get("reviewCount") or product.get("ratingCount")) or 0),
        "product_url": product.get("productUrl") or product.get("url"),
        "deeplink": product.get("deeplink"),
        "vendor": product.get("vendor") or product.get("brand"),
    }


def compose_rationale(
    query: str,
    candidate: Dict[str, Any],
    evidence: Dict[str, Any],
    *,
    budget_max: Optional[float] = None,
    feedback: Optional[Dict[str, int]] = None,
) -> str:
    reasons: List[str] = []
    matched = evidence.get("matched_terms", [])
    if matched:
        reasons.append(f"matches query terms: {', '.join(matched[:4])}")

    rating = candidate.get("rating")
    review_count = candidate.get("review_count", 0)
    if rating:
        reasons.append(f"rating signal {rating:.1f}/5 from {review_count} reviews")

    price = candidate.get("price")
    if budget_max is not None and price is not None:
        if price <= budget_max:
            reasons.append(f"fits the stated budget at ₩{int(price):,}")
        else:
            reasons.append(f"exceeds the stated budget at ₩{int(price):,}")
    elif price is not None:
        reasons.append(f"current listed price is ₩{int(price):,}")

    if feedback:
        clicks = feedback.get("clicks", 0)
        purchases = feedback.get("purchases", 0)
        if purchases:
            reasons.append(f"has {purchases} recorded downstream purchase signals")
        elif clicks:
            reasons.append(f"has {clicks} prior deeplink click signals")

    if not reasons:
        reasons.append(f"retained as a fallback candidate for {query}")

    rationale = "; ".join(reasons)
    if evidence.get("risks"):
        rationale += f". Risks: {', '.join(evidence['risks'])}."
    else:
        rationale += "."
    return rationale


def rank_products(
    query: str,
    products: Iterable[Dict[str, Any]],
    *,
    budget_max: Optional[float] = None,
    evidence_snippets: Optional[Iterable[Any]] = None,
    product_feedback: Optional[Dict[str, Dict[str, int]]] = None,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    feedback_lookup = product_feedback or {}

    for raw_product in products:
        candidate = normalize_product(raw_product)
        price = candidate.get("price")
        if budget_max is not None and price is not None and price > budget_max:
            continue

        evidence = extract_evidence(query, candidate, evidence_snippets)
        feedback = feedback_lookup.get(candidate["product_id"], {})
        engagement_bonus = min(
            feedback.get("clicks", 0) * 0.15 + feedback.get("purchases", 0) * 0.4,
            1.5,
        )
        budget_bonus = 0.0
        if budget_max is not None and price is not None:
            budget_bonus = max(0.0, 1.0 - (price / budget_max))

        score = round(evidence["score"] + engagement_bonus + budget_bonus, 4)
        ranked.append(
            {
                "product_id": candidate["product_id"],
                "product_name": candidate["product_name"],
                "vendor": candidate.get("vendor"),
                "price": candidate.get("price"),
                "currency": candidate.get("currency"),
                "rating": candidate.get("rating"),
                "review_count": candidate.get("review_count"),
                "product_url": candidate.get("product_url"),
                "deeplink": candidate.get("deeplink") or candidate.get("product_url"),
                "score": score,
                "evidence": evidence,
                "risks": evidence.get("risks", []),
                "rationale": compose_rationale(
                    query,
                    candidate,
                    evidence,
                    budget_max=budget_max,
                    feedback=feedback,
                ),
            }
        )

    ranked.sort(
        key=lambda item: (
            item["score"],
            item.get("rating") or 0.0,
            -(item.get("price") or 0.0) if budget_max else item.get("review_count") or 0,
        ),
        reverse=True,
    )
    return ranked[:top_n]
