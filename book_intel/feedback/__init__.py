"""Feedback loop: event emission + daily rollup + learned-signal aggregation."""

from .events import (
    BOOK_CLICK,
    BOOK_CONVERSION,
    BOOK_IMPRESSION,
    EMAIL_CLICK,
    POST_PUBLISH,
    POST_READ,
    emit,
)
from .signal_store import DailyRow, SignalStore

__all__ = [
    "BOOK_IMPRESSION",
    "BOOK_CLICK",
    "BOOK_CONVERSION",
    "POST_READ",
    "POST_PUBLISH",
    "EMAIL_CLICK",
    "emit",
    "DailyRow",
    "SignalStore",
]
