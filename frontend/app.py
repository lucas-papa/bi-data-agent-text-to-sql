"""Streamlit Dashboard for the Autonomous BI & Data Analysis Agent."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.agent import AutonomousBIAgent  # noqa: E402
from backend.database import get_database_schema  # noqa: E402
from backend.schemas import (  # noqa: E402
    AgentQueryResponse,
    ChartRecommendation,
    DatabaseSchemaResponse,
)

DEFAULT_BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

SAMPLE_QUESTIONS: list[str] = [
    "Show quarterly revenue and total orders trend across all quarters.",
    "What is our net revenue and gross profit breakdown by product category?",
    "Analyze order status distribution including cancellation and return rates.",
    "Compare customer count, net revenue, and average order value by customer segment.",
    "Which are the top 10 products by net sales and gross profit?",
    "Show net revenue and average order value by shipping country.",
]


def apply_theme_styles(theme_mode: str) -> None:
    """Inject clean custom CSS for Dark or Light executive dashboard theme."""
    is_dark = theme_mode == "Dark"
    bg_color = "#0E1117" if is_dark else "#F8FAFC"
    card_bg = "#161B26" if is_dark else "#FFFFFF"
    text_color = "#F1F5F9" if is_dark else "#0F172A"
    border_color = "#2D3748" if is_dark else "#E2E8F0"
    accent_color = "#38BDF8" if is_dark else "#0284C7"

    css = f"""
    <style>
        .stApp {{
            background-color: {bg_color};
            color: {text_color};
        }}
        .bi-card {{
            background-color: {card_bg};
            border: 1px solid {border_color};
            border-radius: 10px;
            padding: 1rem 1.25rem;
            margin-bottom: 0.75rem;
        }}
        .bi-badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
            color: {accent_color};
            border: 1px solid {accent_color};
        }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def fetch_warehouse_schema(backend_url: str) -> DatabaseSchemaResponse:
    """Fetch database schema from the FastAPI backend, falling back to local reflection."""
    try:
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(f"{backend_url.rstrip('/')}/api/v1/schema")
            if resp.status_code == 200:
                return DatabaseSchemaResponse.model_validate(resp.json())
    except Exception:
        pass
    return get_database_schema()


def execute_agent_question(
    question: str,
    backend_url: str,
    api_key: str | None = None,
    previous_interaction_id: str | None = None,
) -> AgentQueryResponse:
    """Query the FastAPI `/api/v1/agent/ask` endpoint or fallback to in-process agent."""
    headers: dict[str, str] = {}
    if api_key:
        headers["X-Gemini-Api-Key"] = api_key

    payload: dict[str, Any] = {"question": question}
    if previous_interaction_id:
        payload["previous_interaction_id"] = previous_interaction_id

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{backend_url.rstrip('/')}/api/v1/agent/ask",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 200:
                return AgentQueryResponse.model_validate(resp.json())
    except Exception:
        pass

    agent = AutonomousBIAgent(api_key=api_key or None)
    return agent.ask(question=question, previous_interaction_id=previous_interaction_id)


def build_plotly_figure(
    df: pd.DataFrame,
    recommendation: ChartRecommendation,
    theme_mode: str,
) -> go.Figure | None:
    """Construct an interactive Plotly figure based on the ChartRecommendation."""
    if df.empty or recommendation.chart_type in ("none", "metric"):
        return None

    template = "plotly_dark" if theme_mode == "Dark" else "plotly_white"
    x_col = recommendation.x_column
    y_col = recommendation.y_column
    color_col = recommendation.color_column

    if not x_col or not y_col or x_col not in df.columns or y_col not in df.columns:
        return None

    safe_color = color_col if (color_col and color_col in df.columns) else None

    if recommendation.chart_type == "line":
        fig = px.line(
            df,
            x=x_col,
            y=y_col,
            color=safe_color,
            markers=True,
            title=recommendation.title,
            template=template,
        )
    elif recommendation.chart_type == "pie":
        fig = px.pie(
            df,
            names=x_col,
            values=y_col,
            hole=0.4,
            title=recommendation.title,
            template=template,
        )
    elif recommendation.chart_type == "scatter":
        fig = px.scatter(
            df,
            x=x_col,
            y=y_col,
            color=safe_color,
            size_max=14,
            title=recommendation.title,
            template=template,
        )
    else:
        fig = px.bar(
            df,
            x=x_col,
            y=y_col,
            color=safe_color,
            text_auto=".2s",
            title=recommendation.title,
            template=template,
        )

    fig.update_layout(
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
        height=390,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
    )
    return fig


def render_assistant_response(
    response: AgentQueryResponse,
    theme_mode: str,
    msg_index: int,
) -> None:
    """Render the synthesized executive summary, SQL block, metrics, table, and Plotly chart."""
    st.markdown(response.executive_summary)

    # Top execution telemetry row
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("SQL Execution Time", f"{response.execution_time_ms:.2f} ms")
    col2.metric("Rows Returned", f"{response.row_count:,}")
    col3.metric("Total Turn Latency", f"{response.total_latency_ms:.1f} ms")
    col4.metric("Self-Correction Retries", f"{response.retry_count}")

    # Formatted SQL Query block
    if response.sql_query:
        st.markdown("##### Generated ANSI / SQLite Query")
        st.code(response.sql_query, language="sql")

    # Results Table & Automated Plotly Chart
    if response.rows and response.columns:
        df = pd.DataFrame(response.rows, columns=response.columns)
        rec = response.chart_recommendation

        tab_chart, tab_table, tab_trace = st.tabs(
            [
                f"📈 Chart Suggestion ({rec.chart_type.upper()})",
                f"📋 Interactive Results ({len(df)} rows)",
                f"🛠️ Agent Tool Trace ({len(response.tool_trace)} steps)",
            ]
        )

        with tab_chart:
            st.caption(f"**Chart Advisor Reasoning:** {rec.reasoning}")
            if rec.chart_type == "metric":
                metric_cols = st.columns(min(4, len(df.columns)))
                for idx, col_name in enumerate(df.columns[:4]):
                    metric_cols[idx].metric(
                        col_name.replace("_", " ").title(), str(df.iloc[0][col_name])
                    )
            else:
                fig = build_plotly_figure(df, rec, theme_mode)
                if fig is not None:
                    st.plotly_chart(fig, use_container_width=True, key=f"plotly_{msg_index}")
                else:
                    st.info("No chart available for this data shape; view the table tab.")

        with tab_table:
            st.dataframe(df, use_container_width=True, hide_index=True)

        with tab_trace:
            for step in response.tool_trace:
                badge = "✅" if step.status == "success" else "⚠️"
                st.markdown(
                    f"{badge} **Step {step.step_index}: `{step.tool_name}`** "
                    f"({step.execution_time_ms:.2f} ms) — {step.detail}"
                )


def main() -> None:
    """Entry point for the Streamlit BI & Data Analysis Agent application."""
    st.set_page_config(
        page_title="Autonomous BI & Data Analysis Agent",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_interaction_id" not in st.session_state:
        st.session_state.last_interaction_id = None

    # Sidebar Controls & Warehouse Schema Inspector
    with st.sidebar:
        st.title("⚙️ BI Agent Control Panel")
        theme_mode = st.radio(
            "Dashboard Theme",
            options=["Dark", "Light"],
            index=0,
            horizontal=True,
        )
        backend_url = st.text_input("Backend API URL", value=DEFAULT_BACKEND_URL)
        api_key_input = st.text_input(
            "Gemini API Key (Optional override)",
            type="password",
            help="Uses GEMINI_API_KEY from environment if left blank.",
        )

        st.divider()
        st.subheader("💡 Sample Executive Prompts")
        selected_prompt: str | None = None
        for idx, sample in enumerate(SAMPLE_QUESTIONS):
            if st.button(sample, key=f"sample_btn_{idx}", use_container_width=True):
                selected_prompt = sample

        if st.button("🗑️ Clear Conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.last_interaction_id = None
            st.rerun()

        st.divider()
        st.subheader("🗄️ Warehouse Schema Explorer")
        schema = fetch_warehouse_schema(backend_url)
        for tbl in schema.tables:
            with st.expander(f"📁 {tbl.table_name} ({tbl.row_count:,} rows)"):
                for col in tbl.columns:
                    pk_badge = " 🔑" if col.is_primary_key else ""
                    null_badge = " (NULL)" if col.nullable else " (NOT NULL)"
                    st.markdown(f"- `{col.name}`: **{col.data_type}**{pk_badge}{null_badge}")
                if tbl.foreign_keys:
                    st.caption("Foreign Keys:")
                    for fk in tbl.foreign_keys:
                        st.markdown(
                            f"  ↳ `{fk.from_column}` → `{fk.target_table}.{fk.target_column}`"
                        )

    apply_theme_styles(theme_mode)

    # Header Banner
    st.title("📊 Autonomous BI & Data Analysis Agent")
    st.markdown(
        "Ask natural-language business questions against the **E-Commerce SQLite Warehouse**. "
        "The agent inspects the schema, writes validated read-only ANSI/SQLite SQL, "
        "self-corrects query errors, and synthesizes executive insights with Plotly charts."
    )

    # Render conversation history
    for idx, entry in enumerate(st.session_state.messages):
        with st.chat_message("user"):
            st.markdown(entry["question"])
        with st.chat_message("assistant"):
            render_assistant_response(entry["response"], theme_mode, msg_index=idx)

    # Handle new user input (either from chat_input or sidebar sample button)
    user_query = st.chat_input("Ask a question about customers, orders, products, or revenue...")
    active_question = selected_prompt or user_query

    if active_question:
        with st.chat_message("user"):
            st.markdown(active_question)

        with (
            st.chat_message("assistant"),
            st.spinner("Inspecting schema, executing read-only SQL, and synthesizing insights..."),
        ):
            agent_response = execute_agent_question(
                question=active_question,
                backend_url=backend_url,
                api_key=api_key_input or None,
                previous_interaction_id=st.session_state.last_interaction_id,
            )
            st.session_state.last_interaction_id = agent_response.interaction_id
            st.session_state.messages.append(
                {
                    "question": active_question,
                    "response": agent_response,
                }
            )
            render_assistant_response(
                agent_response,
                theme_mode,
                msg_index=len(st.session_state.messages) - 1,
            )


if __name__ == "__main__":
    main()
