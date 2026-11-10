#!/usr/bin/env python3
"""Minimal Cloud Run URL shortener for buildersclub.co.kr operations.

Endpoints:
- GET /healthz
- GET /s/{slug} -> 302 redirect
- POST /internal/v1/short-links {"url": "https://..."} -> {shortUrl}

Auth:
- POST requires Authorization: Bearer token matching BUILDERS_SHORTENER_API_TOKENS.

Storage:
- Firestore REST API, collection BUILDERS_SHORTENER_COLLECTION (default: short_links).
"""
from __future__ import annotations

import json
import os
import secrets
import string
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, Optional, Sequence
from urllib import error, parse, request

ALPHABET = string.ascii_letters + string.digits


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _allowed_hosts() -> Sequence[str]:
    raw = os.getenv(
        "BUILDERS_SHORTENER_ALLOWED_TARGET_HOSTS",
        "dashboard.tosspayments.com,api.tosspayments.com,ai.retn.kr,buildersclub.co.kr,www.buildersclub.co.kr",
    )
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _is_allowed_target(url: str) -> bool:
    try:
        parsed = parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in _allowed_hosts())


def _bearer_token(header_value: Optional[str]) -> Optional[str]:
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _api_tokens() -> set[str]:
    raw = os.getenv("BUILDERS_SHORTENER_API_TOKENS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


class FirestoreShortener:
    def __init__(self) -> None:
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("BUILDERS_SHORTENER_GCP_PROJECT") or ""
        if not self.project_id:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required")
        self.database = os.getenv("BUILDERS_SHORTENER_FIRESTORE_DATABASE", "(default)")
        self.collection = os.getenv("BUILDERS_SHORTENER_COLLECTION", "short_links")
        self.public_base_url = os.getenv("BUILDERS_SHORTENER_PUBLIC_BASE_URL", "https://a.buildersclub.co.kr").rstrip("/")
        self.slug_length = int(os.getenv("BUILDERS_SHORTENER_SLUG_LENGTH", "7"))
        self.timeout_seconds = float(os.getenv("BUILDERS_SHORTENER_FIRESTORE_TIMEOUT", "5"))
        root = "https://firestore.googleapis.com"
        db = parse.quote(self.database, safe="()")
        self.documents_url = f"{root}/v1/projects/{self.project_id}/databases/{db}/documents"
        self.collection_url = f"{self.documents_url}/{parse.quote(self.collection, safe='')}"
        self._cached_token: Optional[str] = None
        self._cached_token_expiry = 0.0

    def shorten(self, target_url: str) -> str:
        existing = self._find_slug_by_target_url(target_url)
        if existing:
            return f"{self.public_base_url}/s/{existing}"
        while True:
            slug = "".join(secrets.choice(ALPHABET) for _ in range(self.slug_length))
            if self._create_slug(slug, target_url):
                return f"{self.public_base_url}/s/{slug}"

    def resolve(self, slug: str) -> Optional[str]:
        document = self._get_document(slug)
        if not document:
            return None
        return _firestore_string(document, "target_url")

    def record_click(self, slug: str) -> None:
        self._request_json(
            "POST",
            f"{self.documents_url}:commit",
            body={
                "writes": [
                    {
                        "transform": {
                            "document": self._document_name(slug),
                            "fieldTransforms": [
                                {"fieldPath": "click_count", "increment": {"integerValue": "1"}},
                                {"fieldPath": "last_clicked_at", "setToServerValue": "REQUEST_TIME"},
                            ],
                        }
                    }
                ]
            },
            allow_statuses=(404,),
        )

    def _find_slug_by_target_url(self, target_url: str) -> Optional[str]:
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runQuery",
            body={
                "structuredQuery": {
                    "from": [{"collectionId": self.collection}],
                    "where": {
                        "fieldFilter": {
                            "field": {"fieldPath": "target_url"},
                            "op": "EQUAL",
                            "value": {"stringValue": target_url},
                        }
                    },
                    "limit": 1,
                }
            },
        )
        for item in _as_sequence(response):
            document = (item or {}).get("document")
            if document and document.get("name"):
                return str(document["name"]).rsplit("/", 1)[-1]
        return None

    def _create_slug(self, slug: str, target_url: str) -> bool:
        try:
            self._request_json(
                "PATCH",
                f"{self._document_url(slug)}?currentDocument.exists=false",
                body={
                    "fields": {
                        "target_url": {"stringValue": target_url},
                        "click_count": {"integerValue": "0"},
                        "created_at": {"timestampValue": _utc_now_iso()},
                    }
                },
            )
            return True
        except RuntimeError as exc:
            if "409" in str(exc):
                return False
            raise

    def _get_document(self, slug: str) -> Optional[Dict[str, Any]]:
        response = self._request_json("GET", self._document_url(slug), allow_statuses=(404,))
        return response if isinstance(response, dict) else None

    def _document_url(self, slug: str) -> str:
        return f"{self.collection_url}/{parse.quote(slug, safe='')}"

    def _document_name(self, slug: str) -> str:
        return f"projects/{self.project_id}/databases/{self.database}/documents/{self.collection}/{slug}"

    def _request_json(self, method: str, url: str, *, body: Optional[Dict[str, Any]] = None, allow_statuses: Sequence[int] = ()) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {self._get_access_token()}"},
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code in allow_statuses:
                return None
            raise RuntimeError(f"Firestore request failed with {exc.code}: {detail}") from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _get_access_token(self) -> str:
        env_token = os.getenv("BUILDERS_SHORTENER_GCP_ACCESS_TOKEN")
        if env_token:
            return env_token
        if self._cached_token and time.time() < self._cached_token_expiry:
            return self._cached_token
        req = request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self._cached_token = payload["access_token"]
        self._cached_token_expiry = time.time() + int(payload.get("expires_in", 300)) - 30
        return self._cached_token


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return ()
    return (value,)


def _firestore_string(document: Dict[str, Any], field_name: str) -> Optional[str]:
    field = (((document or {}).get("fields") or {}).get(field_name) or {})
    value = field.get("stringValue")
    return str(value) if value is not None else None


class Handler(BaseHTTPRequestHandler):
    shortener: FirestoreShortener
    max_body_bytes = int(os.getenv("BUILDERS_SHORTENER_MAX_BODY_BYTES", "8192"))

    def do_GET(self) -> None:  # noqa: N802
        path = parse.urlsplit(self.path).path
        if path in ("/health", "/healthz"):
            _json_response(self, HTTPStatus.OK, {"ok": True, "service": "builders-shortener"})
            return
        if path.startswith("/s/"):
            slug = path.split("/s/", 1)[1].strip().split("/", 1)[0]
            if self._redirect_slug(slug):
                return
            return
        if path.count("/") == 1 and len(path) > 1:
            slug = path.lstrip("/").strip()
            if self._redirect_slug(slug):
                return
            return
        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = parse.urlsplit(self.path).path
        if path != "/internal/v1/short-links":
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        tokens = _api_tokens()
        if not tokens:
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "API auth is not configured"})
            return
        if _bearer_token(self.headers.get("Authorization")) not in tokens:
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "Missing or invalid bearer token"})
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        target_url = str(payload.get("url") or payload.get("targetUrl") or "").strip()
        if not target_url:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "'url' is required"})
            return
        if len(target_url) > 4096:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "'url' must be 4096 characters or fewer"})
            return
        if not _is_allowed_target(target_url):
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "URL host is not allowed"})
            return
        try:
            short_url = self.shortener.shorten(target_url)
        except Exception as exc:
            _json_response(self, HTTPStatus.BAD_GATEWAY, {"error": "Shortener failed", "detail": str(exc)[:200]})
            return
        _json_response(self, HTTPStatus.OK, {"ok": True, "url": target_url, "shortUrl": short_url})

    def _redirect_slug(self, slug: str) -> bool:
        if not slug:
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Short link not found"})
            return True
        target = self.shortener.resolve(slug)
        if not target or not _is_allowed_target(target):
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Short link not found"})
            return True
        try:
            self.shortener.record_click(slug)
        except Exception:
            pass
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return True

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header") from exc
        if length < 0 or length > self.max_body_bytes:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def run() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    shortener = FirestoreShortener()
    handler = type("BuildersShortenerHandler", (Handler,), {"shortener": shortener})
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
