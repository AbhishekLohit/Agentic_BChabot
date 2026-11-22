"""The Disputes agent.

A fixed workflow, not a free-roaming agent. Per turn the model makes ONE call that reads
the customer's message into structured fields (amount, date, reason, answers, intent).
Everything else is code: finding and checking the payment, eligibility, the order of steps,
which questions come next, the pre-checks, the confirmation gates and the write.

    date + merchant + amount -> search the database -> exact match: customer says yes,
    otherwise: customer picks one of the closest -> confirm the reason -> pre-checks
    -> ask the reason's questions -> summary + confirmation -> file the case

The customer must give the date, the merchant and the amount of the payment before anything is
searched. An exact match (same date, amount to the penny, same merchant) is shown back for a yes.
Otherwise the closest few payments on that exact date, within a few pounds and with a merchant
that fuzzily matches, are listed for the customer to pick. Nothing proceeds until then.

The draft lives in SQLite between turns, so the customer can answer over several messages
and ask unrelated things in between (see dispute_gate in agent_graph.py).
"""
import hashlib
import json
import time
from dataclasses import asdict
from datetime import date
from logging import Logger
from typing import Any

from agents.disputes import checks
from agents.disputes.registry import DisputeRegistry, SlotConfig
from app_models import DisputeDraft, DisputeResult, DisputeTurnExtraction
from utils import agent_utils, db_utils

ASK_PER_TURN = 2
MAX_CANDIDATES = 5
# A message must be at least this long to stand in as the description of the problem.
MIN_WORDS_FOR_DESCRIPTION = 5
# Never a real answer to a question that asks for an explanation.
_BARE_ACKS = {"yes", "y", "no", "n", "ok", "okay", "yeah", "yep", "nope", "sure", "thanks", "thank you"}

TECHNICAL_ERROR = (
    "I'm sorry, but I'm currently unable to process your request due to a technical issue. "
    "Please try again later."
)
HANDOFF_MESSAGE = (
    "I've recorded this as a suspected unauthorised payment and marked it for our fraud team to "
    "follow up. If you think your card or account may be at risk, please contact Barclays straight "
    "away using the number on the back of your card."
)


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fill(text: str, txn: dict | None) -> str:
    """Fill {merchant}, {amount} and {date} in a question from the transaction."""
    if txn is None:
        values = {"merchant": "the merchant", "amount": "the amount", "date": "that date"}
    else:
        values = {
            "merchant": txn["merchant_brand_name"],
            "amount": f"£{float(txn['amount_usd']):,.2f}",
            "date": str(txn["transaction_date"])[:10],
        }
    return text.format_map(_SafeFormat(**values))


def _label(slot_id: str) -> str:
    return slot_id.replace("_", " ").capitalize()


def _fingerprint(draft: DisputeDraft) -> str:
    payload = json.dumps(
        {"t": draft.transaction_id, "r": draft.reason, "a": draft.answers}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _clean_amount(value: float | None) -> float | None:
    """An amount the model read out of the message, or None if it is not a usable amount."""
    try:
        amount = round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None
    return amount if amount is not None and amount > 0 else None


def _clean_date(value: str | None, today: date) -> date | None:
    """A date the model read out of the message, or None if it is not a real, past date."""
    if not value:
        return None
    try:
        parsed = date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None
    return parsed if parsed <= today else None


def _stage(draft: DisputeDraft) -> str:
    if draft.transaction_id is None:
        if draft.proposed_transaction_id:
            return "confirming_transaction"
        return "choosing_transaction" if draft.candidates else "finding_transaction"
    if draft.awaiting_recognition:
        return "awaiting_recognition"
    if draft.status == "awaiting_confirmation":
        return "awaiting_confirmation"
    if draft.reason is None:
        return "choosing_reason"
    return "collecting_details"


def _candidate(txn: dict) -> dict:
    return {
        "transaction_id": txn["transaction_id"],
        "date": str(txn["transaction_date"])[:10],
        "merchant": txn["merchant_brand_name"],
        "amount": round(float(txn["amount_usd"]), 2),
        "type": txn["transaction_type"],
    }


def _identical(candidates: list[dict]) -> bool:
    """Two or more listed payments with the same date, merchant and amount: the same charge
    taken more than once, so there is nothing to tell them apart."""
    return len(candidates) > 1 and len(
        {(c["date"], round(float(c["amount"]), 2), str(c["merchant"]).lower()) for c in candidates}
    ) == 1


def _candidate_line(c: dict) -> str:
    suffix = "" if c.get("type") == "payment" else f" ({str(c.get('type')).replace('_', ' ')})"
    return f"£{c['amount']:,.2f} to {c['merchant']} on {c['date']}{suffix}"


# ---------------------------------------------------------------------------
# Draft persistence
# ---------------------------------------------------------------------------

def load_open_draft(db_conn: Any, customer_id: str, session_id: str, max_age_hours: int) -> DisputeDraft | None:
    """The dispute in progress for this session, or None."""
    raw = db_utils.get_dispute_draft(db_conn, customer_id, session_id, max_age_hours)
    return DisputeDraft.model_validate_json(raw) if raw else None


def _save_draft(db_conn: Any, customer_id: str, session_id: str, draft: DisputeDraft) -> None:
    db_utils.save_dispute_draft(db_conn, customer_id, session_id, draft.model_dump_json())


def _clear_draft(db_conn: Any, customer_id: str, session_id: str) -> None:
    db_utils.clear_dispute_draft(db_conn, customer_id, session_id)


def abandon_draft(db_conn: Any, customer_id: str, session_id: str, draft: DisputeDraft, why: str) -> None:
    """Drop an open draft (the customer moved on to a different dispute)."""
    db_utils.add_dispute_event(
        db_conn, customer_id, session_id, "draft_abandoned",
        {"transaction_id": draft.transaction_id, "why": why},
    )
    _clear_draft(db_conn, customer_id, session_id)


# ---------------------------------------------------------------------------
# The one model call per turn
# ---------------------------------------------------------------------------

async def _extract(
    llm: Any,
    prompt: Any,
    registry: DisputeRegistry,
    draft: DisputeDraft,
    txn: dict | None,
    proposed_txn: dict | None,
    user_message: str,
    logger: Logger,
    agent_metrics: dict,
    dispute_open: bool,
    today: date,
) -> DisputeTurnExtraction:
    options = "\n".join(
        f"- {rid}: {t.label} - {t.description}" for rid, t in registry.types.items()
    )
    slots_text = "none yet (reason not chosen)"
    if draft.reason:
        lines = []
        for s in registry.slots_for(draft.reason):
            answer = draft.answers.get(s.id)
            suffix = f'  [answer so far: "{answer}"]' if answer else ""
            lines.append(f"- {s.id}: {_fill(s.question, txn)}{suffix}")
        slots_text = "\n".join(lines)
    elif registry.common_slots.before:
        slots_text = "\n".join(
            f"- {s.id}: {_fill(s.question, txn)}" for s in registry.common_slots.before
        )

    transaction_text = (
        f"{checks.describe(txn)} (type: {txn['transaction_type']})" if txn else "not chosen yet"
    )
    proposed_text = f"{checks.describe(proposed_txn)}" if proposed_txn else "none"
    candidates_text = "\n".join(
        f"- {c['transaction_id']}: {_candidate_line(c)}" for c in draft.candidates
    ) or "none"
    so_far = (
        f"amount={draft.payment_amount if draft.payment_amount is not None else 'none'}, "
        f"date={draft.payment_date or 'none'}, merchant={draft.merchant_hint or 'none'}"
    )
    user_block = (
        f"today: {today.isoformat()}\n"
        f"dispute_open: {'yes' if dispute_open else 'no'}\n"
        f"transaction: {transaction_text}\n"
        f"proposed_payment (shown to the customer, waiting for their yes or no): {proposed_text}\n"
        f"payment_details_given_so_far: {so_far}\n"
        f"candidate_payments (listed to the customer, pick by id):\n{candidates_text}\n"
        f"stage: {_stage(draft)}\n"
        f"reason_options:\n{options}\n"
        f"current_reason: {draft.reason or 'none'}\n"
        f"slots:\n{slots_text}\n"
        f"asked_last_turn: {', '.join(draft.pending_slot_ids) or 'none'}\n"
        f"customer_message: {user_message}"
    )
    messages = [
        {
            "role": "system",
            "content": [{
                "type": "text",
                "text": prompt.template,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }],
        },
        {"role": "user", "content": user_block},
    ]

    structured_llm = llm.with_structured_output(DisputeTurnExtraction, include_raw=True)
    start = time.time()
    raw = await structured_llm.ainvoke(messages)
    latency = time.time() - start

    try:
        agent_utils.record_metric(
            agent_metrics, "disputes_extraction", agent_utils.agent_metrics(raw["raw"], latency)
        )
    except Exception as e:
        logger.warning(f"Disputes extraction metrics unavailable: {e}")

    parsed = raw.get("parsed")
    if parsed is None:
        logger.warning("Disputes extraction could not be parsed; treating the message as unclear.")
        return DisputeTurnExtraction(intent="unclear")
    return parsed


async def extract_for_open_draft(
    *,
    llm: Any,
    prompt: Any,
    registry: DisputeRegistry,
    db_conn: Any,
    logger: Logger,
    customer_id: str,
    draft: DisputeDraft,
    user_message: str,
    agent_metrics: dict,
) -> DisputeTurnExtraction:
    """Read a message that arrived while a dispute is open. Used by the graph's gate, which
    also needs to know whether the message belongs to the dispute at all."""
    txn = checks.get_transaction(db_conn, customer_id, draft.transaction_id) if draft.transaction_id else None
    proposed = (
        checks.get_transaction(db_conn, customer_id, draft.proposed_transaction_id)
        if draft.proposed_transaction_id else None
    )
    return await _extract(
        llm, prompt, registry, draft, txn, proposed, user_message, logger, agent_metrics,
        dispute_open=True, today=date.today(),
    )


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------

def _ask_result(
    draft: DisputeDraft,
    txn: dict,
    slots: list[SlotConfig],
    findings: list[str],
    registry: DisputeRegistry,
) -> DisputeResult:
    questions = [_fill(s.question, txn) for s in slots]
    label = registry.types[draft.reason].label
    lines = [*findings] if findings else []
    lines.append(f"Transaction under dispute: {checks.describe(txn)}.")
    lines.append(f"Reason: {label}.")
    lines.append("Still needed from the customer (ask naturally, no more than these):")
    lines.extend(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return DisputeResult(
        status="needs_info",
        summary="\n".join(lines),
        needs_user_input=True,
        data={"reason": draft.reason, "slot_ids": [s.id for s in slots], "questions": questions,
              "transaction_id": draft.transaction_id},
    )


def _reason_menu_result(draft: DisputeDraft, txn: dict, registry: DisputeRegistry) -> DisputeResult:
    options = registry.menu()
    lines = [
        f"Transaction under dispute: {checks.describe(txn)}.",
        "The reason for the dispute is not clear yet. Ask the customer which of these best describes the problem:",
    ]
    lines.extend(f"- {text}" for _, text in options)
    return DisputeResult(
        status="needs_reason",
        summary="\n".join(lines),
        needs_user_input=True,
        data={"options": [{"id": rid, "text": text} for rid, text in options],
              "transaction_id": draft.transaction_id},
    )


def _confirmation_result(draft: DisputeDraft, txn: dict, registry: DisputeRegistry) -> DisputeResult:
    label = registry.types[draft.reason].label
    lines = [
        f"Transaction: {checks.describe(txn)}.",
        f"Reason: {label}.",
        "Details given by the customer:",
    ]
    details = []
    for s in registry.slots_for(draft.reason):
        if s.id in draft.answers:
            lines.append(f"- {_label(s.id)}: {draft.answers[s.id]}")
            details.append({"label": _label(s.id), "value": draft.answers[s.id]})
    lines.append("Present this summary accurately and ask the customer whether to submit the dispute.")
    return DisputeResult(
        status="needs_confirmation",
        summary="\n".join(lines),
        needs_user_input=True,
        data={"reason": draft.reason, "reason_label": label, "transaction": checks.describe(txn),
              "answers": draft.answers, "details": details, "transaction_id": draft.transaction_id},
    )


def _not_eligible_result(elig: "checks.Eligibility", txn: dict | None, transaction_id: str | None) -> DisputeResult:
    """Say why a payment cannot be disputed, and which payment it is, so the reply never has to
    guess the payment from anywhere else."""
    summary = elig.message
    data: dict[str, Any] = {"code": elig.code, "transaction_id": transaction_id}
    if txn is not None:
        summary += f" The payment in question is {checks.describe(txn)}."
        data["transaction"] = checks.describe(txn)
    return DisputeResult(status="not_eligible", summary=summary, data=data)


def _need_transaction_result(
    draft: DisputeDraft,
    not_found: bool = False,
    declined: bool = False,
    tolerance: float = 0.0,
) -> DisputeResult:
    """Ask for the date, merchant and amount of the payment, whichever are still missing."""
    missing = []
    if draft.payment_date is None:
        missing.append("date")
    if draft.merchant_hint is None:
        missing.append("merchant")
    if draft.payment_amount is None:
        missing.append("amount")

    lines = []
    if declined:
        lines.append("The customer said none of those was the payment they meant.")
    if not_found:
        lines.append(
            f"No payment to a merchant like \"{draft.merchant_hint}\" was found on exactly {draft.payment_date} "
            f"with an amount within £{tolerance:,.2f} of £{draft.payment_amount:,.2f}. Ask the customer to "
            "check the date (it has to be exactly right), the merchant and the amount."
        )
    else:
        known = []
        if draft.payment_date is not None:
            known.append(f"the date ({draft.payment_date})")
        if draft.merchant_hint is not None:
            known.append(f"the merchant ({draft.merchant_hint})")
        if draft.payment_amount is not None:
            known.append(f"the amount (£{draft.payment_amount:,.2f})")
        if known:
            lines.append(f"The customer has given {', '.join(known)}.")
        lines.append(
            "To find the payment, ask the customer for "
            + ", ".join(f"the {m}" for m in missing) + " of the payment the dispute is about. "
            "All three are needed. The date must be exactly right; the amount can be a few pounds out "
            "and the merchant name does not have to be spelled exactly."
        )
    lines.append(
        "If they are not sure of the details they can ask to see their recent payments first, then carry on."
    )
    return DisputeResult(
        status="needs_transaction",
        summary="\n".join(lines),
        needs_user_input=True,
        data={"missing": missing, "amount": draft.payment_amount, "date": draft.payment_date,
              "merchant": draft.merchant_hint, "not_found": not_found, "tolerance": tolerance},
    )


def _payment_confirmation_result(txn: dict, identical: int = 1) -> DisputeResult:
    """The exact match, shown with its merchant, amount and date for a yes or no. `identical` > 1:
    the same payment was found that many times (the same charge taken more than once)."""
    text = checks.describe(txn)
    if identical > 1:
        summary = (
            f"The customer was charged {identical} times with identical payments: {text}, "
            f"{identical} times.\nShow the merchant, the amount and the date, say it was charged "
            f"{identical} times, and ask the customer to confirm they want to dispute the extra charge."
        )
    else:
        summary = (
            f"An exact match was found for the date, merchant and amount the customer gave: {text}.\n"
            "Show the merchant, the amount and the date, and ask the customer to confirm this is the "
            "payment they want to dispute."
        )
    return DisputeResult(
        status="needs_payment_confirmation",
        summary=summary,
        needs_user_input=True,
        data={"transaction": text, "merchant": txn["merchant_brand_name"],
              "amount": f"£{float(txn['amount_usd']):,.2f}",
              "date": str(txn["transaction_date"])[:10], "transaction_id": txn["transaction_id"],
              "identical_count": identical},
    )


def _choice_result(draft: DisputeDraft) -> DisputeResult:
    """No single exact match: the closest payments (or identical ones), for the customer to pick one."""
    if _identical(draft.candidates):
        lines = [
            f"The customer has {len(draft.candidates)} identical payments with the same merchant, amount "
            "and date, which looks like the same charge taken more than once. Show them, say they are "
            "identical, and ask the customer to reply yes to dispute one of them (or say which one):"
        ]
    else:
        lines = [
            "There is no exact match for the details the customer gave. These are the closest payments "
            "on that date. Show each with its merchant, amount and date, and ask which one the dispute is about:"
        ]
    lines.extend(f"{i}. {_candidate_line(c)}" for i, c in enumerate(draft.candidates, 1))
    lines.append("If it is none of these, they can say so or give corrected details.")
    return DisputeResult(
        status="needs_transaction_choice",
        summary="\n".join(lines),
        needs_user_input=True,
        data={"options": [{**c, "text": _candidate_line(c)} for c in draft.candidates]},
    )


def pending_reminder(
    draft: DisputeDraft,
    txn: dict | None,
    registry: DisputeRegistry,
    proposed: dict | None = None,
) -> str:
    """One line added to a reply when the customer asked something unrelated mid-dispute,
    so they know the dispute is still open and what it is waiting for."""
    subject = f"your dispute about the payment of {checks.describe(txn)}" if txn else "your dispute"
    if draft.transaction_id is None:
        if draft.proposed_transaction_id and proposed:
            question = f"Is this the payment you want to dispute: {checks.describe(proposed)}?"
        elif draft.candidates:
            question = "Which of the payments I listed is the one you want to dispute?"
        else:
            question = "Which payment would you like to dispute? I need its date, merchant and amount."
    elif draft.awaiting_recognition:
        question = "Do you recognise that payment now?"
    elif draft.status == "awaiting_confirmation":
        question = "Shall I submit it?"
    elif draft.reason is None:
        question = "What best describes the problem?"
    else:
        by_id = {s.id: s for s in registry.slots_for(draft.reason)}
        asked = [_fill(by_id[i].question, txn) for i in draft.pending_slot_ids if i in by_id]
        question = " ".join(asked) if asked else "I still have a few questions for you."
    return f'By the way, {subject} is still open. Whenever you are ready: {question} (Say "cancel" to drop it.)'


def plain_reply(result: DisputeResult) -> str:
    """A plain, deterministic reply for a result. Used only if the model that normally writes
    the reply is unavailable."""
    d = result.data
    status = result.status
    if status == "needs_info" and d.get("questions"):
        return "To go on I need a little more detail:\n" + "\n".join(
            f"{i}. {q}" for i, q in enumerate(d["questions"], 1))
    if status == "needs_info" and d.get("recognition_question"):
        return "Do you recognise this payment now?"
    if status == "needs_reason":
        return "Which of these best describes the problem?\n" + "\n".join(
            f"• {o['text']}" for o in d.get("options", []))
    if status == "needs_confirmation":
        lines = [f"Please check these details: {d.get('transaction')}.", f"Reason: {d.get('reason_label')}."]
        lines += [f"• {x['label']}: {x['value']}" for x in d.get("details", [])]
        lines.append("Shall I submit this dispute?")
        return "\n".join(lines)
    if status == "needs_payment_confirmation":
        if (d.get("identical_count") or 1) > 1:
            return (
                f"I found {d['identical_count']} identical payments of {d.get('amount')} to "
                f"{d.get('merchant')} on {d.get('date')}. Shall I dispute the extra charge?"
            )
        return (
            f"I found this payment: {d.get('merchant')}, {d.get('amount')}, on {d.get('date')}. "
            "Is this the payment you want to dispute?"
        )
    if status == "needs_transaction_choice":
        return "I couldn't find an exact match. Is it one of these?\n" + "\n".join(
            f"{i}. {o['text']}" for i, o in enumerate(d.get("options", []), 1))
    if status == "needs_transaction":
        if d.get("not_found"):
            return (
                f"I couldn't find a payment to {d.get('merchant')} on {d.get('date')} close to "
                f"£{d.get('amount'):,.2f}. Could you check the date (it has to be exact), the merchant and the amount?"
            )
        missing = ", ".join(f"the {m}" for m in d.get("missing", ["date", "merchant", "amount"]))
        return f"To find the payment I need {missing} of the payment you want to dispute."
    if status == "filed":
        if d.get("created", True):
            return f"Your dispute has been filed. Your case ID is {d.get('case_id')}."
        return f"A dispute for this payment was already open. Your case ID is {d.get('case_id')}."
    if status == "cancelled":
        return "I've cancelled the dispute. Nothing was submitted."
    if status == "suspected_fraud":
        return HANDOFF_MESSAGE
    return result.summary


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _run_prechecks(
    draft: DisputeDraft,
    txn: dict,
    db_conn: Any,
    customer_id: str,
    session_id: str,
    settings: Any,
    today: date,
) -> tuple[DisputeResult | None, list[str]]:
    """Fixed-SQL checks that can settle a dispute before any questions are asked.
    Returns (a resolved result or None, findings to pass on to the reply writer)."""
    rec = checks.detect_recurring(db_conn, customer_id, txn, settings.RECURRING_MIN_PRIOR_PAYMENTS)
    draft.precheck_notes["recurring"] = asdict(rec)
    findings: list[str] = []
    merchant = txn["merchant_brand_name"]

    if draft.reason == "duplicate_charge":
        dups = checks.detect_duplicate(db_conn, customer_id, txn, settings.DUPLICATE_WINDOW_DAYS)
        draft.precheck_notes["duplicates"] = dups
        if dups:
            listed = "; ".join(f"£{float(d['amount_usd']):,.2f} on {str(d['transaction_date'])[:10]}" for d in dups)
            findings.append(
                f"Check result: another payment to {merchant} for the same amount was found within "
                f"{settings.DUPLICATE_WINDOW_DAYS} days ({listed}). This supports a duplicate charge."
            )
        elif rec.monthly:
            return DisputeResult(
                status="resolved",
                summary=(
                    f"No second matching payment to {merchant} was found, and the payments follow a regular "
                    f"monthly pattern (latest earlier payment {rec.last_date}, typically about "
                    f"£{rec.typical_amount:,.2f}). This looks like a normal recurring charge rather than a duplicate."
                ),
                data={"code": "recurring_not_duplicate"},
            ), findings
        else:
            findings.append(
                f"Check result: no other payment to {merchant} for the same amount was found within "
                f"{settings.DUPLICATE_WINDOW_DAYS} days in the account history. A second charge may still be "
                "pending with the merchant."
            )

    elif draft.reason == "refund_not_received":
        credit = checks.find_refund_credit(db_conn, customer_id, txn)
        draft.precheck_notes["refund_credit"] = credit
        if credit:
            credited = float(credit["amount_usd"])
            paid = float(txn["amount_usd"])
            partial = credited < paid - 0.01
            summary = (
                f"A credit of £{credited:,.2f} from {merchant} arrived on "
                f"{str(credit['transaction_date'])[:10]}, after the payment on {txn['transaction_date']}. "
                "That looks like the refund."
            )
            if partial:
                summary += (
                    f" It is less than the original payment of £{paid:,.2f}, so it may be a partial refund."
                )
            return DisputeResult(
                status="resolved",
                summary=summary,
                data={"code": "refund_found", "credit": credit, "partial": partial},
            ), findings
        wait = checks.refund_wait(txn, today, settings.REFUND_WAIT_DAYS)
        if wait.too_early:
            return DisputeResult(
                status="resolved",
                summary=(
                    f"The payment was made {wait.days_since} days ago. Refunds can take up to "
                    f"{settings.REFUND_WAIT_DAYS} days to arrive, so it is too early to dispute. "
                    f"You could check again after {wait.earliest_check.isoformat()}."
                ),
                data={"code": "refund_too_early", "check_again_after": wait.earliest_check.isoformat()},
            ), findings
        findings.append(f"Check result: no refund from {merchant} has been credited since the payment.")

    elif draft.reason == "cancelled_subscription" and rec.is_recurring:
        findings.append(
            f"Check result: {rec.prior_count} earlier payments to {merchant} were found "
            f"(latest {rec.last_date}), so this looks like a recurring charge."
        )

    return None, findings


def _resolve(db_conn: Any, customer_id: str, session_id: str, draft: DisputeDraft, result: DisputeResult) -> DisputeResult:
    db_utils.add_dispute_event(
        db_conn, customer_id, session_id, "resolved",
        {"transaction_id": draft.transaction_id, "reason": draft.reason, "code": result.data.get("code")},
    )
    _clear_draft(db_conn, customer_id, session_id)
    return result


def _file_case(
    draft: DisputeDraft,
    txn: dict,
    registry: DisputeRegistry,
    db_conn: Any,
    customer_id: str,
    session_id: str,
) -> DisputeResult:
    case_id, created = db_utils.create_dispute(
        db_conn, customer_id, session_id, draft.transaction_id, draft.reason, draft.answers
    )
    db_utils.add_dispute_event(
        db_conn, customer_id, session_id, "filed",
        {"transaction_id": draft.transaction_id, "reason": draft.reason, "created": created},
        case_id=case_id,
    )
    _clear_draft(db_conn, customer_id, session_id)
    label = registry.types[draft.reason].label
    if created:
        summary = (
            f"The dispute has been filed. Case ID: {case_id}. Reason: {label}. Transaction: {checks.describe(txn)}. "
            "The case is open and the team will review the details the customer gave. "
            "It is worth keeping any receipts or messages with the merchant."
        )
    else:
        summary = (
            f"A dispute for this payment and reason was already open, so no duplicate was created. "
            f"Existing case ID: {case_id}."
        )
    return DisputeResult(
        status="filed", summary=summary,
        data={"case_id": case_id, "created": created, "transaction_id": draft.transaction_id},
    )


async def _unrecognised_turn(
    draft: DisputeDraft,
    ext: DisputeTurnExtraction,
    txn: dict,
    registry: DisputeRegistry,
    db_conn: Any,
    customer_id: str,
    session_id: str,
    settings: Any,
) -> DisputeResult:
    """Triage for a payment the customer does not recognise: it ends as recognised
    (nothing to dispute) or as suspected fraud (handed to a person)."""
    merchant = txn["merchant_brand_name"]
    rec = checks.detect_recurring(db_conn, customer_id, txn, settings.RECURRING_MIN_PRIOR_PAYMENTS)
    draft.precheck_notes["recurring"] = asdict(rec)

    def ask_recognition() -> DisputeResult:
        _save_draft(db_conn, customer_id, session_id, draft)
        return DisputeResult(
            status="needs_info",
            summary=(
                f"Transaction under dispute: {checks.describe(txn)}.\n"
                f"Check result: the customer has paid {merchant} {rec.prior_count} times before "
                f"(latest {rec.last_date}, typically about £{rec.typical_amount:,.2f}).\n"
                "Ask whether they recognise this payment now."
            ),
            needs_user_input=True,
            data={"reason": "unrecognised", "recognition_question": True, "transaction_id": draft.transaction_id},
        )

    if draft.awaiting_recognition:
        if ext.recognises_charge is True:
            return _resolve(db_conn, customer_id, session_id, draft, DisputeResult(
                status="resolved",
                summary=f"The customer now recognises the payment to {merchant}, so there is nothing to dispute.",
                data={"code": "recognised"},
            ))
        if ext.recognises_charge is None:
            return ask_recognition()
        draft.awaiting_recognition = False  # still not recognised: move on to the triage questions
    elif not draft.recognition_asked and rec.is_recurring:
        draft.recognition_asked = True
        draft.awaiting_recognition = True
        return ask_recognition()

    slots = registry.slots_for("unrecognised")
    missing = [s for s in slots if s.id not in draft.answers]
    if missing:
        ask = missing[:ASK_PER_TURN]
        draft.pending_slot_ids = [s.id for s in ask]
        _save_draft(db_conn, customer_id, session_id, draft)
        return _ask_result(draft, txn, ask, [], registry)

    db_utils.add_dispute_event(
        db_conn, customer_id, session_id, "suspected_fraud",
        {"transaction_id": draft.transaction_id, "answers": draft.answers,
         "prior_payments_to_merchant": rec.prior_count, "handed_off": True},
    )
    _clear_draft(db_conn, customer_id, session_id)
    answers = "; ".join(f"{_label(k)}: {v}" for k, v in draft.answers.items()) or "none given"
    return DisputeResult(
        status="suspected_fraud",
        summary=(
            f"The customer does not recognise {checks.describe(txn)}. Details: {answers}. "
            "This looks like a possible unauthorised payment rather than a normal dispute, and it has "
            "been passed to the fraud team."
        ),
        data={"transaction_id": draft.transaction_id, "answers": draft.answers,
              "handoff_message": HANDOFF_MESSAGE},
    )


def _propose(
    draft: DisputeDraft,
    txn: dict,
    db_conn: Any,
    customer_id: str,
    session_id: str,
    settings: Any,
    today: date,
    identical: int = 1,
) -> DisputeResult:
    """Show the exact match for a yes or no. If it cannot be disputed, say so now rather than ask
    them to confirm it."""
    elig = checks.check_eligibility(
        txn, today, settings.DISPUTE_WINDOW_DAYS,
        db_utils.has_open_dispute(db_conn, customer_id, txn["transaction_id"]),
    )
    if not elig.ok:
        db_utils.add_dispute_event(
            db_conn, customer_id, session_id, "not_eligible",
            {"transaction_id": txn["transaction_id"], "code": elig.code},
        )
        _clear_draft(db_conn, customer_id, session_id)
        return _not_eligible_result(elig, txn, txn["transaction_id"])
    draft.proposed_transaction_id = txn["transaction_id"]
    return _payment_confirmation_result(txn, identical)


def _find_transaction(
    draft: DisputeDraft,
    ext: DisputeTurnExtraction,
    db_conn: Any,
    customer_id: str,
    session_id: str,
    settings: Any,
    today: date,
) -> DisputeResult | None:
    """Settle which payment the dispute is about.

    Returns None once the customer has CONFIRMED a payment (draft.transaction_id is set);
    otherwise the result that asks them for what is still missing.

        date + merchant + amount  ->  search  ->  exact match: yes or no
                                              ->  otherwise:   pick one of the closest

    All three details are needed before searching. The date must be exact. An exact match is the
    same date, the amount to the penny and the same merchant; it is shown for an explicit yes on a
    later turn. Otherwise up to DISPUTE_MAX_CHOICES payments on that date, within
    DISPUTE_AMOUNT_TOLERANCE pounds and with a merchant that fuzzily matches, are listed; picking
    one (it can only be one of the ids that were listed) is the confirmation. The search is a fixed
    query on this customer's own transactions: no model-written SQL is involved.
    """
    tolerance = settings.DISPUTE_AMOUNT_TOLERANCE
    amount = _clean_amount(ext.transaction_amount)
    on_date = _clean_date(ext.transaction_date, today)
    merchant = ext.merchant_hint.strip() if ext.merchant_hint and ext.merchant_hint.strip() else None
    merchant_changed = merchant is not None and merchant.lower() != (draft.merchant_hint or "").lower()
    # Only details that DIFFER from what the draft already holds are new. Models often repeat the
    # amount, date and merchant from earlier turns (e.g. alongside a "yes"); that is not a new search.
    amount_changed = amount is not None and (
        draft.payment_amount is None or abs(amount - float(draft.payment_amount)) >= 0.005)
    date_changed = on_date is not None and on_date.isoformat() != draft.payment_date
    gave_new_details = amount_changed or date_changed or merchant_changed

    def forget_details(declined: bool) -> DisputeResult:
        draft.payment_amount = None
        draft.payment_date = None
        draft.merchant_hint = None
        draft.last_search = None
        draft.candidates = []
        return _need_transaction_result(draft, declined=declined, tolerance=tolerance)

    # 1. An exact match was shown last turn: the customer's reply decides what happens to it.
    if draft.proposed_transaction_id:
        if ext.intent == "confirm":
            draft.transaction_id = draft.proposed_transaction_id
            draft.proposed_transaction_id = None
            return None
        shown = checks.get_transaction(db_conn, customer_id, draft.proposed_transaction_id)
        if shown is not None and not gave_new_details and ext.intent != "decline":
            same = checks.find_transactions(
                db_conn, customer_id, float(shown["amount_usd"]),
                date.fromisoformat(str(shown["transaction_date"])[:10]), shown["merchant_brand_name"],
                amount_tolerance=0.0, min_merchant_score=settings.DISPUTE_MERCHANT_MATCH, limit=MAX_CANDIDATES,
            )
            n = sum(1 for r in same if checks.is_exact_match(r)) or 1
            return _payment_confirmation_result(shown, n)    # not a clear yes or no: ask again
        draft.proposed_transaction_id = None
        if not gave_new_details:
            return forget_details(declined=True)             # "no, not that one"

    # 2. The closest payments were listed: they picked one, or none of them.
    if draft.candidates and not gave_new_details:
        picked = ext.chosen_transaction_id
        if picked and any(c["transaction_id"] == picked for c in draft.candidates):
            draft.transaction_id = picked
            draft.candidates = []
            return None
        if ext.intent == "decline":
            return forget_details(declined=True)             # "none of those"
        if ext.intent in ("confirm", "provide_info") and _identical(draft.candidates):
            # Identical charges (e.g. charged twice): any of them is the same payment, so a plain
            # "yes" / "either one" picks one that is not already disputed. The duplicate pre-check
            # finds the other charge later.
            pick = next(
                (c for c in reversed(draft.candidates)
                 if not db_utils.has_open_dispute(db_conn, customer_id, c["transaction_id"])),
                draft.candidates[-1],
            )
            draft.transaction_id = pick["transaction_id"]
            draft.candidates = []
            return None
        return _choice_result(draft)                         # unclear: show the list again

    # Restating the same date, merchant and amount after a search found nothing means "look again".
    if not gave_new_details and (amount is not None or on_date is not None or merchant is not None):
        draft.last_search = None

    # 3. Take whatever date, merchant and amount they gave this turn.
    if amount is not None:
        draft.payment_amount = amount
    if on_date is not None:
        draft.payment_date = on_date.isoformat()
    if merchant is not None:
        draft.merchant_hint = merchant
    if gave_new_details:
        draft.candidates = []
        draft.last_search = None

    # 4. All three are needed before anything is searched.
    if draft.payment_amount is None or draft.payment_date is None or draft.merchant_hint is None:
        return _need_transaction_result(draft, tolerance=tolerance)

    # 5. Search, unless this exact search has already come up empty.
    search_key = [draft.payment_amount, draft.payment_date, draft.merchant_hint.lower()]
    if draft.last_search == search_key:
        return _need_transaction_result(draft, not_found=True, tolerance=tolerance)
    rows = checks.find_transactions(
        db_conn, customer_id, draft.payment_amount, date.fromisoformat(draft.payment_date),
        draft.merchant_hint, amount_tolerance=tolerance,
        min_merchant_score=settings.DISPUTE_MERCHANT_MATCH, limit=MAX_CANDIDATES,
    )
    draft.last_search = search_key
    if not rows:
        return _need_transaction_result(draft, not_found=True, tolerance=tolerance)

    # 6. One exact match: show it for a yes. Otherwise list the closest few to pick from.
    exact = [r for r in rows if checks.is_exact_match(r)]
    if len(exact) == 1:
        return _propose(draft, exact[0], db_conn, customer_id, session_id, settings, today)
    if len(exact) > 1 and _identical([_candidate(r) for r in exact]):
        # The same payment charged more than once (e.g. "charged twice"): nothing tells them apart,
        # so show it once with the count, for a yes. The duplicate pre-check finds the other charge.
        pick = next(
            (r for r in reversed(exact)
             if not db_utils.has_open_dispute(db_conn, customer_id, r["transaction_id"])),
            exact[-1],
        )
        return _propose(draft, pick, db_conn, customer_id, session_id, settings, today, identical=len(exact))
    draft.candidates = [_candidate(r) for r in rows[: settings.DISPUTE_MAX_CHOICES]]
    return _choice_result(draft)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_dispute_turn(
    *,
    llm: Any,
    prompt: Any,
    registry: DisputeRegistry,
    db_conn: Any,
    logger: Logger,
    customer_id: str,
    session_id: str,
    user_message: str,
    settings: Any,
    agent_metrics: dict,
    extraction: DisputeTurnExtraction | None = None,
    allow_confirm: bool = True,
    today: date | None = None,
) -> DisputeResult:
    """Run one turn of the dispute workflow.

    customer_id and session_id come from the signed-in request and user_message is the
    customer's message, so nothing a model produces decides whose data is touched.
    `extraction` is the gate's reading of the message when it already made one.
    `allow_confirm` is False when the message also carried an unrelated request: a yes only
    counts when it stands alone.
    """
    try:
        return await _run_turn(
            llm=llm, prompt=prompt, registry=registry, db_conn=db_conn, logger=logger,
            customer_id=customer_id, session_id=session_id, user_message=user_message,
            settings=settings, agent_metrics=agent_metrics,
            extraction=extraction, allow_confirm=allow_confirm, today=today or date.today(),
        )
    except Exception:
        logger.exception("Error in disputes agent")
        return DisputeResult(status="error", summary=TECHNICAL_ERROR)


async def _run_turn(
    *,
    llm: Any,
    prompt: Any,
    registry: DisputeRegistry,
    db_conn: Any,
    logger: Logger,
    customer_id: str,
    session_id: str,
    user_message: str,
    settings: Any,
    agent_metrics: dict,
    extraction: DisputeTurnExtraction | None,
    allow_confirm: bool,
    today: date,
) -> DisputeResult:
    draft = load_open_draft(db_conn, customer_id, session_id, settings.DISPUTE_DRAFT_MAX_AGE_HOURS)
    is_new = draft is None
    if is_new:
        draft = DisputeDraft()

    # The confirmation gate: a "yes" only counts if the summary was shown on an EARLIER turn.
    was_awaiting = draft.status == "awaiting_confirmation"

    txn = checks.get_transaction(db_conn, customer_id, draft.transaction_id) if draft.transaction_id else None
    proposed_txn = (
        checks.get_transaction(db_conn, customer_id, draft.proposed_transaction_id)
        if draft.proposed_transaction_id else None
    )
    ext = extraction or await _extract(
        llm, prompt, registry, draft, txn, proposed_txn, user_message, logger, agent_metrics,
        dispute_open=not is_new, today=today,
    )
    if not allow_confirm and ext.intent == "confirm":
        ext = ext.model_copy(update={"intent": "unclear"})
    logger.info(f"Disputes extraction: {ext.model_dump()}")

    if ext.intent == "cancel":
        if not is_new:
            db_utils.add_dispute_event(
                db_conn, customer_id, session_id, "cancelled", {"transaction_id": draft.transaction_id}
            )
            _clear_draft(db_conn, customer_id, session_id)
        return DisputeResult(status="cancelled", summary="The dispute was cancelled. Nothing was submitted.")

    # --- reason ------------------------------------------------------------
    reason_changed = False
    reason_first_set = False
    if ext.reason and ext.reason != draft.reason:
        previous = draft.reason
        draft.reason = ext.reason
        reason_first_set = previous is None
        if previous is not None:
            reason_changed = True
            keep = {s.id for s in registry.slots_for(ext.reason)}
            draft.answers = {k: v for k, v in draft.answers.items() if k in keep}
        draft.prechecks_done = False
        draft.recognition_asked = False
        draft.awaiting_recognition = False
        draft.status = "collecting"
        draft.summary_hash = None
        db_utils.add_dispute_event(
            db_conn, customer_id, session_id, "reason_changed" if previous else "reason_set",
            {"transaction_id": draft.transaction_id, "reason": ext.reason, "previous": previous},
        )

    # --- answers -----------------------------------------------------------
    valid_ids = (
        {s.id for s in registry.slots_for(draft.reason)} if draft.reason
        else {s.id for s in registry.common_slots.before}
    )
    changed_answers = False
    for a in ext.answers:
        value = (a.value or "").strip()
        if a.slot_id == "description" and value.lower().strip(" .!") in _BARE_ACKS:
            continue   # a bare "yes" is a reply to something else, never a description
        if a.slot_id in valid_ids and value and draft.answers.get(a.slot_id) != value:
            draft.answers[a.slot_id] = value
            changed_answers = True

    # The words the customer used when they first named the problem are the description, so
    # it is not asked for again. (Not counted as a changed answer: it is not a correction.)
    if (
        reason_first_set
        and "description" in valid_ids
        and "description" not in draft.answers
        and len(user_message.split()) >= MIN_WORDS_FOR_DESCRIPTION
    ):
        draft.answers["description"] = user_message.strip()[:300]

    # --- which payment -------------------------------------------------------
    payment_just_found = draft.transaction_id is None
    if payment_just_found:
        ask = _find_transaction(draft, ext, db_conn, customer_id, session_id, settings, today)
        if ask is not None:
            if ask.status != "not_eligible":      # that case has already dropped the draft
                _save_draft(db_conn, customer_id, session_id, draft)
            return ask
        txn = checks.get_transaction(db_conn, customer_id, draft.transaction_id)

    # --- eligibility (re-read every turn) ------------------------------------
    elig = checks.check_eligibility(
        txn, today, settings.DISPUTE_WINDOW_DAYS,
        db_utils.has_open_dispute(db_conn, customer_id, draft.transaction_id) if txn else False,
    )
    if not elig.ok:
        if payment_just_found:
            db_utils.add_dispute_event(
                db_conn, customer_id, session_id, "not_eligible",
                {"transaction_id": draft.transaction_id, "code": elig.code},
            )
        _clear_draft(db_conn, customer_id, session_id)
        return _not_eligible_result(elig, txn, draft.transaction_id)
    if payment_just_found:
        db_utils.add_dispute_event(
            db_conn, customer_id, session_id, "draft_started", {"transaction_id": draft.transaction_id}
        )

    # --- confirmation gate ------------------------------------------------------
    if was_awaiting and draft.reason and not reason_changed:
        if ext.intent == "confirm" and not changed_answers and draft.summary_hash == _fingerprint(draft):
            db_utils.add_dispute_event(
                db_conn, customer_id, session_id, "confirmed", {"transaction_id": draft.transaction_id}
            )
            return _file_case(draft, txn, registry, db_conn, customer_id, session_id)
        draft.status = "collecting"
        draft.summary_hash = None
        if ext.intent == "decline" and not changed_answers:
            draft.pending_slot_ids = []
            _save_draft(db_conn, customer_id, session_id, draft)
            return DisputeResult(
                status="needs_info",
                summary=(
                    f"Transaction under dispute: {checks.describe(txn)}.\n"
                    "The customer did not want to submit the dispute as summarised. "
                    "Ask what they would like to change."
                ),
                needs_user_input=True,
                data={"reason": draft.reason, "transaction_id": draft.transaction_id},
            )

    # --- reason still unknown ------------------------------------------------------
    if draft.reason is None:
        draft.pending_slot_ids = []
        _save_draft(db_conn, customer_id, session_id, draft)
        return _reason_menu_result(draft, txn, registry)

    # --- unrecognised payment: triage, never filed -----------------------------------
    if draft.reason == "unrecognised":
        return await _unrecognised_turn(
            draft, ext, txn, registry, db_conn, customer_id, session_id, settings
        )

    # --- pre-checks, once per reason ----------------------------------------------------
    findings: list[str] = []
    if not draft.prechecks_done:
        resolved, findings = _run_prechecks(
            draft, txn, db_conn, customer_id, session_id, settings, today
        )
        if resolved is not None:
            return _resolve(db_conn, customer_id, session_id, draft, resolved)
        draft.prechecks_done = True

    # --- ask for what is still missing -----------------------------------------------------
    missing = [s for s in registry.slots_for(draft.reason) if s.id not in draft.answers]
    if missing:
        ask_slots = missing[:ASK_PER_TURN]
        draft.pending_slot_ids = [s.id for s in ask_slots]
        draft.status = "collecting"
        _save_draft(db_conn, customer_id, session_id, draft)
        return _ask_result(draft, txn, ask_slots, findings, registry)

    # --- everything answered: show the summary and wait for a yes -----------------------------
    draft.status = "awaiting_confirmation"
    draft.pending_slot_ids = []
    draft.summary_hash = _fingerprint(draft)
    _save_draft(db_conn, customer_id, session_id, draft)
    db_utils.add_dispute_event(
        db_conn, customer_id, session_id, "summary_shown",
        {"transaction_id": draft.transaction_id, "reason": draft.reason},
    )
    return _confirmation_result(draft, txn, registry)
