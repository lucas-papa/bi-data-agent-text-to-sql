"""Automated chart recommendation engine based on tabular result shape and data types."""

from __future__ import annotations

import re
from typing import Any

from backend.schemas import ChartRecommendation

_TEMPORAL_NAME_RE = re.compile(
    r"(date|time|month|quarter|year|week|day|period|yr|qtr|created|shipped|signup)",
    re.IGNORECASE,
)
_TEMPORAL_VALUE_RE = re.compile(
    r"^(\d{4}-\d{2}(-\d{2})?|\d{4}-Q[1-4]|Q[1-4]\s*\d{4}|\d{4}/\d{2})",
    re.IGNORECASE,
)
_PROPORTION_Y_RE = re.compile(
    r"(share|pct|percent|percentage|proportion|ratio)",
    re.IGNORECASE,
)
_STATUS_X_RE = re.compile(
    r"(status|channel|payment_method)",
    re.IGNORECASE,
)


def _is_numeric_value(val: Any) -> bool:
    """Return True if val is an int or float (excluding booleans)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_temporal_column(col_name: str, sample_values: list[Any]) -> bool:
    """Determine whether a column represents dates, quarters, or time periods."""
    if _TEMPORAL_NAME_RE.search(col_name):
        return True
    non_null = [str(v) for v in sample_values if v is not None][:10]
    if not non_null:
        return False
    matches = sum(1 for val in non_null if _TEMPORAL_VALUE_RE.match(val))
    return matches >= max(1, len(non_null) // 2)


def _humanize_column(col_name: str) -> str:
    """Convert snake_case column identifier into Title Case label."""
    return col_name.replace("_", " ").strip().title()


def infer_chart_recommendation(
    columns: list[str],
    rows: list[dict[str, Any]],
) -> ChartRecommendation:
    """Analyze query result columns and rows to suggest an optimal Plotly visualization.

    Heuristics:
    - 0 rows or 0 columns -> 'none'
    - 1 row with >= 1 numeric columns -> 'metric' (KPI scorecard)
    - Temporal column + numeric column (+ optional grouping column) -> 'line'
    - Categorical column + numeric column (2..6 rows, proportion/status/segment) -> 'pie'
    - Categorical column + numeric column -> 'bar'
    - >= 2 numeric columns with >= 4 rows and no categorical/temporal axis -> 'scatter'
    """
    if not columns or not rows:
        return ChartRecommendation(
            chart_type="none",
            title="No Visualization Available",
            reasoning="Query returned no rows or columns to visualize.",
        )

    row_count = len(rows)

    # Classify columns into temporal, numeric, and categorical
    numeric_cols: list[str] = []
    temporal_cols: list[str] = []
    categorical_cols: list[str] = []

    for col in columns:
        non_null_vals = [r.get(col) for r in rows if r.get(col) is not None]
        if not non_null_vals:
            categorical_cols.append(col)
            continue

        # Avoid treating primary/foreign key ID columns as metric values when other metrics exist
        is_id_col = col.lower().endswith("_id") or col.lower() == "id"

        if _is_temporal_column(col, non_null_vals):
            temporal_cols.append(col)
        elif all(_is_numeric_value(v) for v in non_null_vals) and not is_id_col:
            numeric_cols.append(col)
        else:
            categorical_cols.append(col)

    # Fallback: if all numeric columns were filtered as IDs, allow them as numeric
    if not numeric_cols:
        for col in columns:
            non_null_vals = [r.get(col) for r in rows if r.get(col) is not None]
            if non_null_vals and all(_is_numeric_value(v) for v in non_null_vals):
                numeric_cols.append(col)

    # Case 1: Single summary row -> KPI Metric card
    if row_count == 1 and numeric_cols:
        primary_metric = numeric_cols[0]
        return ChartRecommendation(
            chart_type="metric",
            x_column=categorical_cols[0] if categorical_cols else None,
            y_column=primary_metric,
            title=f"KPI Summary: {_humanize_column(primary_metric)}",
            reasoning="Single-row aggregate result is best displayed as executive KPI metrics.",
        )

    # Case 2: Time series (temporal column + at least one numeric metric)
    if temporal_cols and numeric_cols and row_count >= 2:
        x_col = temporal_cols[0]
        y_col = numeric_cols[0]
        color_col = categorical_cols[0] if categorical_cols else None
        return ChartRecommendation(
            chart_type="line",
            x_column=x_col,
            y_column=y_col,
            color_column=color_col,
            title=f"{_humanize_column(y_col)} Over {_humanize_column(x_col)}",
            reasoning=(
                f"Detected temporal dimension '{x_col}' with quantitative metric '{y_col}', "
                "ideal for a trend line chart."
            ),
        )

    # Case 3: Categorical dimension + numeric metric
    if categorical_cols and numeric_cols:
        x_col = categorical_cols[0]
        y_col = numeric_cols[0]
        color_col = categorical_cols[1] if len(categorical_cols) > 1 else None

        y_vals = [float(r[y_col]) for r in rows if _is_numeric_value(r.get(y_col))]
        all_non_negative = bool(y_vals) and all(v >= 0 for v in y_vals)

        if (
            2 <= row_count <= 6
            and color_col is None
            and all_non_negative
            and (_PROPORTION_Y_RE.search(y_col) or _STATUS_X_RE.search(x_col))
        ):
            return ChartRecommendation(
                chart_type="pie",
                x_column=x_col,
                y_column=y_col,
                title=f"{_humanize_column(y_col)} Breakdown by {_humanize_column(x_col)}",
                reasoning=(
                    f"Small number of categories ({row_count}) across '{x_col}' with positive "
                    f"values in '{y_col}' suits a proportional donut/pie chart."
                ),
            )

        return ChartRecommendation(
            chart_type="bar",
            x_column=x_col,
            y_column=y_col,
            color_column=color_col,
            title=f"{_humanize_column(y_col)} by {_humanize_column(x_col)}",
            reasoning=(
                f"Categorical dimension '{x_col}' paired with numeric measure '{y_col}' "
                "is best compared using a bar chart."
            ),
        )

    # Case 4: Two numeric columns -> Scatter plot
    if len(numeric_cols) >= 2 and row_count >= 2:
        x_col, y_col = numeric_cols[0], numeric_cols[1]
        return ChartRecommendation(
            chart_type="scatter",
            x_column=x_col,
            y_column=y_col,
            title=f"{_humanize_column(y_col)} vs. {_humanize_column(x_col)}",
            reasoning=(
                f"Two quantitative measures ('{x_col}' and '{y_col}') across {row_count} records "
                "are well-suited for a bivariate scatter plot."
            ),
        )

    return ChartRecommendation(
        chart_type="none",
        title="Tabular View Only",
        reasoning="Result shape does not contain a clear numeric measure for charting.",
    )
