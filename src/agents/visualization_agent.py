import ast
import time
from typing import Any
from logging import Logger

import plotly.graph_objects as go
import plotly.io as pio

from app_models import Graph_State, VisualizationDecision, VisualizationResponse
from utils import agent_utils


async def visualization_agent(
    state: Graph_State,
    llm: Any,
    prompt: Any,
    logger: Logger,
) -> Graph_State:
    logger.info("Executing visualization agent.")
    try:
        db_results = state.database_query_results or []
        clean_results = [r for r in db_results if not r.startswith("[System Notification:")]

        fallback_active = state.fallback_response and state.fallback_response.fallback_flag

        if not clean_results or fallback_active:
            state.visualization_response = VisualizationResponse(
                decision=VisualizationDecision(
                    should_visualize=False,
                    chart_type="none",
                    chart_title="",
                    x_field=None,
                    y_field=None,
                    reasoning="No data available or fallback active.",
                ),
                plotly_json=None,
            )
            return state

        rows = []
        for r in clean_results:
            try:
                rows.append(ast.literal_eval(r))
            except Exception:
                pass

        if not rows:
            state.visualization_response = VisualizationResponse(
                decision=VisualizationDecision(
                    should_visualize=False,
                    chart_type="none",
                    chart_title="",
                    x_field=None,
                    y_field=None,
                    reasoning="Could not parse DB results.",
                ),
                plotly_json=None,
            )
            return state

        columns = list(rows[0].keys())
        user_query = (
            state.context_builder_response.user_query
            if state.context_builder_response
            else ""
        )

        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": prompt.template,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            },
            {
                "role": "user",
                "content": (
                    f"User question: {user_query}\n"
                    f"Available columns: {', '.join(columns)}\n"
                    f"Number of rows: {len(rows)}\n"
                    f"Sample data (first 3 rows): {rows[:3]}"
                ),
            },
        ]

        structured_llm = llm.with_structured_output(VisualizationDecision, include_raw=True)

        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        latency = time.time() - start_time

        decision: VisualizationDecision = response_raw["parsed"]
        response_message = response_raw["raw"]

        metric_metadata = agent_utils.agent_metrics(response_message, latency)
        if state.agent_metrics is not None:
            state.agent_metrics["visualization_agent"] = metric_metadata

        logger.info(f"Visualization agent full response: {decision.model_dump()}")

        if not decision.should_visualize or decision.chart_type == "none":
            state.visualization_response = VisualizationResponse(
                decision=decision, plotly_json=None
            )
            return state

        plotly_json = _build_plotly_figure(decision, rows, logger)
        state.visualization_response = VisualizationResponse(
            decision=decision, plotly_json=plotly_json
        )

    except Exception as e:
        logger.exception(f"Error in visualization agent: {e}")
        state.visualization_response = VisualizationResponse(
            decision=VisualizationDecision(
                should_visualize=False,
                chart_type="none",
                chart_title="",
                x_field=None,
                y_field=None,
                reasoning=f"Agent error: {e}",
            ),
            plotly_json=None,
        )

    return state


def _build_plotly_figure(
    decision: VisualizationDecision, rows: list[dict], logger: Logger
) -> str | None:
    try:
        chart_type = decision.chart_type
        x_field = decision.x_field
        y_field = decision.y_field
        title = decision.chart_title

        ui_colors = ["#7c3aed", "#a78bfa", "#6d28d9", "#ec4899", "#10b981", "#4f46e5", "#64748b"]

        _label_map = {
            "merchant_system_category1_name": "Category",
            "merchant_system_category2_name": "Sub-Category",
            "merchant_brand_name": "Merchant",
            "amount_usd": "Amount (£)",
            "total_spent": "Total Spent (£)",
            "total": "Total (£)",
            "transaction_date": "Date",
            "month": "Month",
            "year": "Year",
            "day": "Day",
            "country": "Country",
            "channel": "Channel",
            "card_type": "Card Type",
            "count": "Count",
            "transaction_id": "Transaction ID",
            "customer_id": "Customer",
        }

        def _axis_label(field: str | None) -> str:
            if not field:
                return ""
            return _label_map.get(field, field.replace("_", " ").title())

        y_label = _axis_label(y_field)

        common_layout = go.Layout(
            title=dict(text=title, font=dict(size=15, color="#1f2937", family="Outfit"), x=0.02, xanchor="left"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Outfit", color="#374151", size=12),
            margin=dict(l=65, r=30, t=55, b=75),
            colorway=ui_colors,
        )

        if chart_type == "bar" and x_field and y_field:
            x_vals = [str(r.get(x_field, "")) for r in rows]
            y_vals = _to_floats([r.get(y_field, 0) for r in rows])
            fig = go.Figure(
                data=[
                    go.Bar(
                        x=x_vals,
                        y=y_vals,
                        marker_color="#7c3aed",
                        marker_line_color="#6d28d9",
                        marker_line_width=1,
                        opacity=0.88,
                        text=[f"{v:,.2f}" if v >= 1000 else f"{v:,.0f}" for v in y_vals],
                        textposition="outside",
                        textfont=dict(size=11, color="#6d28d9", family="Outfit"),
                        cliponaxis=False,
                        hovertemplate="%{x}<br><b>£%{y:,.2f}</b><extra></extra>",
                    )
                ],
                layout=common_layout,
            )
            fig.update_layout(
                margin=dict(l=65, r=30, t=55, b=90),
                xaxis=dict(
                    showgrid=False,
                    tickangle=-30,
                ),
                yaxis=dict(
                    title=dict(text=y_label, font=dict(size=12, color="#6b7280")),
                    showgrid=True,
                    gridcolor="rgba(124,58,237,0.06)",
                ),
                uniformtext=dict(minsize=9, mode="hide"),
            )

        elif chart_type == "line" and x_field and y_field:
            x_vals = [str(r.get(x_field, "")) for r in rows]
            y_vals = _to_floats([r.get(y_field, 0) for r in rows])
            fig = go.Figure(
                data=[
                    go.Scatter(
                        x=x_vals,
                        y=y_vals,
                        mode="lines+markers",
                        line=dict(color="#7c3aed", width=2.5, shape="spline", smoothing=0.8),
                        marker=dict(
                            color="#ffffff",
                            size=9,
                            line=dict(color="#7c3aed", width=2.5),
                            symbol="circle",
                        ),
                        fill="tozeroy",
                        fillcolor="rgba(124,58,237,0.10)",
                        hovertemplate="%{x}<br><b>£%{y:,.2f}</b><extra></extra>",
                    )
                ],
                layout=common_layout,
            )
            fig.update_layout(
                xaxis=dict(
                    showgrid=False,
                    tickangle=-30,
                    showline=True,
                    linecolor="rgba(0,0,0,0.08)",
                ),
                yaxis=dict(
                    title=dict(text=y_label, font=dict(size=12, color="#6b7280")),
                    showgrid=True,
                    gridcolor="rgba(124,58,237,0.06)",
                    zeroline=False,
                ),
            )

        else:
            return None

        return pio.to_json(fig)

    except Exception as e:
        logger.exception(f"Error building Plotly figure: {e}")
        return None


def _to_floats(values: list) -> list[float]:
    result = []
    for v in values:
        try:
            result.append(float(v))
        except (TypeError, ValueError):
            result.append(0.0)
    return result
