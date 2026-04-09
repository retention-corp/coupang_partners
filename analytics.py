import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalyticsStore:
    def __init__(self, db_path: str = "analytics.sqlite3") -> None:
        self.db_path = str(Path(db_path))
        self.ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    budget_max REAL,
                    category TEXT,
                    constraints_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    query_id INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    deeplink TEXT,
                    rank INTEGER NOT NULL,
                    score REAL NOT NULL,
                    rationale TEXT NOT NULL,
                    risks_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(query_id) REFERENCES queries(id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    query_id INTEGER,
                    recommendation_id INTEGER,
                    event_type TEXT NOT NULL,
                    session_id TEXT,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(query_id) REFERENCES queries(id),
                    FOREIGN KEY(recommendation_id) REFERENCES recommendations(id)
                );

                CREATE TABLE IF NOT EXISTS evidence_snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    query_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    score REAL NOT NULL,
                    risks_json TEXT NOT NULL,
                    FOREIGN KEY(query_id) REFERENCES queries(id)
                );
                """
            )

    def record_query(
        self,
        query_text: str,
        *,
        budget_max: Optional[float] = None,
        category: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> int:
        constraints = constraints or {}
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO queries (created_at, query_text, budget_max, category, constraints_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    utcnow_iso(),
                    query_text,
                    budget_max,
                    category,
                    json.dumps(constraints, ensure_ascii=False, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def record_evidence(self, query_id: int, evidence_snippets: Iterable[Dict[str, Any]]) -> None:
        rows = []
        for snippet in evidence_snippets:
            rows.append(
                (
                    utcnow_iso(),
                    query_id,
                    snippet.get("source", "user_supplied"),
                    snippet.get("snippet", ""),
                    float(snippet.get("score", 0.0)),
                    json.dumps(snippet.get("risks", []), ensure_ascii=False),
                )
            )
        if not rows:
            return

        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO evidence_snippets (created_at, query_id, source, snippet, score, risks_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def record_recommendations(self, query_id: int, recommendations: Iterable[Dict[str, Any]]) -> List[int]:
        ids: List[int] = []
        with self.connect() as connection:
            for rank, recommendation in enumerate(recommendations, start=1):
                cursor = connection.execute(
                    """
                    INSERT INTO recommendations (
                        created_at,
                        query_id,
                        product_id,
                        product_name,
                        deeplink,
                        rank,
                        score,
                        rationale,
                        risks_json,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        utcnow_iso(),
                        query_id,
                        recommendation.get("product_id", ""),
                        recommendation.get("product_name", ""),
                        recommendation.get("deeplink"),
                        rank,
                        float(recommendation.get("score", 0.0)),
                        recommendation.get("rationale", ""),
                        json.dumps(recommendation.get("risks", []), ensure_ascii=False),
                        json.dumps(recommendation, ensure_ascii=False, sort_keys=True),
                    ),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    def record_event(
        self,
        event_type: str,
        *,
        query_id: Optional[int] = None,
        recommendation_id: Optional[int] = None,
        session_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (
                    created_at,
                    query_id,
                    recommendation_id,
                    event_type,
                    session_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    utcnow_iso(),
                    query_id,
                    recommendation_id,
                    event_type,
                    session_id,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def log_assist(
        self,
        *,
        query_text: str,
        budget_max: Optional[float] = None,
        category: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
        evidence_snippets: Optional[Iterable[Dict[str, Any]]] = None,
        recommendations: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        query_id = self.record_query(
            query_text,
            budget_max=budget_max,
            category=category,
            constraints=constraints,
        )
        normalized_snippets = list(evidence_snippets or [])
        self.record_evidence(query_id, normalized_snippets)
        normalized_recommendations = list(recommendations or [])
        recommendation_ids = self.record_recommendations(query_id, normalized_recommendations)
        return {
            "query_id": query_id,
            "recommendation_ids": recommendation_ids,
        }

    def product_feedback(self) -> Dict[str, Dict[str, int]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    r.product_id AS product_id,
                    SUM(CASE WHEN e.event_type = 'result_viewed' THEN 1 ELSE 0 END) AS views,
                    SUM(CASE WHEN e.event_type = 'deeplink_clicked' THEN 1 ELSE 0 END) AS clicks,
                    SUM(CASE WHEN e.event_type = 'purchase_reported' THEN 1 ELSE 0 END) AS purchases
                FROM recommendations r
                LEFT JOIN events e ON e.recommendation_id = r.id
                GROUP BY r.product_id
                """
            ).fetchall()

        feedback: Dict[str, Dict[str, int]] = {}
        for row in rows:
            feedback[row["product_id"]] = {
                "views": int(row["views"] or 0),
                "clicks": int(row["clicks"] or 0),
                "purchases": int(row["purchases"] or 0),
            }
        return feedback

    def summary(self) -> Dict[str, Any]:
        with self.connect() as connection:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM queries) AS queries,
                    (SELECT COUNT(*) FROM recommendations) AS recommendations,
                    (SELECT COUNT(*) FROM events) AS events,
                    (SELECT COUNT(*) FROM evidence_snippets) AS evidence_snippets
                """
            ).fetchone()
            event_rows = connection.execute(
                """
                SELECT event_type, COUNT(*) AS total
                FROM events
                GROUP BY event_type
                ORDER BY total DESC, event_type ASC
                """
            ).fetchall()
            query_rows = connection.execute(
                """
                SELECT query_text, COUNT(*) AS total
                FROM queries
                GROUP BY query_text
                ORDER BY total DESC, query_text ASC
                LIMIT 5
                """
            ).fetchall()
            product_rows = connection.execute(
                """
                SELECT product_id, product_name, COUNT(*) AS total_recommendations
                FROM recommendations
                GROUP BY product_id, product_name
                ORDER BY total_recommendations DESC, product_name ASC
                LIMIT 5
                """
            ).fetchall()

        return {
            "counts": {
                "queries": int(counts["queries"]),
                "recommendations": int(counts["recommendations"]),
                "events": int(counts["events"]),
                "evidence_snippets": int(counts["evidence_snippets"]),
            },
            "event_types": {
                row["event_type"]: int(row["total"])
                for row in event_rows
            },
            "top_queries": [
                {"query": row["query_text"], "count": int(row["total"])}
                for row in query_rows
            ],
            "top_products": [
                {
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "recommendation_count": int(row["total_recommendations"]),
                }
                for row in product_rows
            ],
        }
