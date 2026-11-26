"""Context builder: history handling and the clarification path (no real model)."""
import asyncio
from unittest.mock import Mock

from langchain_core.prompts import PromptTemplate

from agents.context_builder import CLARIFICATION_LEAD, context_builder_agent
from agents.fallback import fallback_agent
from app_models import ChatMessage, ContextBuilderResponse, Graph_State, UIUserRequest
from fakes import ScriptedChatModel

PROMPT = PromptTemplate.from_template("context builder prompt")


def _state(query, history=()):
    hist = [ChatMessage(role=r, content=c) for r, c in history]
    hist.append(ChatMessage(role="user", content=query))   # app always appends the current message
    return Graph_State(
        input_request=UIUserRequest(customer_id="Cust_1", session_id="s", user_query=query),
        chat_history=hist, customer_id="Cust_1", context_builder_response=None,
        suprivisor_response=None, text2sql_generator_response=None, database_query_results=None,
        banksql_queries=None, help_queries=None, out_of_scope_queries=None,
        fallback_response=None, response_generation_response=None, agent_metrics={},
    )


def _run(state, reply):
    llm = ScriptedChatModel(structured_script=[reply], structured_seen=[])
    state = asyncio.run(context_builder_agent(state, llm, PROMPT, Mock()))
    return state, llm.structured_seen[0][1]["content"]


def test_history_is_sent_without_the_current_message_as_plain_lines():
    state = _state("and in October?", [("user", "groceries in September"), ("assistant", "£120")])
    state, user_msg = _run(state, ContextBuilderResponse(contextual_queries=["groceries in October"]))
    assert "User: groceries in September\nAssistant: £120" in user_msg
    assert user_msg.count("and in October?") == 1          # only as the latest question
    assert "ChatMessage(" not in user_msg
    assert state.context_builder_response.contextual_queries == ["groceries in October"]
    assert state.fallback_response is None


def test_first_message_says_there_is_no_history():
    _, user_msg = _run(_state("hi"), ContextBuilderResponse(contextual_queries=["hi"]))
    assert "(none" in user_msg


def test_long_replies_are_cut():
    state = _state("and them?", [("user", "list"), ("assistant", "x" * 5000)])
    _, user_msg = _run(state, ContextBuilderResponse(contextual_queries=["q"]))
    assert "x" * 1500 + " ..." in user_msg and "x" * 1501 not in user_msg


def test_clarification_becomes_the_reply_and_survives_the_fallback_agent():
    state, _ = _run(_state("what about the other one?"), ContextBuilderResponse(
        contextual_queries=[], clarification_question="Which merchant do you mean?"))
    fb = state.fallback_response
    assert fb.fallback_node == "context_builder" and fb.fallback_reason == "clarification"
    assert fb.fallback_message == f"{CLARIFICATION_LEAD} Which merchant do you mean?"
    assert state.context_builder_response.contextual_queries == []
    state = asyncio.run(fallback_agent(state, Mock()))
    assert state.fallback_response.fallback_message == fb.fallback_message   # not replaced


def test_a_rewrite_wins_over_a_clarification():
    state, _ = _run(_state("q"), ContextBuilderResponse(
        contextual_queries=["full query"], clarification_question="Which?"))
    assert state.fallback_response is None
    assert state.context_builder_response.contextual_queries == ["full query"]
    assert state.context_builder_response.clarification_question is None


def test_no_second_clarification_in_a_row():
    history = [("user", "how much did I spend there"),
               ("assistant", f"{CLARIFICATION_LEAD} Which merchant do you mean?")]
    state, _ = _run(_state("the usual one", history), ContextBuilderResponse(
        contextual_queries=[], clarification_question="Which one is the usual one?"))
    assert state.fallback_response is None
    assert state.context_builder_response.contextual_queries == ["the usual one"]


def test_empty_reply_falls_back_to_the_customers_words():
    state, _ = _run(_state("my spend at Tesco"), ContextBuilderResponse(contextual_queries=["  "]))
    assert state.fallback_response is None
    assert state.context_builder_response.contextual_queries == ["my spend at Tesco"]


LIST_HISTORY = [("user", "my spend by category"), ("assistant", "• Bills £5 • Travel £3")]


def _run_two(state, first, second):
    llm = ScriptedChatModel(structured_script=[first, second], structured_seen=[])
    state = asyncio.run(context_builder_agent(state, llm, PROMPT, Mock()))
    return state, llm


def test_unresolved_pronoun_is_retried_once_and_the_fix_is_used():
    state, llm = _run_two(_state("avg in these categories", LIST_HISTORY),
                          ContextBuilderResponse(contextual_queries=["avg in these categories"]),
                          ContextBuilderResponse(contextual_queries=["avg in Bills and Travel"]))
    assert len(llm.structured_seen) == 2
    assert '"these"' in llm.structured_seen[1][-1]["content"]
    assert state.context_builder_response.contextual_queries == ["avg in Bills and Travel"]


def test_retry_that_is_still_unresolved_keeps_the_first_answer():
    state, _ = _run_two(_state("avg in them", LIST_HISTORY),
                        ContextBuilderResponse(contextual_queries=["avg in them"]),
                        ContextBuilderResponse(contextual_queries=["avg in those"]))
    assert state.context_builder_response.contextual_queries == ["avg in them"]


def test_no_retry_without_history_or_without_leftover_pronouns():
    for state, reply in [
        (_state("avg in them"), ContextBuilderResponse(contextual_queries=["avg in them"])),
        (_state("avg in them", LIST_HISTORY), ContextBuilderResponse(contextual_queries=["avg in Bills"])),
    ]:
        llm = ScriptedChatModel(structured_script=[reply], structured_seen=[])
        asyncio.run(context_builder_agent(state, llm, PROMPT, Mock()))
        assert len(llm.structured_seen) == 1


def test_dispute_side_question_uses_pipeline_query():
    state = _state("yes it was Tesco. also what's my balance?")
    state.pipeline_query = "what's my balance?"
    state, user_msg = _run(state, ContextBuilderResponse(contextual_queries=["What is my balance?"]))
    assert "Latest user question: what's my balance?" in user_msg
    assert state.context_builder_response.contextual_queries == ["What is my balance?"]
