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

        logger.info(
            f"Visualization decision: chart_type={decision.chart_type}, "
            f"should_visualize={decision.should_visualize}, reasoning={decision.reasoning}"
        )

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

        barclays_colors = ["#00AFE9", "#0077B6", "#005fa3", "#10b981", "#ffc857", "#64748b", "#0062A0"]

        common_layout = go.Layout(
            title=dict(text=title, font=dict(size=14, color="#1e293b", family="Outfit")),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Outfit", color="#334155", size=12),
            margin=dict(l=45, r=20, t=50, b=45),
            colorway=barclays_colors,
        )

        if chart_type == "bar" and x_field and y_field:
            x_vals = [str(r.get(x_field, "")) for r in rows]
            y_vals = _to_floats([r.get(y_field, 0) for r in rows])
            fig = go.Figure(
                data=[
                    go.Bar(
                        x=x_vals,
                        y=y_vals,
                        marker_color="#00AFE9",
                        marker_line_color="#0077B6",
                        marker_line_width=1,
                    )
                ],
                layout=common_layout,
            )
            fig.update_layout(
                xaxis=dict(showgrid=False, tickangle=-30),
                yaxis=dict(showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
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
                        line=dict(color="#00AFE9", width=3),
                        marker=dict(color="#0077B6", size=7),
                        fill="tozeroy",
                        fillcolor="rgba(0,175,233,0.08)",
                    )
                ],
                layout=common_layout,
            )
            fig.update_layout(
                xaxis=dict(showgrid=False, tickangle=-30),
                yaxis=dict(showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
            )

        elif chart_type == "pie" and x_field and y_field:
            labels = [str(r.get(x_field, "")) for r in rows]
            values = _to_floats([r.get(y_field, 0) for r in rows])
            fig = go.Figure(
                data=[
                    go.Pie(
                        labels=labels,
                        values=values,
                        hole=0.42,
                        marker=dict(colors=barclays_colors, line=dict(color="#ffffff", width=2)),
                    )
                ],
                layout=common_layout,
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5))

        elif chart_type == "table":
            if not rows:
                return None
            headers = list(rows[0].keys())
            col_values = [[str(r.get(h, "")) for r in rows] for h in headers]
            # Alternating row fill for readability
            n_rows = len(rows)
            row_fills = [["#f0faff" if i % 2 == 0 else "#ffffff" for i in range(n_rows)] for _ in headers]
            fig = go.Figure(
                data=[
                    go.Table(
                        header=dict(
                            values=[f"<b>{h}</b>" for h in headers],
                            fill_color="#00AFE9",
                            font=dict(color="white", size=12, family="Outfit"),
                            align="left",
                            line_color="#0077B6",
                            height=32,
                        ),
                        cells=dict(
                            values=col_values,
                            fill_color=row_fills,
                            font=dict(color="#334155", size=11, family="Outfit"),
                            align="left",
                            line_color="#e2e8f0",
                            height=28,
                        ),
                    )
                ],
                layout=common_layout,
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
