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

    "out_of_scope_only": (
        "I’m not able to help with the following request(s):\n\n"
        "{invalid_items}\n\n"
        "You can ask me about food orders, spending, order history, or delivery details."
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
    # Render the final prompt string using .format()
    try:
        valid = state.foodsql_queries
        invalid = state.out_of_scope_queries

        # 1️⃣ Exception
        if state.fallback_response and state.fallback_response.fallback_reason == "exception":
            message = FALLBACK_MESSAGES["exception"]
            reason = "exception"

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

        
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="fallback_agent",
            fallback_reason=reason,
            fallback_message=message
        )
        state.fallback_response= fallback_response

    except Exception:
        logger.exception("Fallback agent failure")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="fallback_agent",
            fallback_reason="exception",
            fallback_message=FALLBACK_MESSAGES["exception"]
        )
        state.fallback_response= fallback_response
    return state