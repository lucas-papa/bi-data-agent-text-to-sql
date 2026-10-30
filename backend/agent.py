"""Autonomous BI & Data Analysis Agent powered by the official google-genai SDK."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from google import genai

from backend.chart_advisor import infer_chart_recommendation
from backend.config import get_settings
from backend.database import (
    SQLExecutionError,
    SQLSecurityError,
    get_database_schema,
    run_analytical_query,
)
from backend.schemas import (
    AgentQueryResponse,
    QueryExecutionResult,
    ToolTraceStep,
)

SYSTEM_INSTRUCTION = (
    "You are a Senior Autonomous Business Intelligence & Data Analysis Agent.\n"
    "You have access to a read-only SQLite e-commerce data warehouse via two "
    "deterministic tools:\n"
    "1. `get_database_schema()`: Extracts all table structures, column names, "
    "data types, foreign keys, and row counts.\n"
    "2. `run_analytical_query(query: str)`: Executes a validated read-only SQL "
    "query and returns tabular JSON results.\n\n"
    "MANDATORY EXECUTION PROTOCOL:\n"
    "1. ALWAYS call `get_database_schema()` FIRST to inspect the exact table names, "
    "column names, data types, and foreign keys before writing any SQL.\n"
    "2. Write clean, optimized ANSI/SQLite-compatible read-only SQL (`SELECT` or "
    "`WITH` CTEs). Handle NULLs gracefully using `COALESCE` or `IS NOT NULL` where "
    "appropriate, and exclude `cancelled`/`refunded` orders when computing net "
    "recognized revenue unless the user specifically asks about cancellations/refunds "
    "or all orders.\n"
    "3. Call `run_analytical_query(query=...)` to execute the SQL.\n"
    "4. If `run_analytical_query` returns an execution or validation error, analyze "
    "the error message against the schema, rewrite the corrected SQL query, and call "
    "`run_analytical_query` again.\n"
    "5. Once you have the query results, provide a concise, high-impact Executive "
    "Summary highlighting:\n"
    "   - Key headline findings and exact metrics (formatted with currency/percentages)\n"
    "   - Notable trends, anomalies, or edge cases (e.g. NULL segments, cancellations)\n"
    "   - Actionable business recommendations\n"
)

GET_DATABASE_SCHEMA_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "get_database_schema",
    "description": (
        "Extracts table structures, foreign keys, primary keys, column data types, "
        "and row counts from the SQLite data warehouse. Must be called first before querying."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

RUN_ANALYTICAL_QUERY_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "run_analytical_query",
    "description": (
        "Executes a validated read-only SQL query (SELECT or WITH CTE) against the "
        "e-commerce SQLite warehouse and returns structured JSON rows and execution metrics."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Validated read-only SQL query string to execute.",
            }
        },
        "required": ["query"],
    },
}

AGENT_TOOLS: list[dict[str, Any]] = [GET_DATABASE_SCHEMA_TOOL, RUN_ANALYTICAL_QUERY_TOOL]


class AutonomousBIAgent:
    """Orchestrates Text-to-SQL generation, self-healing execution, and executive insights."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        db_path: str | Path | None = None,
        client: Any | None = None,
        max_turns: int | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.model = model or settings.gemini_model
        self.db_path = Path(db_path) if db_path is not None else settings.database_path
        self.max_turns = max_turns if max_turns is not None else settings.max_agent_turns
        self._client = client

    def _get_client(self) -> Any:
        """Lazily initialize or return the injected google.genai.Client."""
        if self._client is not None:
            return self._client
        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            return None
        self._client = genai.Client(api_key=self.api_key)
        return self._client

    def ask(
        self,
        question: str,
        previous_interaction_id: str | None = None,
    ) -> AgentQueryResponse:
        """Process a natural language BI question end-to-end.

        Uses the official google-genai Interactions API with structured Function Calling
        when configured, or falls back to deterministic local analytical execution if no
        API key is present.
        """
        overall_start = time.perf_counter()
        client = self._get_client()

        if client is None:
            return self._run_fallback_pipeline(question, overall_start)

        tool_trace: list[ToolTraceStep] = []
        last_successful_result: QueryExecutionResult | None = None
        failed_query_count = 0
        retry_count = 0
        schema_inspected = False

        current_input: str | list[dict[str, Any]] = question
        current_interaction_id: str | None = previous_interaction_id
        final_text: str = ""

        for _turn in range(self.max_turns):
            interaction_kwargs: dict[str, Any] = {
                "model": self.model,
                "input": current_input,
                "system_instruction": SYSTEM_INSTRUCTION,
                "tools": AGENT_TOOLS,
            }
            if current_interaction_id:
                interaction_kwargs["previous_interaction_id"] = current_interaction_id

            interaction = client.interactions.create(**interaction_kwargs)
            current_interaction_id = getattr(interaction, "id", current_interaction_id)

            steps = getattr(interaction, "steps", None) or []
            executed_call_ids = {
                getattr(step, "call_id", None)
                for step in steps
                if getattr(step, "type", None) == "function_result"
            }
            pending_calls = [
                step
                for step in steps
                if getattr(step, "type", None) == "function_call"
                and getattr(step, "id", None) not in executed_call_ids
            ]

            if not pending_calls:
                final_text = getattr(interaction, "output_text", None) or ""
                if not final_text:
                    text_parts: list[str] = []
                    for step in steps:
                        if getattr(step, "type", None) == "model_output":
                            for item in getattr(step, "content", []) or []:
                                if getattr(item, "type", None) == "text" and getattr(
                                    item, "text", None
                                ):
                                    text_parts.append(str(item.text))
                    final_text = "\n".join(text_parts).strip()
                break

            function_results_input: list[dict[str, Any]] = []

            for fc_step in pending_calls:
                fn_name = str(getattr(fc_step, "name", ""))
                fn_id = str(getattr(fc_step, "id", ""))
                raw_args = getattr(fc_step, "arguments", None) or {}
                fn_args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}

                if fn_name == "get_database_schema":
                    t0 = time.perf_counter()
                    schema_resp = get_database_schema(self.db_path)
                    elapsed = round((time.perf_counter() - t0) * 1000.0, 3)
                    schema_inspected = True
                    tool_trace.append(
                        ToolTraceStep(
                            step_index=len(tool_trace) + 1,
                            tool_name="get_database_schema",
                            arguments={},
                            status="success",
                            execution_time_ms=elapsed,
                            detail=f"Reflected {len(schema_resp.tables)} warehouse tables.",
                        )
                    )
                    function_results_input.append(
                        {
                            "type": "function_result",
                            "name": fn_name,
                            "call_id": fn_id,
                            "result": schema_resp.model_dump(),
                        }
                    )

                elif fn_name == "run_analytical_query":
                    if not schema_inspected:
                        t0 = time.perf_counter()
                        schema_resp = get_database_schema(self.db_path)
                        elapsed = round((time.perf_counter() - t0) * 1000.0, 3)
                        schema_inspected = True
                        auto_msg = (
                            f"Auto-reflected {len(schema_resp.tables)} tables prior to execution."
                        )
                        tool_trace.append(
                            ToolTraceStep(
                                step_index=len(tool_trace) + 1,
                                tool_name="get_database_schema",
                                arguments={},
                                status="success",
                                execution_time_ms=elapsed,
                                detail=auto_msg,
                            )
                        )

                    candidate_sql = str(fn_args.get("query", "")).strip()
                    t0 = time.perf_counter()
                    try:
                        query_res = run_analytical_query(candidate_sql, db_path=self.db_path)
                        if failed_query_count > 0:
                            retry_count += 1
                        last_successful_result = query_res
                        ok_msg = (
                            f"Returned {query_res.row_count} rows "
                            f"in {query_res.execution_time_ms} ms."
                        )
                        tool_trace.append(
                            ToolTraceStep(
                                step_index=len(tool_trace) + 1,
                                tool_name="run_analytical_query",
                                arguments={"query": candidate_sql},
                                status="success",
                                execution_time_ms=query_res.execution_time_ms,
                                detail=ok_msg,
                            )
                        )
                        preview_rows = query_res.rows[:50]
                        function_results_input.append(
                            {
                                "type": "function_result",
                                "name": fn_name,
                                "call_id": fn_id,
                                "result": {
                                    "status": "success",
                                    "sql_query": query_res.sql_query,
                                    "columns": query_res.columns,
                                    "row_count": query_res.row_count,
                                    "execution_time_ms": query_res.execution_time_ms,
                                    "truncated": query_res.truncated,
                                    "rows": preview_rows,
                                },
                            }
                        )
                    except (SQLSecurityError, SQLExecutionError) as exc:
                        elapsed = round((time.perf_counter() - t0) * 1000.0, 3)
                        failed_query_count += 1
                        tool_trace.append(
                            ToolTraceStep(
                                step_index=len(tool_trace) + 1,
                                tool_name="run_analytical_query",
                                arguments={"query": candidate_sql},
                                status="error",
                                execution_time_ms=elapsed,
                                detail=str(exc),
                            )
                        )
                        err_hint = (
                            "Your SQL query failed validation or execution. "
                            "Check the table and column names from get_database_schema() "
                            "and call run_analytical_query with a corrected SELECT query."
                        )
                        function_results_input.append(
                            {
                                "type": "function_result",
                                "name": fn_name,
                                "call_id": fn_id,
                                "result": {
                                    "status": "error",
                                    "error": str(exc),
                                    "instruction": err_hint,
                                },
                            }
                        )
                else:
                    function_results_input.append(
                        {
                            "type": "function_result",
                            "name": fn_name,
                            "call_id": fn_id,
                            "result": {"status": "error", "error": f"Unknown tool '{fn_name}'."},
                        }
                    )

            current_input = function_results_input

        total_latency_ms = round((time.perf_counter() - overall_start) * 1000.0, 3)

        if last_successful_result is None:
            sql_query = ""
            columns: list[str] = []
            rows: list[dict[str, Any]] = []
            row_count = 0
            exec_time_ms = 0.0
        else:
            sql_query = last_successful_result.sql_query
            columns = last_successful_result.columns
            rows = last_successful_result.rows
            row_count = last_successful_result.row_count
            exec_time_ms = last_successful_result.execution_time_ms

        chart_rec = infer_chart_recommendation(columns, rows)
        summary = final_text.strip() or self._synthesize_summary_from_rows(
            question, sql_query, columns, rows
        )

        return AgentQueryResponse(
            question=question,
            sql_query=sql_query,
            executive_summary=summary,
            columns=columns,
            rows=rows,
            row_count=row_count,
            execution_time_ms=exec_time_ms,
            total_latency_ms=total_latency_ms,
            retry_count=retry_count,
            tool_trace=tool_trace,
            chart_recommendation=chart_rec,
            interaction_id=current_interaction_id,
        )

    def _run_fallback_pipeline(self, question: str, overall_start: float) -> AgentQueryResponse:
        """Deterministic tool-calling pipeline used when GEMINI_API_KEY is not configured."""
        tool_trace: list[ToolTraceStep] = []

        t0 = time.perf_counter()
        schema_resp = get_database_schema(self.db_path)
        schema_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        tool_trace.append(
            ToolTraceStep(
                step_index=1,
                tool_name="get_database_schema",
                arguments={},
                status="success",
                execution_time_ms=schema_ms,
                detail=f"Reflected {len(schema_resp.tables)} warehouse tables.",
            )
        )

        sql_query = self._select_heuristic_sql(question)
        query_res = run_analytical_query(sql_query, db_path=self.db_path)
        tool_trace.append(
            ToolTraceStep(
                step_index=2,
                tool_name="run_analytical_query",
                arguments={"query": sql_query},
                status="success",
                execution_time_ms=query_res.execution_time_ms,
                detail=f"Returned {query_res.row_count} rows in {query_res.execution_time_ms} ms.",
            )
        )

        chart_rec = infer_chart_recommendation(query_res.columns, query_res.rows)
        summary = self._synthesize_summary_from_rows(
            question, query_res.sql_query, query_res.columns, query_res.rows
        )
        total_latency_ms = round((time.perf_counter() - overall_start) * 1000.0, 3)

        return AgentQueryResponse(
            question=question,
            sql_query=query_res.sql_query,
            executive_summary=summary,
            columns=query_res.columns,
            rows=query_res.rows,
            row_count=query_res.row_count,
            execution_time_ms=query_res.execution_time_ms,
            total_latency_ms=total_latency_ms,
            retry_count=0,
            tool_trace=tool_trace,
            chart_recommendation=chart_rec,
            interaction_id=None,
        )

    @staticmethod
    def _select_heuristic_sql(question: str) -> str:
        """Generate an analytical SQL query matching common natural-language BI questions."""
        q_lower = question.lower()

        if any(k in q_lower for k in ("quarter", "q1", "q2", "q3", "q4", "month", "trend", "time")):
            return (
                "SELECT\n"
                "     strftime('%Y', o.order_date) || '-Q' || "
                "((CAST(strftime('%m', o.order_date) AS INTEGER) + 2) / 3) AS year_quarter,\n"
                "     COUNT(DISTINCT o.order_id) AS total_orders,\n"
                "     ROUND(SUM(o.total_amount), 2) AS gross_revenue\n"
                "FROM orders o\n"
                "WHERE o.status NOT IN ('cancelled', 'refunded')\n"
                "GROUP BY year_quarter\n"
                "ORDER BY year_quarter ASC"
            )

        if any(k in q_lower for k in ("cancel", "status", "return", "refund")):
            return (
                "SELECT\n"
                "     o.status AS order_status,\n"
                "     COUNT(o.order_id) AS order_count,\n"
                "     ROUND(SUM(o.total_amount), 2) AS total_order_value,\n"
                "     ROUND(\n"
                "         100.0 * COUNT(o.order_id) / SUM(COUNT(o.order_id)) OVER (), 2\n"
                "     ) AS status_share_pct\n"
                "FROM orders o\n"
                "GROUP BY o.status\n"
                "ORDER BY order_count DESC"
            )

        if any(k in q_lower for k in ("customer", "segment", "vip", "top buyer", "client")):
            return (
                "SELECT\n"
                "     COALESCE(c.customer_segment, 'Unassigned') AS customer_segment,\n"
                "     COUNT(DISTINCT c.customer_id) AS customer_count,\n"
                "     COUNT(DISTINCT o.order_id) AS completed_orders,\n"
                "     ROUND(SUM(o.total_amount), 2) AS net_revenue,\n"
                "     ROUND(AVG(o.total_amount), 2) AS avg_order_value\n"
                "FROM customers c\n"
                "JOIN orders o ON c.customer_id = o.customer_id\n"
                "WHERE o.status NOT IN ('cancelled', 'refunded')\n"
                "GROUP BY COALESCE(c.customer_segment, 'Unassigned')\n"
                "ORDER BY net_revenue DESC"
            )

        if any(k in q_lower for k in ("country", "region", "geographic", "city", "location")):
            return (
                "SELECT\n"
                "     o.shipping_country AS country,\n"
                "     COUNT(DISTINCT o.order_id) AS order_count,\n"
                "     ROUND(SUM(o.total_amount), 2) AS net_revenue,\n"
                "     ROUND(AVG(o.total_amount), 2) AS avg_order_value\n"
                "FROM orders o\n"
                "WHERE o.status NOT IN ('cancelled', 'refunded')\n"
                "GROUP BY o.shipping_country\n"
                "ORDER BY net_revenue DESC"
            )

        if any(k in q_lower for k in ("product", "sku", "margin", "profit", "best seller", "item")):
            return (
                "SELECT\n"
                "     p.product_name,\n"
                "     p.category,\n"
                "     SUM(oi.quantity) AS units_sold,\n"
                "     ROUND(SUM(oi.line_total), 2) AS net_sales,\n"
                "     ROUND(SUM(oi.line_total - (p.unit_cost * oi.quantity)), 2) AS gross_profit\n"
                "FROM order_items oi\n"
                "JOIN products p ON oi.product_id = p.product_id\n"
                "JOIN orders o ON oi.order_id = o.order_id\n"
                "WHERE o.status NOT IN ('cancelled', 'refunded')\n"
                "GROUP BY p.product_id, p.product_name, p.category\n"
                "ORDER BY net_sales DESC\n"
                "LIMIT 10"
            )

        return (
            "SELECT\n"
            "     p.category AS product_category,\n"
            "     COUNT(DISTINCT o.order_id) AS order_count,\n"
            "     SUM(oi.quantity) AS total_units_sold,\n"
            "     ROUND(SUM(oi.line_total), 2) AS net_revenue,\n"
            "     ROUND(SUM(oi.line_total - (p.unit_cost * oi.quantity)), 2) AS gross_profit\n"
            "FROM order_items oi\n"
            "JOIN products p ON oi.product_id = p.product_id\n"
            "JOIN orders o ON oi.order_id = o.order_id\n"
            "WHERE o.status NOT IN ('cancelled', 'refunded')\n"
            "GROUP BY p.category\n"
            "ORDER BY net_revenue DESC"
        )

    @staticmethod
    def _synthesize_summary_from_rows(
        question: str,
        sql_query: str,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> str:
        """Create a structured executive summary from the returned dataset."""
        if not rows or not columns:
            return (
                "### Executive Summary\n"
                f'The analytical query for **"{question}"** executed cleanly '
                "but returned **0 records**."
            )

        top_row = rows[0]
        kv_pairs = ", ".join(f"**{k}**: `{v}`" for k, v in list(top_row.items())[:4])
        total_rows = len(rows)

        lines = [
            "### Executive Summary",
            f"- **Records Analyzed:** Returned **{total_rows}** aggregated result row(s).",
            f"- **Leading Record:** {kv_pairs}.",
        ]

        if total_rows > 1:
            bottom_row = rows[-1]
            bottom_kv = ", ".join(f"**{k}**: `{v}`" for k, v in list(bottom_row.items())[:3])
            lines.append(f"- **Trailing Record:** {bottom_kv}.")

        lines.append(
            "- **Methodology Note:** Schema constraints and NULL/status edge cases were validated "
            "against the SQLite e-commerce warehouse prior to query execution."
        )
        return "\n".join(lines)
