import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import pytest_asyncio

from postgres_mcp.server import explain_query


@pytest_asyncio.fixture
async def mock_safe_sql_driver():
    driver = MagicMock()
    return driver


@pytest.fixture
def mock_explain_plan_tool():
    tool = MagicMock()
    tool.explain = AsyncMock()
    tool.explain_analyze = AsyncMock()
    tool.explain_with_hypothetical_indexes = AsyncMock()
    return tool


class MockCell:
    def __init__(self, data):
        self.cells = data


@pytest.mark.asyncio
async def test_explain_query_integration():
    result_text = json.dumps({"Plan": {"Node Type": "Seq Scan"}})
    mock_text_result = MagicMock()
    mock_text_result.text = result_text

    with patch("postgres_mcp.server.format_text_response", return_value=[mock_text_result]):
        with patch("postgres_mcp.server.SqlDriver"):
            with patch("postgres_mcp.server.ExplainPlanTool"):
                result = await explain_query("SELECT * FROM users", hypothetical_indexes=None)

                assert isinstance(result, list)
                assert len(result) == 1
                assert result[0].text == result_text


@pytest.mark.asyncio
async def test_explain_query_with_analyze_integration():
    result_text = json.dumps({"Plan": {"Node Type": "Seq Scan"}, "Execution Time": 1.23})
    mock_text_result = MagicMock()
    mock_text_result.text = result_text

    with patch("postgres_mcp.server.format_text_response", return_value=[mock_text_result]):
        with patch("postgres_mcp.server.SqlDriver"):
            with patch("postgres_mcp.server.ExplainPlanTool"):
                result = await explain_query("SELECT * FROM users", analyze=True, hypothetical_indexes=None)

                assert isinstance(result, list)
                assert len(result) == 1
                assert result[0].text == result_text


@pytest.mark.asyncio
async def test_explain_query_with_hypothetical_indexes_integration():
    result_text = json.dumps({"Plan": {"Node Type": "Index Scan"}})
    mock_text_result = MagicMock()
    mock_text_result.text = result_text

    test_sql = "SELECT * FROM users WHERE email = 'test@example.com'"
    test_indexes = [{"table": "users", "columns": ["email"]}]

    with patch("postgres_mcp.server.format_text_response", return_value=[mock_text_result]):
        mock_safe_driver = MagicMock()
        mock_execute_query = AsyncMock(return_value=[MockCell({"exists": 1})])
        mock_safe_driver.execute_query = mock_execute_query

        with patch("postgres_mcp.server.SqlDriver", return_value=mock_safe_driver):
            with patch("postgres_mcp.server.ExplainPlanTool"):
                result = await explain_query(test_sql, hypothetical_indexes=test_indexes)

                assert isinstance(result, list)
                assert len(result) == 1
                assert result[0].text == result_text


@pytest.mark.asyncio
async def test_explain_query_missing_hypopg_integration():
    missing_ext_message = "extension is required"
    mock_text_result = MagicMock()
    mock_text_result.text = missing_ext_message

    test_sql = "SELECT * FROM users WHERE email = 'test@example.com'"
    test_indexes = [{"table": "users", "columns": ["email"]}]

    mock_safe_driver = MagicMock()
    mock_execute_query = AsyncMock(return_value=[])
    mock_safe_driver.execute_query = mock_execute_query

    with patch("postgres_mcp.server.format_text_response", return_value=[mock_text_result]):
        with patch("postgres_mcp.server.SqlDriver", return_value=mock_safe_driver):
            with patch("postgres_mcp.server.ExplainPlanTool"):
                result = await explain_query(test_sql, hypothetical_indexes=test_indexes)

                assert isinstance(result, list)
                assert len(result) == 1
                assert missing_ext_message in result[0].text


@pytest.mark.asyncio
async def test_explain_query_error_handling_integration():
    error_message = "Error executing query"
    mock_text_result = MagicMock()
    mock_text_result.text = f"Error: {error_message}"

    with patch("postgres_mcp.server.format_error_response", return_value=[mock_text_result]):
        with patch(
            "postgres_mcp.server.SqlDriver",
            side_effect=Exception(error_message),
        ):
            result = await explain_query("INVALID SQL")

            assert isinstance(result, list)
            assert len(result) == 1
            assert error_message in result[0].text
