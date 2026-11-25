from logging import Logger
from app_models import Graph_State,FallbackResponse

FALLBACK_MESSAGES = {
    "exception": (
        "I'm sorry, but I'm currently unable to process your request due to a "
        "technical issue. Please try again later."
    ),

    "multiple_valid": (
        "You’ve asked more than one question that I can help with:\n\n"
        "{valid_items}\n\n"
        "Please tell me which **one** you’d like me to answer."
    ),

    "needs_separate_questions": (
        "I can help with each of these, but they need to be asked separately:\n\n"
        "{valid_items}\n\n"
        "Could you ask them **one at a time**? That way I can give you an accurate answer for each."
    ),

    "out_of_scope_only": (
        "I’m not able to help with the following request(s):\n\n"
        "{invalid_items}\n\n"
        "You can ask me about your transactions, spending by category, merchant history, or card usage."
    ),

    "mixed_valid_invalid": (
        "I can help with the following question(s):\n\n"
        "{valid_items}\n\n"
        "However, I can’t help with some of the other requests you mentioned.\n\n"
        "Out-of-scope request(s):\n"
        "{invalid_items}\n\n"
        "Please tell me **which one valid question** you’d like me to answer."
    ),
}


async def fallback_agent(
    state: Graph_State,
    logger: Logger
)-> Graph_State:
    logger.info("Executing fallback agent.")
    
    # If a specific fallback response was already set by a previous node (like guardrail) and it's not an exception, preserve it.
    if state.fallback_response and state.fallback_response.fallback_node == "guardrail" and state.fallback_response.fallback_reason != "exception":
        logger.info("Preserving existing guardrail fallback response.")
        return state

    # Render the final prompt string using .format()
    try:
        valid = getattr(state, "banksql_queries", []) or []
        invalid = getattr(state, "out_of_scope_queries", []) or []

        # 1️⃣ Exception
        if state.fallback_response and state.fallback_response.fallback_reason == "exception":
            message = FALLBACK_MESSAGES["exception"]
            reason = "exception"

        # 1️⃣b Multiple valid intents that can't be combined into one SQL query →
        #     ask the user to send them separately.
        elif state.fallback_response and state.fallback_response.fallback_reason == "needs_separate_questions":
            message = FALLBACK_MESSAGES["needs_separate_questions"].format(
                valid_items="\n".join(f"• {q}" for q in valid)
            )
            reason = "needs_separate_questions"

        # 2️⃣ Mixed: multiple valid + one or more invalid
        elif len(valid) > 1 and len(invalid) > 0:
            message = FALLBACK_MESSAGES["mixed_valid_invalid"].format(
                valid_items="\n".join(f"• {q}" for q in valid),
                invalid_items="\n".join(f"• {q}" for q in invalid),
            )
            reason = "mixed_valid_invalid"

        # 3️⃣ Multiple valid only
        elif len(valid) > 1:
            message = FALLBACK_MESSAGES["multiple_valid"].format(
                valid_items="\n".join(f"• {q}" for q in valid)
            )
            reason = "multiple_valid"

        # 4️⃣ Invalid only
        elif len(invalid) > 0:
            message = FALLBACK_MESSAGES["out_of_scope_only"].format(
                invalid_items="\n".join(f"• {q}" for q in invalid)
            )
            reason = "multiple_invalid"

        # 5️⃣ Catch-all: single valid query that failed downstream (e.g. sql query_error)
        else:
            message = FALLBACK_MESSAGES["exception"]
            reason = state.fallback_response.fallback_reason if state.fallback_response else "exception"
        # Keep WHERE it went wrong and the underlying error, so the UI trace can explain it.
        # With no earlier fallback, the route here came from the supervisor's classification.
        previous = state.fallback_response
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node=(previous.fallback_node if previous and previous.fallback_node else "supervisor"),
            fallback_reason=reason,
            fallback_message=message,
            error_detail=previous.error_detail if previous else None,
        )
        state.fallback_response= fallback_response

    except Exception as e:
        logger.exception("Fallback agent failure")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="fallback_agent",
            fallback_reason="exception",
            fallback_message=FALLBACK_MESSAGES["exception"],
            error_detail=f"{type(e).__name__}: {e}",
        )
        state.fallback_response= fallback_response
    return state