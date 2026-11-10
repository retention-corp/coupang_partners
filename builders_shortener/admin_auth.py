"""Semi-private admin authentication primitives for the builders shortener."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Set


OWNER = "owner"
ADMIN = "admin"
VIEWER = "viewer"

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
SUSPENDED = "suspended"
ACCOUNT_STATUSES = {PENDING, APPROVED, REJECTED, SUSPENDED}

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    OWNER: {
        "session:create",
        "session:delete",
        "user:create",
        "user:read",
        "user:update",
        "user:delete",
        "audit:read",
        "short_link:read",
        "short_link:write",
        "domain_policy:read",
        "domain_policy:write",
    },
    ADMIN: {
        "session:create",
        "session:delete",
        "user:read",
        "audit:read",
        "short_link:read",
        "short_link:write",
        "domain_policy:read",
        "domain_policy:write",
    },
    VIEWER: {
        "session:create",
        "session:delete",
        "user:read",
        "audit:read",
        "short_link:read",
        "domain_policy:read",
    },
}


class AdminAuthError(ValueError):
    """Raised when authentication or authorization fails."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not normalized:
        raise ValueError("username is required")
    return normalized


def _validate_role(role: str) -> str:
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown admin role: {role}")
    return role


def _validate_account_status(status: str) -> str:
    if status not in ACCOUNT_STATUSES:
        raise ValueError(f"unknown admin account status: {status}")
    return status


def hash_password(password: str, *, iterations: int = 210_000) -> str:
    if not password:
        raise ValueError("password is required")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, expected = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(expected.encode("ascii"))
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected_digest)


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class AdminUser:
    username: str
    password_hash: str
    role: str
    active: bool
    status: str
    status_history: List[Dict[str, Any]]
    created_at: str
    updated_at: str

    def public_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload.pop("password_hash", None)
        return payload

    def is_approved(self) -> bool:
        return self.active and self.status == APPROVED


@dataclass
class AdminSession:
    token_hash: str
    username: str
    role: str
    created_at: str
    expires_at: float

    def is_expired(self, now: Optional[float] = None) -> bool:
        return self.expires_at <= (time.time() if now is None else now)


@dataclass
class AuditEvent:
    action: str
    actor: str
    target: str
    outcome: str
    timestamp: str
    metadata: Dict[str, Any]


class JsonAdminStore:
    """Small file-backed store for admin users, sessions, and audit events."""

    def __init__(self, state_path: str, audit_path: Optional[str] = None) -> None:
        self.state_path = Path(state_path)
        self.audit_path = Path(audit_path) if audit_path else self.state_path.with_suffix(".audit.jsonl")
        self._lock = RLock()
        self._ensure_parent_dirs()

    def list_users(self) -> List[AdminUser]:
        state = self._read_state()
        return [self._decode_user(item) for item in state.get("users", {}).values()]

    def get_user(self, username: str) -> Optional[AdminUser]:
        state = self._read_state()
        item = state.get("users", {}).get(_normalize_username(username))
        return self._decode_user(item) if item else None

    def save_user(self, user: AdminUser) -> None:
        with self._lock:
            state = self._read_state()
            users = state.setdefault("users", {})
            users[_normalize_username(user.username)] = asdict(user)
            self._write_state(state)

    def delete_user(self, username: str) -> bool:
        with self._lock:
            state = self._read_state()
            removed = state.setdefault("users", {}).pop(_normalize_username(username), None)
            self._write_state(state)
        return removed is not None

    def save_session(self, session: AdminSession) -> None:
        with self._lock:
            state = self._read_state()
            state.setdefault("sessions", {})[session.token_hash] = asdict(session)
            self._write_state(state)

    def get_session(self, token: str) -> Optional[AdminSession]:
        state = self._read_state()
        item = state.get("sessions", {}).get(session_token_hash(token))
        return self._decode_session(item) if item else None

    def delete_session(self, token: str) -> bool:
        with self._lock:
            state = self._read_state()
            removed = state.setdefault("sessions", {}).pop(session_token_hash(token), None)
            self._write_state(state)
        return removed is not None

    def delete_sessions_for_user(self, username: str) -> int:
        normalized = _normalize_username(username)
        with self._lock:
            state = self._read_state()
            sessions = state.setdefault("sessions", {})
            removed = [key for key, value in sessions.items() if value.get("username") == normalized]
            for key in removed:
                sessions.pop(key, None)
            self._write_state(state)
        return len(removed)

    def prune_expired_sessions(self, now: Optional[float] = None) -> int:
        deadline = time.time() if now is None else now
        with self._lock:
            state = self._read_state()
            sessions = state.setdefault("sessions", {})
            expired = [key for key, value in sessions.items() if float(value.get("expires_at", 0)) <= deadline]
            for key in expired:
                sessions.pop(key, None)
            self._write_state(state)
        return len(expired)

    def append_audit_event(self, event: AuditEvent) -> None:
        self._ensure_parent_dirs()
        with self._lock:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True))
                handle.write("\n")

    def list_audit_events(self, limit: int = 100) -> List[AuditEvent]:
        if not self.audit_path.exists():
            return []
        events: List[AuditEvent] = []
        with self.audit_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                events.append(self._decode_audit_event(json.loads(line)))
        return events[-limit:]

    def _ensure_parent_dirs(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_state(self) -> Dict[str, Any]:
        with self._lock:
            if not self.state_path.exists():
                return {"users": {}, "sessions": {}}
            with self.state_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("admin state file must contain a JSON object")
        payload.setdefault("users", {})
        payload.setdefault("sessions", {})
        return payload

    def _write_state(self, payload: Dict[str, Any]) -> None:
        self._ensure_parent_dirs()
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.state_path.name}.", dir=str(self.state_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, self.state_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)

    @staticmethod
    def _decode_user(payload: Dict[str, Any]) -> AdminUser:
        active = bool(payload.get("active", True))
        status = _validate_account_status(str(payload.get("status") or (APPROVED if active else SUSPENDED)))
        raw_history = payload.get("status_history")
        if isinstance(raw_history, list):
            status_history = [item for item in raw_history if isinstance(item, dict)]
        else:
            created_at = str(payload["created_at"])
            status_history = [
                {
                    "status": status,
                    "changed_at": created_at,
                    "actor": "legacy",
                    "reason": "decoded_from_active",
                }
            ]
        return AdminUser(
            username=_normalize_username(str(payload["username"])),
            password_hash=str(payload["password_hash"]),
            role=_validate_role(str(payload["role"])),
            active=active,
            status=status,
            status_history=status_history,
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )

    @staticmethod
    def _decode_session(payload: Dict[str, Any]) -> AdminSession:
        return AdminSession(
            token_hash=str(payload["token_hash"]),
            username=_normalize_username(str(payload["username"])),
            role=_validate_role(str(payload["role"])),
            created_at=str(payload["created_at"]),
            expires_at=float(payload["expires_at"]),
        )

    @staticmethod
    def _decode_audit_event(payload: Dict[str, Any]) -> AuditEvent:
        metadata = payload.get("metadata", {})
        return AuditEvent(
            action=str(payload["action"]),
            actor=str(payload["actor"]),
            target=str(payload["target"]),
            outcome=str(payload["outcome"]),
            timestamp=str(payload["timestamp"]),
            metadata=metadata if isinstance(metadata, dict) else {},
        )


class AdminAuthService:
    def __init__(self, store: JsonAdminStore, *, session_ttl_seconds: int = 8 * 60 * 60) -> None:
        if session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be positive")
        self.store = store
        self.session_ttl_seconds = session_ttl_seconds

    def create_user(
        self,
        actor: Optional[AdminUser],
        username: str,
        password: str,
        role: str,
        *,
        status: str = APPROVED,
    ) -> AdminUser:
        self.require_permission(actor, "user:create")
        normalized = _normalize_username(username)
        if self.store.get_user(normalized):
            self._audit("user.create", actor, normalized, "denied", {"reason": "already_exists"})
            raise AdminAuthError("admin user already exists")
        now = utc_now_iso()
        initial_status = _validate_account_status(status)
        user = AdminUser(
            username=normalized,
            password_hash=hash_password(password),
            role=_validate_role(role),
            active=initial_status == APPROVED,
            status=initial_status,
            status_history=[
                {
                    "status": initial_status,
                    "changed_at": now,
                    "actor": actor.username if actor else "system",
                    "reason": "created",
                }
            ],
            created_at=now,
            updated_at=now,
        )
        self.store.save_user(user)
        self._audit("user.create", actor, normalized, "success", {"role": role, "status": user.status})
        return user

    def update_user(
        self,
        actor: Optional[AdminUser],
        username: str,
        *,
        password: Optional[str] = None,
        role: Optional[str] = None,
        active: Optional[bool] = None,
        status: Optional[str] = None,
    ) -> AdminUser:
        self.require_permission(actor, "user:update")
        user = self._require_user(username)
        next_role = _validate_role(role) if role is not None else user.role
        next_status = _validate_account_status(status) if status is not None else user.status
        if active is not None and status is None:
            next_status = APPROVED if active else SUSPENDED
        next_active = next_status == APPROVED
        if self._would_remove_last_admin(user.username, role=next_role, status=next_status, active=next_active):
            self._audit("user.update", actor, user.username, "denied", {"reason": "last_admin"})
            raise AdminAuthError("cannot remove or demote the last active administrator")
        if password is not None:
            user.password_hash = hash_password(password)
            self.store.delete_sessions_for_user(user.username)
        if role is not None:
            user.role = next_role
            self.store.delete_sessions_for_user(user.username)
        if status is not None or active is not None:
            if user.status != next_status:
                user.status_history.append(
                    {
                        "status": next_status,
                        "changed_at": utc_now_iso(),
                        "actor": actor.username if actor else "system",
                        "reason": "status_update",
                    }
                )
            user.status = next_status
            user.active = next_active
            self.store.delete_sessions_for_user(user.username)
        user.updated_at = utc_now_iso()
        self.store.save_user(user)
        self._audit(
            "user.update",
            actor,
            user.username,
            "success",
            {"role": user.role, "status": user.status, "active": user.active},
        )
        return user

    def delete_user(self, actor: Optional[AdminUser], username: str) -> bool:
        self.require_permission(actor, "user:delete")
        normalized = _normalize_username(username)
        if actor and actor.username == normalized:
            self._audit("user.delete", actor, normalized, "denied", {"reason": "self_delete"})
            raise AdminAuthError("admin user cannot delete their own account")
        user = self.store.get_user(normalized)
        if user and self._would_remove_last_admin(user.username, status=SUSPENDED, active=False):
            self._audit("user.delete", actor, normalized, "denied", {"reason": "last_admin"})
            raise AdminAuthError("cannot remove or demote the last active administrator")
        removed = self.store.delete_user(normalized)
        self.store.delete_sessions_for_user(normalized)
        self._audit("user.delete", actor, normalized, "success" if removed else "not_found", {})
        return removed

    def list_users(self, actor: AdminUser) -> List[Dict[str, Any]]:
        self.require_permission(actor, "user:read")
        return [user.public_dict() for user in self.store.list_users()]

    def authenticate(self, username: str, password: str) -> str:
        user = self.store.get_user(username)
        if not user or not user.is_approved() or not verify_password(password, user.password_hash):
            self._audit(
                "session.create",
                None,
                _normalize_username(username),
                "denied",
                {"reason": "bad_credentials" if not user else "account_not_approved"},
            )
            raise AdminAuthError("invalid admin credentials")
        token = secrets.token_urlsafe(32)
        session = AdminSession(
            token_hash=session_token_hash(token),
            username=user.username,
            role=user.role,
            created_at=utc_now_iso(),
            expires_at=time.time() + self.session_ttl_seconds,
        )
        self.store.prune_expired_sessions()
        self.store.save_session(session)
        self._audit("session.create", user, user.username, "success", {})
        return token

    def resolve_session(self, token: str) -> AdminUser:
        session = self.store.get_session(token)
        if not session or session.is_expired():
            if session:
                self.store.delete_session(token)
            raise AdminAuthError("admin session is invalid or expired")
        user = self.store.get_user(session.username)
        if not user or not user.is_approved() or user.role != session.role:
            self.store.delete_session(token)
            raise AdminAuthError("admin session user is inactive or changed")
        return user

    def logout(self, actor: AdminUser, token: str) -> bool:
        self.require_permission(actor, "session:delete")
        removed = self.store.delete_session(token)
        self._audit("session.delete", actor, actor.username, "success" if removed else "not_found", {})
        return removed

    def require_permission(self, actor: Optional[AdminUser], permission: str) -> None:
        if actor is None:
            raise AdminAuthError("admin authentication is required")
        allowed = ROLE_PERMISSIONS.get(actor.role, set())
        if permission not in allowed:
            self._audit("permission.check", actor, actor.username, "denied", {"permission": permission})
            raise AdminAuthError("admin role is not allowed to perform this action")

    def ensure_bootstrap_owner(self, username: str, password: str) -> Optional[AdminUser]:
        """Create the first owner only when the user store is empty."""
        if self.store.list_users():
            return None
        now = utc_now_iso()
        user = AdminUser(
            username=_normalize_username(username),
            password_hash=hash_password(password),
            role=OWNER,
            active=True,
            status=APPROVED,
            status_history=[
                {
                    "status": APPROVED,
                    "changed_at": now,
                    "actor": "system",
                    "reason": "bootstrap",
                }
            ],
            created_at=now,
            updated_at=now,
        )
        self.store.save_user(user)
        self._audit("user.bootstrap", user, user.username, "success", {"role": OWNER})
        return user

    def _require_user(self, username: str) -> AdminUser:
        user = self.store.get_user(username)
        if not user:
            raise AdminAuthError("admin user not found")
        return user

    def _would_remove_last_admin(
        self,
        username: str,
        *,
        role: Optional[str] = None,
        active: Optional[bool] = None,
        status: Optional[str] = None,
    ) -> bool:
        normalized = _normalize_username(username)
        remaining = 0
        for user in self.store.list_users():
            candidate_role = role if user.username == normalized and role is not None else user.role
            candidate_status = status if user.username == normalized and status is not None else user.status
            candidate_active = active if user.username == normalized and active is not None else user.active
            if candidate_active and candidate_status == APPROVED and candidate_role in {OWNER, ADMIN}:
                remaining += 1
        return remaining == 0

    def _audit(
        self,
        action: str,
        actor: Optional[AdminUser],
        target: str,
        outcome: str,
        metadata: Dict[str, Any],
    ) -> None:
        self.store.append_audit_event(
            AuditEvent(
                action=action,
                actor=actor.username if actor else "anonymous",
                target=target,
                outcome=outcome,
                timestamp=utc_now_iso(),
                metadata=metadata,
            )
        )


def build_admin_auth_service_from_env() -> AdminAuthService:
    state_path = os.getenv("BUILDERS_SHORTENER_ADMIN_STATE_PATH", "/tmp/builders-shortener-admin.json")
    audit_path = os.getenv("BUILDERS_SHORTENER_ADMIN_AUDIT_PATH")
    ttl = int(os.getenv("BUILDERS_SHORTENER_ADMIN_SESSION_TTL_SECONDS", str(8 * 60 * 60)))
    service = AdminAuthService(JsonAdminStore(state_path, audit_path), session_ttl_seconds=ttl)
    bootstrap_username = os.getenv("BUILDERS_SHORTENER_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    bootstrap_password = os.getenv("BUILDERS_SHORTENER_BOOTSTRAP_ADMIN_PASSWORD", "")
    if bootstrap_username and bootstrap_password:
        service.ensure_bootstrap_owner(bootstrap_username, bootstrap_password)
    return service
