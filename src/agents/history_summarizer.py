from typing import Any
from logging import Logger
import json

from pydantic import BaseModel
from langchain_core.prompts import PromptTemplate

from app_models import ChatMessage


class _SummaryOut(BaseModel):
    summary: str


async def summarize_chat_history(
    history_without_current: list[ChatMessage],
    llm: Any,
    prompt: PromptTemplate,
    logger: Logger,
) -> list[ChatMessage]:
    """
    Compress history_without_current by summarising all but the last 4 messages
    (2 exchange pairs) and prepending a summary ChatMessage.
    Returns the compressed list; caller re-appends the current user message.
    Falls back to the original list if LLM call fails.
    """
    if len(history_without_current) < 6:
        return history_without_current

    verbatim_tail = history_without_current[-4:]
    older = history_without_current[:-4]

    # Build list of {user, assistant} pairs from older exchanges
    pairs = []
    for i in range(0, len(older) - 1, 2):
        pairs.append({"user": older[i].content, "assistant": older[i + 1].content})

    if not pairs:
        return history_without_current

    messages = [
        {"role": "system", "content": prompt.template},
        {"role": "user", "content": json.dumps(pairs, ensure_ascii=False)},
    ]
    try:
        structured_llm = llm.with_structured_output(_SummaryOut, include_raw=False)
        result = await structured_llm.ainvoke(messages)
        summary_msg = ChatMessage(role="assistant", content=result.summary)
        logger.info("[summarizer] History compressed successfully.")
        return [summary_msg] + verbatim_tail
    except Exception as exc:
        logger.warning(f"[summarizer] Compression failed, keeping full history: {exc}")
        return history_without_current
