#!/usr/bin/env python3
"""Local verification for coupang_followups P1.1 — Hosted /v1/public/assist response
field hardening. Calls the hosted backend to get real Coupang search results, runs
each candidate through the updated fetch_product_page_evidence + normalize_product
pipeline, and checks that review_count>0 and a non-empty summary surface on at
least one item. This is the closest local proxy to the tracker's Verification step
when a Cloud Run redeploy is gated (e.g. during P0.3 lock)."""
import json
import os
import sys
from urllib import request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from product_page_evidence import fetch_product_page_evidence  # noqa: E402
from recommendation import normalize_product  # noqa: E402


def fetch_prod_shortlist(query: str):
    req = request.Request(
        "https://a.retn.kr/v1/public/assist",
        data=json.dumps({"query": query, "limit": 3}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "smoke-test"},
        method="POST",
    )
    with request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8")).get("shortlist") or []


def verify_item(item):
    raw = {
        "productId": item.get("product_id"),
        "productName": item.get("title"),
        "productUrl": item.get("deeplink"),
        "productPrice": item.get("price"),
    }
    evidence = fetch_product_page_evidence(raw, timeout_seconds=8)
    if evidence:
        raw.update(evidence)
    normalized = normalize_product(raw)
    return {
        "product_id": normalized["product_id"],
        "rating": normalized["rating"],
        "review_count": normalized["review_count"],
        "summary_len": len(normalized["summary"] or ""),
        "summary_preview": (normalized["summary"] or "")[:80],
        "page_evidence_fetched": evidence is not None,
    }


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "무선청소기 리뷰 많은 것"
    shortlist = fetch_prod_shortlist(query)
    print(f"query={query!r} candidates={len(shortlist)}")
    positives = 0
    for item in shortlist:
        result = verify_item(item)
        print(json.dumps(result, ensure_ascii=False))
        if result["review_count"] and result["review_count"] > 0 and result["summary_len"] > 0:
            positives += 1
    print(f"items_with_review_and_summary={positives}/{len(shortlist)}")
    return 0 if positives >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
