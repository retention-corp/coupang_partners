#!/usr/bin/env python3
import argparse
import json
import os
import sys
from urllib import request
from urllib.error import HTTPError, URLError


def _post_json(url: str, payload):
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") or exc.reason
        raise RuntimeError(f"backend returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"backend request failed: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    recommend = subparsers.add_parser("recommend")
    recommend.add_argument("--backend", default=os.getenv("OPENCLAW_SHOPPING_BASE_URL"))
    recommend.add_argument("--query", required=True)
    recommend.add_argument("--price-max", type=int, default=None)
    recommend.add_argument("--limit", type=int, default=5)
    recommend.add_argument("--include-term", action="append", default=[])
    recommend.add_argument("--exclude-term", action="append", default=[])

    deeplinks = subparsers.add_parser("deeplinks")
    deeplinks.add_argument("--backend", default=os.getenv("OPENCLAW_SHOPPING_BASE_URL"))
    deeplinks.add_argument("--url", action="append", required=True)

    args = parser.parse_args()
    if not args.backend:
        parser.error("--backend is required when OPENCLAW_SHOPPING_BASE_URL is not set")
    try:
        if args.command == "recommend":
            payload = {
                "query": args.query,
                "budget": args.price_max,
                "limit": args.limit,
                "constraints": {
                    "must_have": args.include_term,
                    "avoid": args.exclude_term,
                },
            }
            result = _post_json(args.backend.rstrip("/") + "/v1/assist", payload)
        else:
            result = _post_json(args.backend.rstrip("/") + "/v1/deeplinks", {"urls": args.url})
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": {"message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
