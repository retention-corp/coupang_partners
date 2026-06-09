import re
from typing import Any, Dict, Iterable, List, Optional

from evidence import build_evidence

DISCLOSURE_TEXT = "파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음"
INTENT_RECOMMENDATION = "recommendation"
INTENT_EXTREMUM_SEARCH = "extremum_search"

_LENGTH_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(cm|센티(?:미터)?|미터|m)(?![a-zA-Z가-힣])",
    re.IGNORECASE,
)
_EXTREMUM_RULES = (
    (re.compile(r"(?:제일|가장)\s*긴|최장"), "length_m", "desc"),
    (re.compile(r"(?:제일|가장)\s*(?:싼|저렴한)|최저가"), "price", "asc"),
    (re.compile(r"평점\s*(?:높은|좋은)|별점\s*(?:높은|좋은)"), "rating", "desc"),
    (re.compile(r"리뷰\s*(?:많은|많고)|후기\s*(?:많은|많고)"), "review_count", "desc"),
)
_QUERY_NOISE_PATTERNS = (
    re.compile(r"쿠팡(?:에서|으로)?"),
    re.compile(r"(?:제일|가장)\s*긴\s*거?"),
    re.compile(r"최장"),
    re.compile(r"(?:제일|가장)\s*(?:싼|저렴한)\s*거?"),
    re.compile(r"최저가"),
    re.compile(r"평점\s*(?:높은|좋은)\s*거?"),
    re.compile(r"별점\s*(?:높은|좋은)\s*거?"),
    re.compile(r"리뷰\s*(?:많은|많고)\s*거?"),
    re.compile(r"후기\s*(?:많은|많고)\s*거?"),
    re.compile(r"(?:제품|상품)"),
    re.compile(r"(?:찾아줘|검색해줘|보여줘|골라줘|추천해줘|링크\s*줘|알려줘|찾아봐줘)"),
)
_CATEGORY_EXCLUSION_MAP = {
    "꽃병": ["식물", "화초", "스투키", "몬스테라", "공기정화"],
    "무선청소기": ["업소용", "산업용", "대형"],
    "청소기": ["업소용", "산업용", "대형"],
    "오트밀크": ["물티슈", "양말", "제로사이다", "사이다", "키친타월", "휴지"],
}
_QUERY_MUST_HAVE_RULES = (
    ("오트밀크", ["오트", "밀크"]),
    ("오트 밀크", ["오트", "밀크"]),
    ("귀리음료", ["귀리", "음료"]),
    ("바리스타", ["바리스타"]),
    ("aux", ["aux"]),
    ("3.5mm", ["3.5mm"]),
)
_QUERY_RELEVANCE_STOPWORDS = {
    "추천",
    "추천해줘",
    "찾아줘",
    "검색",
    "검색해줘",
    "보여줘",
    "골라줘",
    "알려줘",
    "이하",
    "이상",
    "미만",
    "초과",
    "만원",
    "원",
    "쿠팡",
    "로켓",
    "배송",
    "무료배송",
    "저렴한",
    "싼",
    "가성비",
    "좋은",
}
_QUERY_RELEVANCE_MODIFIERS = {
    "무선",
    "유선",
    "저소음",
    "원룸",
    "소형",
    "대형",
    "미니",
    "휴대용",
}


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
    length_m = _extract_length_meters(title)
    rating = (
        raw_product.get("rating")
        or raw_product.get("ratingAverage")
        or raw_product.get("page_rating")
        or 0
    )
    review_count = (
        raw_product.get("review_count")
        or raw_product.get("reviewCount")
        or raw_product.get("page_review_count")
        or 0
    )
    description = raw_product.get("description") or ""
    page_description = raw_product.get("page_description") or ""
    page_snippets = list(raw_product.get("page_snippets") or [])
    summary = _build_product_summary(title, description, page_description, page_snippets)
    return {
        "product_id": str(raw_product.get("product_id") or raw_product.get("productId") or raw_product.get("id") or title),
        "title": title,
        "price": price,
        "currency": raw_product.get("currency", "KRW"),
        "vendor": raw_product.get("vendor") or raw_product.get("brand"),
        "rating": rating,
        "review_count": review_count,
        "length_m": length_m,
        "deeplink": deeplink,
        "description": description,
        "summary": summary,
        "page_title": raw_product.get("page_title") or "",
        "page_description": page_description,
        "page_snippets": page_snippets,
        "page_facts": list(raw_product.get("page_facts") or []),
        "page_url": raw_product.get("page_url") or "",
        "metadata": raw_product,
    }


def _build_product_summary(
    title: str,
    description: str,
    page_description: str,
    page_snippets: List[str],
) -> str:
    """Synthesize a short top-level summary so recommendations expose at least one
    reader-ready sentence without walking into `metadata`. Preference order:
    Coupang metadata description → landing-page description → first landing-page snippet.
    Capped at 280 chars to keep JSON responses small for the MCP callers.
    """

    for candidate in (description, page_description):
        text = (candidate or "").strip()
        if text:
            return text[:280]
    for snippet in page_snippets:
        text = (str(snippet) or "").strip()
        if text:
            return text[:280]
    return (title or "").strip()[:280]


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
    inferred_must_have = _infer_must_have_terms(query)
    intent_type, sort_key, sort_direction = _detect_intent(query)
    return {
        "query": query,
        "query_core": _clean_search_query(query),
        "budget": budget_value,
        "category": category,
        "brand": brand,
        "must_have": list(dict.fromkeys(must_have + inferred_must_have)),
        "avoid": list(dict.fromkeys(avoid)),
        "limit": _coerce_int(payload.get("limit") or payload.get("max_candidates")) or 3,
        "intent_type": intent_type,
        "sort_key": sort_key,
        "sort_direction": sort_direction,
    }


def build_search_queries(normalized: Dict[str, Any]) -> List[str]:
    query = normalized["query"]
    base_query = normalized.get("query_core") or query
    queries: List[str] = []
    if base_query:
        queries.append(base_query)
    if query and query != base_query and normalized.get("intent_type") != INTENT_EXTREMUM_SEARCH:
        queries.append(query)
    category = normalized.get("category")
    if category and category not in base_query:
        queries.append(f"{category} {base_query}".strip())
    for token in normalized.get("must_have", []):
        if token.lower() not in base_query.lower():
            queries.append(f"{token} {base_query}".strip())
    brand = normalized.get("brand")
    if brand and brand not in base_query:
        queries.append(f"{brand} {base_query}".strip())
    deduped: List[str] = []
    for item in queries:
        if item and item not in deduped:
            deduped.append(item)
    return deduped[:4]


def infer_exclusion_terms(normalized: Dict[str, Any]) -> List[str]:
    query = normalized["query"]
    exclusions = list(normalized.get("avoid", []))
    category = str(normalized.get("category") or "")
    for key, terms in _CATEGORY_EXCLUSION_MAP.items():
        if key and (key in query or key == category):
            exclusions.extend(terms)
    return list(dict.fromkeys(exclusions))


def _infer_must_have_terms(query: str) -> List[str]:
    lowered_query = query.lower()
    inferred: List[str] = []
    for needle, terms in _QUERY_MUST_HAVE_RULES:
        if needle in lowered_query:
            inferred.extend(terms)
    return list(dict.fromkeys(inferred))


def _budget_delta_score(price: Optional[int], budget: Optional[int]) -> float:
    if budget is None or price is None:
        return 0.0
    if price > budget:
        return -8.0 - ((price - budget) / max(budget, 1))
    return max(0.0, 6.0 - abs(budget - price) / max(budget, 1) * 6.0)


def _compose_rationale(
    product: Dict[str, Any],
    evidence: Dict[str, Any],
    budget: Optional[int],
    *,
    intent_type: str,
    sort_key: Optional[str],
    comparison_label: Optional[str],
) -> str:
    fragments: List[str] = []
    if intent_type == INTENT_EXTREMUM_SEARCH:
        fragments.extend(_comparison_fragments(product, sort_key, comparison_label))
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
    intent_type: str = INTENT_RECOMMENDATION,
    sort_key: Optional[str] = None,
    sort_direction: Optional[str] = None,
) -> List[Dict[str, Any]]:
    gated_recommendations: List[Dict[str, Any]] = []
    fallback_recommendations: List[Dict[str, Any]] = []
    inferred_query_terms = _infer_must_have_terms(query)
    required_query_terms = inferred_query_terms or _required_relevance_terms(query)
    for raw_product in products:
        product = normalize_product(raw_product)
        if budget is not None and product["price"] is not None and product["price"] > budget * 1.25:
            continue
        evidence = build_evidence(product, query, evidence_snippets)
        confidence = evidence.get("confidence", "low")
        confidence_adjustment = {"high": 1.5, "medium": 0.0, "low": -4.0}.get(confidence, -4.0)
        score = round(evidence["score"] + _budget_delta_score(product["price"], budget) + confidence_adjustment, 2)
        comparison_value = _comparison_value(product, sort_key)
        comparison_label = _format_comparison_value(sort_key, comparison_value)
        risks = list(evidence["risks"])
        comparison_risk = _comparison_risk(sort_key, comparison_value)
        if comparison_risk and comparison_risk not in risks:
            risks.append(comparison_risk)
        recommendation = (
            {
                **product,
                "score": score,
                "rationale": _compose_rationale(
                    product,
                    evidence,
                    budget,
                    intent_type=intent_type,
                    sort_key=sort_key,
                    comparison_label=comparison_label,
                ),
                "risks": risks,
                "evidence": {
                    "confidence": confidence,
                    "facts": evidence["facts"],
                    "matched_terms": evidence["matched_terms"],
                    "snippet_count": len(evidence["snippets"]),
                },
                "comparison": {
                    "sort_key": sort_key,
                    "sort_direction": sort_direction,
                    "value": comparison_value,
                    "label": comparison_label,
                },
            }
        )
        if _passes_generic_relevance_gate(
            recommendation,
            intent_type=intent_type,
            comparison_value=comparison_value,
            inferred_query_terms=inferred_query_terms,
            required_query_terms=required_query_terms,
        ):
            gated_recommendations.append(recommendation)
        fallback_recommendations.append(recommendation)

    recommendations = gated_recommendations or ([] if required_query_terms else fallback_recommendations)
    if intent_type == INTENT_EXTREMUM_SEARCH and sort_key:
        _sort_extremum_recommendations(recommendations, sort_key, sort_direction or "desc")
    else:
        recommendations.sort(key=lambda item: (item["score"], item.get("review_count", 0), -(item.get("price") or 0)), reverse=True)
    return recommendations[:top_n]


def _passes_generic_relevance_gate(
    recommendation: Dict[str, Any],
    *,
    intent_type: str,
    comparison_value: Optional[float],
    inferred_query_terms: Optional[List[str]] = None,
    required_query_terms: Optional[List[str]] = None,
) -> bool:
    evidence = recommendation.get("evidence", {})
    matched_terms = evidence.get("matched_terms") or []
    snippet_count = int(evidence.get("snippet_count") or 0)
    listing_facts = " ".join(evidence.get("facts") or [])
    has_listing_signal = "Listing signals:" in listing_facts
    title = str(recommendation.get("title") or "").lower()
    searchable_text = _recommendation_searchable_text(recommendation)

    if required_query_terms and not any(term.lower() in searchable_text for term in required_query_terms):
        return False

    if matched_terms:
        return True
    if inferred_query_terms and all(term.lower() in title for term in inferred_query_terms):
        return True
    if snippet_count > 0:
        return True
    if has_listing_signal:
        return True
    if intent_type == INTENT_EXTREMUM_SEARCH and comparison_value is not None:
        return True
    return False


def _required_relevance_terms(query: str) -> List[str]:
    tokens = re.findall(r"[\w가-힣]+", _clean_search_query(query).lower())
    required: List[str] = []
    for token in tokens:
        if len(token) <= 1:
            continue
        if any(char.isdigit() for char in token):
            continue
        if token in _QUERY_RELEVANCE_STOPWORDS or token in _QUERY_RELEVANCE_MODIFIERS:
            continue
        required.append(token)
    return list(dict.fromkeys(required))


def _recommendation_searchable_text(recommendation: Dict[str, Any]) -> str:
    metadata = recommendation.get("metadata") or {}
    fields = [
        recommendation.get("title"),
        recommendation.get("summary"),
        recommendation.get("description"),
        recommendation.get("page_title"),
        recommendation.get("page_description"),
        metadata.get("productName"),
        metadata.get("categoryName"),
    ]
    fields.extend(recommendation.get("page_snippets") or [])
    return " ".join(str(value).lower() for value in fields if str(value or "").strip())


def build_assist_response(
    *,
    normalized: Dict[str, Any],
    search_plan: List[str],
    recommendations: List[Dict[str, Any]],
    query_id: Optional[str],
) -> Dict[str, Any]:
    best_fit = recommendations[0] if recommendations else None
    risks: List[str] = []
    for item in recommendations:
        for risk in item.get("risks", []):
            if risk not in risks:
                risks.append(risk)

    summary_parts = []
    if best_fit:
        summary_parts.append(_build_summary_lead(best_fit, normalized))
        if best_fit.get("rationale"):
            summary_parts.append(best_fit["rationale"])
    else:
        summary_parts.append("현재 조건으로는 충분히 강한 후보를 찾지 못했습니다")

    conversation_ux = _build_conversation_ux(normalized, recommendations[:3])

    return {
        "query_id": query_id,
        "normalized_intent": normalized,
        "search_plan": search_plan,
        "best_fit": best_fit,
        "shortlist": recommendations[:3],
        "conversation_ux": conversation_ux,
        "summary": " ".join(summary_parts),
        "risks": risks,
        "disclosure": DISCLOSURE_TEXT,
    }


def _build_conversation_ux(normalized: Dict[str, Any], recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    query = normalized.get("query") or "요청하신 상품"
    if not recommendations:
        return {
            "mode": "number_choice",
            "message": f"{query} 기준으로 바로 추천할 만한 후보를 찾지 못했습니다. 없으면 '없다'라고 답하면 됩니다.",
            "choices": [],
            "reply_hint": "없다",
        }

    lines = [f"{query} 후보를 찾았습니다."]
    choices: List[Dict[str, Any]] = []
    for index, item in enumerate(recommendations, start=1):
        metrics = _format_choice_metrics(item)
        reason = _format_choice_reason(item)
        line = f"{index}. {item.get('title') or '상품명 없음'}"
        if metrics:
            line += f"\n   {metrics}"
        if reason:
            line += f"\n   이유: {reason}"
        lines.append(line)
        choices.append(
            {
                "number": index,
                "product_id": item.get("product_id"),
                "title": item.get("title"),
                "price": item.get("price"),
                "rating": item.get("rating"),
                "review_count": item.get("review_count"),
                "reason": reason,
                "deeplink": item.get("short_deeplink") or item.get("deeplink"),
            }
        )
    lines.append("살만한 게 있으면 번호만 보내주세요. 없으면 '없다'라고 보내면 됩니다.")
    return {
        "mode": "number_choice",
        "message": "\n\n".join(lines),
        "choices": choices,
        "reply_hint": "번호만 입력하거나 없다",
    }


def _format_choice_metrics(item: Dict[str, Any]) -> str:
    metrics: List[str] = []
    price = item.get("price")
    if price is not None:
        metrics.append(f"₩{int(price):,}")
    rating = item.get("rating")
    if rating:
        metrics.append(f"평점 {float(rating):.1f}")
    review_count = item.get("review_count")
    if review_count:
        metrics.append(f"리뷰 {int(review_count):,}개")
    comparison_label = (item.get("comparison") or {}).get("label")
    if comparison_label:
        sort_key = (item.get("comparison") or {}).get("sort_key")
        if sort_key == "price":
            metrics.append(f"비교 기준 {comparison_label}")
        elif sort_key == "rating":
            metrics.append(f"비교 기준 {comparison_label}")
        elif sort_key == "review_count":
            metrics.append(f"비교 기준 {comparison_label}")
        elif sort_key == "length_m":
            metrics.append(f"길이 {comparison_label}")
    return " · ".join(metrics)


def _format_choice_reason(item: Dict[str, Any]) -> str:
    title = str(item.get("title") or "")
    evidence = item.get("evidence") or {}
    matched_terms = [str(term) for term in evidence.get("matched_terms") or []]
    signals = []
    if matched_terms:
        signals.append(f"요청어({', '.join(matched_terms[:4])})와 맞음")
    if item.get("rating") and float(item.get("rating") or 0) >= 4.5:
        signals.append("평점이 높음")
    if item.get("review_count") and int(item.get("review_count") or 0) >= 100:
        signals.append("리뷰 수가 충분함")
    summary = str(item.get("summary") or "").strip()
    if summary and summary != title:
        compact = re.sub(r"\s+", " ", summary)
        signals.append(compact[:80])
    if not signals:
        signals.append("현재 확보된 상품 정보 안에서 가장 조건에 가까움")
    return ", ".join(signals[:3])


def _parse_budget_from_text(text: str) -> Optional[int]:
    man_match = re.search(r"(\d+)\s*만\s*원", text)
    if man_match:
        return int(man_match.group(1)) * 10000

    won_match = re.search(r"(\d[\d,]*)\s*원", text)
    if won_match:
        return int(won_match.group(1).replace(",", ""))

    return None


def _detect_intent(query: str) -> tuple[str, Optional[str], Optional[str]]:
    for pattern, sort_key, sort_direction in _EXTREMUM_RULES:
        if pattern.search(query):
            return INTENT_EXTREMUM_SEARCH, sort_key, sort_direction
    return INTENT_RECOMMENDATION, None, None


def _clean_search_query(query: str) -> str:
    cleaned = query
    for pattern in _QUERY_NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    return cleaned or query


def _extract_length_meters(text: str) -> Optional[float]:
    matches: List[float] = []
    for raw_value, unit in _LENGTH_PATTERN.findall(text):
        value = float(raw_value)
        normalized_unit = unit.lower()
        if normalized_unit.startswith("c") or unit.startswith("센티"):
            value = value / 100
        matches.append(value)
    return max(matches) if matches else None


def _comparison_value(product: Dict[str, Any], sort_key: Optional[str]) -> Optional[float]:
    if sort_key == "length_m":
        return product.get("length_m")
    if sort_key == "price":
        price = product.get("price")
        return float(price) if price is not None else None
    if sort_key == "rating":
        rating = product.get("rating")
        return float(rating) if rating is not None else None
    if sort_key == "review_count":
        review_count = product.get("review_count")
        return float(review_count) if review_count is not None else None
    return None


def _format_comparison_value(sort_key: Optional[str], value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if sort_key == "length_m":
        if float(value).is_integer():
            return f"{int(value)}m"
        return f"{value:.1f}m"
    if sort_key == "price":
        return f"₩{int(value):,}"
    if sort_key == "rating":
        return f"{value:.1f}점"
    if sort_key == "review_count":
        return f"{int(value):,}개 리뷰"
    return str(value)


def _comparison_fragments(product: Dict[str, Any], sort_key: Optional[str], comparison_label: Optional[str]) -> List[str]:
    if sort_key == "length_m":
        if comparison_label:
            return [f"It exposes an explicit cable length of {comparison_label}, which is the longest among the returned listings"]
        return ["It matches the cable query, but the listing does not expose an explicit length in the title metadata"]
    if sort_key == "price" and comparison_label:
        return [f"It is the lowest-priced option among the returned listings at {comparison_label}"]
    if sort_key == "rating" and comparison_label:
        return [f"It has the highest visible rating among the returned listings at {comparison_label}"]
    if sort_key == "review_count" and comparison_label:
        return [f"It has the largest visible review count among the returned listings at {comparison_label}"]
    return []


def _comparison_risk(sort_key: Optional[str], comparison_value: Optional[float]) -> Optional[str]:
    if sort_key == "length_m" and comparison_value is None:
        return "Listing title does not expose an explicit cable length, so longest-item ranking is uncertain."
    return None


def _sort_extremum_recommendations(
    recommendations: List[Dict[str, Any]],
    sort_key: str,
    sort_direction: str,
) -> None:
    if sort_key == "price" or sort_direction == "asc":
        recommendations.sort(
            key=lambda item: (
                item["comparison"]["value"] is None,
                item["comparison"]["value"] if item["comparison"]["value"] is not None else float("inf"),
                -item["score"],
                -(item.get("review_count", 0)),
            )
        )
        return

    recommendations.sort(
        key=lambda item: (
            item["comparison"]["value"] is not None,
            item["comparison"]["value"] if item["comparison"]["value"] is not None else -1,
            item["score"],
            item.get("review_count", 0),
            -(item.get("price") or 0),
        ),
        reverse=True,
    )


def _build_summary_lead(best_fit: Dict[str, Any], normalized: Dict[str, Any]) -> str:
    if normalized.get("intent_type") != INTENT_EXTREMUM_SEARCH:
        return "가장 적합한 후보는 '{title}'입니다".format(title=best_fit["title"])

    sort_key = normalized.get("sort_key")
    comparison_label = best_fit.get("comparison", {}).get("label")
    title = best_fit["title"]
    if sort_key == "length_m":
        if comparison_label:
            return "현재 반환된 목록 중 길이 표기가 가장 긴 후보는 '{title}' ({label})입니다".format(
                title=title,
                label=comparison_label,
            )
        return "'{title}'는 현재 반환된 목록 중 유력한 후보지만 길이 표기가 없어 최장 판단의 불확실성이 있습니다".format(
            title=title
        )
    if sort_key == "price" and comparison_label:
        return "현재 반환된 목록 중 최저가 후보는 '{title}' ({label})입니다".format(title=title, label=comparison_label)
    if sort_key == "rating" and comparison_label:
        return "현재 반환된 목록 중 평점이 가장 높은 후보는 '{title}' ({label})입니다".format(title=title, label=comparison_label)
    if sort_key == "review_count" and comparison_label:
        return "현재 반환된 목록 중 리뷰가 가장 많은 후보는 '{title}' ({label})입니다".format(title=title, label=comparison_label)
    return "현재 반환된 목록 중 가장 유력한 후보는 '{title}'입니다".format(title=title)
