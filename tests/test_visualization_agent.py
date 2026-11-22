import asyncio
import sqlite3
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import plotly.io as pio
from pydantic import ValidationError
from langgraph.graph import END, START, StateGraph

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.sql_execution import MAX_CHART_RESULT_ROWS, execute_query
from agents.visualization_agent import (
    MAX_BAR_CATEGORIES,
    MAX_LINE_POINTS,
    MAX_PIE_CATEGORIES,
    _build_plotly_figure,
    _downsample_line_rows,
    _explicit_chart_requested,
    _validate_chart_data,
    visualization_agent,
)
from agents.response_generator import response_generator_agent
from agents.sql_execution import MAX_RESULT_ROWS
from agents.text2sql_generator import text2sql_generator_agent
from utils.chart_intent import (
    is_explicit_chart_request,
    is_explicit_pie_request,
)
from app_models import (
    Graph_State,
    SubGraph_State,
    Text2SqlGeneratorResponse,
    UIUserRequest,
    VisualizationDecision,
    VisualizationResponse,
)


def _decision(
    chart_type: str = "bar",
    x_field: str | None = "merchant",
    y_field: str | None = "total_spent_usd",
) -> VisualizationDecision:
    return VisualizationDecision(
        should_visualize=True,
        chart_type=chart_type,
        chart_title="Spending",
        x_field=x_field,
        y_field=y_field,
        reasoning="Compare the results.",
    )


def _graph_state(
    rows: list[dict], user_query: str = "Compare spending."
) -> Graph_State:
    return Graph_State(
        input_request=UIUserRequest(
            customer_id="Cust_0410",
            session_id="test-session",
            user_query=user_query,
        ),
        chat_history=None,
        customer_id="Cust_0410",
        context_builder_response=None,
        entity_extraction_response=None,
        suprivisor_response=None,
        text2sql_generator_response=None,
        database_query_results=rows,
        banksql_queries=["Compare spending."],
        out_of_scope_queries=None,
        fallback_response=None,
        response_generation_response=None,
        agent_metrics={},
    )


class _FakeStructuredLLM:
    def __init__(self, decision: VisualizationDecision):
        self.decision = decision
        self.messages = None

    def with_structured_output(self, *_args, **_kwargs):
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        return {"parsed": self.decision, "raw": SimpleNamespace()}


class VisualizationAgentTests(unittest.TestCase):
    def test_explicit_viz_request_creates_chart_for_one_result(self):
        rows = [{"total_spent_usd": 42.5}]
        llm = _FakeStructuredLLM(_decision())
        state = _graph_state(rows, "Create a viz of my spending.")

        with patch("agents.visualization_agent.agent_utils.agent_metrics"):
            asyncio.run(
                visualization_agent(
                    state, llm, SimpleNamespace(template="Choose a chart."), Mock()
                )
            )

        self.assertIsNone(llm.messages)
        self.assertTrue(
            state.visualization_response.decision.should_visualize,
            state.visualization_response.decision.reasoning,
        )
        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(list(figure.data[0].x), ["Result"])
        self.assertEqual(list(figure.data[0].y), [42.5])

    def test_explicit_chart_request_skips_natural_language_response(self):
        state = _graph_state(
            [{"total_spent_usd": 42.5}],
            "Create a viz of my spending.",
        )
        visualization_llm = _FakeStructuredLLM(_decision())

        async def run_agents():
            await visualization_agent(
                state,
                visualization_llm,
                SimpleNamespace(template="Choose a chart."),
                Mock(),
            )
            await response_generator_agent(
                state,
                Mock(side_effect=AssertionError("Text LLM should not be called")),
                SimpleNamespace(template="Generate a text response."),
                Mock(),
            )

        asyncio.run(run_agents())

        self.assertTrue(state.visualization_only)
        self.assertEqual(state.response_generation_response.generated_response, "")

    def test_explicit_chart_request_aggregates_transaction_rows_by_date(self):
        rows = [
            {"transaction_date": day, "amount_usd": amount, "transaction_id": f"id-{i}"}
            for i, (day, amount) in enumerate(
                [("2026-01-02", 10), ("2026-01-01", 5), ("2026-01-02", 7)]
            )
        ]
        state = _graph_state(rows, "Make a chart of these transactions.")

        asyncio.run(
            visualization_agent(
                state,
                _FakeStructuredLLM(_decision()),
                SimpleNamespace(template="Choose a chart."),
                Mock(),
            )
        )

        self.assertTrue(state.visualization_response.decision.should_visualize)
        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(list(figure.data[0].x), ["2026-01-01", "2026-01-02"])
        self.assertEqual(list(figure.data[0].y), [5, 17])

    def test_explicit_request_supports_common_visualization_spellings(self):
        for query in (
            "Create a viz",
            "Create a vizualization",
            "Create a visualization",
            "Create a visualisation",
            "Show a chart",
            "I need a visualization",
            "Plot my spending",
            "Graph my spending",
        ):
            with self.subTest(query=query):
                state = _graph_state([{"total_spent_usd": 10}], query)
                self.assertTrue(_explicit_chart_requested(state))

    def test_explicit_chart_detection_ignores_questions_about_charts(self):
        for query in (
            "What chart would best show spending?",
            "Can you explain this chart?",
            "Tell me about data visualization.",
            "I want to know what chart is best.",
            "Can you explain this chart, please?",
        ):
            with self.subTest(query=query):
                self.assertFalse(
                    _explicit_chart_requested(_graph_state([], query))
                )

    def test_explicit_pie_chart_request_is_recognized(self):
        state = _graph_state(
            [], "Create a pie chart of my spending by merchant."
        )

        self.assertTrue(_explicit_chart_requested(state))
        self.assertTrue(is_explicit_pie_request(state.input_request.user_query))

    def test_explicit_pie_chart_shows_top_five_and_other(self):
        rows = [
            {"merchant": f"Merchant {index}", "total_spent_usd": index + 1}
            for index in range(10)
        ]
        state = _graph_state(
            rows, "Create a pie chart of my spending by merchant."
        )

        asyncio.run(
            visualization_agent(
                state,
                _FakeStructuredLLM(_decision()),
                SimpleNamespace(template="Choose a chart."),
                Mock(),
            )
        )

        decision = state.visualization_response.decision
        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(decision.chart_type, "pie")
        self.assertTrue(state.visualization_only)
        self.assertEqual(figure.data[0].type, "pie")
        self.assertEqual(len(figure.data[0].labels), MAX_PIE_CATEGORIES + 1)
        self.assertEqual(figure.data[0].labels[-1], "Other")
        self.assertEqual(figure.data[0].values[-1], sum(range(1, 6)))

    def test_pie_is_selected_for_valid_category_composition(self):
        rows = [
            {"category": category, "transaction_count": count}
            for category, count in (("Groceries", 8), ("Travel", 3))
        ]
        state = _graph_state(rows, "Show the distribution of transactions.")
        llm = _FakeStructuredLLM(
            VisualizationDecision(
                should_visualize=True,
                chart_type="pie",
                chart_title="Transactions by category",
                x_field="category",
                y_field="transaction_count",
                reasoning="The category counts represent parts of a whole.",
            )
        )

        with patch("agents.visualization_agent.agent_utils.agent_metrics"):
            asyncio.run(
                visualization_agent(
                    state, llm, SimpleNamespace(template="Choose a chart."), Mock()
                )
            )

        self.assertTrue(
            state.visualization_response.decision.should_visualize,
            state.visualization_response.decision.reasoning,
        )
        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(state.visualization_response.decision.chart_type, "pie")
        self.assertEqual(list(figure.data[0].labels), ["Groceries", "Travel"])

    def test_unsupported_explicit_pie_values_suggest_line_or_bar(self):
        rows = [
            {"merchant": "A", "amount_usd": 10},
            {"merchant": "B", "amount_usd": -2},
        ]
        state = _graph_state(
            rows, "Create a pie chart of spending by merchant."
        )

        asyncio.run(
            visualization_agent(
                state,
                _FakeStructuredLLM(_decision()),
                SimpleNamespace(template="Choose a chart."),
                Mock(),
            )
        )

        reason = state.visualization_response.decision.reasoning
        self.assertFalse(state.visualization_response.decision.should_visualize)
        self.assertIn("Pie chart not supported", reason)
        self.assertIn("negative", reason)
        self.assertIn("line or bar chart", reason)
        self.assertFalse(state.visualization_only)

    def test_pie_validation_rejects_time_series_and_non_additive_metrics(self):
        time_rows = [
            {"month": "2026-01", "total_spent_usd": 12},
            {"month": "2026-02", "total_spent_usd": 18},
        ]
        rate_rows = [
            {"category": "A", "interest_rate": 3.5},
            {"category": "B", "interest_rate": 5.0},
        ]

        self.assertIn(
            "categorical values",
            _validate_chart_data(
                _decision("pie", "month", "total_spent_usd"),
                time_rows,
                explicit_request=True,
            ),
        )
        self.assertIn(
            "additive measure",
            _validate_chart_data(
                _decision("pie", "category", "interest_rate"),
                rate_rows,
                explicit_request=True,
            ),
        )

    def test_unsupported_pie_recommendation_is_included_in_user_response(self):
        state = _graph_state(
            [{"merchant": "A", "amount_usd": -1}],
            "Create a pie chart of spending by merchant.",
        )
        state.visualization_response = VisualizationResponse(
            decision=VisualizationDecision(
                should_visualize=False,
                chart_type="none",
                chart_title="",
                x_field=None,
                y_field=None,
                reasoning=(
                    "Pie chart not supported for these values: Pie charts cannot "
                    "represent negative values. Please try a line or bar chart instead."
                ),
            ),
            plotly_json=None,
        )

        class FakeStreamingLLM:
            async def astream(self, _messages):
                yield "Here is the result."

        with patch(
            "agents.response_generator.agent_utils.message_to_text",
            return_value="Here is the result.",
        ), patch("agents.response_generator.agent_utils.agent_metrics"):
            asyncio.run(
                response_generator_agent(
                    state,
                    FakeStreamingLLM(),
                    SimpleNamespace(template="Generate a text response."),
                    Mock(),
                )
            )

        self.assertIn(
            "Please try a line or bar chart instead.",
            state.response_generation_response.generated_response,
        )

    def test_truncated_sql_results_are_not_charted(self):
        state = _graph_state(
            [{"merchant": name, "total_spent_usd": amount} for name, amount in
             [("A", 10), ("B", 20), ("C", 30)]],
            "Create a chart of my spending.",
        )
        state.database_results_truncated = True
        llm = _FakeStructuredLLM(_decision())

        asyncio.run(
            visualization_agent(
                state, llm, SimpleNamespace(template="Choose a chart."), Mock()
            )
        )

        self.assertIsNone(llm.messages)
        self.assertFalse(state.visualization_response.decision.should_visualize)
        self.assertIn("truncated", state.visualization_response.decision.reasoning)

    def test_sql_subgraph_truncation_flag_reaches_parent_graph_state(self):
        async def mark_truncated(state):
            state.database_results_truncated = True
            return state

        subgraph_builder = StateGraph(SubGraph_State)
        subgraph_builder.add_node("mark_truncated", mark_truncated)
        subgraph_builder.add_edge(START, "mark_truncated")
        subgraph_builder.add_edge("mark_truncated", END)

        graph_builder = StateGraph(Graph_State)
        graph_builder.add_node("sql_generation", subgraph_builder.compile())
        graph_builder.add_edge(START, "sql_generation")
        graph_builder.add_edge("sql_generation", END)

        result = asyncio.run(
            graph_builder.compile().ainvoke(_graph_state([]))
        )

        self.assertTrue(result["database_results_truncated"])

    def test_explicit_amounts_are_summed_but_balances_use_latest_values(self):
        amount_state = _graph_state(
            [
                {"transaction_date": day, "amount_usd": amount, "transaction_id": key}
                for day, amount, key in (
                    ("2026-01-01", 10, "a"),
                    ("2026-01-02", 15, "b"),
                    ("2026-01-02", 5, "c"),
                )
            ],
            "Create a chart of my transactions.",
        )
        balance_state = _graph_state(
            [
                {"merchant": merchant, "transaction_date": day, "current_balance": amount}
                for merchant, day, amount in (
                    ("A", "2026-01-01", 100),
                    ("A", "2026-02-01", 150),
                    ("B", "2026-01-01", 80),
                    ("B", "2026-02-01", 120),
                )
            ],
            "Create a chart of my balances.",
        )
        for row in balance_state.database_query_results:
            row["amount_usd"] = 1.0

        for state in (amount_state, balance_state):
            asyncio.run(
                visualization_agent(
                    state,
                    _FakeStructuredLLM(_decision()),
                    SimpleNamespace(template="Choose a chart."),
                    Mock(),
                )
            )

        amount_figure = pio.from_json(amount_state.visualization_response.plotly_json)
        balance_figure = pio.from_json(balance_state.visualization_response.plotly_json)
        self.assertEqual(list(amount_figure.data[0].y), [10, 20])
        self.assertEqual(list(balance_figure.data[0].y), [150, 120])
        self.assertIn("Current Balance", balance_figure.layout.yaxis.title.text)

    def test_explicit_rate_chart_averages_rates_instead_of_summing(self):
        state = _graph_state(
            [
                {"merchant": "A", "apr_rate": 3.0},
                {"merchant": "A", "apr_rate": 9.0},
            ],
            "Create a chart of my APR rates.",
        )

        asyncio.run(
            visualization_agent(
                state,
                _FakeStructuredLLM(_decision()),
                SimpleNamespace(template="Choose a chart."),
                Mock(),
            )
        )

        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(list(figure.data[0].y), [6.0])

    def test_year_month_grouping_preserves_years_and_chronological_order(self):
        rows = [
            {"year": year, "month": month, "amount_usd": amount}
            for year, month, amount in (
                (2026, 1, 10),
                (2025, 1, 5),
                (2025, 12, 8),
            )
        ]
        state = _graph_state(rows, "Create a chart of monthly spending.")

        asyncio.run(
            visualization_agent(
                state,
                _FakeStructuredLLM(_decision()),
                SimpleNamespace(template="Choose a chart."),
                Mock(),
            )
        )

        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(
            list(figure.data[0].x), ["2025-01", "2025-12", "2026-01"]
        )
        self.assertEqual(list(figure.data[0].y), [5, 8, 10])

    def test_line_downsampling_preserves_spikes_and_dips(self):
        rows = [
            {
                "date": f"2026-{index // 28 + 1:02}-{index % 28 + 1:02}",
                "amount": (
                    1000 if index == 35 else -500 if index == 75 else 0
                ),
            }
            for index in range(100)
        ]

        sampled = _downsample_line_rows(rows, "date", "amount", MAX_LINE_POINTS)

        self.assertEqual(len(sampled), MAX_LINE_POINTS)
        self.assertIn("2026-02-08", [row["date"] for row in sampled])
        self.assertIn("2026-03-20", [row["date"] for row in sampled])

    def test_explicit_bar_chart_combines_omitted_categories_into_other(self):
        rows = [
            {"merchant": f"Merchant {index}", "amount_usd": index + 1}
            for index in range(MAX_BAR_CATEGORIES + 3)
        ]
        state = _graph_state(rows, "Create a chart of my spending by merchant.")

        asyncio.run(
            visualization_agent(
                state,
                _FakeStructuredLLM(_decision()),
                SimpleNamespace(template="Choose a chart."),
                Mock(),
            )
        )

        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(len(figure.data[0].x), MAX_BAR_CATEGORIES)
        self.assertEqual(figure.data[0].x[-1], "Other")
        self.assertEqual(figure.data[0].y[-1], sum(range(1, 5)))

    def test_ordinary_prompt_still_uses_conservative_chart_decision(self):
        rows = [
            {"merchant": f"Merchant {index}", "total_spent_usd": index + 10}
            for index in range(3)
        ]
        llm = _FakeStructuredLLM(
            VisualizationDecision(
                should_visualize=False,
                chart_type="none",
                chart_title="",
                x_field=None,
                y_field=None,
                reasoning="Text is sufficient.",
            )
        )
        state = _graph_state(rows, "How much did I spend?")

        asyncio.run(
            visualization_agent(
                state, llm, SimpleNamespace(template="Choose a chart."), Mock()
            )
        )

        self.assertIsNotNone(llm.messages)
        self.assertFalse(state.visualization_response.decision.should_visualize)

    def test_currency_is_inferred_from_result_field(self):
        rows = [
            {"merchant": "A", "total_spent_usd": 12.5},
            {"merchant": "B", "total_spent_usd": 18.0},
            {"merchant": "C", "total_spent_usd": 9.0},
        ]

        figure = pio.from_json(
            _build_plotly_figure(_decision(), rows, Mock())
        )

        self.assertEqual(figure.layout.yaxis.title.text, "Total Spent (USD)")
        self.assertIn("USD", figure.data[0].hovertemplate)

    def test_currency_uses_consistent_currency_values_in_results(self):
        rows = [
            {"merchant": name, "total_spent": amount, "currency": "GBP"}
            for name, amount in [("A", 12), ("B", 15), ("C", 18)]
        ]

        figure = pio.from_json(
            _build_plotly_figure(
                _decision(y_field="total_spent"), rows, Mock()
            )
        )

        self.assertEqual(figure.layout.yaxis.title.text, "Total Spent (GBP)")
        self.assertIn("GBP", figure.data[0].hovertemplate)

    def test_mixed_currency_results_are_rejected(self):
        rows = [
            {"merchant": "A", "total_spent": 12, "currency": "USD"},
            {"merchant": "B", "total_spent": 15, "currency": "GBP"},
            {"merchant": "C", "total_spent": 18, "currency": "USD"},
        ]

        self.assertIn(
            "mixed or conflicting currencies",
            _validate_chart_data(_decision(y_field="total_spent"), rows),
        )

    def test_invalid_chart_fields_and_non_numeric_values_are_rejected(self):
        rows = [
            {"merchant": "A", "total_spent": 12},
            {"merchant": "B", "total_spent": "unknown"},
            {"merchant": "C", "total_spent": 18},
        ]

        self.assertIn(
            "not present",
            _validate_chart_data(
                _decision(y_field="missing_measure"), rows
            ),
        )
        self.assertIn(
            "non-numeric",
            _validate_chart_data(_decision(y_field="total_spent"), rows),
        )

    def test_unsupported_chart_types_are_rejected_by_schema(self):
        self.assertEqual(_decision(chart_type="pie").chart_type, "pie")
        with self.assertRaises(ValidationError):
            _decision(chart_type="scatter")

    def test_line_chart_orders_time_rows_and_uses_chart_specific_limit(self):
        rows = [
            {"month": month, "total_spent_usd": amount}
            for month, amount in [("2026-03", 3), ("2026-01", 1), ("2026-02", 2)]
        ]

        figure = pio.from_json(
            _build_plotly_figure(
                _decision("line", "month", "total_spent_usd"), rows, Mock()
            )
        )

        self.assertEqual(list(figure.data[0].x), ["2026-01", "2026-02", "2026-03"])
        self.assertGreater(MAX_LINE_POINTS, MAX_BAR_CATEGORIES)

    def test_visualization_prompt_contains_schema_not_transaction_values(self):
        secret_value = "sensitive-merchant-value"
        rows = [
            {"merchant": f"{secret_value}-{index}", "total_spent_usd": index + 10}
            for index in range(3)
        ]
        llm = _FakeStructuredLLM(_decision())
        state = _graph_state(rows)

        with patch("agents.visualization_agent.agent_utils.agent_metrics"):
            asyncio.run(
                visualization_agent(
                    state, llm, SimpleNamespace(template="Choose a chart."), Mock()
                )
            )

        prompt_text = llm.messages[1]["content"]
        self.assertNotIn(secret_value, prompt_text)
        self.assertIn("total_spent_usd", prompt_text)
        self.assertNotIn("Sample data", prompt_text)

    def test_bar_row_limit_groups_omitted_categories_after_chart_selection(self):
        rows = [
            {"merchant": f"Merchant {index}", "total_spent_usd": index + 1}
            for index in range(MAX_BAR_CATEGORIES + 1)
        ]
        llm = _FakeStructuredLLM(_decision())
        state = _graph_state(rows)

        with patch("agents.visualization_agent.agent_utils.agent_metrics"):
            asyncio.run(
                visualization_agent(
                    state, llm, SimpleNamespace(template="Choose a chart."), Mock()
                )
            )

        self.assertIsNotNone(llm.messages)
        self.assertTrue(state.visualization_response.decision.should_visualize)
        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(len(figure.data[0].x), MAX_BAR_CATEGORIES)
        self.assertEqual(figure.data[0].x[-1], "Other")

    def test_long_line_series_is_downsampled_after_chart_type_is_selected(self):
        rows = [
            {"month": f"2026-{index:02}", "total_spent_usd": index}
            for index in range(1, MAX_LINE_POINTS + 2)
        ]
        llm = _FakeStructuredLLM(_decision("line", "month", "total_spent_usd"))
        state = _graph_state(rows)

        with patch("agents.visualization_agent.agent_utils.agent_metrics"):
            asyncio.run(
                visualization_agent(
                    state, llm, SimpleNamespace(template="Choose a chart."), Mock()
                )
            )

        self.assertIsNotNone(llm.messages)
        self.assertTrue(state.visualization_response.decision.should_visualize)
        figure = pio.from_json(state.visualization_response.plotly_json)
        self.assertEqual(len(figure.data[0].x), MAX_LINE_POINTS)
        self.assertEqual(figure.data[0].x[0], "2026-01")
        self.assertEqual(figure.data[0].x[-1], f"2026-{MAX_LINE_POINTS + 1:02}")
        self.assertIn("(sampled)", figure.layout.title.text)

    def test_sql_execution_keeps_rows_structured(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE transactions (merchant TEXT, amount REAL)")
        connection.executemany(
            "INSERT INTO transactions VALUES (?, ?)",
            [("A", 12.0), ("B", 14.0), ("C", 16.0)],
        )
        state = SubGraph_State(
            text2sql_generator_response=Text2SqlGeneratorResponse(
                user_query="show spending",
                sql_query="SELECT merchant, amount FROM transactions ORDER BY merchant",
            ),
            database_query_results=None,
            fallback_response=None,
            banksql_queries=["show spending"],
            input_request=None,
            agent_metrics={},
        )

        asyncio.run(execute_query(state, connection, Mock()))

        self.assertEqual(
            state.database_query_results,
            [
                {"merchant": "A", "amount": 12.0},
                {"merchant": "B", "amount": 14.0},
                {"merchant": "C", "amount": 16.0},
            ],
        )
        self.assertFalse(state.database_results_truncated)
        connection.close()

    def test_sql_execution_detects_rows_truncated_by_result_limit(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE transactions (merchant TEXT, amount REAL)")
        connection.executemany(
            "INSERT INTO transactions VALUES (?, ?)",
            [(f"Merchant {index:02}", float(index)) for index in range(MAX_RESULT_ROWS + 2)],
        )
        state = SubGraph_State(
            text2sql_generator_response=Text2SqlGeneratorResponse(
                user_query="show transactions",
                sql_query="SELECT merchant, amount FROM transactions ORDER BY merchant",
            ),
            database_query_results=None,
            fallback_response=None,
            banksql_queries=["show transactions"],
            input_request=None,
            agent_metrics={},
        )

        asyncio.run(execute_query(state, connection, Mock()))

        self.assertEqual(len(state.database_query_results), MAX_RESULT_ROWS)
        self.assertTrue(state.database_results_truncated)
        connection.close()

    def test_chart_sql_aggregates_before_using_larger_chart_result_cap(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE transactions (merchant TEXT, amount REAL)"
        )
        connection.executemany(
            "INSERT INTO transactions VALUES (?, ?)",
            [
                (f"Merchant {index % 25:02}", float(index + 1))
                for index in range(50)
            ],
        )
        state = SubGraph_State(
            text2sql_generator_response=Text2SqlGeneratorResponse(
                user_query="Create a chart of spending by merchant",
                sql_query=(
                    "SELECT merchant, SUM(amount) AS total_spent "
                    "FROM transactions GROUP BY merchant ORDER BY merchant"
                ),
            ),
            database_query_results=None,
            fallback_response=None,
            banksql_queries=["spending by merchant"],
            input_request=UIUserRequest(
                customer_id="Cust_0410",
                session_id="test-session",
                user_query="Create a chart of spending by merchant.",
            ),
            agent_metrics={},
        )

        asyncio.run(execute_query(state, connection, Mock()))

        self.assertEqual(len(state.database_query_results), 25)
        self.assertLessEqual(len(state.database_query_results), MAX_CHART_RESULT_ROWS)
        self.assertFalse(state.database_results_truncated)
        self.assertEqual(
            sum(row["total_spent"] for row in state.database_query_results),
            sum(range(1, 51)),
        )
        connection.close()

    def test_chart_intent_requests_grouped_sql_from_text2sql_generator(self):
        state = SubGraph_State(
            text2sql_generator_response=None,
            database_query_results=None,
            fallback_response=None,
            banksql_queries=["spending by month"],
            input_request=UIUserRequest(
                customer_id="Cust_0410",
                session_id="test-session",
                user_query="Create a chart of my spending by month.",
            ),
            agent_metrics={},
        )

        class FakeSQLLLM:
            messages = None

            def with_structured_output(self, *_args, **_kwargs):
                return self

            async def ainvoke(self, messages):
                self.messages = messages
                return {
                    "parsed": Text2SqlGeneratorResponse(
                        user_query="spending by month",
                        sql_query=(
                            "SELECT year, month, SUM(amount_usd) AS total_spent "
                            "FROM transactions GROUP BY year, month"
                        ),
                    ),
                    "raw": SimpleNamespace(),
                }

        llm = FakeSQLLLM()
        metric = SimpleNamespace(
            model_used="test",
            latency=0,
            model_cost_without_cache=0,
            model_cost_with_cache=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            cached_tokens=0,
            cache_creation_tokens=0,
        )

        with patch("agents.text2sql_generator.agent_utils.agent_metrics", return_value=metric):
            asyncio.run(
                text2sql_generator_agent(
                    state,
                    llm,
                    SimpleNamespace(template="Generate SQL."),
                    Mock(),
                )
            )

        self.assertTrue(is_explicit_chart_request(state.input_request.user_query))
        self.assertIn("Aggregate in SQL before applying any row limit", llm.messages[1]["content"])
        self.assertIn("do not add a LIMIT", llm.messages[1]["content"])

    def test_chart_request_raw_rows_still_use_transaction_result_cap(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE transactions (merchant TEXT, amount REAL)"
        )
        connection.executemany(
            "INSERT INTO transactions VALUES (?, ?)",
            [
                (f"Merchant {index:02}", float(index))
                for index in range(MAX_RESULT_ROWS + 2)
            ],
        )
        state = SubGraph_State(
            text2sql_generator_response=Text2SqlGeneratorResponse(
                user_query="Create a chart of my transactions",
                sql_query=(
                    "SELECT merchant, amount FROM transactions ORDER BY merchant"
                ),
            ),
            database_query_results=None,
            fallback_response=None,
            banksql_queries=["transactions"],
            input_request=UIUserRequest(
                customer_id="Cust_0410",
                session_id="test-session",
                user_query="Create a chart of my transactions.",
            ),
            agent_metrics={},
        )

        asyncio.run(execute_query(state, connection, Mock()))

        self.assertEqual(len(state.database_query_results), MAX_RESULT_ROWS)
        self.assertTrue(state.database_results_truncated)
        connection.close()

    def test_grouped_chart_results_have_a_separate_bounded_cap(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE transactions (merchant TEXT, amount REAL)"
        )
        connection.executemany(
            "INSERT INTO transactions VALUES (?, ?)",
            [(f"Merchant {index:03}", float(index)) for index in range(502)],
        )
        state = SubGraph_State(
            text2sql_generator_response=Text2SqlGeneratorResponse(
                user_query="Create a chart of spending by merchant",
                sql_query=(
                    "SELECT merchant, SUM(amount) AS total_spent "
                    "FROM transactions GROUP BY merchant ORDER BY merchant"
                ),
            ),
            database_query_results=None,
            fallback_response=None,
            banksql_queries=["spending by merchant"],
            input_request=UIUserRequest(
                customer_id="Cust_0410",
                session_id="test-session",
                user_query="Create a chart of spending by merchant.",
            ),
            agent_metrics={},
        )

        asyncio.run(execute_query(state, connection, Mock()))

        self.assertEqual(len(state.database_query_results), MAX_CHART_RESULT_ROWS)
        self.assertTrue(state.database_results_truncated)
        connection.close()


if __name__ == "__main__":
    unittest.main()
