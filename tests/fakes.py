"""A chat model that plays back a script, so tests need no API keys.

messages_script    AIMessages returned, in order, by plain/streamed calls (the orchestrator
                   turns and the help-centre answer).
structured_script  parsed objects returned, in order, by with_structured_output(...) calls
                   (the guardrail and the dispute extraction).
"""
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def ai(content: str = "", tool_calls: list | None = None) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata=USAGE,
        response_metadata={"model_name": "fake"},
    )


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class ScriptedChatModel(BaseChatModel):
    messages_script: list = []
    structured_script: list = []
    seen: list = []              # message lists passed to plain calls
    structured_seen: list = []   # message lists passed to structured calls

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        if not self.messages_script:
            raise AssertionError("ScriptedChatModel ran out of scripted messages")
        return ChatResult(generations=[ChatGeneration(message=self.messages_script.pop(0))])

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        async def _run(messages):
            self.structured_seen.append(messages)
            if not self.structured_script:
                raise AssertionError("ScriptedChatModel ran out of scripted structured outputs")
            parsed = self.structured_script.pop(0)
            if include_raw:
                return {"raw": ai(), "parsed": parsed, "parsing_error": None}
            return parsed

        def _sync(_):
            raise NotImplementedError("use ainvoke")

        return RunnableLambda(_sync, afunc=_run)
