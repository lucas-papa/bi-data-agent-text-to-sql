"""Strict Pydantic v2 schemas for the BI Data Agent API and tool interfaces."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ColumnInfo(BaseModel):
    """Metadata describing a single database column."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Column identifier")
    data_type: str = Field(..., description="Declared SQLite column data type")
    nullable: bool = Field(..., description="Whether the column permits NULL values")
    is_primary_key: bool = Field(..., description="Whether the column is part of the primary key")
    default_value: str | None = Field(default=None, description="Default expression if defined")


class ForeignKeyInfo(BaseModel):
    """Metadata describing a foreign key constraint on a table."""

    model_config = ConfigDict(extra="forbid")

    from_column: str = Field(..., description="Local column referencing the target table")
    target_table: str = Field(..., description="Referenced parent table name")
    target_column: str = Field(..., description="Referenced parent column name")


class TableSchema(BaseModel):
    """Complete structural representation of a single database table."""

    model_config = ConfigDict(extra="forbid")

    table_name: str = Field(..., description="Database table name")
    row_count: int = Field(..., ge=0, description="Total number of rows currently in the table")
    columns: list[ColumnInfo] = Field(default_factory=list, description="Ordered table columns")
    foreign_keys: list[ForeignKeyInfo] = Field(
        default_factory=list, description="Foreign key relationships"
    )


class DatabaseSchemaResponse(BaseModel):
    """Full database schema returned by get_database_schema()."""

    model_config = ConfigDict(extra="forbid")

    dialect: str = Field(default="sqlite", description="SQL dialect of the warehouse")
    database_path: str = Field(..., description="Path to the SQLite warehouse file")
    tables: list[TableSchema] = Field(..., description="All user tables in the warehouse")


class ChartRecommendation(BaseModel):
    """Automated Plotly visualization recommendation derived from query results."""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "scatter", "pie", "metric", "none"] = Field(
        ..., description="Recommended Plotly chart type"
    )
    x_column: str | None = Field(default=None, description="Column mapped to X axis or category")
    y_column: str | None = Field(default=None, description="Column mapped to Y axis or value")
    color_column: str | None = Field(
        default=None, description="Optional grouping/series dimension column"
    )
    title: str = Field(..., description="Suggested chart title")
    reasoning: str = Field(..., description="Explanation of why this chart fits the data shape")


class QueryExecutionResult(BaseModel):
    """Structured result returned by run_analytical_query()."""

    model_config = ConfigDict(extra="forbid")

    sql_query: str = Field(..., description="The validated SQL query that was executed")
    columns: list[str] = Field(default_factory=list, description="Result set column names")
    rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Tabular rows serialized as dictionaries"
    )
    row_count: int = Field(..., ge=0, description="Number of rows returned")
    execution_time_ms: float = Field(..., ge=0.0, description="SQLite query execution time in ms")
    truncated: bool = Field(
        default=False, description="True if results exceeded max_rows and were truncated"
    )


class DirectSQLQueryRequest(BaseModel):
    """Request payload for direct read-only SQL execution."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=10000, description="Read-only SQL query")
    max_rows: int = Field(default=500, ge=1, le=5000, description="Maximum rows to return")

    @field_validator("query")
    @classmethod
    def strip_and_require_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("SQL query must not be empty or whitespace only.")
        return cleaned


class DirectSQLQueryResponse(BaseModel):
    """Response payload for direct read-only SQL execution with chart suggestion."""

    model_config = ConfigDict(extra="forbid")

    execution: QueryExecutionResult
    chart_recommendation: ChartRecommendation


class SQLValidateRequest(BaseModel):
    """Request payload to validate whether a SQL string is safe and read-only."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=10000)


class SQLValidateResponse(BaseModel):
    """Validation outcome for a candidate SQL string."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    normalized_query: str | None = None
    error_message: str | None = None


class ToolTraceStep(BaseModel):
    """Audit record of a single deterministic tool call made by the BI agent."""

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(..., ge=1)
    tool_name: str = Field(..., description="Name of the invoked tool")
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["success", "error"] = Field(...)
    execution_time_ms: float = Field(default=0.0, ge=0.0)
    detail: str = Field(default="", description="Short summary or error message")


class AgentQueryRequest(BaseModel):
    """Request payload for natural-language BI analysis."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        ...,
        min_length=2,
        max_length=2000,
        description="Natural language business intelligence question",
    )
    previous_interaction_id: str | None = Field(
        default=None,
        description="Optional Gemini Interaction ID for multi-turn conversational follow-ups",
    )

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Question must contain at least 2 non-whitespace characters.")
        return cleaned


class AgentQueryResponse(BaseModel):
    """Comprehensive response returned by the Autonomous BI Agent."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="Original natural language question")
    sql_query: str = Field(..., description="Final validated SQL query executed by the agent")
    executive_summary: str = Field(
        ..., description="Synthesized executive summary and key business insights"
    )
    columns: list[str] = Field(default_factory=list, description="Result column names")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="Result tabular rows")
    row_count: int = Field(default=0, ge=0, description="Number of returned rows")
    execution_time_ms: float = Field(
        default=0.0, ge=0.0, description="Execution time of the final SQL query in ms"
    )
    total_latency_ms: float = Field(
        default=0.0, ge=0.0, description="End-to-end agent turn latency in ms"
    )
    retry_count: int = Field(
        default=0, ge=0, description="Number of SQL self-correction retries performed"
    )
    tool_trace: list[ToolTraceStep] = Field(
        default_factory=list, description="Ordered trace of agent tool invocations"
    )
    chart_recommendation: ChartRecommendation = Field(
        ..., description="Automated Plotly visualization recommendation"
    )
    interaction_id: str | None = Field(
        default=None, description="Gemini Interaction ID for stateful follow-up queries"
    )


class HealthResponse(BaseModel):
    """API health and warehouse readiness check response."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "degraded"]
    version: str
    database_exists: bool
    table_count: int
    gemini_configured: bool
    model: str
