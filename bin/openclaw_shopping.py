#!/usr/bin/env python3
import argparse
import json
import sys
from urllib import error, request


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Query the hosted OpenClaw shopping backend")
    parser.add_argument("query", help="shopping request in natural language")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="backend base URL")
    parser.add_argument("--budget", type=int, default=None, help="optional budget in KRW")
    parser.add_argument("--category", default=None, help="optional category hint")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="optional user-visible evidence snippet text (repeatable)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    payload = {
        "query": args.query,
        "budget": args.budget,
        "category": args.category,
        "evidence_snippets": [{"text": item, "source": "cli"} for item in args.evidence],
    }
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        f"{args.base_url.rstrip('/')}/v1/assist",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=30) as response:
            print(json.dumps(json.loads(response.read().decode("utf-8")), ensure_ascii=False))
        return 0
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        message = raw or json.dumps({"error": exc.reason})
        print(message, file=sys.stderr)
        return 1
    except error.URLError as exc:
        print(json.dumps({"error": str(exc.reason)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
