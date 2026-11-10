import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence
from urllib import error, parse, request


ALPHABET = string.ascii_letters + string.digits
ALIAS_ALPHABET = string.ascii_lowercase + string.digits + "-"
RESERVED_ALIASES = {"admin", "api", "docs", "health", "login", "s"}
SAFETY_STATUS_SAFE = "safe"
SAFETY_STATUS_UNSAFE = "unsafe"
SAFETY_STATUS_REVIEW = "review"
SAFETY_STATUSES = {SAFETY_STATUS_SAFE, SAFETY_STATUS_UNSAFE, SAFETY_STATUS_REVIEW}
DEFAULT_SAFETY_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_WEB_RISK_THREAT_TYPES = (
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_target_url(url: str) -> str:
    parsed = parse.urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    return parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def _target_host(url: str) -> str:
    try:
        return (parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _target_hash(url: str) -> str:
    return hashlib.sha256(_normalize_target_url(url).encode("utf-8")).hexdigest()


def _validate_alias(alias: str) -> str:
    normalized = (alias or "").strip().lower()
    if not normalized:
        raise ValueError("alias is required")
    if len(normalized) < 3 or len(normalized) > 64:
        raise ValueError("alias must be 3 to 64 characters")
    if normalized in RESERVED_ALIASES:
        raise ValueError("alias is reserved")
    if normalized.startswith("-") or normalized.endswith("-") or "--" in normalized:
        raise ValueError("alias may not start or end with '-' or contain '--'")
    if any(char not in ALIAS_ALPHABET for char in normalized):
        raise ValueError("alias may contain only lowercase letters, numbers, and hyphens")
    return normalized


def _validate_slug_identifier(slug: str) -> str:
    normalized = (slug or "").strip()
    if not normalized:
        raise ValueError("slug is required")
    if "/" in normalized or any(char.isspace() for char in normalized):
        raise ValueError("slug may not contain slashes or whitespace")
    return normalized


def _validate_landing_url(url: str) -> str:
    normalized = _normalize_target_url(url)
    parsed = parse.urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("landing_url must be an absolute http(s) URL")
    return normalized


def _host_matches(host: str, rules: Sequence[str]) -> bool:
    normalized_host = host.lower().strip()
    for rule in rules:
        normalized_rule = rule.lower().strip()
        if not normalized_rule:
            continue
        if normalized_host == normalized_rule or normalized_host.endswith(f".{normalized_rule}"):
            return True
    return False


def _coerce_safety_status(status: object) -> str:
    normalized = str(status or "").strip().lower()
    return normalized if normalized in SAFETY_STATUSES else SAFETY_STATUS_REVIEW


class UrlSafetyDecision:
    def __init__(
        self,
        *,
        status: str,
        reason: str,
        source: str,
        target_hash: str,
        host: str,
        checked_at: Optional[str] = None,
        expires_at: Optional[float] = None,
    ) -> None:
        self.status = _coerce_safety_status(status)
        self.reason = reason
        self.source = source
        self.target_hash = target_hash
        self.host = host
        self.checked_at = checked_at or _utc_now_iso()
        self.expires_at = expires_at

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (now if now is not None else time.time())


class UrlSafetyPolicy:
    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str] = (),
        blocked_hosts: Sequence[str] = (),
        ttl_seconds: int = DEFAULT_SAFETY_CACHE_TTL_SECONDS,
        checker: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self.allowed_hosts = tuple(host.strip().lower() for host in allowed_hosts if host.strip())
        self.blocked_hosts = tuple(host.strip().lower() for host in blocked_hosts if host.strip())
        self.ttl_seconds = max(60, int(ttl_seconds or DEFAULT_SAFETY_CACHE_TTL_SECONDS))
        self.checker = checker

    @classmethod
    def from_env(cls) -> "UrlSafetyPolicy":
        checker = None
        provider = os.getenv("OPENCLAW_SHORTENER_SAFETY_PROVIDER", "").strip().lower()
        api_key = os.getenv("OPENCLAW_WEB_RISK_API_KEY", "").strip()
        if provider in {"webrisk", "web-risk", "google-webrisk", "google_web_risk"} and api_key:
            checker = WebRiskUrlSafetyChecker(
                api_key=api_key,
                threat_types=_split_env("OPENCLAW_WEB_RISK_THREAT_TYPES") or DEFAULT_WEB_RISK_THREAT_TYPES,
                timeout_seconds=_read_float_env("OPENCLAW_WEB_RISK_TIMEOUT_SECONDS", 2.0),
            )
        return cls(
            allowed_hosts=_split_env("OPENCLAW_SHORTENER_SAFETY_ALLOWED_HOSTS"),
            blocked_hosts=_split_env("OPENCLAW_SHORTENER_SAFETY_BLOCKED_HOSTS"),
            ttl_seconds=_read_int_env(
                "OPENCLAW_SHORTENER_SAFETY_CACHE_TTL_SECONDS",
                DEFAULT_SAFETY_CACHE_TTL_SECONDS,
            ),
            checker=checker,
        )

    def decide(
        self,
        url: str,
        *,
        cached: Optional[UrlSafetyDecision] = None,
        allow_external_check: bool = False,
    ) -> UrlSafetyDecision:
        normalized_url = _normalize_target_url(url)
        target_hash = _target_hash(normalized_url)
        host = _target_host(normalized_url)
        if _host_matches(host, self.blocked_hosts):
            return self._new_decision(
                status=SAFETY_STATUS_UNSAFE,
                reason="blocked_host_policy",
                source="policy",
                target_hash=target_hash,
                host=host,
            )
        if self.allowed_hosts and not _host_matches(host, self.allowed_hosts):
            return self._new_decision(
                status=SAFETY_STATUS_REVIEW,
                reason="host_not_allowlisted",
                source="policy",
                target_hash=target_hash,
                host=host,
            )
        if cached and not cached.is_expired():
            return cached
        if allow_external_check and self.checker:
            try:
                checked_status = self.checker(normalized_url)
            except Exception:
                return self._new_decision(
                    status=SAFETY_STATUS_REVIEW,
                    reason="checker_error",
                    source="checker",
                    target_hash=target_hash,
                    host=host,
                )
            return self._new_decision(
                status=_coerce_safety_status(checked_status),
                reason="checker_result",
                source="checker",
                target_hash=target_hash,
                host=host,
            )
        return self._new_decision(
            status=SAFETY_STATUS_SAFE,
            reason="default_safe",
            source="policy",
            target_hash=target_hash,
            host=host,
        )

    def _new_decision(
        self,
        *,
        status: str,
        reason: str,
        source: str,
        target_hash: str,
        host: str,
    ) -> UrlSafetyDecision:
        return UrlSafetyDecision(
            status=status,
            reason=reason,
            source=source,
            target_hash=target_hash,
            host=host,
            expires_at=time.time() + self.ttl_seconds,
        )


class WebRiskUrlSafetyChecker:
    def __init__(
        self,
        *,
        api_key: str,
        threat_types: Sequence[str] = DEFAULT_WEB_RISK_THREAT_TYPES,
        timeout_seconds: float = 2.0,
        endpoint: str = "https://webrisk.googleapis.com/v1/uris:search",
    ) -> None:
        self.api_key = api_key
        self.threat_types = tuple(item.strip() for item in threat_types if item.strip())
        self.timeout_seconds = max(0.1, float(timeout_seconds or 2.0))
        self.endpoint = endpoint.rstrip("?")

    def __call__(self, url: str) -> str:
        params = [("key", self.api_key), ("uri", _normalize_target_url(url))]
        params.extend(("threatTypes", threat_type) for threat_type in self.threat_types)
        request_obj = request.Request(f"{self.endpoint}?{parse.urlencode(params)}", method="GET")
        with request.urlopen(request_obj, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return SAFETY_STATUS_UNSAFE if payload.get("threat") else SAFETY_STATUS_SAFE


@dataclass
class ShortLinkRecord:
    slug: str
    target_url: str
    short_url: str
    alias: Optional[str]
    active: bool
    owner_username: Optional[str]
    owner_status: str
    created_at: Optional[str]
    updated_at: Optional[str]
    click_count: int = 0
    last_clicked_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "target_url": self.target_url,
            "short_url": self.short_url,
            "alias": self.alias,
            "active": self.active,
            "owner_username": self.owner_username,
            "owner_status": self.owner_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "click_count": self.click_count,
            "last_clicked_at": self.last_clicked_at,
            "legacy": self.owner_status == "legacy",
        }


class ShortLinkError(ValueError):
    code = "short_link_error"


class AliasConflictError(ShortLinkError):
    code = "alias_conflict"


class ShortLinkValidationError(ShortLinkError):
    code = "validation_error"


class UrlShortener:
    def shorten(self, url: str) -> Optional[str]:
        raise NotImplementedError

    def resolve(self, slug: str) -> Optional[str]:
        return None

    def record_click(self, slug: str) -> None:
        return None

    def get_summary(self) -> Dict[str, int]:
        return {"total_short_links": 0, "total_short_link_clicks": 0}

    def create_link(
        self,
        target_url: str,
        *,
        alias: Optional[str] = None,
        owner_username: Optional[str] = None,
    ) -> ShortLinkRecord:
        raise NotImplementedError

    def create_links(
        self,
        rows: Sequence[Dict[str, Any]],
        *,
        owner_username: Optional[str] = None,
    ) -> Dict[str, Any]:
        results = []
        for index, row in enumerate(rows, start=1):
            try:
                record = self.create_link(
                    str(row.get("target_url") or row.get("url") or ""),
                    alias=row.get("alias"),
                    owner_username=owner_username,
                )
                results.append({"row": index, "ok": True, "link": record.to_dict()})
            except ShortLinkError as exc:
                results.append({"row": index, "ok": False, "error": exc.code, "message": str(exc)})
            except ValueError as exc:
                results.append({"row": index, "ok": False, "error": "validation_error", "message": str(exc)})
        return {"ok": all(item["ok"] for item in results), "results": results}

    def list_links(self, *, owner_username: Optional[str] = None, include_inactive: bool = True) -> Sequence[ShortLinkRecord]:
        raise NotImplementedError

    def update_link(
        self,
        slug: str,
        *,
        active: Optional[bool] = None,
    ) -> ShortLinkRecord:
        raise NotImplementedError


class BuiltinShortener(UrlShortener):
    def __init__(
        self,
        db_path: str,
        public_base_url: str,
        slug_length: int = 7,
        safety_policy: Optional[UrlSafetyPolicy] = None,
    ) -> None:
        self.db_path = str(Path(db_path))
        self.public_base_url = public_base_url.rstrip("/")
        self.slug_length = slug_length
        self.safety_policy = safety_policy or UrlSafetyPolicy()
        self._initialize()

    def shorten(self, url: str) -> Optional[str]:
        if not url:
            return None
        decision = self._decide_and_cache(url)
        if decision.status == SAFETY_STATUS_UNSAFE:
            return None
        slug = self._ensure_slug(url)
        return f"{self.public_base_url}/s/{slug}"

    def resolve(self, slug: str) -> Optional[str]:
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                "SELECT target_url FROM short_links WHERE slug = ? AND active = 1",
                (slug,),
            ).fetchone()
        if not row:
            return None
        target_url = row[0]
        decision = self.safety_policy.decide(
            target_url,
            cached=self._get_cached_safety_decision(target_url),
            allow_external_check=True,
        )
        self._cache_safety_decision(decision, target_url=target_url)
        if decision.status == SAFETY_STATUS_UNSAFE:
            return None
        return target_url

    def record_click(self, slug: str) -> None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
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
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            total_short_links = connection.execute("SELECT COUNT(*) FROM short_links").fetchone()[0]
            total_short_link_clicks = connection.execute("SELECT COALESCE(SUM(click_count), 0) FROM short_links").fetchone()[0]
        return {
            "total_short_links": int(total_short_links),
            "total_short_link_clicks": int(total_short_link_clicks),
        }

    def create_link(
        self,
        target_url: str,
        *,
        alias: Optional[str] = None,
        owner_username: Optional[str] = None,
    ) -> ShortLinkRecord:
        normalized_url = _validate_landing_url(target_url)
        slug = _validate_alias(alias) if alias else self._generate_unused_slug()
        decision = self._decide_and_cache(normalized_url)
        if decision.status == SAFETY_STATUS_UNSAFE:
            raise ShortLinkValidationError("landing_url is blocked by safety policy")
        now = _utc_now_iso()
        owner = (owner_username or "").strip().lower() or None
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            try:
                connection.execute(
                    """
                    INSERT INTO short_links
                        (slug, target_url, alias, active, owner_username, owner_status, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (slug, normalized_url, slug if alias else None, owner, "assigned" if owner else "unassigned", now, now),
                )
            except sqlite3.IntegrityError as exc:
                if alias:
                    raise AliasConflictError("alias_conflict: alias is already assigned globally") from exc
                raise
        return self._record_from_sqlite_slug(slug)

    def list_links(self, *, owner_username: Optional[str] = None, include_inactive: bool = True) -> Sequence[ShortLinkRecord]:
        clauses = []
        params = []
        if owner_username:
            clauses.append("owner_username = ?")
            params.append(owner_username.strip().lower())
        if not include_inactive:
            clauses.append("active = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            rows = connection.execute(
                f"""
                SELECT slug, target_url, alias, active, owner_username, owner_status,
                       created_at, updated_at, click_count, last_clicked_at
                FROM short_links
                {where}
                ORDER BY created_at DESC, slug ASC
                """,
                tuple(params),
            ).fetchall()
        return [self._record_from_sqlite_row(row) for row in rows]

    def update_link(
        self,
        slug: str,
        *,
        active: Optional[bool] = None,
    ) -> ShortLinkRecord:
        normalized_slug = _validate_slug_identifier(slug)
        updates = []
        params = []
        if active is not None:
            updates.append("active = ?")
            params.append(1 if active else 0)
        if not updates:
            return self._record_from_sqlite_slug(normalized_slug)
        updates.append("updated_at = ?")
        params.append(_utc_now_iso())
        params.append(normalized_slug)
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            cursor = connection.execute(
                f"UPDATE short_links SET {', '.join(updates)} WHERE slug = ?",
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise ShortLinkValidationError("short link not found")
        return self._record_from_sqlite_slug(normalized_slug)

    def _initialize(self) -> None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS short_links (
                    slug TEXT PRIMARY KEY,
                    target_url TEXT NOT NULL,
                    alias TEXT UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    owner_username TEXT,
                    owner_status TEXT NOT NULL DEFAULT 'legacy',
                    click_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT,
                    last_clicked_at TEXT
                )
                """
            )
            self._migrate_short_links_schema(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS url_safety_cache (
                    target_hash TEXT PRIMARY KEY,
                    target_url TEXT NOT NULL,
                    host TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    def _migrate_short_links_schema(self, connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(short_links)").fetchall()}
        migration_columns = {
            "alias": "ALTER TABLE short_links ADD COLUMN alias TEXT",
            "active": "ALTER TABLE short_links ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
            "owner_username": "ALTER TABLE short_links ADD COLUMN owner_username TEXT",
            "owner_status": "ALTER TABLE short_links ADD COLUMN owner_status TEXT NOT NULL DEFAULT 'legacy'",
            "updated_at": "ALTER TABLE short_links ADD COLUMN updated_at TEXT",
        }
        for column_name, statement in migration_columns.items():
            if column_name not in columns:
                connection.execute(statement)
        indexes = connection.execute("PRAGMA index_list(short_links)").fetchall()
        target_url_unique = False
        for index in indexes:
            if not index[2]:
                continue
            indexed_columns = [
                row[2]
                for row in connection.execute(f"PRAGMA index_info({str(index[1])!r})").fetchall()
            ]
            if indexed_columns == ["target_url"]:
                target_url_unique = True
                break
        if target_url_unique:
            connection.execute("ALTER TABLE short_links RENAME TO short_links_legacy_unique")
            connection.execute(
                """
                CREATE TABLE short_links (
                    slug TEXT PRIMARY KEY,
                    target_url TEXT NOT NULL,
                    alias TEXT UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    owner_username TEXT,
                    owner_status TEXT NOT NULL DEFAULT 'legacy',
                    click_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT,
                    last_clicked_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO short_links
                    (slug, target_url, alias, active, owner_username, owner_status,
                     click_count, created_at, updated_at, last_clicked_at)
                SELECT slug, target_url, alias, active, owner_username, owner_status,
                       click_count, created_at, updated_at, last_clicked_at
                FROM short_links_legacy_unique
                """
            )
            connection.execute("DROP TABLE short_links_legacy_unique")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_short_links_alias_unique
            ON short_links(alias)
            WHERE alias IS NOT NULL
            """
        )

    def _decide_and_cache(self, url: str) -> UrlSafetyDecision:
        decision = self.safety_policy.decide(
            url,
            cached=self._get_cached_safety_decision(url),
            allow_external_check=True,
        )
        self._cache_safety_decision(decision, target_url=url)
        return decision

    def _get_cached_safety_decision(self, url: str) -> Optional[UrlSafetyDecision]:
        target_hash = _target_hash(url)
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                """
                SELECT host, status, reason, source, checked_at, expires_at
                FROM url_safety_cache
                WHERE target_hash = ?
                """,
                (target_hash,),
            ).fetchone()
        if not row:
            return None
        return UrlSafetyDecision(
            host=row[0],
            status=row[1],
            reason=row[2],
            source=row[3],
            checked_at=row[4],
            expires_at=float(row[5]),
            target_hash=target_hash,
        )

    def _cache_safety_decision(self, decision: UrlSafetyDecision, target_url: Optional[str] = None) -> None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO url_safety_cache
                    (target_hash, target_url, host, status, reason, source, checked_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_hash) DO UPDATE SET
                    target_url = excluded.target_url,
                    host = excluded.host,
                    status = excluded.status,
                    reason = excluded.reason,
                    source = excluded.source,
                    checked_at = excluded.checked_at,
                    expires_at = excluded.expires_at
                """,
                (
                    decision.target_hash,
                    _normalize_target_url(target_url or ""),
                    decision.host,
                    decision.status,
                    decision.reason,
                    decision.source,
                    decision.checked_at,
                    float(decision.expires_at or (time.time() + self.safety_policy.ttl_seconds)),
                ),
            )

    def _ensure_slug(self, url: str) -> str:
        normalized_url = _validate_landing_url(url)
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            existing = connection.execute(
                "SELECT slug FROM short_links WHERE target_url = ?",
                (normalized_url,),
            ).fetchone()
            if existing:
                return existing[0]

            while True:
                slug = "".join(secrets.choice(ALPHABET) for _ in range(self.slug_length))
                try:
                    connection.execute(
                        "INSERT INTO short_links (slug, target_url, active, owner_status, updated_at) VALUES (?, ?, 1, 'legacy', ?)",
                        (slug, normalized_url, _utc_now_iso()),
                    )
                    return slug
                except sqlite3.IntegrityError:
                    continue

    def _generate_unused_slug(self) -> str:
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            while True:
                slug = "".join(secrets.choice(ALPHABET) for _ in range(self.slug_length))
                existing = connection.execute("SELECT 1 FROM short_links WHERE slug = ?", (slug,)).fetchone()
                if not existing:
                    return slug

    def _record_from_sqlite_slug(self, slug: str) -> ShortLinkRecord:
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                """
                SELECT slug, target_url, alias, active, owner_username, owner_status,
                       created_at, updated_at, click_count, last_clicked_at
                FROM short_links
                WHERE slug = ?
                """,
                (slug,),
            ).fetchone()
        if not row:
            raise ShortLinkValidationError("short link not found")
        return self._record_from_sqlite_row(row)

    def _record_from_sqlite_row(self, row: Sequence[Any]) -> ShortLinkRecord:
        owner_status = str(row[5] or "legacy")
        return ShortLinkRecord(
            slug=str(row[0]),
            target_url=str(row[1]),
            short_url=f"{self.public_base_url}/s/{row[0]}",
            alias=str(row[2]) if row[2] else None,
            active=bool(row[3]),
            owner_username=str(row[4]) if row[4] else None,
            owner_status=owner_status,
            created_at=str(row[6]) if row[6] else None,
            updated_at=str(row[7]) if row[7] else None,
            click_count=int(row[8] or 0),
            last_clicked_at=str(row[9]) if row[9] else None,
        )


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
        safety_policy: Optional[UrlSafetyPolicy] = None,
        safety_collection: str = "url_safety_cache",
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
        self.safety_policy = safety_policy or UrlSafetyPolicy()
        self.safety_collection = safety_collection
        self.emulator_host = emulator_host or os.getenv("FIRESTORE_EMULATOR_HOST")
        root = self._build_api_root()
        self.documents_url = (
            f"{root}/v1/projects/{self.project_id}/databases/{parse.quote(self.database, safe='()')}/documents"
        )
        self.collection_url = f"{self.documents_url}/{parse.quote(self.collection, safe='')}"
        self.safety_collection_url = f"{self.documents_url}/{parse.quote(self.safety_collection, safe='')}"
        self._cached_token: Optional[str] = None
        self._cached_token_expiry = 0.0

    def shorten(self, url: str) -> Optional[str]:
        if not url:
            return None
        decision = self._decide_and_cache(url)
        if decision.status == SAFETY_STATUS_UNSAFE:
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
        if _firestore_bool(document, "active") is False:
            return None
        if not self._allows_cached_redirect(document):
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

    def create_link(
        self,
        target_url: str,
        *,
        alias: Optional[str] = None,
        owner_username: Optional[str] = None,
    ) -> ShortLinkRecord:
        normalized_url = _validate_landing_url(target_url)
        slug = _validate_alias(alias) if alias else self._generate_unused_slug()
        decision = self._decide_and_cache(normalized_url)
        if decision.status == SAFETY_STATUS_UNSAFE:
            raise ShortLinkValidationError("landing_url is blocked by safety policy")
        owner = (owner_username or "").strip().lower() or None
        while not self._create_slug(
            slug=slug,
            target_url=normalized_url,
            alias=slug if alias else None,
            owner_username=owner,
            owner_status="assigned" if owner else "unassigned",
        ):
            if alias:
                raise AliasConflictError("alias_conflict: alias is already assigned globally")
            slug = self._generate_unused_slug()
        record = self._record_from_firestore_document(self._get_document(slug), slug=slug)
        if record is None:
            raise ShortLinkValidationError("short link was not readable after creation")
        return record

    def list_links(self, *, owner_username: Optional[str] = None, include_inactive: bool = True) -> Sequence[ShortLinkRecord]:
        filters = []
        if owner_username:
            filters.append(
                {
                    "fieldFilter": {
                        "field": {"fieldPath": "owner_username"},
                        "op": "EQUAL",
                        "value": {"stringValue": owner_username.strip().lower()},
                    }
                }
            )
        if not include_inactive:
            filters.append(
                {
                    "fieldFilter": {
                        "field": {"fieldPath": "active"},
                        "op": "EQUAL",
                        "value": {"booleanValue": True},
                    }
                }
            )
        query: Dict[str, Any] = {"from": [{"collectionId": self.collection}]}
        if len(filters) == 1:
            query["where"] = filters[0]
        elif filters:
            query["where"] = {"compositeFilter": {"op": "AND", "filters": filters}}
        response = self._request_json(
            "POST",
            f"{self.documents_url}:runQuery",
            body={"structuredQuery": query},
        )
        records = []
        for item in _as_sequence(response):
            document = (item or {}).get("document")
            record = self._record_from_firestore_document(document)
            if record:
                records.append(record)
        return records

    def update_link(
        self,
        slug: str,
        *,
        active: Optional[bool] = None,
    ) -> ShortLinkRecord:
        normalized_slug = _validate_slug_identifier(slug)
        fields: Dict[str, Any] = {"updated_at": {"timestampValue": _utc_now_iso()}}
        update_masks = ["updated_at"]
        if active is not None:
            fields["active"] = {"booleanValue": bool(active)}
            update_masks.append("active")
        query = "&".join(f"updateMask.fieldPaths={parse.quote(field, safe='')}" for field in update_masks)
        response = self._request_json(
            "PATCH",
            f"{self._document_url(normalized_slug)}?{query}",
            body={"fields": fields},
            allow_statuses=(404,),
        )
        if not response:
            raise ShortLinkValidationError("short link not found")
        record = self._record_from_firestore_document(response, slug=normalized_slug)
        if record is None:
            raise ShortLinkValidationError("short link not found")
        return record

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

    def _create_slug(
        self,
        *,
        slug: str,
        target_url: str,
        alias: Optional[str] = None,
        owner_username: Optional[str] = None,
        owner_status: str = "legacy",
    ) -> bool:
        now = _utc_now_iso()
        try:
            self._request_json(
                "PATCH",
                f"{self._document_url(slug)}?currentDocument.exists=false",
                body={
                    "fields": {
                        "target_url": {"stringValue": target_url},
                        **({"alias": {"stringValue": alias}} if alias else {}),
                        "active": {"booleanValue": True},
                        **({"owner_username": {"stringValue": owner_username}} if owner_username else {}),
                        "owner_status": {"stringValue": owner_status},
                        "target_hash": {"stringValue": _target_hash(target_url)},
                        "target_host": {"stringValue": _target_host(target_url)},
                        "safety_status": {"stringValue": self._last_decision_for(target_url).status},
                        "click_count": {"integerValue": "0"},
                        "created_at": {"timestampValue": now},
                        "updated_at": {"timestampValue": now},
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

    def _allows_cached_redirect(self, document: Dict[str, Any]) -> bool:
        target_url = _firestore_string(document, "target_url") or ""
        embedded_status = _firestore_string(document, "safety_status")
        if embedded_status == SAFETY_STATUS_UNSAFE:
            return False
        decision = self.safety_policy.decide(
            target_url,
            cached=self._get_cached_safety_decision(target_url),
            allow_external_check=True,
        )
        self._cache_safety_decision(decision, target_url=target_url)
        if decision.status == SAFETY_STATUS_UNSAFE:
            return False
        return True

    def _decide_and_cache(self, url: str) -> UrlSafetyDecision:
        decision = self.safety_policy.decide(
            url,
            cached=self._get_cached_safety_decision(url),
            allow_external_check=True,
        )
        self._cache_safety_decision(decision, target_url=url)
        return decision

    def _last_decision_for(self, url: str) -> UrlSafetyDecision:
        return self.safety_policy.decide(
            url,
            cached=self._get_cached_safety_decision(url),
            allow_external_check=False,
        )

    def _get_cached_safety_decision(self, url: str) -> Optional[UrlSafetyDecision]:
        response = self._request_json(
            "GET",
            f"{self.safety_collection_url}/{parse.quote(_target_hash(url), safe='')}",
            allow_statuses=(404,),
        )
        if not response or not isinstance(response, dict):
            return None
        return UrlSafetyDecision(
            status=_firestore_string(response, "status") or SAFETY_STATUS_REVIEW,
            reason=_firestore_string(response, "reason") or "cached",
            source=_firestore_string(response, "source") or "cache",
            target_hash=_target_hash(url),
            host=_firestore_string(response, "host") or _target_host(url),
            checked_at=_firestore_string(response, "checked_at"),
            expires_at=float(_firestore_int(response, "expires_at_epoch")),
        )

    def _cache_safety_decision(self, decision: UrlSafetyDecision, target_url: Optional[str] = None) -> None:
        self._request_json(
            "PATCH",
            f"{self.safety_collection_url}/{parse.quote(decision.target_hash, safe='')}",
            body={
                "fields": {
                    "target_url": {"stringValue": _normalize_target_url(target_url or "")},
                    "host": {"stringValue": decision.host},
                    "status": {"stringValue": decision.status},
                    "reason": {"stringValue": decision.reason},
                    "source": {"stringValue": decision.source},
                    "checked_at": {"stringValue": decision.checked_at},
                    "expires_at_epoch": {"integerValue": str(int(decision.expires_at or time.time()))},
                }
            },
        )

    def _document_url(self, slug: str) -> str:
        return f"{self.collection_url}/{parse.quote(slug, safe='')}"

    def _document_name(self, slug: str) -> str:
        return (
            f"projects/{self.project_id}/databases/{self.database}/documents/"
            f"{self.collection}/{slug}"
        )

    def _generate_unused_slug(self) -> str:
        while True:
            slug = "".join(secrets.choice(ALPHABET) for _ in range(self.slug_length))
            if not self._get_document(slug):
                return slug

    def _record_from_firestore_document(
        self,
        document: Optional[Dict[str, Any]],
        *,
        slug: Optional[str] = None,
    ) -> Optional[ShortLinkRecord]:
        if not document:
            return None
        resolved_slug = slug or str(document.get("name") or "").rsplit("/", 1)[-1]
        if not resolved_slug:
            return None
        owner_username = _firestore_string(document, "owner_username")
        owner_status = _firestore_string(document, "owner_status") or ("assigned" if owner_username else "legacy")
        active = _firestore_bool(document, "active")
        return ShortLinkRecord(
            slug=resolved_slug,
            target_url=_firestore_string(document, "target_url") or "",
            short_url=f"{self.public_base_url}/s/{resolved_slug}",
            alias=_firestore_string(document, "alias"),
            active=True if active is None else active,
            owner_username=owner_username,
            owner_status=owner_status,
            created_at=_firestore_string(document, "created_at") or _firestore_timestamp(document, "created_at"),
            updated_at=_firestore_string(document, "updated_at") or _firestore_timestamp(document, "updated_at"),
            click_count=_firestore_int(document, "click_count"),
            last_clicked_at=_firestore_string(document, "last_clicked_at") or _firestore_timestamp(document, "last_clicked_at"),
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


def _firestore_timestamp(document: Dict[str, Any], field_name: str) -> Optional[str]:
    field = _firestore_fields(document).get(field_name) or {}
    value = field.get("timestampValue")
    return str(value) if value is not None else None


def _firestore_bool(document: Dict[str, Any], field_name: str) -> Optional[bool]:
    field = _firestore_fields(document).get(field_name) or {}
    value = field.get("booleanValue")
    return bool(value) if value is not None else None


def _firestore_int(document: Dict[str, Any], field_name: str) -> int:
    field = _firestore_fields(document).get(field_name) or {}
    value = field.get("integerValue")
    if value is None:
        return 0
    return int(value)


def _split_env(name: str) -> Sequence[str]:
    raw = os.getenv(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
