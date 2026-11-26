"""Response generator: how SQL rows are handed to the model (no real model)."""
import asyncio
from unittest.mock import Mock

import pytest
from langchain_core.prompts import PromptTemplate

from agents.response_generator import response_generator_agent
from app_models import Graph_State, UIUserRequest
from fakes import ScriptedChatModel, ai

PROMPT = PromptTemplate.from_template("response prompt")
ROWS = [{"merchant_brand_name": "Rent", "total_spent": 51600.0},
        {"merchant_brand_name": "Hive Pharmacy", "total_spent": 4708.026666666667}]


def _sent(question, rows):
    state = Graph_State(
        input_request=UIUserRequest(customer_id="C", session_id="s", user_query=question),
        chat_history=[], customer_id="C", context_builder_response=None, suprivisor_response=None,
        text2sql_generator_response=None, database_query_results=[dict(r) for r in rows],
        banksql_queries=[question], help_queries=None, out_of_scope_queries=None,
        fallback_response=None, response_generation_response=None, agent_metrics={},
    )
    llm = ScriptedChatModel(messages_script=[ai("reply")], seen=[])
    state = asyncio.run(response_generator_agent(state, llm, PROMPT, Mock()))
    assert state.response_generation_response.generated_response == "reply"
    return llm.seen[0][-1].content


def test_money_is_sent_as_fixed_two_decimal_text():
    sent = _sent("Show my top merchant", ROWS)
    assert "'51,600.00'" in sent and "'4,708.03'" in sent
    assert "51600.0" not in sent


@pytest.mark.parametrize("question", ["What was my average spend in my top 5 merchants last year?",
                                      "show my Top-3 categories", "top five merchants"])
def test_top_n_with_several_rows_asks_for_every_row(question):
    assert "one bullet per row" in _sent(question, ROWS)


@pytest.mark.parametrize("question, rows", [
    ("Show my spend habits this year", ROWS),            # overview: unchanged
    ("Who is my top merchant?", ROWS),                   # singular top
    ("My top 5 merchants", ROWS[:1]),                     # only one row
])
def test_no_top_n_notice_otherwise(question, rows):
    assert "one bullet per row" not in _sent(question, rows)
