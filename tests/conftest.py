import asyncio
from typing import Generator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from k8s_fixtures import create_k8s_postgres
from utils import create_postgres_container

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver
from postgres_mcp.sql import reset_postgres_version_cache

load_dotenv()


# Define a custom event loop policy that handles cleanup better
@pytest.fixture(scope="session")
def event_loop_policy():
    """Create and return a custom event loop policy for tests."""
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="class", params=["postgres:12", "postgres:15", "postgres:16"])
def test_postgres_connection_string(request) -> Generator[tuple[str, str], None, None]:
    yield from create_postgres_container(request.param)


@pytest.fixture(autouse=True)
def reset_pg_version_cache():
    """Reset the PostgreSQL version cache before each test."""
    reset_postgres_version_cache()
    yield


@pytest.fixture(scope="session")
def k8s_pg_connection_string() -> Generator[tuple[str, str], None, None]:
    yield from create_k8s_postgres()


@pytest_asyncio.fixture
async def k8s_sql_driver(k8s_pg_connection_string) -> SqlDriver:
    connection_string, _version = k8s_pg_connection_string
    pool = DbConnPool(
        connection_url=connection_string,
        reconnect_config=ReconnectConfig(initial_delay=0.5, max_delay=5.0),
    )
    await pool.pool_connect()
    driver = SqlDriver(conn=pool)
    yield driver
    await pool.close()


@pytest_asyncio.fixture
async def k8s_pg_with_test_data(k8s_sql_driver) -> SqlDriver:
    await k8s_sql_driver.execute_query(
        "CREATE TABLE IF NOT EXISTS test_large (id int, value text, amount numeric(12,2))"
    )
    await k8s_sql_driver.execute_query(
        "TRUNCATE test_large"
    )
    await k8s_sql_driver.execute_query(
        "INSERT INTO test_large SELECT g, 'row_' || g, (random() * 10000)::numeric(12,2) "
        "FROM generate_series(1, 500000) g"
    )
    yield k8s_sql_driver
    await k8s_sql_driver.execute_query("DROP TABLE IF EXISTS test_large")
