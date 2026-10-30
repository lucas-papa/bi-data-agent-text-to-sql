"""FastAPI application entry point for the Autonomous BI & Data Analysis Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from backend.agent import AutonomousBIAgent
from backend.chart_advisor import infer_chart_recommendation
from backend.config import Settings, get_settings
from backend.database import (
    SQLExecutionError,
    SQLSecurityError,
    ensure_warehouse_exists,
    get_database_schema,
    run_analytical_query,
    validate_readonly_sql,
)
from backend.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    DatabaseSchemaResponse,
    DirectSQLQueryRequest,
    DirectSQLQueryResponse,
    HealthResponse,
    SQLValidateRequest,
    SQLValidateResponse,
)

SettingsDep = Annotated[Settings, Depends(get_settings)]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Ensure the SQLite data warehouse exists and is seeded on application startup."""
    ensure_warehouse_exists()
    yield


app = FastAPI(
    title="Autonomous BI & Data Analysis Agent API",
    description=(
        "Production-grade Text-to-SQL and Executive Insights API powered by FastAPI, "
        "SQLite read-only warehouse guardrails, and the official Google GenAI SDK."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def get_bi_agent(
    settings: SettingsDep,
    x_gemini_api_key: Annotated[str | None, Header(alias="X-Gemini-Api-Key")] = None,
) -> AutonomousBIAgent:
    """Dependency provider for AutonomousBIAgent supporting optional header key override."""
    effective_key = x_gemini_api_key or settings.gemini_api_key
    return AutonomousBIAgent(
        api_key=effective_key,
        model=settings.gemini_model,
        db_path=settings.database_path,
        max_turns=settings.max_agent_turns,
    )


AgentDep = Annotated[AutonomousBIAgent, Depends(get_bi_agent)]


@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health & readiness probe",
)
def health_check(settings: SettingsDep) -> HealthResponse:
    """Return service health, SQLite warehouse status, and Gemini SDK readiness."""
    db_path = ensure_warehouse_exists(settings.database_path)
    schema = get_database_schema(db_path)
    gemini_ready = bool(
        settings.gemini_api_key and settings.gemini_api_key != "your_gemini_api_key_here"
    )
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        database_exists=db_path.exists(),
        table_count=len(schema.tables),
        gemini_configured=gemini_ready,
        model=settings.gemini_model,
    )


@app.get(
    "/api/v1/schema",
    response_model=DatabaseSchemaResponse,
    tags=["Warehouse"],
    summary="Inspect database schema",
)
def read_schema(settings: SettingsDep) -> DatabaseSchemaResponse:
    """Extract all tables, columns, data types, foreign keys, and row counts."""
    return get_database_schema(settings.database_path)


@app.post(
    "/api/v1/query/validate",
    response_model=SQLValidateResponse,
    tags=["Warehouse"],
    summary="Validate read-only SQL safety",
)
def validate_sql_endpoint(payload: SQLValidateRequest) -> SQLValidateResponse:
    """Check whether a candidate SQL query passes read-only security validation."""
    try:
        normalized = validate_readonly_sql(payload.query)
        return SQLValidateResponse(
            is_valid=True,
            normalized_query=normalized,
            error_message=None,
        )
    except SQLSecurityError as exc:
        return SQLValidateResponse(
            is_valid=False,
            normalized_query=None,
            error_message=str(exc),
        )


@app.post(
    "/api/v1/query/sql",
    response_model=DirectSQLQueryResponse,
    tags=["Warehouse"],
    summary="Execute a validated read-only SQL query",
)
def execute_sql_endpoint(
    payload: DirectSQLQueryRequest,
    settings: SettingsDep,
) -> DirectSQLQueryResponse:
    """Execute a read-only analytical SQL query and return results with a chart suggestion."""
    try:
        execution = run_analytical_query(
            query=payload.query,
            db_path=settings.database_path,
            max_rows=payload.max_rows,
        )
    except SQLSecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except SQLExecutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    chart_rec = infer_chart_recommendation(execution.columns, execution.rows)
    return DirectSQLQueryResponse(
        execution=execution,
        chart_recommendation=chart_rec,
    )


@app.post(
    "/api/v1/agent/ask",
    response_model=AgentQueryResponse,
    tags=["Agent"],
    summary="Ask the Autonomous BI Agent a natural language question",
)
def ask_agent_endpoint(
    payload: AgentQueryRequest,
    agent: AgentDep,
) -> AgentQueryResponse:
    """Run the Text-to-SQL + Executive Insights agent workflow."""
    try:
        return agent.ask(
            question=payload.question,
            previous_interaction_id=payload.previous_interaction_id,
        )
    except SQLSecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution failed: {exc}",
        ) from exc
