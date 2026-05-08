from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest

from postgres_mcp.event_store import EventCategory
from postgres_mcp.event_store import EventStore
from postgres_mcp.event_store import QueryRecord
from postgres_mcp.sql.sql_driver import ConnState
from postgres_mcp.sql.sql_driver import DbConnPool


@pytest.fixture
def setup_server_globals():
    import postgres_mcp.server as srv
    original_db = srv.db_connection
    original_es = srv.event_store

    pool = DbConnPool(connection_url="postgresql://test:****@localhost/db")
    pool._state = ConnState.CONNECTED
    pool._is_valid = True
    store = EventStore(buffer_size=50)

    srv.db_connection = pool
    srv.event_store = store
    yield pool, store
    srv.db_connection = original_db
    srv.event_store = original_es


class TestStatusMinimal:
    @pytest.mark.asyncio
    async def test_returns_current_state(self, setup_server_globals):
        pool, store = setup_server_globals
        from postgres_mcp.server import status
        result = await status()
        assert len(result) == 1
        text = result[0].text
        assert "connected" in text

    @pytest.mark.asyncio
    async def test_error_state(self, setup_server_globals):
        pool, store = setup_server_globals
        pool._state = ConnState.ERROR
        from postgres_mcp.server import status
        result = await status()
        assert "error" in result[0].text


class TestStatusWithParams:
    @pytest.mark.asyncio
    async def test_with_errors(self, setup_server_globals):
        pool, store = setup_server_globals
        store.record(EventCategory.ERROR, "connection dropped")
        store.record(EventCategory.ERROR, "timeout exceeded")
        from postgres_mcp.server import status
        result = await status(errors=2)
        text = result[0].text
        assert "connection dropped" in text
        assert "timeout exceeded" in text

    @pytest.mark.asyncio
    async def test_with_warnings(self, setup_server_globals):
        pool, store = setup_server_globals
        store.record(EventCategory.WARNING, "slow query detected")
        from postgres_mcp.server import status
        result = await status(warnings=1)
        assert "slow query detected" in result[0].text

    @pytest.mark.asyncio
    async def test_with_events(self, setup_server_globals):
        pool, store = setup_server_globals
        store.record(EventCategory.EVENT, "reconnected")
        from postgres_mcp.server import status
        result = await status(events=1)
        assert "reconnected" in result[0].text

    @pytest.mark.asyncio
    async def test_with_metadata(self, setup_server_globals):
        pool, store = setup_server_globals
        pool._reconnect_count = 3
        from postgres_mcp.server import status
        result = await status(metadata=True)
        text = result[0].text
        assert "reconnect_count" in text
        assert "3" in text

    @pytest.mark.asyncio
    async def test_with_queries(self, setup_server_globals):
        pool, store = setup_server_globals
        store.record_query(QueryRecord(
            timestamp=datetime.now(timezone.utc),
            duration_ms=42.5,
            row_count=100,
            byte_size=2048,
            timed_out=False,
            output_mode="inline",
        ))
        from postgres_mcp.server import status
        result = await status(queries=1)
        text = result[0].text
        assert "42.5" in text
        assert "inline" in text


class TestStatusNoCredentials:
    @pytest.mark.asyncio
    async def test_no_connection_string_in_output(self, setup_server_globals):
        pool, store = setup_server_globals
        pool.connection_url = "postgresql://user:secretpass@host/db"
        pool._last_error = "failed at postgresql://user:secretpass@host/db"
        from postgres_mcp.server import status
        result = await status(errors=5, metadata=True)
        text = result[0].text
        assert "secretpass" not in text
