"""Integration tests for FastAPI routes (/health, /schema, /query/*, /agent/ask)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_endpoint_returns_healthy_status(api_client: TestClient) -> None:
    """Verify GET /api/v1/health reports warehouse readiness."""
    response = api_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database_exists"] is True
    assert data["table_count"] == 4


def test_schema_endpoint_returns_four_warehouse_tables(api_client: TestClient) -> None:
    """Verify GET /api/v1/schema returns full table and column metadata."""
    response = api_client.get("/api/v1/schema")
    assert response.status_code == 200
    data = response.json()
    assert data["dialect"] == "sqlite"
    table_names = {t["table_name"] for t in data["tables"]}
    assert table_names == {"customers", "orders", "order_items", "products"}


def test_validate_sql_endpoint_accepts_select_and_rejects_ddl(api_client: TestClient) -> None:
    """Verify POST /api/v1/query/validate distinguishes safe SELECT from malicious DROP."""
    safe_resp = api_client.post(
        "/api/v1/query/validate",
        json={"query": "SELECT category, COUNT(*) FROM products GROUP BY category;"},
    )
    assert safe_resp.status_code == 200
    assert safe_resp.json()["is_valid"] is True

    bad_resp = api_client.post(
        "/api/v1/query/validate",
        json={"query": "DROP TABLE customers;"},
    )
    assert bad_resp.status_code == 200
    bad_data = bad_resp.json()
    assert bad_data["is_valid"] is False
    assert "DROP" in bad_data["error_message"]


def test_execute_sql_endpoint_executes_select_and_blocks_dml(api_client: TestClient) -> None:
    """Verify POST /api/v1/query/sql returns rows and chart suggestions, and returns 403 on DML."""
    ok_resp = api_client.post(
        "/api/v1/query/sql",
        json={
            "query": (
                "SELECT category, ROUND(AVG(unit_price), 2) AS avg_price "
                "FROM products GROUP BY category ORDER BY avg_price DESC"
            ),
            "max_rows": 50,
        },
    )
    assert ok_resp.status_code == 200
    body = ok_resp.json()
    assert body["execution"]["row_count"] == 5
    assert body["chart_recommendation"]["chart_type"] == "bar"

    # Attempt malicious DELETE -> must return 403 Forbidden
    forbidden_resp = api_client.post(
        "/api/v1/query/sql",
        json={"query": "DELETE FROM orders WHERE status = 'cancelled'"},
    )
    assert forbidden_resp.status_code == 403

    # Attempt broken SQL syntax -> must return 400 Bad Request
    syntax_resp = api_client.post(
        "/api/v1/query/sql",
        json={"query": "SELECT invalid_col FROM non_existent_table"},
    )
    assert syntax_resp.status_code == 400


def test_ask_agent_endpoint_returns_structured_insights(api_client: TestClient) -> None:
    """Verify POST /api/v1/agent/ask returns SQL, summary, rows, and chart suggestion."""
    response = api_client.post(
        "/api/v1/agent/ask",
        json={"question": "Compare customer segments by net revenue and average order value."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["question"] == "Compare customer segments by net revenue and average order value."
    assert "SELECT" in data["sql_query"]
    assert data["row_count"] >= 3
    assert "Executive Summary" in data["executive_summary"]
    assert data["chart_recommendation"]["chart_type"] in ("bar", "pie")
    assert len(data["tool_trace"]) >= 2
