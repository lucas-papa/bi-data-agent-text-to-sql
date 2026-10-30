"""Read-only SQLite data warehouse access, SQL validation, and schema reflection."""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.schemas import (
    ColumnInfo,
    DatabaseSchemaResponse,
    ForeignKeyInfo,
    QueryExecutionResult,
    TableSchema,
)
from scripts.seed_db import seed_database


class SQLSecurityError(ValueError):
    """Raised when a SQL statement violates read-only or multi-statement constraints."""


class SQLExecutionError(RuntimeError):
    """Raised when SQLite fails to compile or execute a query."""


FORBIDDEN_SQL_KEYWORDS: frozenset[str] = frozenset(
    {
        "DROP",
        "DELETE",
        "UPDATE",
        "INSERT",
        "ALTER",
        "CREATE",
        "REPLACE",
        "TRUNCATE",
        "ATTACH",
        "DETACH",
        "PRAGMA",
        "VACUUM",
        "REINDEX",
        "GRANT",
        "REVOKE",
        "MERGE",
        "UPSERT",
        "EXEC",
        "EXECUTE",
    }
)

ALLOWED_START_KEYWORDS: frozenset[str] = frozenset({"SELECT", "WITH", "EXPLAIN", "VALUES"})

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\r\n]*")
_SINGLE_QUOTED_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_DOUBLE_QUOTED_IDENTIFIER_RE = re.compile(r'"(?:""|[^"])*"')
_WORD_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def _strip_sql_comments_and_literals(query: str) -> str:
    """Remove SQL comments and mask string literals to allow safe token inspection."""
    no_block = _BLOCK_COMMENT_RE.sub(" ", query)
    no_comments = _LINE_COMMENT_RE.sub(" ", no_block)
    no_single_quotes = _SINGLE_QUOTED_LITERAL_RE.sub("''", no_comments)
    return _DOUBLE_QUOTED_IDENTIFIER_RE.sub('""', no_single_quotes)


def validate_readonly_sql(query: str) -> str:
    """Validate that a SQL query is strictly read-only and contains a single statement.

    Prohibits DDL/DML operations (DROP, DELETE, UPDATE, INSERT, ALTER, CREATE,
    TRUNCATE, REPLACE, ATTACH, DETACH, PRAGMA, VACUUM) and stacked queries.

    Args:
        query: Raw SQL query string.

    Returns:
        Normalized SQL query string with trailing semicolon removed.

    Raises:
        SQLSecurityError: If the query is empty, contains multiple statements,
            does not start with an allowed read-only keyword, or contains any
            prohibited DDL/DML keyword.
    """
    if not isinstance(query, str) or not query.strip():
        raise SQLSecurityError("SQL query cannot be empty.")

    cleaned_raw = query.strip()
    sanitized = _strip_sql_comments_and_literals(cleaned_raw).strip()

    if not sanitized:
        raise SQLSecurityError("SQL query contains only comments or whitespace.")

    # Strip optional single trailing semicolon
    if sanitized.endswith(";"):
        sanitized = sanitized[:-1].strip()
    if cleaned_raw.endswith(";"):
        cleaned_raw = cleaned_raw[:-1].strip()

    # Reject multi-statement / stacked queries
    if ";" in sanitized:
        raise SQLSecurityError(
            "Multiple SQL statements (stacked queries with ';') are strictly prohibited."
        )

    tokens = [tok.upper() for tok in _WORD_TOKEN_RE.findall(sanitized)]
    if not tokens:
        raise SQLSecurityError("No valid SQL tokens found in query.")

    # Check for forbidden DDL/DML keywords anywhere in the token stream
    for token in tokens:
        if token in FORBIDDEN_SQL_KEYWORDS:
            raise SQLSecurityError(
                f"Prohibited DDL/DML or administrative operation detected: '{token}'. "
                "Only read-only analytical queries are permitted."
            )

    first_keyword = tokens[0]
    if first_keyword not in ALLOWED_START_KEYWORDS:
        raise SQLSecurityError(
            f"Query must begin with a read-only statement ({', '.join(sorted(ALLOWED_START_KEYWORDS))}), "
            f"got '{first_keyword}'."
        )

    return cleaned_raw


def _strict_query_authorizer(
    action_code: int,
    param1: str | None,
    param2: str | None,
    db_name: str | None,
    trigger_name: str | None,
) -> int:
    """SQLite engine-level authorizer callback enforcing read-only query execution."""
    allowed_actions = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }
    recursive_code = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
    allowed_actions.add(recursive_code)

    if action_code in allowed_actions:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def ensure_warehouse_exists(db_path: str | Path | None = None) -> Path:
    """Ensure the SQLite warehouse file exists; seed it automatically if missing."""
    resolved = Path(db_path) if db_path is not None else get_settings().database_path
    if not resolved.exists() or resolved.stat().st_size == 0:
        seed_database(resolved)
    return resolved.resolve()


def _open_readonly_connection(db_path: Path, enforce_authorizer: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection in URI read-only mode with optional authorizer."""
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    if enforce_authorizer:
        conn.set_authorizer(_strict_query_authorizer)
    return conn


def get_database_schema(db_path: str | Path | None = None) -> DatabaseSchemaResponse:
    """Extract table structures, primary keys, foreign keys, column data types, and row counts.

    This function serves both the REST API and the deterministic Gemini agent tool.
    """
    target_path = ensure_warehouse_exists(db_path)
    tables: list[TableSchema] = []

    conn = _open_readonly_connection(target_path, enforce_authorizer=False)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name;
            """
        )
        table_names = [ str(row["name"]) for row in cursor.fetchall() ]

        for table_name in table_names:
            # Column metadata via PRAGMA table_info
            cursor.execute(f'PRAGMA table_info("{table_name}");')
            columns: list[ColumnInfo] = []
            for col_row in cursor.fetchall():
                columns.append(
                    ColumnInfo(
                        name=str(col_row["name"]),
                        data_type=str(col_row["type"] or "TEXT"),
                        nullable=not bool(col_row["notnull"]),
                        is_primary_key=bool(col_row["pk"]),
                        default_value=(
                            str(col_row["dflt_value"])
                            if col_row["dflt_value"] is not None
                            else None
                        ),
                    )
                )

            # Foreign key metadata via PRAGMA foreign_key_list
            cursor.execute(f'PRAGMA foreign_key_list("{table_name}");')
            foreign_keys: list[ForeignKeyInfo] = []
            for fk_row in cursor.fetchall():
                foreign_keys.append(
                    ForeignKeyInfo(
                        from_column=str(fk_row["from"]),
                        target_table=str(fk_row["table"]),
                        target_column=str(fk_row["to"]),
                    )
                )

            # Row count
            cursor.execute(f'SELECT COUNT(*) AS cnt FROM "{table_name}";')
            count_row = cursor.fetchone()
            row_count = int(count_row["cnt"]) if count_row is not None else 0

            tables.append(
                TableSchema(
                    table_name=table_name,
                    row_count=row_count,
                    columns=columns,
                    foreign_keys=foreign_keys,
                )
            )
    finally:
        conn.close()

    return DatabaseSchemaResponse(
        dialect="sqlite",
        database_path=str(target_path),
        tables=tables,
    )


def run_analytical_query(
    query: str,
    db_path: str | Path | None = None,
    max_rows: int | None = None,
) -> QueryExecutionResult:
    """Execute a validated read-only SQL query and return structured tabular data.

    Enforces read-only constraints at three levels:
    1. Token/lexical SQL validation (validate_readonly_sql)
    2. SQLite URI read-only file mode (?mode=ro) + PRAGMA query_only = ON
    3. SQLite C-engine authorizer callback (_strict_query_authorizer)

    Args:
        query: SQL query to validate and execute.
        db_path: Optional override path to the SQLite database.
        max_rows: Maximum number of rows to fetch before truncating.

    Returns:
        QueryExecutionResult containing columns, rows, row_count, and execution_time_ms.

    Raises:
        SQLSecurityError: If query contains DDL/DML or unsafe statements.
        SQLExecutionError: If SQLite fails to execute the query.
    """
    validated_sql = validate_readonly_sql(query)
    target_path = ensure_warehouse_exists(db_path)
    effective_max_rows = max_rows if max_rows is not None else get_settings().max_query_rows

    conn = _open_readonly_connection(target_path, enforce_authorizer=True)
    start_time = time.perf_counter()
    try:
        cursor = conn.cursor()
        cursor.execute(validated_sql)
        raw_rows = cursor.fetchmany(effective_max_rows + 1)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 3)

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        truncated = len(raw_rows) > effective_max_rows
        sliced_rows = raw_rows[:effective_max_rows]

        serialized_rows: list[dict[str, Any]] = [
            {col: row[idx] for idx, col in enumerate(columns)} for row in sliced_rows
        ]

        return QueryExecutionResult(
            sql_query=validated_sql,
            columns=columns,
            rows=serialized_rows,
            row_count=len(serialized_rows),
            execution_time_ms=elapsed_ms,
            truncated=truncated,
        )
    except sqlite3.DatabaseError as exc:
        msg = str(exc)
        if "not authorized" in msg.lower() or "attempt to write a readonly database" in msg.lower():
            raise SQLSecurityError(f"Read-only database policy blocked operation: {msg}") from exc
        raise SQLExecutionError(f"SQLite execution error: {msg}") from exc
    finally:
        conn.close()
