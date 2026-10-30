"""Unit tests for the AutonomousBIAgent function-calling loop and ChartAdvisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.agent import AutonomousBIAgent
from backend.chart_advisor import infer_chart_recommendation


@dataclass
class FakeStep:
    """Mock step object matching the google-genai Interactions API steps schema."""

    type: str
    id: str | None = None
    name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass
class FakeInteraction:
    """Mock Interaction response object returned by client.interactions.create()."""

    id: str
    status: str
    steps: list[FakeStep] = field(default_factory=list)
    output_text: str | None = None


class FakeInteractionsResource:
    """Mock interactions resource recording turns and returning scripted responses."""

    def __init__(self, scripted_turns: list[FakeInteraction]) -> None:
        self._turns = list(scripted_turns)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeInteraction:
        self.calls.append(kwargs)
        return self._turns.pop(0)


class FakeGenAIClient:
    """Mock google.genai.Client exposing .interactions.create()."""

    def __init__(self, scripted_turns: list[FakeInteraction]) -> None:
        self.interactions = FakeInteractionsResource(scripted_turns)


def test_agent_inspects_schema_and_self_heals_failed_sql(seeded_db_path: Path) -> None:
    """Verify the agent inspects schema first, retries on broken SQL, and synthesizes insights."""
    scripted_turns = [
        # Turn 1: Agent calls get_database_schema()
        FakeInteraction(
            id="int_turn_1",
            status="requires_action",
            steps=[
                FakeStep(
                    type="function_call",
                    id="fc_schema_1",
                    name="get_database_schema",
                    arguments={},
                )
            ],
        ),
        # Turn 2: Agent attempts a query with a typo column name -> triggers SQLExecutionError
        FakeInteraction(
            id="int_turn_2",
            status="requires_action",
            steps=[
                FakeStep(
                    type="function_call",
                    id="fc_query_bad",
                    name="run_analytical_query",
                    arguments={"query": "SELECT wrong_column FROM products"},
                )
            ],
        ),
        # Turn 3: Agent rewrites the SQL query after receiving error feedback
        FakeInteraction(
            id="int_turn_3",
            status="requires_action",
            steps=[
                FakeStep(
                    type="function_call",
                    id="fc_query_good",
                    name="run_analytical_query",
                    arguments={
                        "query": (
                            "SELECT category, COUNT(*) AS product_count "
                            "FROM products GROUP BY category ORDER BY product_count DESC"
                        )
                    },
                )
            ],
        ),
        # Turn 4: Agent returns final executive summary
        FakeInteraction(
            id="int_turn_4",
            status="completed",
            steps=[],
            output_text=(
                "### Executive Summary\nElectronics and Accessories lead the product catalog."
            ),
        ),
    ]

    fake_client = FakeGenAIClient(scripted_turns)
    agent = AutonomousBIAgent(
        db_path=seeded_db_path,
        client=fake_client,
        model="gemini-3.8-flash",
    )

    response = agent.ask("How many products do we have in each category?")

    assert response.retry_count == 1
    assert response.row_count > 0
    assert "GROUP BY category" in response.sql_query
    assert "Executive Summary" in response.executive_summary
    assert response.interaction_id == "int_turn_4"

    # Verify tool trace order: schema (ok) -> query (error) -> query (ok)
    assert len(response.tool_trace) == 3
    assert response.tool_trace[0].tool_name == "get_database_schema"
    assert response.tool_trace[0].status == "success"
    assert response.tool_trace[1].tool_name == "run_analytical_query"
    assert response.tool_trace[1].status == "error"
    assert response.tool_trace[2].tool_name == "run_analytical_query"
    assert response.tool_trace[2].status == "success"

    # Verify interaction-scoped tools and system_instruction were passed on every turn
    assert len(fake_client.interactions.calls) == 4
    for call_kwargs in fake_client.interactions.calls:
        assert "tools" in call_kwargs
        assert "system_instruction" in call_kwargs
        assert call_kwargs["model"] == "gemini-3.8-flash"


def test_agent_fallback_pipeline_executes_deterministically(seeded_db_path: Path) -> None:
    """Verify fallback pipeline runs schema reflection and SQL execution when no API key is set."""
    agent = AutonomousBIAgent(api_key=None, db_path=seeded_db_path)

    resp = agent.ask("Show quarterly revenue trend across all quarters")
    assert resp.row_count >= 6
    assert resp.chart_recommendation.chart_type == "line"
    assert len(resp.tool_trace) == 2
    assert resp.tool_trace[0].tool_name == "get_database_schema"
    assert resp.tool_trace[1].tool_name == "run_analytical_query"


def test_chart_advisor_recommendations_across_data_shapes() -> None:
    """Verify ChartAdvisor selects metric, line, pie, bar, scatter, and none appropriately."""
    # 1. Empty rows -> none
    empty_rec = infer_chart_recommendation(["a", "b"], [])
    assert empty_rec.chart_type == "none"

    # 2. Single row aggregate -> metric
    metric_rec = infer_chart_recommendation(
        ["total_revenue", "order_count"],
        [{"total_revenue": 125430.50, "order_count": 420}],
    )
    assert metric_rec.chart_type == "metric"

    # 3. Temporal + numeric -> line
    line_rec = infer_chart_recommendation(
        ["year_quarter", "gross_revenue"],
        [
            {"year_quarter": "2025-Q1", "gross_revenue": 42000.0},
            {"year_quarter": "2025-Q2", "gross_revenue": 51000.0},
        ],
    )
    assert line_rec.chart_type == "line"
    assert line_rec.x_column == "year_quarter"
    assert line_rec.y_column == "gross_revenue"

    # 4. Status share breakdown (<=6 rows) -> pie
    pie_rec = infer_chart_recommendation(
        ["order_status", "status_share_pct"],
        [
            {"order_status": "completed", "status_share_pct": 64.0},
            {"order_status": "shipped", "status_share_pct": 12.0},
            {"order_status": "cancelled", "status_share_pct": 10.0},
        ],
    )
    assert pie_rec.chart_type == "pie"

    # 5. Country + revenue -> bar
    bar_rec = infer_chart_recommendation(
        ["country", "net_revenue"],
        [
            {"country": "United States", "net_revenue": 55000.0},
            {"country": "Germany", "net_revenue": 32000.0},
            {"country": "Brazil", "net_revenue": 28000.0},
        ],
    )
    assert bar_rec.chart_type == "bar"

    # 6. Two numeric columns -> scatter
    scatter_rec = infer_chart_recommendation(
        ["unit_price", "unit_cost"],
        [
            {"unit_price": 100.0, "unit_cost": 45.0},
            {"unit_price": 250.0, "unit_cost": 110.0},
            {"unit_price": 500.0, "unit_cost": 260.0},
        ],
    )
    assert scatter_rec.chart_type == "scatter"
