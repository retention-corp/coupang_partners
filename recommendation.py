import re
from typing import Any, Dict, Iterable, List, Optional

from evidence import build_evidence

DISCLOSURE_TEXT = "파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음"


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


def normalize_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    query = (payload.get("query") or payload.get("intent") or "").strip()
    constraints = payload.get("constraints") or {}
    budget = payload.get("budget")
    if budget in (None, ""):
        budget = payload.get("price_max")
    if budget in (None, ""):
        budget = _parse_budget_from_text(query)
    budget_value = _coerce_int(budget)
    category = payload.get("category") or constraints.get("category")
    brand = payload.get("brand") or constraints.get("brand")
    must_have = [str(item).strip() for item in constraints.get("must_have", []) if str(item).strip()]
    avoid = [str(item).strip() for item in constraints.get("avoid", []) if str(item).strip()]
    must_have.extend(str(item).strip() for item in payload.get("include_terms", []) if str(item).strip())
    avoid.extend(str(item).strip() for item in payload.get("exclude_terms", []) if str(item).strip())
    return {
        "query": query,
        "budget": budget_value,
        "category": category,
        "brand": brand,
        "must_have": list(dict.fromkeys(must_have)),
        "avoid": list(dict.fromkeys(avoid)),
        "limit": _coerce_int(payload.get("limit") or payload.get("max_candidates")) or 3,
    }


def build_search_queries(normalized: Dict[str, Any]) -> List[str]:
    query = normalized["query"]
    queries: List[str] = []
    if query:
        queries.append(query)
    category = normalized.get("category")
    if category and category not in query:
        queries.append(f"{category} {query}".strip())
    for token in normalized.get("must_have", []):
        if token not in query:
            queries.append(f"{token} {query}".strip())
    brand = normalized.get("brand")
    if brand and brand not in query:
        queries.append(f"{brand} {query}".strip())
    deduped: List[str] = []
    for item in queries:
        if item and item not in deduped:
            deduped.append(item)
    return deduped[:4]


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
    if evidence.get("listing_signals"):
        fragments.append(f"It shows strong listing signals such as {', '.join(evidence['listing_signals'])}")
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
    evidence_snippets: Optional[Iterable[Dict[str, Any]]] = None,
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


def build_assist_response(
    *,
    normalized: Dict[str, Any],
    search_plan: List[str],
    recommendations: List[Dict[str, Any]],
    query_id: str,
) -> Dict[str, Any]:
    best_fit = recommendations[0] if recommendations else None
    risks: List[str] = []
    for item in recommendations:
        for risk in item.get("risks", []):
            if risk not in risks:
                risks.append(risk)

    summary_parts = []
    if best_fit:
        summary_parts.append(
            "가장 적합한 후보는 '{title}'입니다".format(title=best_fit["title"])
        )
        if best_fit.get("rationale"):
            summary_parts.append(best_fit["rationale"])
    else:
        summary_parts.append("현재 조건으로는 충분히 강한 후보를 찾지 못했습니다")

    return {
        "query_id": query_id,
        "normalized_intent": normalized,
        "search_plan": search_plan,
        "best_fit": best_fit,
        "shortlist": recommendations[:3],
        "summary": " ".join(summary_parts),
        "risks": risks,
        "disclosure": DISCLOSURE_TEXT,
    }


def _parse_budget_from_text(text: str) -> Optional[int]:
    man_match = re.search(r"(\d+)\s*만\s*원", text)
    if man_match:
        return int(man_match.group(1)) * 10000

    won_match = re.search(r"(\d[\d,]*)\s*원", text)
    if won_match:
        return int(won_match.group(1).replace(",", ""))

    return None
