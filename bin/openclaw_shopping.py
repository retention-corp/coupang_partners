#!/usr/bin/env python3
"""Thin CLI bridge for the hosted OpenClaw shopping backend."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List
from urllib import error, request

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_PATH = "/v1/assist"


class CliError(RuntimeError):
    """Raised when the CLI cannot complete the backend request."""


def default_timeout_seconds(parser: argparse.ArgumentParser) -> int:
    raw_timeout = os.getenv("OPENCLAW_SHOPPING_TIMEOUT_SECONDS")
    if not raw_timeout:
        return DEFAULT_TIMEOUT_SECONDS

    try:
        timeout = int(raw_timeout)
    except ValueError:
        parser.error("OPENCLAW_SHOPPING_TIMEOUT_SECONDS must be an integer")

    if timeout <= 0:
        parser.error("OPENCLAW_SHOPPING_TIMEOUT_SECONDS must be greater than zero")

    return timeout


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query the hosted OpenClaw shopping backend and print the JSON response.",
    )
    parser.add_argument("query", help="Natural-language shopping request")
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENCLAW_SHOPPING_BASE_URL"),
        help="Hosted backend base URL. Defaults to OPENCLAW_SHOPPING_BASE_URL.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=default_timeout_seconds(parser),
        help="Request timeout in seconds. Defaults to OPENCLAW_SHOPPING_TIMEOUT_SECONDS or 30.",
    )
    parser.add_argument("--budget", type=int, help="Optional budget or price ceiling")
    parser.add_argument("--category", help="Optional category hint")
    parser.add_argument("--brand", help="Optional brand preference")
    parser.add_argument(
        "--must-have",
        action="append",
        default=[],
        dest="must_have",
        help="Constraint that should be included in the recommendation request. Repeat as needed.",
    )
    parser.add_argument(
        "--avoid",
        action="append",
        default=[],
        help="Constraint that should be excluded from the recommendation request. Repeat as needed.",
    )
    parser.add_argument(
        "--evidence-snippet",
        action="append",
        default=[],
        dest="evidence_snippets",
        help="Optional evidence snippet to forward to the backend. Repeat as needed.",
    )
    args = parser.parse_args(argv)
    if not args.base_url:
        parser.error("--base-url is required when OPENCLAW_SHOPPING_BASE_URL is not set")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"query": args.query}
    if args.budget is not None:
        payload["budget"] = args.budget

    constraints: Dict[str, Any] = {}
    if args.category:
        constraints["category"] = args.category
    if args.brand:
        constraints["brand"] = args.brand
    if args.must_have:
        constraints["must_have"] = args.must_have
    if args.avoid:
        constraints["avoid"] = args.avoid
    if constraints:
        payload["constraints"] = constraints

    if args.evidence_snippets:
        payload["evidence_snippets"] = args.evidence_snippets

    return payload


def request_assist(base_url: str, payload: Dict[str, Any], timeout: int) -> Any:
    normalized_base_url = base_url.rstrip("/")
    url = f"{normalized_base_url}{DEFAULT_PATH}"
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        detail = raw or exc.reason
        raise CliError(f"Backend returned HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise CliError(f"Backend request failed: {exc.reason}") from exc

    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(f"Backend returned invalid JSON: {raw}") from exc


def main(argv: List[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        payload = build_payload(args)
        response_payload = request_assist(args.base_url, payload, args.timeout)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(response_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
