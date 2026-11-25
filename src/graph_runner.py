"""Run one chat turn through the agent graph: shared by /process-debug and the
end-to-end evaluation runner (evaluation/e2e/run.py), so both drive exactly the same graph.

Also holds the UI/eval agent-trace builder (moved here from app.py)."""
from __future__ import annotations

from typing import Any

from langgraph.errors import GraphRecursionError

from agent_graph import Agent_Graph
from agent_subraph import Agent_SubGraph
from app_models import ChatMessage, Graph_State, UIUserRequest
from app_setting import AppSettings

# Hard cap on agent steps per question (LangGraph super-steps); see AGENT_RECURSION_LIMIT.
GRAPH_CONFIG = {"recursion_limit": AppSettings().AGENT_RECURSION_LIMIT}


def _metric_dict(metric: Any) -> dict | None:
    """Reduce a per-agent Metrics object (or dict) to the demo-relevant subset."""
    if metric is None:
        return None
    if hasattr(metric, "model_dump"):
        metric = metric.model_dump()
    if not isinstance(metric, dict):
        return None
    return {
        "model_used": metric.get("model_used"),
        "total_tokens": metric.get("total_tokens"),
        "latency": metric.get("latency"),
        "cached_tokens": metric.get("cached_tokens"),
    }


def _sum_metrics(metrics: dict, *names: str) -> dict | None:
    """Combine the metrics of agents that can run more than once in a request
    (stored as name, name_2, name_3, ...)."""
    items = [
        _metric_dict(m) for key, m in metrics.items()
        if any(key == n or key.startswith(f"{n}_") for n in names)
    ]
    items = [m for m in items if m]
    if not items:
        return None
    return {
        "model_used": items[0].get("model_used"),
        "total_tokens": sum(m.get("total_tokens") or 0 for m in items),
        "latency": round(sum(m.get("latency") or 0 for m in items), 3),
        "cached_tokens": sum(m.get("cached_tokens") or 0 for m in items),
    }


def _build_agent_trace(response: dict) -> list[dict]:
    """Assemble an ordered, UI-friendly trace of what each backend agent did.

    Reads only the intermediate outputs already present in the LangGraph result
    (and `agent_metrics`). Purely descriptive — never raises on missing fields.
    """
    metrics = response.get("agent_metrics") or {}

    def metric(name: str) -> dict | None:
        return _metric_dict(metrics.get(name))

    fallback = response.get("fallback_response")
    fallback_node = getattr(fallback, "fallback_node", None) if fallback else None
    fallback_active = bool(fallback and getattr(fallback, "fallback_flag", False)) or bool(fallback_node)

    trace: list[dict] = []

    # 1) Guardrail
    guardrail_tripped = fallback_node == "guardrail"
    trace.append({
        "agent": "guardrail",
        "title": "Guardrail",
        "status": "fallback" if guardrail_tripped else "ok",
        "summary": (
            f"Blocked: {getattr(fallback, 'fallback_reason', 'violation')}"
            if guardrail_tripped else "No violations detected"
        ),
        "details": {},
        "metrics": metric("guardrail"),
    })

    # 1b) Dispute gate: only shown when a dispute was already open for this session
    gate_kind = response.get("dispute_gate_kind")
    gate_open = gate_kind is not None
    if gate_open:
        gate_summaries = {
            "dispute": "Reply to the open dispute",
            "side_question": "Unrelated question while a dispute is open",
            "both": "Reply to the dispute plus an unrelated question",
            "new_dispute": "Customer started a different dispute",
        }
        trace.append({
            "agent": "dispute_gate",
            "title": "Dispute Gate",
            "status": "ok",
            "summary": gate_summaries.get(gate_kind, str(gate_kind)),
            "details": {"message_kind": gate_kind, "side_question": response.get("pipeline_query")},
            "metrics": _sum_metrics(metrics, "disputes_extraction"),
        })

    # 2) Context builder
    cb = response.get("context_builder_response")
    if cb is not None:
        queries = getattr(cb, "contextual_queries", []) or []
        trace.append({
            "agent": "context_builder",
            "title": "Context Builder",
            "status": "ok",
            "summary": f"Resolved {len(queries)} self-contained quer{'y' if len(queries) == 1 else 'ies'}",
            "details": {"contextual_queries": list(queries)},
            "metrics": metric("context_builder"),
        })

    # 3) Supervisor
    sup = response.get("suprivisor_response")
    if sup is not None:
        classified = [
            {
                "sub_query": getattr(c, "sub_query", ""),
                "class_label": getattr(c, "class_label", ""),
                "transaction_query_type": getattr(c, "transaction_query_type", ""),
            }
            for c in (getattr(sup, "classified_sub_queries", []) or [])
        ]
        bank = response.get("banksql_queries") or []
        help_q = response.get("help_center_queries") or []
        oos = response.get("out_of_scope_queries") or []
        disp = response.get("dispute_queries") or []
        trace.append({
            "agent": "supervisor",
            "title": "Supervisor",
            "status": "ok",
            "summary": (
                f"{len(bank)} bank quer{'y' if len(bank) == 1 else 'ies'}, {len(help_q)} help, "
                + (f"{len(disp)} dispute, " if disp else "")
                + f"{len(oos)} out-of-scope"
            ),
            "details": {
                "classified": classified,
                "banksql_queries": list(bank),
                "help_center_queries": list(help_q),
                "dispute_queries": list(disp),
                "out_of_scope_queries": list(oos),
            },
            "metrics": metric("suprivisor"),
        })

    # 3a) Disputes agent
    dispute = response.get("dispute_result")
    if dispute is not None:
        trace.append({
            "agent": "disputes",
            "title": "Disputes",
            "status": "fallback" if dispute.status == "error" else "ok",
            "summary": f"Status: {dispute.status}",
            "details": {"status": dispute.status, "summary": dispute.summary, "data": dispute.data},
            # the gate already accounts for its own extraction call
            "metrics": _sum_metrics(metrics, *(("dispute_reply",) if gate_open else ("disputes_extraction", "dispute_reply"))),
        })

    # 3b) Help centre (RAG) branch
    help_resp = response.get("help_center_response")
    help_q = response.get("help_center_queries") or []
    if help_resp is not None or help_q:
        trace.append({
            "agent": "help_center",
            "title": "Help Centre",
            "status": "ok",
            "summary": (
                f"Answered {len(help_q)} help quer{'y' if len(help_q) == 1 else 'ies'} from the "
                + "help documents (RAG)"
            ),
            "details": {
                "help_center_queries": list(help_q),
                "help_source": response.get("help_source"),
                "citation_check": response.get("help_citation_report"),
            },
            "metrics": metric("help_center"),
        })

    # 4) Text-to-SQL (+ optional correction)
    t2s = response.get("text2sql_generator_response")
    if t2s is not None:
        sql_query = getattr(t2s, "sql_query", "") or ""
        trace.append({
            "agent": "text2sql_generator",
            "title": "Text-to-SQL",
            "status": "ok",
            "summary": "Generated SQL query",
            "details": {"sql_query": sql_query},
            "metrics": metric("text2sql_generator"),
        })
        if response.get("sql_query_correction"):
            trace.append({
                "agent": "sql_correction",
                "title": "SQL Correction",
                "status": "ok",
                "summary": "Repaired a failed SQL query",
                "details": {},
                "metrics": metric("sql_correction"),
            })

        # 5) SQL execution (DB call — no LLM metrics)
        rows = response.get("database_query_results")
        if rows is not None:
            trace.append({
                "agent": "sql_execution",
                "title": "SQL Execution",
                "status": "ok",
                "summary": f"Returned {len(rows)} row{'' if len(rows) == 1 else 's'}",
                "details": {"row_count": len(rows), "sample_rows": [str(r) for r in rows[:5]]},
                "metrics": None,
            })

    # 6) Visualization
    viz = response.get("visualization_response")
    if viz is not None:
        decision = getattr(viz, "decision", None)
        should = bool(getattr(decision, "should_visualize", False)) if decision else False
        rendered = bool(getattr(viz, "plotly_json", None))
        trace.append({
            "agent": "visualization_agent",
            "title": "Visualization",
            "status": "ok" if should else "skipped",
            "summary": (
                f"{getattr(decision, 'chart_type', 'chart')} chart"
                if should else "No chart needed"
            ),
            "details": {
                "chart_type": getattr(decision, "chart_type", None) if decision else None,
                "chart_title": getattr(decision, "chart_title", None) if decision else None,
                "reasoning": getattr(decision, "reasoning", None) if decision else None,
                "rendered": rendered,
            },
            "metrics": metric("visualization_agent"),
        })

    # 7) Response generator
    rg = response.get("response_generation_response")
    if rg is not None:
        trace.append({
            "agent": "response_generator",
            "title": "Response Generator",
            "status": "ok",
            "summary": "Rephrased results into a chat reply",
            "details": {"generated_response": getattr(rg, "generated_response", "")},
            "metrics": metric("response_generator"),
        })

    # 8) Fallback (non-guardrail)
    if fallback_active and fallback_node and fallback_node != "guardrail":
        trace.append({
            "agent": "fallback",
            "title": "Fallback",
            "status": "fallback",
            "summary": f"Triggered at '{fallback_node}'",
            "details": {
                "fallback_node": fallback_node,
                "fallback_reason": getattr(fallback, "fallback_reason", None),
            },
            "metrics": None,
        })

    return trace


def build_agent_graph(llm: Any, prompts: Any, logger: Any, db_conn: Any, settings: Any,
                      dispute_registry: Any = None, help_vector_store: Any = None,
                      redis_client: Any = None) -> Agent_Graph:
    """Compile the outer graph (with the SQL subgraph as a node)."""
    agent_subgraph = Agent_SubGraph(openai_llm=llm, prompts=prompts, logger=logger,
                                    db_conn=db_conn, redis_client=redis_client)
    return Agent_Graph(openai_llm=llm, prompts=prompts, logger=logger,
                       agent_subgraph=agent_subgraph, db_conn=db_conn, settings=settings,
                       dispute_registry=dispute_registry, help_vector_store=help_vector_store)


def build_graph_state(user_query: str, customer_id: str, session_id: str,
                      chat_history: list[ChatMessage] | None = None) -> Graph_State:
    """Initial state for one turn: the prior history plus the new user message."""
    history = list(chat_history or []) + [ChatMessage(role="user", content=user_query)]
    return Graph_State(
        input_request=UIUserRequest(user_query=user_query, customer_id=customer_id,
                                    session_id=session_id),
        chat_history=history,
        customer_id=customer_id,
        context_builder_response=None,
        suprivisor_response=None,
        text2sql_generator_response=None,
        database_query_results=None,
        banksql_queries=None,
        help_queries=None,
        out_of_scope_queries=None,
        fallback_response=None,
        response_generation_response=None,
        agent_metrics=None,
    )


async def run_turn(agent_graph: Agent_Graph, state: Graph_State,
                   config: dict | None = None) -> dict:
    """Invoke the graph for one turn and attach `agent_trace`.

    `config` is merged over GRAPH_CONFIG (e.g. LangSmith run_name / tags / metadata).
    Raises GraphRecursionError when the step limit is hit; callers decide how to report it."""
    response = await agent_graph.app.ainvoke(state, config={**GRAPH_CONFIG, **(config or {})})
    response["agent_trace"] = _build_agent_trace(response)
    return response


__all__ = ["GRAPH_CONFIG", "GraphRecursionError", "build_agent_graph", "build_graph_state",
           "run_turn", "_build_agent_trace", "_metric_dict", "_sum_metrics"]
