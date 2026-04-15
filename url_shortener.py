import json
import os
import secrets
import sqlite3
import string
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence
from urllib import error, parse, request


ALPHABET = string.ascii_letters + string.digits


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UrlShortener:
    def shorten(self, url: str) -> Optional[str]:
        raise NotImplementedError

    def resolve(self, slug: str) -> Optional[str]:
        return None

    def record_click(self, slug: str) -> None:
        return None

    def get_summary(self) -> Dict[str, int]:
        return {"total_short_links": 0, "total_short_link_clicks": 0}


class BuiltinShortener(UrlShortener):
    def __init__(self, db_path: str, public_base_url: str, slug_length: int = 7) -> None:
        self.db_path = str(Path(db_path))
        self.public_base_url = public_base_url.rstrip("/")
        self.slug_length = slug_length
        self._initialize()

    def shorten(self, url: str) -> Optional[str]:
        if not url:
            return None
        slug = self._ensure_slug(url)
        return f"{self.public_base_url}/s/{slug}"

    def resolve(self, slug: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT target_url FROM short_links WHERE slug = ?",
                (slug,),
            ).fetchone()
        return row[0] if row else None

    def record_click(self, slug: str) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE short_links
                SET click_count = click_count + 1,
                    last_clicked_at = CURRENT_TIMESTAMP
                WHERE slug = ?
                """,
                (slug,),
            )

    def get_summary(self) -> Dict[str, int]:
        with sqlite3.connect(self.db_path) as connection:
            total_short_links = connection.execute("SELECT COUNT(*) FROM short_links").fetchone()[0]
            total_short_link_clicks = connection.execute("SELECT COALESCE(SUM(click_count), 0) FROM short_links").fetchone()[0]
        return {
            "total_short_links": int(total_short_links),
            "total_short_link_clicks": int(total_short_link_clicks),
        }

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS short_links (
                    slug TEXT PRIMARY KEY,
                    target_url TEXT NOT NULL UNIQUE,
                    click_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_clicked_at TEXT
                )
                """
            )

    def _ensure_slug(self, url: str) -> str:
        with sqlite3.connect(self.db_path) as connection:
            existing = connection.execute(
                "SELECT slug FROM short_links WHERE target_url = ?",
                (url,),
            ).fetchone()
            if existing:
                return existing[0]

            while True:
                slug = "".join(secrets.choice(ALPHABET) for _ in range(self.slug_length))
                try:
                    connection.execute(
                        "INSERT INTO short_links (slug, target_url) VALUES (?, ?)",
                        (slug, url),
                    )
                    return slug
                except sqlite3.IntegrityError:
                    continue


class FirestoreShortener(UrlShortener):
    def __init__(
        self,
        *,
        project_id: str,
        public_base_url: str,
        collection: str = "short_links",
        database: str = "(default)",
        slug_length: int = 7,
        timeout_seconds: float = 5.0,
        access_token: Optional[str] = None,
        emulator_host: Optional[str] = None,
    ) -> None:
        if not project_id:
            raise ValueError("project_id is required for FirestoreShortener")
        self.project_id = project_id
        self.public_base_url = public_base_url.rstrip("/")
        self.collection = collection
        self.database = database
        self.slug_length = slug_length
        self.timeout_seconds = timeout_seconds
        self._access_token = access_token
        self.emulator_host = emulator_host or os.getenv("FIRESTORE_EMULATOR_HOST")
        root = self._build_api_root()
        self.documents_url = (
            f"{root}/v1/projects/{self.project_id}/databases/{parse.quote(self.database, safe='()')}/documents"
        )
        self.collection_url = f"{self.documents_url}/{parse.quote(self.collection, safe='')}"
        self._cached_token: Optional[str] = None
        self._cached_token_expiry = 0.0

    def shorten(self, url: str) -> Optional[str]:
        if not url:
            return None
        slug = self._find_slug_by_target_url(url)
        if slug:
            return f"{self.public_base_url}/s/{slug}"

        while True:
            slug = "".join(secrets.choice(ALPHABET) for _ in range(self.slug_length))
            if self._create_slug(slug=slug, target_url=url):
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

    def get_summary(self) -> Dict[str, int]:
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runAggregationQuery",
            body={
                "structuredAggregationQuery": {
                    "aggregations": [{"alias": "total", "count": {}}],
                    "structuredQuery": {
                        "from": [{"collectionId": self.collection}],
                    },
                }
            },
        )
        total = 0
        for item in _as_sequence(response):
            aggregate_fields = (((item or {}).get("result") or {}).get("aggregateFields") or {})
            integer_value = (((aggregate_fields.get("total") or {}).get("integerValue")) or "0")
            total = int(integer_value)
        click_response = self._request_json(
            "POST",
            f"{self.documents_url}:runQuery",
            body={
                "structuredQuery": {
                    "from": [{"collectionId": self.collection}],
                    "select": {"fields": [{"fieldPath": "click_count"}]},
                }
            },
        )
        total_clicks = 0
        for item in _as_sequence(click_response):
            document = (item or {}).get("document")
            if not document:
                continue
            total_clicks += _firestore_int(document, "click_count")
        return {"total_short_links": total, "total_short_link_clicks": total_clicks}

    def _find_slug_by_target_url(self, url: str) -> Optional[str]:
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
                            "value": {"stringValue": url},
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

    def _create_slug(self, *, slug: str, target_url: str) -> bool:
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
        response = self._request_json(
            "GET",
            self._document_url(slug),
            allow_statuses=(404,),
        )
        if not response or not isinstance(response, dict):
            return None
        return response

    def _document_url(self, slug: str) -> str:
        return f"{self.collection_url}/{parse.quote(slug, safe='')}"

    def _document_name(self, slug: str) -> str:
        return (
            f"projects/{self.project_id}/databases/{self.database}/documents/"
            f"{self.collection}/{slug}"
        )

    def _build_api_root(self) -> str:
        if self.emulator_host:
            host = self.emulator_host
            if "://" not in host:
                host = f"http://{host}"
            return host.rstrip("/")
        return "https://firestore.googleapis.com"

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        allow_statuses: Sequence[int] = (),
    ) -> Any:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
        }
        if not self.emulator_host:
            headers["Authorization"] = f"Bearer {self._get_access_token()}"
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_obj = request.Request(url, data=payload, headers=headers, method=method)
        try:
            with request.urlopen(request_obj, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except error.HTTPError as exc:
            if exc.code in allow_statuses:
                exc.read()
                return None
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"Firestore shortener request failed with {exc.code}: {detail}") from exc

        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if time.time() < self._cached_token_expiry and self._cached_token:
            return self._cached_token

        env_token = os.getenv("OPENCLAW_GCP_ACCESS_TOKEN")
        if env_token:
            self._cached_token = env_token
            self._cached_token_expiry = time.time() + 300
            return env_token

        metadata_request = request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            method="GET",
        )
        with request.urlopen(metadata_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self._cached_token = payload["access_token"]
        self._cached_token_expiry = time.time() + int(payload.get("expires_in", 300)) - 30
        return self._cached_token


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return ()
    return (value,)


def _firestore_fields(document: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return ((document or {}).get("fields") or {})


def _firestore_string(document: Dict[str, Any], field_name: str) -> Optional[str]:
    field = _firestore_fields(document).get(field_name) or {}
    value = field.get("stringValue")
    return str(value) if value is not None else None


def _firestore_int(document: Dict[str, Any], field_name: str) -> int:
    field = _firestore_fields(document).get(field_name) or {}
    value = field.get("integerValue")
    if value is None:
        return 0
    return int(value)
