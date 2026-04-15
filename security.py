import ipaddress
import hashlib
import json
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Iterable, Optional, Tuple
from urllib import parse


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_request_id() -> str:
    return str(uuid.uuid4())


def log_event(event: str, **fields: object) -> None:
    record = {
        "timestamp": utc_now_iso(),
        "event": event,
        **fields,
    }
    print(json.dumps(record, ensure_ascii=False), flush=True)


def parse_api_tokens(raw: Optional[str]) -> Tuple[str, ...]:
    if not raw:
        return ()
    return tuple(token.strip() for token in raw.split(",") if token.strip())


def shopping_api_tokens_from_env() -> Tuple[str, ...]:
    raw_tokens = os.getenv("OPENCLAW_SHOPPING_API_TOKENS")
    if raw_tokens:
        return parse_api_tokens(raw_tokens)

    raw_token = os.getenv("OPENCLAW_SHOPPING_API_TOKEN")
    if not raw_token:
        return ()
    token = raw_token.strip()
    return (token,) if token else ()


def shopping_client_allowlist_enabled_from_env() -> bool:
    value = os.getenv("OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def shopping_client_allowlist_from_env() -> Tuple[str, ...]:
    raw_clients = os.getenv("OPENCLAW_SHOPPING_CLIENT_ALLOWLIST", "")
    if not raw_clients:
        raw_clients = os.getenv("OPENCLAW_ALLOWED_CLIENT_IDS", "")
    return tuple(client.strip() for client in raw_clients.split(",") if client.strip())


def is_client_allowlisted(raw_client_id: Optional[str], allowlist: Tuple[str, ...], enforce: bool) -> bool:
    if not enforce:
        return True
    if not raw_client_id:
        return False
    return raw_client_id in allowlist


def _read_int(value: Optional[str], default: int) -> int:
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def shopping_rate_limit_defaults_from_env() -> Tuple[int, int]:
    window_seconds = _read_int(os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS"), 60)
    max_requests = _read_int(os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS"), 30)
    return window_seconds, max_requests


def shopping_auth_required_from_env() -> bool:
    if shopping_api_tokens_from_env():
        return True

    public_base_url = (os.getenv("OPENCLAW_SHOPPING_PUBLIC_BASE_URL") or "").strip()
    if not public_base_url:
        return False

    try:
        parsed = parse.urlparse(public_base_url)
    except ValueError:
        return True

    host = (parsed.hostname or "").lower()
    return host not in {"", "127.0.0.1", "localhost"}


def parse_bearer_token(header_value: Optional[str]) -> Optional[str]:
    if not header_value:
        return None
    prefix = "Bearer "
    if not header_value.startswith(prefix):
        return None
    token = header_value[len(prefix):].strip()
    return token or None


def summarize_client(remote_addr: Optional[str], client_id: Optional[str], token: Optional[str]) -> str:
    if client_id:
        return f"client:{client_id}"
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        return f"token:{token_hash}"
    return f"ip:{remote_addr or 'unknown'}"


def rate_limit_key(
    remote_addr: Optional[str],
    client_id: Optional[str],
    token: Optional[str],
    *,
    allowlisted_client: bool = False,
) -> str:
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        return f"token:{token_hash}"
    if allowlisted_client and client_id:
        return f"client:{client_id}:{remote_addr or 'unknown'}"
    return f"ip:{remote_addr or 'unknown'}"


def normalize_client_ip(remote_addr: Optional[str]) -> str:
    if not remote_addr:
        return "unknown"
    try:
        return str(ipaddress.ip_address(remote_addr))
    except ValueError:
        return remote_addr


def validate_payload_limits(payload: Dict[str, object]) -> Optional[str]:
    query = str(payload.get("query") or payload.get("intent") or "")
    if len(query) > 300:
        return "'query' must be 300 characters or fewer"

    constraints = payload.get("constraints")
    if constraints is not None and not isinstance(constraints, dict):
        return "'constraints' must be an object"

    snippets = payload.get("evidence_snippets") or []
    if not isinstance(snippets, list):
        return "'evidence_snippets' must be a list"
    if len(snippets) > 10:
        return "'evidence_snippets' must contain at most 10 items"
    for item in snippets:
        if not isinstance(item, (str, dict)):
            return "Each evidence snippet must be a string or object"
        text = item if isinstance(item, str) else item.get("text", "")
        if len(str(text)) > 500:
            return "Each evidence snippet must be 500 characters or fewer"
    return None


def validate_deeplink_url(url: str, allowed_hosts: Iterable[str]) -> bool:
    try:
        parsed = parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    normalized_hosts = tuple(entry.lower().strip() for entry in allowed_hosts if entry.strip())
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in normalized_hosts)


class RateLimiter:
    def __init__(self, *, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._lock = threading.Lock()
        self._buckets: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            cutoff = now - self.window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True


def build_rate_limiter_from_env() -> RateLimiter:
    window_seconds = _read_int(os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS"), 60)
    max_requests = _read_int(os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS"), 30)
    if window_seconds <= 0:
        window_seconds = 60
    if max_requests <= 0:
        max_requests = 30
    return RateLimiter(window_seconds=window_seconds, max_requests=max_requests)


def build_rate_limiter_for_mode(public: bool, authenticated: bool) -> Optional[RateLimiter]:
    if public:
        window_env = os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_PUBLIC")
        max_env = os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_PUBLIC")
        if window_env is None and max_env is None:
            return None
        fallback_window, fallback_requests = shopping_rate_limit_defaults_from_env()
        window_seconds = _read_int(window_env, fallback_window)
        max_requests = _read_int(max_env, fallback_requests)
    elif authenticated:
        window_env = os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_AUTH")
        max_env = os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_AUTH")
        if window_env is None and max_env is None:
            return None
        fallback_window, fallback_requests = shopping_rate_limit_defaults_from_env()
        window_seconds = _read_int(window_env, fallback_window)
        max_requests = _read_int(max_env, fallback_requests)
    else:
        window_env = os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_ADMIN")
        max_env = os.getenv("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_ADMIN")
        if window_env is None and max_env is None:
            return None
        fallback_window, fallback_requests = shopping_rate_limit_defaults_from_env()
        window_seconds = _read_int(window_env, fallback_window)
        max_requests = _read_int(max_env, fallback_requests)

    if window_seconds <= 0:
        window_seconds = 60
    if max_requests <= 0:
        max_requests = 30
    return RateLimiter(window_seconds=window_seconds, max_requests=max_requests)
