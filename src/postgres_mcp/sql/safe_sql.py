from __future__ import annotations

from typing import Any
from typing import Optional

from psycopg.sql import SQL
from psycopg.sql import Composable
from psycopg.sql import Literal
from typing_extensions import LiteralString

from .sql_driver import SqlDriver


class SafeSqlDriver(SqlDriver):
    """SQL parameterization utilities.

    Originally provided SQL validation via pglast; that path was removed
    in the pgmcp-fluid fork (access control is the DB role's job). The
    static helper methods remain because 15+ DBA-tool call sites depend
    on them for safe parameter interpolation via psycopg.sql.
    """

    def __init__(self, sql_driver: SqlDriver, timeout: float | None = None):
        self.sql_driver = sql_driver
        self.timeout = timeout

    @staticmethod
    def sql_to_query(sql: Composable) -> str:
        return sql.as_string()

    @staticmethod
    def param_sql_to_query(query: str, params: list[Any]) -> str:
        sql_params = [p if isinstance(p, Composable) else Literal(p) for p in params]
        return SafeSqlDriver.sql_to_query(
            SQL(query).format(*sql_params)  # type: ignore
        )

    @staticmethod
    async def execute_param_query(
        sql_driver: SqlDriver,
        query: LiteralString,
        params: list[Any] | None = None,
    ) -> Optional[list[SqlDriver.RowResult]]:
        if params:
            query_params = SafeSqlDriver.param_sql_to_query(query, params)
            return await sql_driver.execute_query(query_params)  # type: ignore
        else:
            return await sql_driver.execute_query(query)
