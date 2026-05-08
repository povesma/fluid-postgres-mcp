from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from postgres_mcp.event_store import Event
from postgres_mcp.event_store import EventCategory
from postgres_mcp.event_store import EventStore
from postgres_mcp.event_store import QueryRecord


class TestRingBuffer:
    def test_wraps_when_full(self):
        store = EventStore(buffer_size=3)
        for i in range(5):
            store.record(EventCategory.ERROR, f"err-{i}")
        events = store.get_events(EventCategory.ERROR, 10)
        assert len(events) == 3
        assert events[0].message == "err-2"
        assert events[2].message == "err-4"

    def test_per_category_independence(self):
        store = EventStore(buffer_size=10)
        store.record(EventCategory.ERROR, "err")
        store.record(EventCategory.WARNING, "warn")
        store.record(EventCategory.EVENT, "evt")
        assert len(store.get_events(EventCategory.ERROR, 10)) == 1
        assert len(store.get_events(EventCategory.WARNING, 10)) == 1
        assert len(store.get_events(EventCategory.EVENT, 10)) == 1

    def test_get_events_returns_most_recent_n(self):
        store = EventStore(buffer_size=10)
        for i in range(7):
            store.record(EventCategory.EVENT, f"e-{i}")
        events = store.get_events(EventCategory.EVENT, 3)
        assert len(events) == 3
        assert events[0].message == "e-4"
        assert events[2].message == "e-6"

    def test_get_events_returns_all_when_fewer_than_n(self):
        store = EventStore(buffer_size=10)
        store.record(EventCategory.ERROR, "only-one")
        events = store.get_events(EventCategory.ERROR, 5)
        assert len(events) == 1


class TestQueryRecords:
    def test_record_and_retrieve_query(self):
        store = EventStore(buffer_size=10)
        qr = QueryRecord(
            timestamp=datetime.now(timezone.utc),
            duration_ms=42.5,
            row_count=100,
            byte_size=2048,
            timed_out=False,
            output_mode="inline",
        )
        store.record_query(qr)
        queries = store.get_queries(5)
        assert len(queries) == 1
        assert queries[0].duration_ms == 42.5
        assert queries[0].output_mode == "inline"

    def test_query_buffer_wraps(self):
        store = EventStore(buffer_size=2)
        for i in range(4):
            store.record_query(QueryRecord(
                timestamp=datetime.now(timezone.utc),
                duration_ms=float(i),
                row_count=i,
                byte_size=i * 100,
                timed_out=False,
                output_mode="file",
            ))
        queries = store.get_queries(10)
        assert len(queries) == 2
        assert queries[0].duration_ms == 2.0
        assert queries[1].duration_ms == 3.0


class TestConfigurableBufferSize:
    def test_custom_buffer_size(self):
        store = EventStore(buffer_size=5)
        for i in range(10):
            store.record(EventCategory.ERROR, f"e-{i}")
        assert len(store.get_events(EventCategory.ERROR, 100)) == 5


class TestEventTimestamp:
    def test_events_have_utc_timestamps(self):
        store = EventStore()
        store.record(EventCategory.EVENT, "test")
        events = store.get_events(EventCategory.EVENT, 1)
        assert events[0].timestamp.tzinfo is not None
