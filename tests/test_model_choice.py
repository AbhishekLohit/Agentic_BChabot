"""Per-request chat model choice (Qwen / Haiku) and the history budget."""
import asyncio
from types import SimpleNamespace

from app import _default_choice, _llm_for, _model_choices, list_models
from agents.context_builder import MAX_HISTORY_TOTAL_CHARS, _format_history
from app_models import ChatMessage, UIUserRequest
from app_setting import AppSettings


def _request(choices, default=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        model_choices=choices, default_model_choice=default)))


def _ask(model):
    return UIUserRequest(customer_id="C", session_id="s", user_query="q", model=model)


def test_choices_are_qwen_on_capstone_and_haiku_on_anthropic():
    choices = _model_choices(AppSettings(ANTHROPIC_HAIKU_MODEL="claude-haiku-5-5"))
    assert choices == {"qwen": ("capstone", None), "haiku": ("anthropic", "claude-haiku-5-5")}


def test_the_picked_model_is_used_and_anything_else_gets_the_default():
    qwen, haiku, default = object(), object(), object()
    req = _request({"qwen": qwen, "haiku": haiku})
    assert _llm_for(req, _ask("haiku"), default) is haiku
    assert _llm_for(req, _ask(" Qwen "), default) is qwen
    assert _llm_for(req, _ask(None), default) is default
    assert _llm_for(req, _ask("gpt-9"), default) is default           # unknown choice
    assert _llm_for(_request({"qwen": qwen}), _ask("haiku"), default) is default  # not set up


def test_old_clients_without_a_model_field_still_work():
    assert UIUserRequest(customer_id="C", session_id="s", user_query="q").model is None


def test_models_endpoint_lists_only_what_was_set_up():
    out = asyncio.run(list_models(_request({"qwen": object()}, default="qwen")))
    assert out == {"models": ["qwen"], "default": "qwen"}


def test_claude_haiku_is_the_default_choice_when_it_was_set_up():
    settings = AppSettings(UI_DEFAULT_MODEL="haiku", LLM_PROVIDER="anthropic")
    assert _default_choice(settings, {"haiku": object(), "qwen": object()}) == "haiku"
    assert list(_model_choices(settings))[0] == "haiku"           # listed first in the picker


def test_default_falls_back_when_haiku_is_unavailable():
    no_haiku = {"qwen": object()}
    assert _default_choice(AppSettings(UI_DEFAULT_MODEL="haiku", LLM_PROVIDER="capstone"), no_haiku) == "qwen"
    assert _default_choice(AppSettings(UI_DEFAULT_MODEL="haiku", LLM_PROVIDER="anthropic"), no_haiku) is None


def test_long_history_keeps_the_newest_messages_within_budget():
    history = [ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"msg{i} " + "x" * 1400)
               for i in range(20)]                       # 10 turns of long messages
    text = _format_history(history)
    assert len(text) <= MAX_HISTORY_TOTAL_CHARS + 1600
    assert "msg19" in text and "msg0 " not in text       # newest kept, oldest dropped
    assert text.index("msg18") < text.index("msg19")     # still oldest first
