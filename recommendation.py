from typing import Any, Dict, Iterable, List, Optional

from evidence import build_evidence


def _coerce_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_product(raw_product: Dict[str, Any]) -> Dict[str, Any]:
    title = raw_product.get("title") or raw_product.get("productName") or "Untitled product"
    price = _coerce_int(raw_product.get("price") or raw_product.get("salePrice") or raw_product.get("productPrice"))
    deeplink = raw_product.get("deeplink") or raw_product.get("productUrl") or raw_product.get("url")
    return {
        "product_id": str(raw_product.get("product_id") or raw_product.get("productId") or raw_product.get("id") or title),
        "title": title,
        "price": price,
        "currency": raw_product.get("currency", "KRW"),
        "vendor": raw_product.get("vendor") or raw_product.get("brand"),
        "rating": raw_product.get("rating") or raw_product.get("ratingAverage") or 0,
        "review_count": raw_product.get("review_count") or raw_product.get("reviewCount") or 0,
        "deeplink": deeplink,
        "metadata": raw_product,
    }


def _budget_delta_score(price: Optional[int], budget: Optional[int]) -> float:
    if budget is None or price is None:
        return 0.0
    if price > budget:
        return -8.0 - ((price - budget) / max(budget, 1))
    return max(0.0, 6.0 - abs(budget - price) / max(budget, 1) * 6.0)


def _compose_rationale(product: Dict[str, Any], evidence: Dict[str, Any], budget: Optional[int]) -> str:
    fragments: List[str] = []
    if evidence["matched_terms"]:
        fragments.append(f"It directly matches {', '.join(evidence['matched_terms'])} from the shopping request")
    if product.get("price") is not None and budget is not None and product["price"] <= budget:
        fragments.append(f"it stays within the ₩{budget:,} budget")
    if product.get("rating"):
        fragments.append(f"its rating is {float(product['rating']):.1f}")
    if product.get("review_count"):
        fragments.append(f"it has {int(product['review_count'])} reviews")
    if evidence["snippets"]:
        fragments.append("the supplied evidence snippets reinforce the fit")
    if not fragments:
        fragments.append("it is the strongest available match from the current metadata")
    sentence = "; ".join(fragments)
    return sentence[0].upper() + sentence[1:] + "."


def recommend_products(
    *,
    query: str,
    products: Iterable[Dict[str, Any]],
    budget: Optional[int] = None,
    evidence_snippets: Iterable[Dict[str, Any]] | None = None,
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    recommendations: List[Dict[str, Any]] = []
    for raw_product in products:
        product = normalize_product(raw_product)
        if budget is not None and product["price"] is not None and product["price"] > budget * 1.25:
            continue
        evidence = build_evidence(product, query, evidence_snippets)
        score = round(evidence["score"] + _budget_delta_score(product["price"], budget), 2)
        recommendations.append(
            {
                **product,
                "score": score,
                "rationale": _compose_rationale(product, evidence, budget),
                "risks": evidence["risks"],
                "evidence": {
                    "facts": evidence["facts"],
                    "matched_terms": evidence["matched_terms"],
                    "snippet_count": len(evidence["snippets"]),
                },
            }
        )

    recommendations.sort(key=lambda item: (item["score"], item.get("review_count", 0), -(item.get("price") or 0)), reverse=True)
    return recommendations[:top_n]
