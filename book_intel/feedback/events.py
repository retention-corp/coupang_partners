"""Event taxonomy + single-call emitter.

Every event goes into `analytics.AnalyticsStore.events` with `metadata_json` carrying
the per-event fields; no schema changes to the analytics DB are required. Keeping the
event type names + emit call in one module means rollup readers and signal-store
writers can share constants without crosswise imports.

The emitter swallows failures by design — a missing or misbehaving analytics store
must not break the hot request path. Errors are logged at debug level only.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

LOGGER = logging.getLogger("book_intel.feedback.events")

# Impression: the book appeared in a response the user saw.
BOOK_IMPRESSION = "book_impression"

# Click: user redirected through /s/{slug} to a Coupang affiliate URL.
BOOK_CLICK = "book_click"

# Conversion: Coupang Partners reported commission for an affiliate click.
BOOK_CONVERSION = "book_conversion"

# Post read: reader engaged with a published blog post / newsletter article.
POST_READ = "post_read"

# Post publish: a blog post was generated + published (tags prompt_variant / tier).
POST_PUBLISH = "post_publish"

# Email click: newsletter link clicked.
EMAIL_CLICK = "email_click"


class _Recorder(Protocol):
    def record_event(
        self,
        *,
        event_type: str,
        query_id: str | None = None,
        recommendation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...


def emit(
    store: _Recorder | None,
    event_type: str,
    *,
    query_id: str | None = None,
    recommendation_id: str | None = None,
    **metadata: Any,
) -> str | None:
    """Fire-and-forget event emission. Returns the event_id on success, None on failure."""

    if store is None or not event_type:
        return None
    cleaned = {k: v for k, v in metadata.items() if v is not None}
    try:
        return store.record_event(
            event_type=event_type,
            query_id=query_id,
            recommendation_id=recommendation_id,
            metadata=cleaned or None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.debug("emit %s failed: %s", event_type, exc)
        return None


__all__ = [
    "BOOK_IMPRESSION",
    "BOOK_CLICK",
    "BOOK_CONVERSION",
    "POST_READ",
    "POST_PUBLISH",
    "EMAIL_CLICK",
    "emit",
]
