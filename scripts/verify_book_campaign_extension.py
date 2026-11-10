#!/usr/bin/env python3
"""Verify the protected book campaign extension without hidden mutations."""
import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib import error, parse, request


class HttpTransport:
    def request(self, method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]] = None, follow: bool = True) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        opener = request.build_opener() if follow else request.build_opener(_NoRedirect())
        try:
            with opener.open(request.Request(url, data=data, headers=headers, method=method), timeout=15) as response:
                raw = response.read(); payload = json.loads(raw) if raw else {}
                return response.status, payload, dict(response.headers)
        except error.HTTPError as exc:
            raw = exc.read(); payload = json.loads(raw) if raw else {}
            return exc.code, payload, dict(exc.headers)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--pilot-campaign", required=True); parser.add_argument("--pilot-slug", required=True)
    parser.add_argument("--canary-campaign", required=True); parser.add_argument("--canary-target")
    parser.add_argument("--canary-slug"); parser.add_argument("--create-canary", action="store_true")
    parser.add_argument("--exercise-canary-redirect-once", action="store_true")
    parser.add_argument("--legacy-fixture-slug"); parser.add_argument("--legacy-fixture-target-sha256")
    parser.add_argument("--base-url-public", required=True); parser.add_argument("--base-url-ops", required=True)
    parser.add_argument("--token", default=os.getenv("BOOK_CARDNEWS_COUPANG_OPS_TOKEN"))
    parser.add_argument("--client-id", default="insta-cardnews-book-sales-v1")
    parser.add_argument("--change-sha", default=os.getenv("BOOK_CAMPAIGN_CHANGE_SHA"))
    parser.add_argument("--source-manifest-sha", default=os.getenv("BOOK_CAMPAIGN_SOURCE_MANIFEST_SHA"))
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.verify_only:
        if args.create_canary or args.exercise_canary_redirect_once or not args.canary_slug:
            raise ValueError("verify-only requires --canary-slug and rejects mutation flags")
    elif not (args.create_canary and args.exercise_canary_redirect_once and args.canary_target):
        raise ValueError("execution mode requires canary create, target, and one redirect exercise")
    if not re.fullmatch(r"canary-smoke-[0-9a-f]{12}-[0-9a-f]{12}", args.canary_campaign):
        raise ValueError("invalid canary campaign id")
    if bool(args.legacy_fixture_slug) != bool(args.legacy_fixture_target_sha256):
        raise ValueError("legacy fixture slug and target hash must be paired")
    if not args.token:
        raise ValueError("ops token is required")


def verify(args: argparse.Namespace, transport: HttpTransport) -> Dict[str, Any]:
    validate_args(args)
    public = args.base_url_public.rstrip("/"); ops = args.base_url_ops.rstrip("/")
    good = {"Authorization": f"Bearer {args.token}", "X-OpenClaw-Client-Id": args.client_id, "Content-Type": "application/json"}
    status, health, _ = transport.request("GET", ops + "/health", {})
    if status != 200: raise RuntimeError("ops health failed")
    if args.change_sha and health.get("bookCampaignChangeSha") != args.change_sha: raise RuntimeError("change hash mismatch")
    if args.source_manifest_sha and health.get("bookCampaignSourceManifestSha") != args.source_manifest_sha: raise RuntimeError("source hash mismatch")
    if transport.request("GET", public + f"/internal/v1/book-campaign-links/{args.pilot_slug}", good)[0] != 404: raise RuntimeError("public route exposed")
    wrong = {**good, "Authorization": "Bearer wrong"}
    if transport.request("GET", ops + f"/internal/v1/book-campaign-links/{args.pilot_slug}", wrong)[0] != 401: raise RuntimeError("wrong bearer contract failed")
    disallowed = {**good, "X-OpenClaw-Client-Id": "disallowed-verifier"}
    if transport.request("GET", ops + f"/internal/v1/book-campaign-links/{args.pilot_slug}", disallowed)[0] != 403: raise RuntimeError("allowlist contract failed")
    pilot_before = transport.request("GET", ops + f"/internal/v1/book-campaign-links/{args.pilot_slug}", good)[1]
    if pilot_before.get("campaign_id") != args.pilot_campaign: raise RuntimeError("pilot lookup mismatch")
    start = datetime.now(timezone.utc).isoformat()
    canary_slug = args.canary_slug
    if not args.verify_only:
        status, created, _ = transport.request("POST", ops + "/internal/v1/book-campaign-links", good, {"campaign_id": args.canary_campaign, "target_url": args.canary_target})
        if status != 200: raise RuntimeError("canary create failed")
        canary_slug = created["slug"]
    end = datetime.now(timezone.utc).isoformat()
    query = parse.urlencode({"from": start, "to": end})
    before_count = transport.request("GET", ops + f"/internal/v1/book-campaigns/{args.canary_campaign}/clicks?{query}", good)[1].get("raw_clicks", 0)
    redirect_location = None
    if not args.verify_only and before_count == 0:
        status, _, headers = transport.request("GET", public + f"/s/{canary_slug}", {}, follow=False)
        if status != 302: raise RuntimeError("canary redirect failed")
        redirect_location = headers.get("Location")
    end2 = datetime.now(timezone.utc).isoformat(); query2 = parse.urlencode({"from": start, "to": end2})
    canary_count = transport.request("GET", ops + f"/internal/v1/book-campaigns/{args.canary_campaign}/clicks?{query2}", good)[1].get("raw_clicks", 0)
    if canary_count != 1: raise RuntimeError("canary click count is not exactly one")
    pilot_after = transport.request("GET", ops + f"/internal/v1/book-campaign-links/{args.pilot_slug}", good)[1]
    if pilot_before != pilot_after: raise RuntimeError("pilot lookup changed counters")
    legacy = None
    if args.legacy_fixture_slug:
        legacy = transport.request("GET", ops + f"/internal/v1/book-campaign-links/{args.legacy_fixture_slug}", good)[1]
        if legacy.get("record_type") != "legacy" or hashlib.sha256(legacy.get("target_url", "").encode()).hexdigest() != args.legacy_fixture_target_sha256:
            raise RuntimeError("legacy fixture mismatch")
    return {"mode": "verify-only" if args.verify_only else "execution", "health": health, "pilot_counter_equal": True, "canary_campaign": args.canary_campaign, "canary_slug": canary_slug, "canary_clicks": canary_count, "redirect_location": redirect_location, "legacy": legacy}


def main() -> int:
    args = build_parser().parse_args()
    try:
        print(json.dumps(verify(args, HttpTransport()), ensure_ascii=False, sort_keys=True)); return 0
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True)); return 2


if __name__ == "__main__": raise SystemExit(main())
