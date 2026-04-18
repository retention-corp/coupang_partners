import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib import error, parse, request


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalyticsStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self.initialize()

    def initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS queries (
                    id TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    budget INTEGER,
                    category TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    id TEXT PRIMARY KEY,
                    query_id TEXT NOT NULL,
                    rank_index INTEGER NOT NULL,
                    product_id TEXT,
                    title TEXT NOT NULL,
                    score REAL NOT NULL,
                    deeplink TEXT,
                    rationale TEXT NOT NULL,
                    risks_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (query_id) REFERENCES queries (id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    query_id TEXT,
                    recommendation_id TEXT,
                    event_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence_snippets (
                    id TEXT PRIMARY KEY,
                    query_id TEXT NOT NULL,
                    snippet_text TEXT NOT NULL,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (query_id) REFERENCES queries (id)
                );
                """
            )
            # Idempotent migration: `client_id` was added after the initial schema
            # shipped. Existing rows get NULL; new queries can be tagged per caller.
            existing = {row[1] for row in connection.execute("PRAGMA table_info(queries)").fetchall()}
            if "client_id" not in existing:
                connection.execute("ALTER TABLE queries ADD COLUMN client_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_queries_client_id_created_at "
                "ON queries (client_id, created_at DESC)"
            )

    def record_assist(
        self,
        *,
        query_text: str,
        budget: Optional[int],
        category: Optional[str],
        evidence_snippets: Iterable[Dict[str, Any]],
        recommendations: List[Dict[str, Any]],
        client_id: Optional[str] = None,
    ) -> str:
        query_id = str(uuid.uuid4())
        created_at = _utc_now()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO queries (id, query_text, budget, category, created_at, client_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (query_id, query_text, budget, category, created_at, client_id),
            )
            for snippet in evidence_snippets:
                connection.execute(
                    "INSERT INTO evidence_snippets (id, query_id, snippet_text, source, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        query_id,
                        snippet.get("text", ""),
                        snippet.get("source"),
                        created_at,
                    ),
                )
            for index, recommendation in enumerate(recommendations, start=1):
                connection.execute(
                    """
                    INSERT INTO recommendations (
                        id, query_id, rank_index, product_id, title, score,
                        deeplink, rationale, risks_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        query_id,
                        index,
                        recommendation.get("product_id"),
                        recommendation.get("title", "Untitled product"),
                        float(recommendation.get("score", 0.0)),
                        recommendation.get("deeplink"),
                        recommendation.get("rationale", ""),
                        json.dumps(recommendation.get("risks", []), ensure_ascii=False),
                        created_at,
                    ),
                )
        return query_id

    def record_event(
        self,
        *,
        event_type: str,
        query_id: Optional[str] = None,
        recommendation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO events (id, query_id, recommendation_id, event_type, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    query_id,
                    recommendation_id,
                    event_type,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    _utc_now(),
                ),
            )
        return event_id

    def get_summary(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as connection:
            counts = {
                "total_queries": connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0],
                "total_recommendations": connection.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0],
                "total_events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "total_evidence_snippets": connection.execute("SELECT COUNT(*) FROM evidence_snippets").fetchone()[0],
            }
            latest_query = connection.execute(
                "SELECT query_text, created_at FROM queries ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            event_types = connection.execute(
                "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY COUNT(*) DESC, event_type ASC"
            ).fetchall()
            categories = connection.execute(
                """
                SELECT category, COUNT(*)
                FROM queries
                WHERE category IS NOT NULL AND category != ''
                GROUP BY category
                ORDER BY COUNT(*) DESC, category ASC
                """
            ).fetchall()

        summary: Dict[str, Any] = {
            **counts,
            "latest_query": None,
            "event_breakdown": [
                {"event_type": row[0], "count": row[1]}
                for row in event_types
            ],
            "category_breakdown": [
                {"category": row[0], "count": row[1]}
                for row in categories
            ],
        }
        if latest_query:
            summary["latest_query"] = {
                "query_text": latest_query[0],
                "created_at": latest_query[1],
            }
        return summary

    def get_recent_queries_for_client(
        self,
        client_id: str,
        *,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return the most recent `limit` queries for a client, newest first.

        Used by persona inference to look up what the same caller has been asking about
        across past requests. Returns an empty list when client_id is missing/empty so
        callers don't need to special-case cold starts.
        """

        if not client_id:
            return []
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT query_text, category, budget, created_at "
                "FROM queries WHERE client_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (client_id, int(limit)),
            ).fetchall()
        return [
            {
                "query_text": row[0],
                "category": row[1],
                "budget": row[2],
                "created_at": row[3],
            }
            for row in rows
        ]


class FirestoreAnalyticsStore:
    def __init__(
        self,
        *,
        project_id: str,
        collection_prefix: str = "shopping",
        database: str = "(default)",
        timeout_seconds: float = 5.0,
        access_token: Optional[str] = None,
        emulator_host: Optional[str] = None,
    ) -> None:
        if not project_id:
            raise ValueError("project_id is required for FirestoreAnalyticsStore")
        self.project_id = project_id
        self.collection_prefix = collection_prefix.strip() or "shopping"
        self.database = database
        self.timeout_seconds = timeout_seconds
        self._access_token = access_token
        self.emulator_host = emulator_host or os.getenv("FIRESTORE_EMULATOR_HOST")
        self._cached_token: Optional[str] = None
        self._cached_token_expiry = 0.0
        root = self._build_api_root()
        self.documents_url = (
            f"{root}/v1/projects/{self.project_id}/databases/{parse.quote(self.database, safe='()')}/documents"
        )
        self.collections = {
            "queries": f"{self.collection_prefix}_queries",
            "recommendations": f"{self.collection_prefix}_recommendations",
            "events": f"{self.collection_prefix}_events",
            "evidence_snippets": f"{self.collection_prefix}_evidence_snippets",
        }

    def record_assist(
        self,
        *,
        query_text: str,
        budget: Optional[int],
        category: Optional[str],
        evidence_snippets: Iterable[Dict[str, Any]],
        recommendations: List[Dict[str, Any]],
        client_id: Optional[str] = None,
    ) -> str:
        query_id = str(uuid.uuid4())
        created_at = _utc_now()
        self._create_document(
            self.collections["queries"],
            query_id,
            {
                "query_text": {"stringValue": query_text},
                "created_at": {"timestampValue": created_at},
                **_optional_int_field("budget", budget),
                **_optional_string_field("category", category),
                **_optional_string_field("client_id", client_id),
            },
        )
        for snippet in evidence_snippets:
            self._create_document(
                self.collections["evidence_snippets"],
                str(uuid.uuid4()),
                {
                    "query_id": {"stringValue": query_id},
                    "snippet_text": {"stringValue": str(snippet.get("text", ""))},
                    "created_at": {"timestampValue": created_at},
                    **_optional_string_field("source", snippet.get("source")),
                },
            )
        for index, recommendation in enumerate(recommendations, start=1):
            self._create_document(
                self.collections["recommendations"],
                str(uuid.uuid4()),
                {
                    "query_id": {"stringValue": query_id},
                    "rank_index": {"integerValue": str(index)},
                    "title": {"stringValue": str(recommendation.get("title", "Untitled product"))},
                    "score": {"doubleValue": float(recommendation.get("score", 0.0))},
                    "created_at": {"timestampValue": created_at},
                    "risks_json": {"stringValue": json.dumps(recommendation.get("risks", []), ensure_ascii=False)},
                    **_optional_string_field("product_id", recommendation.get("product_id")),
                    **_optional_string_field("deeplink", recommendation.get("deeplink")),
                    **_optional_string_field("rationale", recommendation.get("rationale")),
                },
            )
        return query_id

    def record_event(
        self,
        *,
        event_type: str,
        query_id: Optional[str] = None,
        recommendation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        self._create_document(
            self.collections["events"],
            event_id,
            {
                "event_type": {"stringValue": event_type},
                "metadata_json": {"stringValue": json.dumps(metadata or {}, ensure_ascii=False)},
                "created_at": {"timestampValue": _utc_now()},
                **_optional_string_field("query_id", query_id),
                **_optional_string_field("recommendation_id", recommendation_id),
            },
        )
        return event_id

    def get_summary(self) -> Dict[str, Any]:
        total_queries = self._count_collection(self.collections["queries"])
        total_recommendations = self._count_collection(self.collections["recommendations"])
        total_events = self._count_collection(self.collections["events"])
        total_evidence_snippets = self._count_collection(self.collections["evidence_snippets"])
        latest_query = self._latest_query()
        event_breakdown = self._event_breakdown()
        category_breakdown = self._category_breakdown()
        return {
            "total_queries": total_queries,
            "total_recommendations": total_recommendations,
            "total_events": total_events,
            "total_evidence_snippets": total_evidence_snippets,
            "latest_query": latest_query,
            "event_breakdown": event_breakdown,
            "category_breakdown": category_breakdown,
        }

    def get_recent_queries_for_client(
        self,
        client_id: str,
        *,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return the most recent `limit` queries for a client, newest first."""

        if not client_id:
            return []
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runQuery",
            body={
                "structuredQuery": {
                    "from": [{"collectionId": self.collections["queries"]}],
                    "where": {
                        "fieldFilter": {
                            "field": {"fieldPath": "client_id"},
                            "op": "EQUAL",
                            "value": {"stringValue": client_id},
                        }
                    },
                    "orderBy": [{"field": {"fieldPath": "created_at"}, "direction": "DESCENDING"}],
                    "limit": int(limit),
                }
            },
        )
        results: List[Dict[str, Any]] = []
        for item in _as_sequence(response):
            document = (item or {}).get("document")
            if not document:
                continue
            results.append(
                {
                    "query_text": _firestore_string(document, "query_text") or "",
                    "category": _firestore_string(document, "category"),
                    "budget": _firestore_integer(document, "budget"),
                    "created_at": _firestore_timestamp(document, "created_at") or "",
                }
            )
        return results

    def _create_document(self, collection: str, document_id: str, fields: Dict[str, Dict[str, Any]]) -> None:
        self._request_json(
            "PATCH",
            f"{self._document_url(collection, document_id)}?currentDocument.exists=false",
            body={"fields": fields},
        )

    def _count_collection(self, collection: str) -> int:
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runAggregationQuery",
            body={
                "structuredAggregationQuery": {
                    "aggregations": [{"alias": "total", "count": {}}],
                    "structuredQuery": {
                        "from": [{"collectionId": collection}],
                    },
                }
            },
        )
        total = 0
        for item in _as_sequence(response):
            aggregate_fields = (((item or {}).get("result") or {}).get("aggregateFields") or {})
            integer_value = (((aggregate_fields.get("total") or {}).get("integerValue")) or "0")
            total = int(integer_value)
        return total

    def _latest_query(self) -> Optional[Dict[str, str]]:
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runQuery",
            body={
                "structuredQuery": {
                    "from": [{"collectionId": self.collections["queries"]}],
                    "orderBy": [{"field": {"fieldPath": "created_at"}, "direction": "DESCENDING"}],
                    "limit": 1,
                }
            },
        )
        for item in _as_sequence(response):
            document = (item or {}).get("document")
            if not document:
                continue
            return {
                "query_text": _firestore_string(document, "query_text") or "",
                "created_at": _firestore_timestamp(document, "created_at") or "",
            }
        return None

    def _event_breakdown(self) -> List[Dict[str, Any]]:
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runQuery",
            body={
                "structuredQuery": {
                    "from": [{"collectionId": self.collections["events"]}],
                    "select": {
                        "fields": [{"fieldPath": "event_type"}],
                    },
                }
            },
        )
        counts: Dict[str, int] = {}
        for item in _as_sequence(response):
            document = (item or {}).get("document")
            if not document:
                continue
            event_type = _firestore_string(document, "event_type")
            if not event_type:
                continue
            counts[event_type] = counts.get(event_type, 0) + 1
        return [
            {"event_type": event_type, "count": count}
            for event_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _category_breakdown(self) -> List[Dict[str, Any]]:
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runQuery",
            body={
                "structuredQuery": {
                    "from": [{"collectionId": self.collections["queries"]}],
                    "select": {
                        "fields": [{"fieldPath": "category"}],
                    },
                }
            },
        )
        counts: Dict[str, int] = {}
        for item in _as_sequence(response):
            document = (item or {}).get("document")
            if not document:
                continue
            category = _firestore_string(document, "category")
            if not category:
                continue
            counts[category] = counts.get(category, 0) + 1
        return [
            {"category": category, "count": count}
            for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _document_url(self, collection: str, document_id: str) -> str:
        return (
            f"{self.documents_url}/{parse.quote(collection, safe='')}/{parse.quote(document_id, safe='')}"
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
        headers = {"Content-Type": "application/json; charset=utf-8"}
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
            raise RuntimeError(f"Firestore analytics request failed with {exc.code}: {detail}") from exc
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


def build_analytics_store_from_env(*, db_path: str) -> Any:
    provider = (os.getenv("OPENCLAW_SHOPPING_ANALYTICS_PROVIDER") or "").strip().lower()
    if provider in ("", "sqlite", "builtin", "local"):
        return AnalyticsStore(db_path)
    if provider in ("firestore", "gcp"):
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("OPENCLAW_GCP_PROJECT") or ""
        return FirestoreAnalyticsStore(
            project_id=project_id,
            collection_prefix=os.getenv("OPENCLAW_ANALYTICS_COLLECTION_PREFIX", "shopping"),
            database=os.getenv("OPENCLAW_FIRESTORE_DATABASE", "(default)"),
            access_token=os.getenv("OPENCLAW_GCP_ACCESS_TOKEN"),
            emulator_host=os.getenv("FIRESTORE_EMULATOR_HOST"),
        )
    raise ValueError(f"Unsupported analytics provider: {provider}")


def _optional_string_field(field_name: str, value: Optional[Any]) -> Dict[str, Dict[str, Any]]:
    if value in (None, ""):
        return {}
    return {field_name: {"stringValue": str(value)}}


def _optional_int_field(field_name: str, value: Optional[int]) -> Dict[str, Dict[str, Any]]:
    if value is None:
        return {}
    return {field_name: {"integerValue": str(int(value))}}


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


def _firestore_timestamp(document: Dict[str, Any], field_name: str) -> Optional[str]:
    field = _firestore_fields(document).get(field_name) or {}
    value = field.get("timestampValue")
    return str(value) if value is not None else None


def _firestore_integer(document: Dict[str, Any], field_name: str) -> Optional[int]:
    field = _firestore_fields(document).get(field_name) or {}
    value = field.get("integerValue")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
