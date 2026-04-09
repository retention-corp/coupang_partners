import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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

    def record_assist(
        self,
        *,
        query_text: str,
        budget: Optional[int],
        category: Optional[str],
        evidence_snippets: Iterable[Dict[str, Any]],
        recommendations: List[Dict[str, Any]],
    ) -> str:
        query_id = str(uuid.uuid4())
        created_at = _utc_now()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO queries (id, query_text, budget, category, created_at) VALUES (?, ?, ?, ?, ?)",
                (query_id, query_text, budget, category, created_at),
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

        summary: Dict[str, Any] = {
            **counts,
            "latest_query": None,
            "event_breakdown": [
                {"event_type": row[0], "count": row[1]}
                for row in event_types
            ],
        }
        if latest_query:
            summary["latest_query"] = {
                "query_text": latest_query[0],
                "created_at": latest_query[1],
            }
        return summary
