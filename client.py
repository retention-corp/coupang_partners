import hashlib
import hmac
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Union
from urllib import error, parse, request


DEFAULT_DOMAIN = "https://api-gateway.coupang.com"
V1_PREFIX = "/v2/providers/affiliate_open_api/apis/openapi/v1"
V2_PREFIX = "/v2/providers/affiliate_open_api/apis/openapi/v2"


class CoupangApiError(RuntimeError):
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Coupang API request failed with status {status_code}: {payload}")


def _read_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


class _SlidingWindow:
    def __init__(self, *, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = max(1, int(window_seconds))
        self.max_requests = max(1, int(max_requests))
        self._hits: Deque[float] = deque()

    def allow(self, now: float) -> bool:
        cutoff = now - self.window_seconds
        while self._hits and self._hits[0] < cutoff:
            self._hits.popleft()
        if len(self._hits) >= self.max_requests:
            return False
        self._hits.append(now)
        return True


class CoupangUpstreamRateGuard:
    """Process-local guard for Coupang Partners upstream limits.

    Cloud Run can run multiple instances, so defaults are intentionally below the
    published limits. With the deployment's max-instances=3, defaults keep aggregate
    traffic below 100 all-API/minute, 50 search/minute, and 500 report/hour.
    """

    def __init__(self) -> None:
        self.enabled = os.getenv("COUPANG_UPSTREAM_RATE_LIMIT_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.cooldown_seconds = max(0, _read_int_env("COUPANG_UPSTREAM_COOLDOWN_SECONDS", 300))
        self._lock = threading.Lock()
        self._windows = {
            "all": _SlidingWindow(
                window_seconds=60,
                max_requests=_read_int_env("COUPANG_UPSTREAM_LIMIT_ALL_PER_MINUTE", 30),
            ),
            "search": _SlidingWindow(
                window_seconds=60,
                max_requests=_read_int_env("COUPANG_UPSTREAM_LIMIT_SEARCH_PER_MINUTE", 15),
            ),
            "deeplink": _SlidingWindow(
                window_seconds=60,
                max_requests=_read_int_env("COUPANG_UPSTREAM_LIMIT_DEEPLINK_PER_MINUTE", 15),
            ),
            "report": _SlidingWindow(
                window_seconds=3600,
                max_requests=_read_int_env("COUPANG_UPSTREAM_LIMIT_REPORT_PER_HOUR", 150),
            ),
        }
        self._cooldown_until: Dict[str, float] = {}

    def allow(self, operation: str) -> tuple[bool, str]:
        if not self.enabled:
            return True, ""
        now = time.monotonic()
        operation = operation if operation in self._windows else "other"
        with self._lock:
            for key in ("all", operation):
                cooldown_until = self._cooldown_until.get(key, 0.0)
                if cooldown_until > now:
                    return False, f"cooldown:{key}"
            if not self._windows["all"].allow(now):
                return False, "local_limit:all"
            if operation in self._windows and not self._windows[operation].allow(now):
                return False, f"local_limit:{operation}"
        return True, ""

    def trip(self, operation: str) -> None:
        if not self.enabled or self.cooldown_seconds <= 0:
            return
        operation = operation if operation in self._windows else "all"
        until = time.monotonic() + self.cooldown_seconds
        with self._lock:
            self._cooldown_until["all"] = max(self._cooldown_until.get("all", 0.0), until)
            self._cooldown_until[operation] = max(self._cooldown_until.get(operation, 0.0), until)


_DEFAULT_RATE_GUARD = CoupangUpstreamRateGuard()


@dataclass(frozen=True)
class SignedRequest:
    authorization: str
    signed_date: str
    path_with_query: str


class CoupangPartnersClient:
    def __init__(
        self,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        *,
        domain: str = DEFAULT_DOMAIN,
        timeout: int = 30,
        rate_guard: Optional[CoupangUpstreamRateGuard] = None,
    ) -> None:
        self.access_key = access_key or os.getenv("COUPANG_ACCESS_KEY")
        self.secret_key = secret_key or os.getenv("COUPANG_SECRET_KEY")
        self.domain = domain.rstrip("/")
        self.timeout = timeout
        self.rate_guard = rate_guard or _DEFAULT_RATE_GUARD

        if not self.access_key or not self.secret_key:
            raise ValueError(
                "Coupang credentials are required. Set COUPANG_ACCESS_KEY and "
                "COUPANG_SECRET_KEY or pass them explicitly."
            )

    @classmethod
    def from_env(cls, *, domain: str = DEFAULT_DOMAIN, timeout: int = 30) -> "CoupangPartnersClient":
        return cls(domain=domain, timeout=timeout)

    @staticmethod
    def build_signed_date(now: Optional[datetime] = None) -> str:
        current = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        return current.strftime("%y%m%dT%H%M%SZ")

    def sign(
        self,
        method: str,
        path_with_query: str,
        *,
        signed_date: Optional[str] = None,
    ) -> SignedRequest:
        path, _, query = path_with_query.partition("?")
        http_method = method.upper()
        signed_date = signed_date or self.build_signed_date()
        message = f"{signed_date}{http_method}{path}{query}"
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "CEA algorithm=HmacSHA256, "
            f"access-key={self.access_key}, "
            f"signed-date={signed_date}, "
            f"signature={signature}"
        )
        return SignedRequest(
            authorization=authorization,
            signed_date=signed_date,
            path_with_query=path_with_query,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        signed_date: Optional[str] = None,
        opener: Any = None,
    ) -> Any:
        path_with_query = self._build_path_with_query(path, query=query)
        operation = self._classify_operation(path)
        allowed, reason = self.rate_guard.allow(operation)
        if not allowed:
            raise CoupangApiError(
                429,
                {
                    "rCode": "LOCAL_RATE_LIMIT",
                    "rMessage": "Local guard blocked Coupang API call before upstream limit could be exceeded.",
                    "operation": operation,
                    "reason": reason,
                },
            )
        signed = self.sign(method, path_with_query, signed_date=signed_date)
        body = None
        final_headers = {
            "Authorization": signed.authorization,
            "Content-Type": "application/json",
        }
        if headers:
            final_headers.update(headers)

        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")

        http_request = request.Request(
            url=f"{self.domain}{path_with_query}",
            data=body,
            headers=final_headers,
            method=method.upper(),
        )
        urlopen = opener or request.urlopen

        try:
            with urlopen(http_request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            payload = json.loads(raw) if raw else {"message": exc.reason}
            if exc.code == 429:
                self.rate_guard.trip(operation)
            raise CoupangApiError(exc.code, payload) from exc

    def deeplink(self, coupang_urls: List[str]) -> Any:
        return self.request(
            "POST",
            f"{V1_PREFIX}/deeplink",
            json_body={"coupangUrls": coupang_urls},
        )

    def get_bestcategories(self, category_id: Union[int, str]) -> Any:
        return self.request("GET", f"{V1_PREFIX}/products/bestcategories/{category_id}")

    def get_goldbox(self) -> Any:
        return self.request("GET", f"{V1_PREFIX}/products/goldbox")

    def get_coupang_pl(self) -> Any:
        return self.request("GET", f"{V1_PREFIX}/products/coupangPL")

    def get_coupang_pl_brand(self, brand_id: Union[int, str]) -> Any:
        return self.request("GET", f"{V1_PREFIX}/products/coupangPL/{brand_id}")

    def search_products(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/products/search", query=params)

    def get_reco_v1(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/products/reco", query=params)

    def get_reco_v2(self, payload: Dict[str, Any]) -> Any:
        return self.request("POST", f"{V2_PREFIX}/products/reco", json_body=payload)

    def get_report_clicks(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/clicks", query=params)

    def get_report_orders(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/orders", query=params)

    def get_report_cancels(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/cancels", query=params)

    def get_report_commission(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/commission", query=params)

    def get_ad_impression_click_report(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/ads/impression-click", query=params)

    def get_ad_orders_report(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/ads/orders", query=params)

    def get_ad_cancels_report(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/ads/cancels", query=params)

    def get_ad_performance_report(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/ads/performance", query=params)

    def get_ad_commission_report(self, **params: Any) -> Any:
        return self.request("GET", f"{V1_PREFIX}/reports/ads/commission", query=params)

    @staticmethod
    def minimal_reco_v2_payload(
        *,
        site_id: str,
        site_domain: str,
        device_id: str,
        image_size: str,
        user_puid: str,
        tracking_limited: int = 0,
    ) -> Dict[str, Any]:
        return {
            "site": {
                "id": site_id,
                "domain": site_domain,
            },
            "device": {
                "id": device_id,
                "lmt": tracking_limited,
            },
            "imp": {
                "imageSize": image_size,
            },
            "user": {
                "puid": user_puid,
            },
        }

    @staticmethod
    def _build_path_with_query(path: str, *, query: Optional[Dict[str, Any]] = None) -> str:
        if not path.startswith("/"):
            raise ValueError("API path must start with '/'.")

        if not query:
            return path

        filtered = []
        for key, value in query.items():
            if value is None:
                continue
            if isinstance(value, bool):
                filtered.append((key, str(value).lower()))
            else:
                filtered.append((key, str(value)))

        query_string = parse.urlencode(filtered)
        return f"{path}?{query_string}" if query_string else path

    @staticmethod
    def _classify_operation(path: str) -> str:
        if "/products/search" in path:
            return "search"
        if path.endswith("/deeplink") or "/deeplink" in path:
            return "deeplink"
        if "/reports/" in path:
            return "report"
        return "other"
