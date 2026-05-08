from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Optional


class EventCategory(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    EVENT = "event"
    QUERY = "query"


@dataclass
class Event:
    timestamp: datetime
    category: EventCategory
    message: str


@dataclass
class QueryRecord:
    timestamp: datetime
    duration_ms: float
    row_count: int
    byte_size: int
    timed_out: bool
    output_mode: str


class EventStore:
    def __init__(self, buffer_size: int = 100):
        self._buffers: dict[EventCategory, deque[Event]] = {
            EventCategory.ERROR: deque(maxlen=buffer_size),
            EventCategory.WARNING: deque(maxlen=buffer_size),
            EventCategory.EVENT: deque(maxlen=buffer_size),
        }
        self._queries: deque[QueryRecord] = deque(maxlen=buffer_size)

    def record(self, category: EventCategory, message: str) -> None:
        if category == EventCategory.QUERY:
            return
        self._buffers[category].append(Event(
            timestamp=datetime.now(timezone.utc),
            category=category,
            message=message,
        ))

    def record_query(self, record: QueryRecord) -> None:
        self._queries.append(record)

    def get_events(self, category: EventCategory, n: int) -> list[Event]:
        buf = self._buffers.get(category, deque())
        return list(buf)[-n:]

    def get_queries(self, n: int) -> list[QueryRecord]:
        return list(self._queries)[-n:]
