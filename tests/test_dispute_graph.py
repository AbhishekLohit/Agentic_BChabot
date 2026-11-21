"""The Disputes agent inside the LangGraph workflow: starting a dispute from the supervisor,
and the gate that keeps an open dispute going while the customer asks other things."""
import asyncio
import json
import logging
from datetime import date, timedelta
from types import SimpleNamespace

from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda

from agent_graph import Agent_Graph
from agents.disputes.workflow import plain_reply, run_dispute_turn
from app import _build_agent_trace
from app_models import (
    ChatMessage,
    ClassifiedSubQuery,
    ContextBuilderResponse,
    DisputeDraft,
    DisputeTurnExtraction,
    Graph_State,
    GuardRailResponse,
    Metrics,
    Prompts,
    SlotAnswer,
    SuprivisorResponse,
    Text2SqlGeneratorResponse,
    UIUserRequest,
)
from conftest import events
from fakes import ScriptedChatModel, ai
from utils import db_utils

LOGGER = logging.getLogger("test")

SAFE = GuardRailResponse(guardrail_invoked=None, guardrail_reason=None)
GREETING = GuardRailResponse(guardrail_invoked="GREETING", guardrail_reason=None)


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def prompts() -> Prompts:
    template = PromptTemplate.from_template("template")
    return Prompts(**{name: template for name in Prompts.model_fields})


def ctx(*queries) -> ContextBuilderResponse:
    return ContextBuilderResponse(contextual_queries=list(queries))


def sup(*items) -> SuprivisorResponse:
    """items are (query, class_label) pairs."""
    return SuprivisorResponse(classified_sub_queries=[
        ClassifiedSubQuery(
            sub_query=q, class_label=label,
            transaction_query_type="general" if label == "BankSQL" else "none",
        )
        for q, label in items
    ])


def ext(reason=None, answers=None, intent="provide_info", amount=None, on=None, merchant=None,
        kind="dispute", side=None, chosen=None):
    return DisputeTurnExtraction(
        reason=reason,
        answers=[SlotAnswer(slot_id=k, value=v) for k, v in (answers or {}).items()],
        intent=intent,
        transaction_amount=amount,
        transaction_date=on,
        merchant_hint=merchant,
        chosen_transaction_id=chosen,
        message_kind=kind,
        side_question_text=side,
    )


class StubSql:
    """Stands in for Agent_SubGraph, which the dispute path no longer uses at all: it answers
    ordinary data questions, and `calls` shows what it was asked."""

    def __init__(self, answer_rows=None):
        self.answer_rows = answer_rows if answer_rows is not None else ["{'current_balance': 100.0}"]
        self.calls: list[str] = []
        self.app = RunnableLambda(self._run)

    async def _run(self, state):
        self.calls.append((state.banksql_queries or [""])[0])
        metrics = dict(state.agent_metrics or {})
        metrics["text2sql_generator"] = Metrics(
            model_used="fake", input_tokens=1, output_tokens=1, total_tokens=2, cached_tokens=0,
            latency=0.1, model_cost_without_cache=0.0, model_cost_with_cache=0.0,
        )
        return {
            "text2sql_generator_response": Text2SqlGeneratorResponse(user_query="q", sql_query="SELECT 1"),
            "database_query_results": self.answer_rows,
            "agent_metrics": metrics,
        }


def build(llm, db, registry, settings, sql=None, with_registry=True):
    return Agent_Graph(
        openai_llm=llm, prompts=prompts(), logger=LOGGER, agent_subgraph=sql or StubSql(),
        help_store=None, db_conn=db, settings=settings,
        dispute_registry=registry if with_registry else None,
    )


def run(graph, query, customer="C1", session="S1", history=None):
    state = Graph_State(
        input_request=UIUserRequest(customer_id=customer, session_id=session, user_query=query),
        chat_history=[*(history or []), ChatMessage(role="user", content=query)],
        customer_id=customer,
        context_builder_response=None, suprivisor_response=None, text2sql_generator_response=None,
        database_query_results=None, banksql_queries=None, out_of_scope_queries=None,
        fallback_response=None, response_generation_response=None, agent_metrics=None,
    )
    return asyncio.run(graph.app.ainvoke(state))


def final_text(result) -> str:
    return result["response_generation_response"].generated_response


def draft_of(db, customer="C1", session="S1"):
    raw = db_utils.get_dispute_draft(db, customer, session)
    return json.loads(raw) if raw else None


def save_draft(db, **kw):
    db_utils.save_dispute_draft(db, "C1", "S1", DisputeDraft(**kw).model_dump_json())


ALL_WRONG_AMOUNT = {
    "description": "Charged more than quoted", "expected_amount": "30", "amount_basis": "Quote",
    "difference_reason": "Unknown", "contacted_merchant": "No", "evidence": "Email",
}


def _turn(db, registry, settings, extraction):
    llm = ScriptedChatModel(structured_script=[extraction])
    return asyncio.run(run_dispute_turn(
        llm=llm, prompt=SimpleNamespace(template="x"), registry=registry, db_conn=db, logger=LOGGER,
        customer_id="C1", session_id="S1", user_message="x", settings=settings, agent_metrics={},
    ))


def open_confirmation_draft(db, registry, settings, amount=45.0, on=None):
    """Leave a dispute waiting for the customer's final yes, the way real earlier turns would:
    the payment named and confirmed, every question answered, the summary shown."""
    shown = _turn(db, registry, settings, ext("wrong_amount", ALL_WRONG_AMOUNT, amount=amount, on=on or days_ago(5), merchant="Acme"))
    assert shown.status == "needs_payment_confirmation"
    summary = _turn(db, registry, settings, ext(intent="confirm"))
    assert summary.status == "needs_confirmation"


# --- starting a dispute from the supervisor ---------------------------------------------------------

def test_a_dispute_request_is_routed_to_the_disputes_agent(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    sql = StubSql()
    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ctx("I want to dispute my 45 pound payment"),
            sup(("I want to dispute my 45 pound payment", "Dispute")),
            ext("wrong_amount", amount=45.0, on=days_ago(5), merchant="Acme"),
        ],
        messages_script=[ai("I found £45.00 to Acme. Is this the payment you want to dispute?")],
    )
    result = run(build(llm, db, registry, settings, sql=sql), "I want to dispute my 45 pound payment")

    assert final_text(result) == "I found £45.00 to Acme. Is this the payment you want to dispute?"
    assert result["dispute_result"].status == "needs_payment_confirmation"
    assert result["dispute_handled"] is True
    assert draft_of(db)["proposed_transaction_id"] == "T1" and draft_of(db)["transaction_id"] is None
    assert sql.calls == []                                       # the payment was found by a fixed query, not the SQL agent
    assert "disputes_extraction" in result["agent_metrics"] and "dispute_reply" in result["agent_metrics"]
    assert "text2sql_generator" not in result["agent_metrics"]   # no text-to-SQL call was made


def test_a_dispute_with_only_a_merchant_asks_for_the_date_and_amount(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    llm = ScriptedChatModel(
        structured_script=[SAFE, ctx("dispute my last Acme payment"),
                           sup(("dispute my last Acme payment", "Dispute")), ext(merchant="Acme")],
        messages_script=[ai("What was the date and the amount of the payment?")],
    )
    result = run(build(llm, db, registry, settings), "dispute my last Acme payment")
    assert result["dispute_result"].status == "needs_transaction"
    assert result["dispute_result"].data["missing"] == ["date", "amount"]
    assert draft_of(db)["merchant_hint"] == "Acme"


def test_the_search_only_covers_the_signed_in_customers_payments(db, registry, settings, add_txn):
    add_txn("T_OTHER", days_ago(5), 45.0, merchant="Acme", customer="C2")
    llm = ScriptedChatModel(
        structured_script=[SAFE, ctx("dispute 45 pounds"), sup(("dispute 45 pounds", "Dispute")),
                           ext(amount=45.0, on=days_ago(5), merchant="Acme")],
        messages_script=[ai("I couldn't find that payment.")],
    )
    result = run(build(llm, db, registry, settings), "dispute 45 pounds", customer="C1")
    assert result["dispute_result"].status == "needs_transaction"
    assert result["dispute_result"].data["not_found"] is True
    assert db.execute("SELECT COUNT(*) FROM dispute_events WHERE customer_id = 'C2'").fetchone()[0] == 0


def test_the_reply_falls_back_to_plain_wording_if_the_writer_fails(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    llm = ScriptedChatModel(
        structured_script=[SAFE, ctx("dispute Acme 45"), sup(("dispute Acme 45", "Dispute")),
                           ext("wrong_amount", amount=45.0, on=days_ago(5), merchant="Acme")],
        messages_script=[],          # the writer has nothing scripted, so it raises
    )
    result = run(build(llm, db, registry, settings), "dispute Acme 45")
    assert final_text(result) == plain_reply(result["dispute_result"])
    assert final_text(result).startswith("I found this payment: Acme, £45.00")


def test_a_dispute_and_a_data_question_in_one_message_are_both_answered(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    sql = StubSql(answer_rows=["{'current_balance': 100.0}"])
    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ctx("I was charged twice for 45 pounds.", "What is my balance?"),
            sup(("I was charged twice for 45 pounds.", "Dispute"), ("What is my balance?", "BankSQL")),
            ext("duplicate_charge", amount=45.0, on=days_ago(5), merchant="Acme"),
        ],
        messages_script=[ai("I found the Acme payment. Is this the one?"), ai("Your balance is £100.00.")],
    )
    result = run(build(llm, db, registry, settings, sql=sql), "I was charged twice for 45 pounds. What is my balance?")
    text = final_text(result)
    assert text.index("Acme payment") < text.index("£100.00")          # dispute first, then the answer
    assert sql.calls == ["What is my balance?"]                        # the SQL agent only saw the real question
    assert result["dispute_result"].status == "needs_payment_confirmation"


def test_without_a_dispute_registry_a_dispute_request_is_out_of_scope(db, registry, settings):
    llm = ScriptedChatModel(
        structured_script=[SAFE, ctx("dispute my payment"), sup(("dispute my payment", "Dispute"))],
    )
    result = run(build(llm, db, registry, settings, with_registry=False), "dispute my payment")
    assert "not able to help" in final_text(result)
    assert result.get("dispute_result") is None


# --- confirming the payment ---------------------------------------------------------------------------------

def test_a_yes_to_the_payment_shown_moves_the_dispute_on(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, proposed_transaction_id="T1", payment_amount=45.0, payment_date=days_ago(5))
    sql = StubSql()
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(intent="confirm")],           # the gate reads it once
        messages_script=[ai("Great. Which of these best describes the problem?")],
    )
    result = run(build(llm, db, registry, settings, sql=sql), "yes")
    assert result["dispute_result"].status == "needs_reason"
    assert draft_of(db)["transaction_id"] == "T1" and draft_of(db)["proposed_transaction_id"] is None
    assert len(llm.structured_seen) == 2                           # guardrail + one dispute reading
    assert sql.calls == []


def test_a_no_to_the_payment_shown_asks_for_the_amount_and_date_again(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, proposed_transaction_id="T1", payment_amount=45.0, payment_date=days_ago(5))
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(intent="decline")],
        messages_script=[ai("No problem. What are the amount and the date of the payment?")],
    )
    result = run(build(llm, db, registry, settings), "no, not that one")
    assert result["dispute_result"].status == "needs_transaction"
    assert draft_of(db)["proposed_transaction_id"] is None and draft_of(db)["payment_amount"] is None


# --- continuity: an open dispute and a message that is not about it --------------------------------------

def test_an_unrelated_question_is_answered_and_the_dispute_is_left_open(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"],
               answers={"description": "Overcharged"})
    before = draft_of(db)

    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ext(kind="side_question"),                       # the gate
            ctx("What is my current balance?"),
            sup(("What is my current balance?", "BankSQL")),
        ],
        messages_script=[ai("Your balance is £100.00.")],
    )
    result = run(build(llm, db, registry, settings), "what's my balance?")
    text = final_text(result)

    assert text.startswith("Your balance is £100.00.")
    assert "your dispute about the payment of £45.00 to Acme" in text and "still open" in text
    assert "What amount did you expect to pay?" in text          # the question it is waiting on
    assert result.get("dispute_result") is None                  # the Disputes agent did not run
    assert draft_of(db) == before                                # and the draft is untouched


def test_the_reminder_repeats_the_payment_waiting_for_a_yes(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, proposed_transaction_id="T1", payment_amount=45.0, payment_date=days_ago(5))
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(kind="side_question"), ctx("What is my current balance?"),
                           sup(("What is my current balance?", "BankSQL"))],
        messages_script=[ai("Your balance is £100.00.")],
    )
    text = final_text(run(build(llm, db, registry, settings), "what's my balance?"))
    assert "Is this the payment you want to dispute: £45.00 to Acme" in text
    assert draft_of(db)["proposed_transaction_id"] == "T1"


def test_a_reply_to_the_dispute_goes_straight_to_the_disputes_agent(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount", "amount_basis"],
               answers={"description": "Overcharged"})
    sql = StubSql()
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(answers={"expected_amount": "30", "amount_basis": "Quote"})],
        messages_script=[ai("Thanks. Do you know why the amount differs?")],
    )
    result = run(build(llm, db, registry, settings, sql=sql), "30 pounds, from their quote")

    assert final_text(result) == "Thanks. Do you know why the amount differs?"
    assert result["dispute_result"].status == "needs_info"
    assert sql.calls == []                                       # no SQL agent and no context builder
    assert "context_builder" not in result["agent_metrics"]
    assert draft_of(db)["answers"]["expected_amount"] == "30"
    assert len(llm.structured_seen) == 2                         # guardrail + one dispute reading, not two


def test_a_reply_with_an_unrelated_question_gets_both_answered(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount", "amount_basis"],
               answers={"description": "Overcharged"})
    sql = StubSql(answer_rows=["{'current_balance': 100.0}"])
    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ext(answers={"expected_amount": "30"}, kind="both", side="What is my current balance?"),
            ctx("What is my current balance?"),
            sup(("What is my current balance?", "BankSQL")),
        ],
        messages_script=[ai("Noted the £30. How did you get to that figure?"), ai("Your balance is £100.00.")],
    )
    result = run(build(llm, db, registry, settings, sql=sql), "30 pounds. Also what's my balance?")
    text = final_text(result)

    assert text.index("Noted the £30") < text.index("£100.00")
    assert "\n\n" in text                                  # set apart as separate paragraphs
    assert draft_of(db)["answers"]["expected_amount"] == "30"
    assert sql.calls == ["What is my current balance?"]          # only the unrelated part went to the SQL agent
    context_builder_input = llm.structured_seen[2][1]["content"]
    assert "Latest user question: What is my current balance?" in context_builder_input
    assert "reminder" not in text and "still open" not in text   # the reply already covers the dispute


def test_a_yes_that_also_asks_something_else_does_not_file_the_case(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    open_confirmation_draft(db, registry, settings)
    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ext(intent="confirm", kind="both", side="What is my current balance?"),
            ctx("What is my current balance?"),
            sup(("What is my current balance?", "BankSQL")),
        ],
        messages_script=[ai("Here is the summary again. Shall I submit this dispute?"), ai("Your balance is £100.00.")],
    )
    result = run(build(llm, db, registry, settings), "yes, and what's my balance?")
    assert result["dispute_result"].status == "needs_confirmation"
    assert db.execute("SELECT COUNT(*) FROM disputes").fetchone()[0] == 0
    assert draft_of(db)["status"] == "awaiting_confirmation"


def test_a_yes_that_also_asks_something_else_does_not_confirm_the_payment_either(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, proposed_transaction_id="T1", payment_amount=45.0, payment_date=days_ago(5))
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(intent="confirm", kind="both", side="What is my current balance?"),
                           ctx("What is my current balance?"), sup(("What is my current balance?", "BankSQL"))],
        messages_script=[ai("Is this the payment: Acme, £45.00?"), ai("Your balance is £100.00.")],
    )
    result = run(build(llm, db, registry, settings), "yes, and what's my balance?")
    assert result["dispute_result"].status == "needs_payment_confirmation"
    assert draft_of(db)["transaction_id"] is None


def test_a_bare_yes_is_not_treated_as_a_greeting_while_a_dispute_waits(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    open_confirmation_draft(db, registry, settings)
    llm = ScriptedChatModel(
        structured_script=[GREETING, ext(intent="confirm")],
        messages_script=[
            ai("Hello there!"),                                  # the guardrail drafts a greeting; it is discarded
            ai("Done. Your dispute has been submitted."),        # the writer (forgets the case id)
        ],
    )
    result = run(build(llm, db, registry, settings), "yes")

    assert result["dispute_result"].status == "filed"
    case = db.execute("SELECT case_id FROM disputes").fetchone()
    assert case is not None
    assert case["case_id"] in final_text(result)                 # added even though the writer left it out
    assert "Hello there" not in final_text(result)
    assert draft_of(db) is None


def test_the_same_message_is_still_a_greeting_when_nothing_is_open(db, registry, settings):
    llm = ScriptedChatModel(structured_script=[GREETING], messages_script=[ai("Hello there!")])
    result = run(build(llm, db, registry, settings), "yes")
    assert final_text(result) == "Hello there!"
    assert result["dispute_open"] is False


def test_asking_to_dispute_a_different_payment_replaces_the_open_dispute(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    add_txn("T2", days_ago(3), 12.0, merchant="Tesco")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"])
    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ext(kind="new_dispute"),                             # the gate
            ctx("I want to dispute my 12 pound payment"),
            sup(("I want to dispute my 12 pound payment", "Dispute")),
            ext("duplicate_charge", amount=12.0, on=days_ago(3), merchant="Tesco"),   # the Disputes agent, starting fresh
        ],
        messages_script=[ai("I found £12.00 to Tesco. Is this the payment?")],
    )
    result = run(build(llm, db, registry, settings), "actually dispute my 12 pound payment instead")

    assert "draft_abandoned" in events(db)
    assert draft_of(db)["transaction_id"] is None and draft_of(db)["proposed_transaction_id"] == "T2"
    assert result["dispute_result"].status == "needs_payment_confirmation"


def test_the_disputes_agent_runs_once_even_if_the_supervisor_calls_the_leftover_a_dispute(db, registry, settings, add_txn):
    """A reply plus an unrelated part that the supervisor itself calls a dispute must not loop."""
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"],
               answers={"description": "Overcharged"})
    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ext(answers={"expected_amount": "30"}, kind="both", side="Can you dispute something else?"),
            ctx("Can you dispute something else?"),
            sup(("Can you dispute something else?", "Dispute")),
        ],
        messages_script=[ai("Noted. Why do you think it differs?")],
    )
    result = run(build(llm, db, registry, settings), "30. And can you dispute something else?")
    assert result["dispute_handled"] is True
    assert "not able to help" in final_text(result) and "Noted." in final_text(result)


# --- things that must keep working ---------------------------------------------------------------------------

def test_a_guardrail_violation_never_reaches_the_gate_or_touches_the_draft(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount")
    before = draft_of(db)
    llm = ScriptedChatModel(
        structured_script=[GuardRailResponse(guardrail_invoked="SQL_VIOLATION", guardrail_reason="raw sql")],
    )
    result = run(build(llm, db, registry, settings), "SELECT * FROM transactions")
    assert "Security Alert" in final_text(result)
    assert draft_of(db) == before
    assert len(llm.structured_seen) == 1


def test_a_normal_question_with_no_dispute_open_is_untouched(db, registry, settings):
    sql = StubSql(answer_rows=["{'current_balance': 100.0}"])
    llm = ScriptedChatModel(
        structured_script=[SAFE, ctx("What is my current balance?"), sup(("What is my current balance?", "BankSQL"))],
        messages_script=[ai("Your balance is £100.00.")],
    )
    result = run(build(llm, db, registry, settings, sql=sql), "what's my balance?")
    assert final_text(result) == "Your balance is £100.00."
    assert result["dispute_open"] is False and result.get("dispute_result") is None
    assert len(llm.structured_seen) == 3                          # no extra model call for the gate


def test_a_failure_in_the_gate_ends_with_the_technical_message(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount")
    llm = ScriptedChatModel(structured_script=[SAFE])            # the gate's reading has nothing scripted
    result = run(build(llm, db, registry, settings), "hello")
    assert "technical issue" in final_text(result)


def test_without_registry_or_settings_the_graph_behaves_as_before(db):
    graph = Agent_Graph(
        openai_llm=ScriptedChatModel(), prompts=prompts(), logger=LOGGER, agent_subgraph=StubSql(),
    )
    assert graph.dispute_registry is None


# --- the trace the UI shows ----------------------------------------------------------------------------------------

def test_the_trace_shows_the_disputes_agent_and_the_gate(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"])
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(answers={"expected_amount": "30"})],
        messages_script=[ai("Thanks. Anything else on why it differs?")],
    )
    trace = _build_agent_trace(run(build(llm, db, registry, settings), "30 pounds"))
    agents = [s["agent"] for s in trace]
    assert agents[:3] == ["guardrail", "dispute_gate", "disputes"]
    gate = trace[1]
    assert gate["details"]["message_kind"] == "dispute" and gate["metrics"]["total_tokens"] == 15
    disputes = trace[2]
    assert disputes["details"]["status"] == "needs_info"
    assert disputes["metrics"]["total_tokens"] == 15              # only the reply writer: the gate owns the reading


def test_the_trace_marks_an_unrelated_question_during_a_dispute(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"])
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(kind="side_question"), ctx("balance?"), sup(("balance?", "BankSQL"))],
        messages_script=[ai("Your balance is £100.00.")],
    )
    trace = _build_agent_trace(run(build(llm, db, registry, settings), "balance?"))
    gate = next(s for s in trace if s["agent"] == "dispute_gate")
    assert gate["details"]["message_kind"] == "side_question"
    assert "disputes" not in [s["agent"] for s in trace]


def test_the_trace_shows_the_gate_when_the_customer_switches_to_another_dispute(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    add_txn("T2", days_ago(3), 12.0, merchant="Tesco")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"])
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(kind="new_dispute"), ctx("dispute my 12 pound payment"),
                           sup(("dispute my 12 pound payment", "Dispute")),
                           ext("duplicate_charge", amount=12.0, on=days_ago(3), merchant="Tesco")],
        messages_script=[ai("I found £12.00 to Tesco. Is this the payment?")],
    )
    trace = _build_agent_trace(run(build(llm, db, registry, settings), "dispute my 12 pound payment instead"))
    gate = next(s for s in trace if s["agent"] == "dispute_gate")
    assert gate["details"]["message_kind"] == "new_dispute"
    assert gate["summary"] == "Customer started a different dispute"
    assert [s["agent"] for s in trace].index("dispute_gate") < [s["agent"] for s in trace].index("disputes")


def test_the_amount_and_date_are_read_from_what_the_customer_typed_not_the_context_builders_rewrite(db, registry, settings, add_txn):
    """The context builder fills gaps from earlier conversation. For a dispute the amount has to
    come from the customer, so the Disputes agent reads their own message."""
    add_txn("T1", days_ago(5), 36.67, merchant="Great Northern Hotel")
    llm = ScriptedChatModel(
        structured_script=[
            SAFE,
            ctx("I want to dispute a payment of £36.67 to Great Northern Hotel"),       # filled in from history
            sup(("I want to dispute a payment of £36.67 to Great Northern Hotel", "Dispute")),
            ext(amount=30.0, on=days_ago(5), merchant="Great Northern Hotel"),                                           # what they actually typed
        ],
        messages_script=[ai("I can't find a payment of £30.00 on that date. Could you check?")],
    )
    result = run(build(llm, db, registry, settings), "I want to dispute a payment of about 30 pounds")
    extraction_input = llm.structured_seen[3][1]["content"]
    assert "customer_message: I want to dispute a payment of about 30 pounds" in extraction_input
    assert "36.67" not in extraction_input
    assert result["dispute_result"].status == "needs_transaction"       # 30 is more than £3 from 36.67: no accidental match
    assert draft_of(db)["proposed_transaction_id"] is None


def test_the_reply_writer_is_given_the_dispute_result_but_not_the_chat_history(db, registry, settings, add_txn):
    """Chat history can hold earlier chats; every fact in a dispute reply must come from the result."""
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    history = [ChatMessage(role="user", content="what did I pay Kim Store?"),
               ChatMessage(role="assistant", content="You paid Kim Store £252.35 on 12 June.")]
    llm = ScriptedChatModel(
        structured_script=[SAFE, ctx("dispute 45 pounds"), sup(("dispute 45 pounds", "Dispute")),
                           ext(amount=45.0, on=days_ago(5), merchant="Acme")],
        messages_script=[ai("I found £45.00 to Acme. Is this the payment?")],
    )
    run(build(llm, db, registry, settings), "dispute 45 pounds", history=history)
    writer_input = llm.seen[0][1].content
    assert "Kim Store" not in writer_input and "252.35" not in writer_input
    assert "dispute_in_progress: no" in writer_input and "Acme" in writer_input


def test_a_reply_during_an_open_dispute_tells_the_writer_not_to_start_over(db, registry, settings, add_txn):
    add_txn("T1", days_ago(5), 45.0, merchant="Acme")
    save_draft(db, transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"])
    llm = ScriptedChatModel(
        structured_script=[SAFE, ext(answers={"expected_amount": "30"})],
        messages_script=[ai("Thanks. Why do you think it differs?")],
    )
    run(build(llm, db, registry, settings), "30 pounds")
    assert "dispute_in_progress: yes" in llm.seen[0][1].content
