"""Unit tests for SQL safety validation, schema reflection, and seed data edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.database import (
    SQLExecutionError,
    SQLSecurityError,
    get_database_schema,
    run_analytical_query,
    validate_readonly_sql,
)


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "DROP TABLE orders;",
        "DELETE FROM customers WHERE customer_id = 1;",
        "UPDATE products SET unit_price = 0.01 WHERE product_id = 1;",
        (
            "INSERT INTO customers (first_name, last_name, email, country, signup_date) "
            "VALUES ('a','b','c','US','2026-01-01');"
        ),
        "ALTER TABLE orders ADD COLUMN hacked INTEGER;",
        "TRUNCATE TABLE order_items;",
        "CREATE TABLE pwned (id INT);",
        "REPLACE INTO products VALUES (1, 'a', 'b', 'c', 'd', 1, 1, 1, 0, '2025-01-01');",
        "SELECT * FROM orders; DROP TABLE orders;",
        "SELECT * FROM customers; DELETE FROM customers;",
        "/* comment bypass */ DROP /* inline */ TABLE customers",
        "-- leading comment\nUPDATE orders SET total_amount = 0",
        "WITH cte AS (SELECT 1) DELETE FROM orders",
        "PRAGMA writable_schema = 1;",
        "ATTACH DATABASE ':memory:' AS aux;",
        "VACUUM;",
        "   ",
        "-- only comments here",
    ],
)
def test_validate_readonly_sql_rejects_malicious_queries(
    malicious_sql: str,
    seeded_db_path: Path,
) -> None:
    """Assert all DDL, DML, administrative, and stacked SQL queries are rejected."""
    with pytest.raises(SQLSecurityError):
        validate_readonly_sql(malicious_sql)

    with pytest.raises(SQLSecurityError):
        run_analytical_query(malicious_sql, db_path=seeded_db_path)


@pytest.mark.parametrize(
    "valid_sql",
    [
        "SELECT COUNT(*) AS total FROM customers;",
        "WITH top_orders AS (SELECT * FROM orders LIMIT 5) SELECT COUNT(*) FROM top_orders",
        "SELECT status, SUM(total_amount) FROM orders WHERE status != 'cancelled' GROUP BY status",
        "SELECT 'DROP' AS literal_word_inside_quotes FROM products LIMIT 1",
    ],
)
def test_validate_readonly_sql_allows_safe_analytical_queries(
    valid_sql: str,
    seeded_db_path: Path,
) -> None:
    """Assert valid SELECT and WITH CTE queries pass validation and execute cleanly."""
    normalized = validate_readonly_sql(valid_sql)
    assert not normalized.endswith(";")

    result = run_analytical_query(valid_sql, db_path=seeded_db_path)
    assert result.row_count >= 1
    assert result.execution_time_ms >= 0.0


def test_schema_reflection_extracts_all_tables_and_foreign_keys(seeded_db_path: Path) -> None:
    """Verify get_database_schema reflects customers, orders, order_items, and products."""
    schema = get_database_schema(seeded_db_path)
    assert schema.dialect == "sqlite"

    table_map = {tbl.table_name: tbl for tbl in schema.tables}
    assert set(table_map.keys()) == {"customers", "orders", "order_items", "products"}

    # Check row counts match deterministic seed
    assert table_map["customers"].row_count == 120
    assert table_map["products"].row_count == 20
    assert table_map["orders"].row_count == 580
    assert table_map["order_items"].row_count > 1000

    # Verify primary keys and foreign keys on order_items
    oi_cols = {c.name: c for c in table_map["order_items"].columns}
    assert oi_cols["order_item_id"].is_primary_key is True
    assert oi_cols["discount_amount"].nullable is True

    oi_fks = {
        (fk.from_column, fk.target_table, fk.target_column)
        for fk in table_map["order_items"].foreign_keys
    }
    assert ("order_id", "orders", "order_id") in oi_fks
    assert ("product_id", "products", "product_id") in oi_fks


def test_seed_data_contains_required_edge_cases(seeded_db_path: Path) -> None:
    """Verify the seeded warehouse includes NULLs, cancellations, and multi-quarter dates."""
    # 1. Check NULL edge cases
    null_check = run_analytical_query(
        """
        SELECT
            SUM(CASE WHEN customer_segment IS NULL THEN 1 ELSE 0 END) AS null_segments,
            SUM(CASE WHEN phone IS NULL THEN 1 ELSE 0 END) AS null_phones
        FROM customers
        """,
        db_path=seeded_db_path,
    )
    assert null_check.rows[0]["null_segments"] > 0
    assert null_check.rows[0]["null_phones"] > 0

    # 2. Check cancelled and refunded orders exist and cancelled orders have NULL shipped_date
    status_check = run_analytical_query(
        """
        SELECT status, COUNT(*) AS cnt, COUNT(shipped_date) AS shipped_cnt
        FROM orders
        GROUP BY status
        """,
        db_path=seeded_db_path,
    )
    by_status = {row["status"]: row for row in status_check.rows}
    assert "cancelled" in by_status
    assert by_status["cancelled"]["cnt"] > 0
    assert by_status["cancelled"]["shipped_cnt"] == 0

    # 3. Check orders span multiple quarters across 2025 and 2026
    quarters_check = run_analytical_query(
        """
        SELECT DISTINCT
            strftime('%Y', order_date) || '-Q' ||
            ((CAST(strftime('%m', order_date) AS INTEGER) + 2) / 3) AS qtr
        FROM orders
        ORDER BY qtr
        """,
        db_path=seeded_db_path,
    )
    assert quarters_check.row_count >= 6


def test_run_analytical_query_raises_execution_error_on_invalid_column(
    seeded_db_path: Path,
) -> None:
    """Verify SQLite syntax or missing column errors raise SQLExecutionError."""
    with pytest.raises(SQLExecutionError):
        run_analytical_query(
            "SELECT non_existent_column_xyz FROM orders",
            db_path=seeded_db_path,
        )
