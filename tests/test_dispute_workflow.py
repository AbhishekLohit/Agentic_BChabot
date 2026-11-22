import asyncio
import json
import logging
from types import SimpleNamespace

from agents.disputes import workflow
from agents.disputes.workflow import run_dispute_turn
from app_models import DisputeDraft, DisputeResult, DisputeTurnExtraction, SlotAnswer
from conftest import TODAY, events
from fakes import ScriptedChatModel
from utils import db_utils

PROMPT = SimpleNamespace(template="extract")
LOGGER = logging.getLogger("test")


def ext(reason=None, answers=None, intent="provide_info", recognises=None,
        amount=None, on=None, merchant=None, chosen=None):
    return DisputeTurnExtraction(
        reason=reason,
        answers=[SlotAnswer(slot_id=k, value=v) for k, v in (answers or {}).items()],
        intent=intent,
        recognises_charge=recognises,
        transaction_amount=amount,
        transaction_date=on,
        merchant_hint=merchant,
        chosen_transaction_id=chosen,
    )


class Flow:
    """Drives run_dispute_turn one customer message at a time."""

    def __init__(self, db, registry, settings, customer="C1", session="S1"):
        self.db, self.registry, self.settings = db, registry, settings
        self.customer, self.session = customer, session
        self.metrics: dict = {}

    def _row(self, transaction_id):
        row = self.db.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
        return dict(row)

    def turn(self, message, extraction, pre_extracted=False, allow_confirm=True):
        """One customer message. `extraction` is what the model "reads" from it."""
        llm = ScriptedChatModel(structured_script=[] if pre_extracted else [extraction])
        result = asyncio.run(run_dispute_turn(
            llm=llm, prompt=PROMPT, registry=self.registry, db_conn=self.db, logger=LOGGER,
            customer_id=self.customer, session_id=self.session, user_message=message,
            settings=self.settings, agent_metrics=self.metrics,
            extraction=extraction if pre_extracted else None, allow_confirm=allow_confirm, today=TODAY,
        ))
        self.last_llm = llm
        return result

    def say(self, message, extraction, txn_id=None, pre_extracted=False, allow_confirm=True):
        """Without txn_id this is one message. With txn_id the customer names that payment by its
        amount and date, is shown it, and says yes, and the result of that yes is returned (so a
        test can go straight on to the reason and the questions). If the payment cannot be
        confirmed (not found, not eligible, several matches) that earlier result is returned."""
        if txn_id is None:
            return self.turn(message, extraction, pre_extracted, allow_confirm)
        row = self._row(txn_id)
        named = extraction.model_copy(update={
            "transaction_amount": row["amount_usd"],
            "transaction_date": str(row["transaction_date"])[:10],
            "merchant_hint": row["merchant_brand_name"],
        })
        shown = self.turn(message, named)
        if shown.status != "needs_payment_confirmation":
            return shown
        return self.turn("yes, that's the one", ext(intent="confirm"))

    def draft(self):
        raw = db_utils.get_dispute_draft(self.db, self.customer, self.session)
        return json.loads(raw) if raw else None

    def cases(self):
        return self.db.execute("SELECT * FROM disputes").fetchall()


def flow(db, registry, settings, **kw):
    return Flow(db, registry, settings, **kw)


# --- the customer has to give the date, the merchant and the amount --------------------------------

def test_with_nothing_given_it_asks_for_the_date_merchant_and_amount(db, registry, settings):
    f = flow(db, registry, settings)
    r = f.say("I have a problem", ext(intent="unclear"))
    assert r.status == "needs_transaction" and r.needs_user_input
    assert r.data["missing"] == ["date", "merchant", "amount"] and r.data["not_found"] is False
    assert "date" in r.summary and "merchant" in r.summary and "amount" in r.summary


def test_only_what_is_still_missing_is_asked_for_and_nothing_is_searched_until_all_three(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    f = flow(db, registry, settings)
    r = f.turn("it was 45 pounds", ext(amount=45.0))
    assert r.status == "needs_transaction" and r.data["missing"] == ["date", "merchant"]
    r = f.turn("on 1 November", ext(on="2026-11-01"))
    assert r.status == "needs_transaction" and r.data["missing"] == ["merchant"]
    assert f.draft()["last_search"] is None                       # amount + date alone are not enough
    r = f.turn("at Acme", ext(merchant="Acme"))                    # what was said before is remembered
    assert r.status == "needs_payment_confirmation" and r.data["merchant"] == "Acme"


def test_amount_and_date_without_a_merchant_are_not_enough(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    r = flow(db, registry, settings).turn("45 on 1 Nov", ext(amount=45.0, on="2026-11-01"))
    assert r.status == "needs_transaction" and r.data["missing"] == ["merchant"]


def test_unusable_amounts_and_dates_count_as_not_given(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    for amount, on in [(-5.0, "not a date"), (0.0, "2099-01-01"), (None, "2026-13-45")]:
        f = flow(db, registry, settings, session=f"S{amount}{on}")
        r = f.turn("whatever", ext(amount=amount, on=on, merchant="Acme"))
        assert r.status == "needs_transaction"
        assert f.draft()["payment_date"] is None


# --- an exact match is shown for a yes ---------------------------------------------------------------------

def test_an_exact_match_is_shown_back_with_its_merchant_amount_and_date(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 36.67, merchant="Great Northern Hotel")
    add_txn("T2", "2026-11-01", 37.50, merchant="Great Northern Hotel")    # close, but not exact
    f = flow(db, registry, settings)
    r = f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Great Northern Hotel"))
    assert r.status == "needs_payment_confirmation" and r.needs_user_input
    assert r.data["merchant"] == "Great Northern Hotel"
    assert r.data["amount"] == "£36.67" and r.data["date"] == "2026-11-01"
    assert f.draft()["transaction_id"] is None and f.draft()["proposed_transaction_id"] == "T1"


def test_a_loosely_written_merchant_still_counts_as_exact(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 36.67, merchant="Great Northern Hotel")
    for said in ("great northern", "GNH", "Great Northern Hotel Ltd"[:20]):
        f = flow(db, registry, settings, session=said)
        r = f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant=said))
        assert r.status == "needs_payment_confirmation", said
        assert r.data["merchant"] == "Great Northern Hotel"          # the real name, from the database


def test_nothing_goes_ahead_until_the_customer_says_yes(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.turn("dispute it", ext("wrong_amount", amount=45.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("the amount was wrong", ext("wrong_amount", {"expected_amount": "30"}))
    assert r.status == "needs_payment_confirmation"
    assert f.draft()["transaction_id"] is None and f.cases() == []
    assert f.draft()["answers"]["expected_amount"] == "30"       # but what they said is kept


def test_yes_confirms_the_payment_and_the_dispute_moves_on(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("yes", ext(intent="confirm"))
    assert r.status == "needs_reason" and "£45.00 to Acme" in r.summary
    assert f.draft()["transaction_id"] == "T1" and f.draft()["proposed_transaction_id"] is None
    assert "draft_started" in events(db)


def test_a_yes_in_the_same_message_as_the_details_does_not_confirm_the_payment(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    r = f.turn("yes it was 45 at Acme on 1 Nov", ext(intent="confirm", amount=45.0, on="2026-11-01", merchant="Acme"))
    assert r.status == "needs_payment_confirmation"
    assert f.draft()["transaction_id"] is None


def test_a_yes_alongside_another_request_does_not_confirm_the_payment(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("yes, and what is my balance?", ext(intent="confirm"), allow_confirm=False)
    assert r.status == "needs_payment_confirmation" and f.draft()["transaction_id"] is None
    assert f.turn("yes", ext(intent="confirm")).status == "needs_reason"


def test_a_vague_reply_to_the_payment_shows_it_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("hmm", ext(intent="unclear"))
    assert r.status == "needs_payment_confirmation" and r.data["merchant"] == "Acme"


def test_no_drops_the_payment_and_asks_for_all_three_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("no, that's not it", ext(intent="decline"))
    assert r.status == "needs_transaction" and r.data["missing"] == ["date", "merchant", "amount"]
    d = f.draft()
    assert d["proposed_transaction_id"] is None and d["payment_amount"] is None and d["merchant_hint"] is None


def test_no_with_a_different_date_searches_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    add_txn("T2", "2026-10-20", 45.0, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("no, it was on 20 October", ext(intent="decline", on="2026-10-20"))
    assert r.status == "needs_payment_confirmation" and f.draft()["proposed_transaction_id"] == "T2"


# --- no exact match: the closest three ---------------------------------------------------------------------

def test_no_exact_match_lists_the_closest_three(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.00, merchant="Great Northern Hotel")    # 0.33 out
    add_txn("B", "2026-11-01", 35.50, merchant="Great Northern Hotel")    # 1.17 out
    add_txn("C", "2026-11-01", 39.00, merchant="Great Northern Hotel")    # 2.33 out
    add_txn("D", "2026-11-01", 39.60, merchant="Great Northern Hotel")    # 2.93 out: 4th, not shown
    add_txn("E", "2026-11-01", 40.00, merchant="Great Northern Hotel")    # 3.33 out: beyond £3
    f = flow(db, registry, settings)
    r = f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Great Northern Hotel"))
    assert r.status == "needs_transaction_choice"
    assert [o["transaction_id"] for o in r.data["options"]] == ["A", "B", "C"]
    assert "no exact match" in r.summary.lower()
    assert f.draft()["transaction_id"] is None and len(f.draft()["candidates"]) == 3


def test_the_merchant_has_to_match_for_a_close_payment_to_be_offered(db, registry, settings, add_txn):
    add_txn("HOTEL", "2026-11-01", 38.00, merchant="Great Northern Hotel")
    add_txn("TESCO", "2026-11-01", 36.70, merchant="Tesco")              # closer amount, wrong merchant
    r = flow(db, registry, settings).turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Grate Nothern"))
    assert [o["transaction_id"] for o in r.data["options"]] == ["HOTEL"]


def test_more_than_three_pounds_out_or_another_day_finds_nothing(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 40.01, merchant="Acme")                   # 3.01 out
    add_txn("T2", "2026-11-02", 37.00, merchant="Acme")                   # right amount, next day
    r = flow(db, registry, settings).turn("dispute it", ext(amount=37.0, on="2026-11-01", merchant="Acme"))
    assert r.status == "needs_transaction" and r.data["not_found"] is True
    assert "exactly right" in r.summary and "£3.00" in r.summary


def test_identical_exact_matches_are_shown_once_with_the_count(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 20.0, merchant="Acme")
    add_txn("T2", "2026-11-01", 20.0, merchant="Acme")
    f = flow(db, registry, settings)
    r = f.turn("dispute it", ext(amount=20.0, on="2026-11-01", merchant="Acme"))
    assert r.status == "needs_payment_confirmation" and r.data["identical_count"] == 2
    assert "charged 2 times" in r.summary and f.draft()["transaction_id"] is None   # still needs a yes


def test_a_yes_to_identical_charges_picks_one_of_them(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 20.0, merchant="Acme")
    add_txn("T2", "2026-11-01", 20.0, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("charged twice", ext(amount=20.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("yes, take one of those", ext(intent="confirm"))
    assert r.status != "needs_transaction_choice"
    assert f.draft()["transaction_id"] in ("T1", "T2") and f.draft()["candidates"] == []


def test_a_yes_to_identical_charges_skips_one_already_disputed(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 20.0, merchant="Acme")
    add_txn("T2", "2026-11-01", 20.0, merchant="Acme")
    db_utils.create_dispute(db, "C1", "S0", "T2", "duplicate_charge", {})
    f = flow(db, registry, settings)
    f.turn("charged twice", ext(amount=20.0, on="2026-11-01", merchant="Acme"))
    f.turn("yes", ext(intent="confirm"))
    assert f.draft()["transaction_id"] == "T1"


def test_a_yes_that_repeats_the_same_details_still_picks_an_identical_charge(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 20.0, merchant="Acme")
    add_txn("T2", "2026-11-01", 20.0, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("charged twice", ext(amount=20.0, on="2026-11-01", merchant="Acme"))
    # the model repeats the details it already knows alongside the yes
    r = f.turn("yes", ext(intent="confirm", amount=20.0, on="2026-11-01", merchant="Acme"))
    assert r.status != "needs_transaction_choice" and f.draft()["transaction_id"] in ("T1", "T2")


def test_a_yes_that_repeats_the_same_details_confirms_a_shown_payment(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 20.0, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=20.0, on="2026-11-01", merchant="Acme"))
    f.turn("yes", ext(intent="confirm", amount=20.0, on="2026-11-01", merchant="Acme"))
    assert f.draft()["transaction_id"] == "T1"


def test_a_yes_to_different_payments_still_needs_a_pick(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.00, merchant="Acme")
    add_txn("B", "2026-11-01", 35.50, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Acme"))
    r = f.turn("yes", ext(intent="confirm"))
    assert r.status == "needs_transaction_choice" and f.draft()["transaction_id"] is None


def test_picking_from_the_list_is_the_confirmation(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.00, merchant="Acme")
    add_txn("B", "2026-11-01", 35.50, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Acme"))
    r = f.turn("the second one", ext(chosen="B"))               # no extra yes is needed
    assert r.status == "needs_reason" and "£35.50" in r.summary
    assert f.draft()["transaction_id"] == "B" and f.draft()["candidates"] == []


def test_a_pick_that_is_not_in_the_list_is_ignored(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.00, merchant="Acme")
    add_txn("B", "2026-11-01", 35.50, merchant="Acme")
    add_txn("Z", "2026-11-01", 99.00, merchant="Other")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Acme"))
    r = f.turn("that one", ext(chosen="Z"))
    assert r.status == "needs_transaction_choice" and f.draft()["transaction_id"] is None


def test_an_unclear_reply_to_the_list_shows_it_again(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.00, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Acme"))
    assert f.turn("hmm", ext(intent="unclear")).status == "needs_transaction_choice"


def test_none_of_those_asks_for_the_details_again(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.00, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Acme"))
    r = f.turn("none of those", ext(intent="decline"))
    assert r.status == "needs_transaction" and r.data["missing"] == ["date", "merchant", "amount"]
    assert "none of those" in r.summary and f.draft()["candidates"] == []


def test_corrected_details_after_the_list_search_again(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.00, merchant="Acme")
    add_txn("X", "2026-11-01", 77.00, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Acme"))
    r = f.turn("none of those, it was 77", ext(amount=77.0))
    assert r.status == "needs_payment_confirmation" and f.draft()["proposed_transaction_id"] == "X"


def test_the_same_failed_search_is_not_repeated(db, registry, settings, add_txn):
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=99.0, on="2026-11-01", merchant="Acme"))
    add_txn("T1", "2026-11-01", 99.0, merchant="Acme")           # appears after the first search
    r = f.turn("hmm", ext(intent="unclear"))
    assert r.status == "needs_transaction" and r.data["not_found"] is True
    r = f.turn("it was 99 at Acme on 1 Nov", ext(amount=99.0, on="2026-11-01", merchant="Acme"))
    assert r.status == "needs_payment_confirmation"


def test_only_the_signed_in_customers_payments_are_searched(db, registry, settings, add_txn):
    add_txn("T_THEIRS", "2026-11-01", 45.0, merchant="Acme", customer="C2")
    f = flow(db, registry, settings, customer="C1")
    r = f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    assert r.status == "needs_transaction" and r.data["not_found"] is True


def test_something_the_customer_already_said_survives_the_search(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 21.0, merchant="Kim Store")
    f = flow(db, registry, settings)
    f.turn("Kim Store charged me the wrong amount",
           ext("wrong_amount", {"description": "Charged the wrong amount"}, amount=20.0, on="2026-11-01",
               merchant="Kim Store"))
    assert f.draft()["reason"] == "wrong_amount"
    r = f.turn("the first one", ext(chosen="A"))
    assert r.status == "needs_info" and r.data["slot_ids"] == ["expected_amount", "amount_basis"]


# --- eligibility --------------------------------------------------------------------------------------------

def test_old_payment_is_not_eligible_and_leaves_no_draft(db, registry, settings, add_txn):
    add_txn("T1", "2026-06-13", 45.0)
    f = flow(db, registry, settings)
    r = f.say("charged twice", ext(), txn_id="T1")
    assert r.status == "not_eligible" and r.data["code"] == "outside_window"
    assert f.draft() is None
    assert events(db).count("not_eligible") == 1


def test_direct_debit_is_not_eligible(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, txn_type="direct_debit")
    r = flow(db, registry, settings).say("wrong amount", ext(), txn_id="T1")
    assert r.status == "not_eligible" and r.data["code"] == "not_card_payment"


def test_a_payment_already_being_disputed_is_explained_at_once(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    db_utils.create_dispute(db, "C1", "OTHER", "T1", "wrong_amount", {})
    r = flow(db, registry, settings).turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    assert r.status == "not_eligible" and r.data["code"] == "already_disputed"


def test_a_payment_picked_from_the_list_is_still_checked_for_eligibility(db, registry, settings, add_txn):
    add_txn("A", "2026-11-01", 37.0, merchant="Acme", txn_type="direct_debit")
    add_txn("B", "2026-11-01", 35.5, merchant="Acme")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=36.67, on="2026-11-01", merchant="Acme"))
    r = f.turn("the first one", ext(chosen="A"))
    assert r.status == "not_eligible" and r.data["code"] == "not_card_payment"
    assert f.draft() is None


# --- choosing the reason --------------------------------------------------------------------------

def test_unclear_reason_returns_the_menu(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    result = f.say("something is wrong with this", ext(intent="unclear"), txn_id="T1")
    assert result.status == "needs_reason" and result.needs_user_input
    assert len(result.data["options"]) == 7
    assert f.draft()["reason"] is None


def test_description_given_before_the_reason_is_kept(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("they took too much", ext(answers={"description": "They took too much"}), txn_id="T1")
    result = f.say("it was the wrong amount", ext(reason="wrong_amount"))
    # description is already answered, so the first two questions are the reason's own.
    assert result.data["slot_ids"] == ["expected_amount", "amount_basis"]


# --- the happy path, over several turns ---------------------------------------------------------------

def test_wrong_amount_dispute_end_to_end(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    f = flow(db, registry, settings)

    r = f.say("Acme charged me the wrong amount",
              ext("wrong_amount", {"description": "Charged more than the quote"}), txn_id="T1")
    assert r.status == "needs_info"
    assert r.data["slot_ids"] == ["expected_amount", "amount_basis"]
    assert len(r.data["questions"]) == 2          # never more than two at a time

    r = f.say("30 pounds, from their email quote",
              ext(answers={"expected_amount": "30.00", "amount_basis": "Emailed quote"}))
    assert r.data["slot_ids"] == ["difference_reason", "contacted_merchant"]

    r = f.say("no idea why, and yes I emailed them",
              ext(answers={"difference_reason": "Not known", "contacted_merchant": "Emailed, no reply"}))
    assert r.data["slot_ids"] == ["evidence"]

    r = f.say("I have the quote email", ext(answers={"evidence": "Quote email"}))
    assert r.status == "needs_confirmation"
    assert "30.00" in r.summary and "Wrong amount" in r.summary
    assert r.data["reason_label"] == "Wrong amount" and r.data["transaction"].startswith("£45.00")
    assert "Acme" in r.data["transaction"] and "2026-11-01" in r.data["transaction"]   # merchant, amount, date
    assert f.cases() == []                         # nothing filed yet

    r = f.say("yes please", ext(intent="confirm"))
    assert r.status == "filed"
    case = f.cases()[0]
    assert case["transaction_id"] == "T1" and case["reason"] == "wrong_amount"
    assert json.loads(case["answers_json"])["expected_amount"] == "30.00"
    assert r.data["case_id"] == case["case_id"]
    assert f.draft() is None
    assert events(db) == ["reason_set", "draft_started", "summary_shown", "confirmed", "filed"]


def _answer_everything(f, reason, answers):
    return f.say("here is everything", ext(reason, answers), txn_id="T1")


WRONG_AMOUNT_ALL = {
    "description": "Charged more than quoted", "expected_amount": "30", "amount_basis": "Quote",
    "difference_reason": "Unknown", "contacted_merchant": "No", "evidence": "Email",
}


# --- the final confirmation gate -----------------------------------------------------------------------------

def test_the_summary_is_only_reached_after_the_payment_was_confirmed(db, registry, settings, add_txn):
    """Every detail is given in the first message, yet two confirmations are still needed."""
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    r = f.turn("everything, in one go",
               ext("wrong_amount", WRONG_AMOUNT_ALL, amount=45.0, on="2026-11-01", merchant="Acme"))
    assert r.status == "needs_payment_confirmation"                 # 1st: is this the payment?
    r = f.turn("yes", ext(intent="confirm"))
    assert r.status == "needs_confirmation"                          # 2nd: the summary
    assert f.cases() == []
    r = f.turn("yes", ext(intent="confirm"))
    assert r.status == "filed"


def test_confirm_in_the_same_message_as_the_details_does_not_file(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    r = f.say("everything, and yes submit it",
              ext("wrong_amount", WRONG_AMOUNT_ALL, intent="confirm"), txn_id="T1")
    assert r.status == "needs_confirmation"
    assert f.cases() == []
    assert f.draft()["status"] == "awaiting_confirmation"


def test_changing_a_detail_after_the_summary_asks_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    _answer_everything(f, "wrong_amount", WRONG_AMOUNT_ALL)

    r = f.say("actually it was 35", ext(answers={"expected_amount": "35"}))
    assert r.status == "needs_confirmation" and "35" in r.summary
    assert f.cases() == []

    r = f.say("yes", ext(intent="confirm"))
    assert r.status == "filed"
    assert json.loads(f.cases()[0]["answers_json"])["expected_amount"] == "35"


def test_confirm_that_also_changes_a_detail_is_not_a_confirmation(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    _answer_everything(f, "wrong_amount", WRONG_AMOUNT_ALL)
    r = f.say("yes but it was 35", ext(answers={"expected_amount": "35"}, intent="confirm"))
    assert r.status == "needs_confirmation"
    assert f.cases() == []


def test_a_yes_alongside_another_request_is_not_a_confirmation(db, registry, settings, add_txn):
    """"yes, and what's my balance?": the yes has to stand alone."""
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    _answer_everything(f, "wrong_amount", WRONG_AMOUNT_ALL)
    r = f.say("yes, and what's my balance?", ext(intent="confirm"), allow_confirm=False)
    assert r.status == "needs_confirmation" and f.cases() == []
    # and on its own it does count
    r = f.say("yes", ext(intent="confirm"))
    assert r.status == "filed"


def test_declining_the_summary_asks_what_to_change(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    _answer_everything(f, "wrong_amount", WRONG_AMOUNT_ALL)
    r = f.say("no, not like that", ext(intent="decline"))
    assert r.status == "needs_info" and "change" in r.summary
    assert f.cases() == [] and f.draft()["status"] == "collecting"


def test_an_unclear_reply_to_the_summary_shows_it_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    _answer_everything(f, "wrong_amount", WRONG_AMOUNT_ALL)
    r = f.say("hmm", ext(intent="unclear"))
    assert r.status == "needs_confirmation" and f.cases() == []


def test_a_tampered_draft_cannot_be_confirmed(db, registry, settings, add_txn):
    """The summary fingerprint must still match what was shown."""
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    _answer_everything(f, "wrong_amount", WRONG_AMOUNT_ALL)
    draft = f.draft()
    draft["answers"]["expected_amount"] = "9999"
    db_utils.save_dispute_draft(db, "C1", "S1", json.dumps(draft))
    r = f.say("yes", ext(intent="confirm"))
    assert r.status == "needs_confirmation" and f.cases() == []


def test_cancelling_clears_the_draft(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("charged twice", ext("duplicate_charge"), txn_id="T1")
    r = f.say("forget it", ext(intent="cancel"))
    assert r.status == "cancelled"
    assert f.draft() is None and f.cases() == []


def test_cancelling_while_a_payment_is_shown_clears_the_draft(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    assert f.turn("never mind", ext(intent="cancel")).status == "cancelled"
    assert f.draft() is None


def test_a_filed_payment_cannot_be_disputed_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    _answer_everything(f, "wrong_amount", WRONG_AMOUNT_ALL)
    f.say("yes", ext(intent="confirm"))
    r = f.say("dispute it again", ext(), txn_id="T1")
    assert r.status == "not_eligible" and r.data["code"] == "already_disputed"
    assert len(f.cases()) == 1


def test_switching_reason_keeps_shared_answers_and_drops_the_rest(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("wrong amount", ext("wrong_amount", {"description": "Overcharged", "expected_amount": "30"}), txn_id="T1")
    r = f.say("sorry, I never got the goods", ext("goods_not_received"))
    assert r.data["slot_ids"] == ["order_date", "promised_delivery"]
    assert f.draft()["answers"] == {"description": "Overcharged"}
    assert "reason_changed" in events(db)


def test_the_draft_survives_between_calls(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("wrong amount", ext("wrong_amount"), txn_id="T1")
    r = f.say("30 pounds", ext(answers={"expected_amount": "30"}))
    assert r.status == "needs_info"
    assert f.draft()["answers"]["expected_amount"] == "30"


def test_drafts_are_per_session(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    flow(db, registry, settings, session="S1").say("wrong amount", ext("wrong_amount"), txn_id="T1")
    other = flow(db, registry, settings, session="S2")
    assert other.say("hello", ext(intent="unclear")).status == "needs_transaction"


def test_answers_for_unknown_slots_are_ignored(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("wrong amount", ext("wrong_amount", {"made_up_slot": "x", "expected_amount": "30"}), txn_id="T1")
    assert f.draft()["answers"] == {"expected_amount": "30"}


# --- a message the gate has already read -----------------------------------------------------------------------

def test_a_pre_read_message_does_not_call_the_model_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("wrong amount", ext("wrong_amount"), txn_id="T1")
    r = f.say("30 pounds", ext(answers={"expected_amount": "30"}), pre_extracted=True)
    assert r.status == "needs_info"
    assert f.last_llm.structured_seen == []
    assert f.draft()["answers"]["expected_amount"] == "30"


def test_a_pre_read_yes_confirms_a_payment_that_was_shown_earlier(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=45.0, on="2026-11-01", merchant="Acme"))
    r = f.turn("yes", ext(intent="confirm"), pre_extracted=True)
    assert r.status == "needs_reason" and f.last_llm.structured_seen == []


# --- pre-checks ------------------------------------------------------------------------------------------------------

def test_duplicate_is_confirmed_by_a_matching_second_payment(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    add_txn("T2", "2026-11-02", 45.0)
    r = flow(db, registry, settings).say("charged twice", ext("duplicate_charge"), txn_id="T1")
    assert r.status == "needs_info"
    assert "another payment" in r.summary and "supports a duplicate" in r.summary


def test_duplicate_that_is_really_a_monthly_charge_is_resolved(db, registry, settings, add_txn):
    for i, d in enumerate(["2026-08-01", "2026-09-01", "2026-10-01"]):
        add_txn(f"P{i}", d, 12.99, merchant="StreamCo")
    add_txn("T1", "2026-11-01", 12.99, merchant="StreamCo")
    f = flow(db, registry, settings)
    r = f.say("charged twice", ext("duplicate_charge"), txn_id="T1")
    assert r.status == "resolved" and r.data["code"] == "recurring_not_duplicate"
    assert f.draft() is None and f.cases() == []
    assert "resolved" in events(db)


def test_duplicate_with_no_match_still_continues(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    r = flow(db, registry, settings).say("charged twice", ext("duplicate_charge"), txn_id="T1")
    assert r.status == "needs_info" and "may still be pending" in r.summary


def test_refund_already_received_is_resolved(db, registry, settings, add_txn):
    add_txn("T1", "2026-10-01", 60.0, merchant="ShopCo")
    add_txn("R1", "2026-10-20", 60.0, merchant="ShopCo", txn_type="income")
    r = flow(db, registry, settings).say("no refund", ext("refund_not_received"), txn_id="T1")
    assert r.status == "resolved" and r.data["code"] == "refund_found"


def test_a_smaller_credit_is_reported_as_a_possible_partial_refund(db, registry, settings, add_txn):
    add_txn("T1", "2026-10-01", 60.0, merchant="ShopCo")
    add_txn("R1", "2026-10-20", 40.0, merchant="ShopCo", txn_type="income")
    r = flow(db, registry, settings).say("no refund", ext("refund_not_received"), txn_id="T1")
    assert r.status == "resolved" and r.data["partial"] is True
    assert "partial refund" in r.summary


def test_a_full_credit_is_not_called_partial(db, registry, settings, add_txn):
    add_txn("T1", "2026-10-01", 60.0, merchant="ShopCo")
    add_txn("R1", "2026-10-20", 60.0, merchant="ShopCo", txn_type="income")
    r = flow(db, registry, settings).say("no refund", ext("refund_not_received"), txn_id="T1")
    assert r.data["partial"] is False and "partial" not in r.summary


def test_refund_too_early_to_dispute_is_resolved(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-05", 60.0, merchant="ShopCo")
    r = flow(db, registry, settings).say("no refund", ext("refund_not_received"), txn_id="T1")
    assert r.status == "resolved" and r.data["code"] == "refund_too_early"
    assert r.data["check_again_after"] == "2026-11-15"


def test_refund_missing_after_the_wait_continues_to_questions(db, registry, settings, add_txn):
    add_txn("T1", "2026-10-01", 60.0, merchant="ShopCo")
    r = flow(db, registry, settings).say("no refund", ext("refund_not_received"), txn_id="T1")
    assert r.status == "needs_info" and "no refund" in r.summary.lower()


# --- unrecognised payments: triage, never filed -----------------------------------------------------------------------

def _gym_history(add_txn):
    for i, d in enumerate(["2026-08-01", "2026-09-01", "2026-10-01"]):
        add_txn(f"P{i}", d, 30.0, merchant="Gym")
    add_txn("T1", "2026-11-01", 30.0, merchant="Gym")


def test_unrecognised_but_a_regular_merchant_asks_if_they_recognise_it_now(db, registry, settings, add_txn):
    _gym_history(add_txn)
    f = flow(db, registry, settings)
    r = f.say("I don't recognise this", ext("unrecognised"), txn_id="T1")
    assert r.status == "needs_info" and r.data.get("recognition_question")
    assert "3 times before" in r.summary

    r = f.say("oh that's my gym", ext(recognises=True))
    assert r.status == "resolved" and r.data["code"] == "recognised"
    assert f.draft() is None and f.cases() == []


def test_unclear_answer_to_the_recognition_question_asks_again(db, registry, settings, add_txn):
    _gym_history(add_txn)
    f = flow(db, registry, settings)
    f.say("I don't recognise this", ext("unrecognised"), txn_id="T1")
    r = f.say("hm?", ext(intent="unclear"))
    assert r.status == "needs_info" and r.data.get("recognition_question")


def test_still_unrecognised_ends_as_suspected_fraud(db, registry, settings, add_txn):
    _gym_history(add_txn)
    f = flow(db, registry, settings)
    f.say("I don't recognise this", ext("unrecognised"), txn_id="T1")

    r = f.say("no, still not mine", ext(recognises=False))
    assert r.status == "needs_info" and r.data["slot_ids"] == ["others_use_card", "card_in_possession"]

    r = f.say("nobody else, I have my card",
              ext(answers={"others_use_card": "No", "card_in_possession": "Yes"}))
    assert r.status == "suspected_fraud"
    assert r.data["answers"] == {"others_use_card": "No", "card_in_possession": "Yes"}
    assert r.data["handoff_message"] == workflow.HANDOFF_MESSAGE
    assert f.cases() == [] and f.draft() is None      # triage never files a dispute
    assert events(db)[-1] == "suspected_fraud"
    detail = json.loads(db.execute("SELECT detail_json FROM dispute_events ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert detail["handed_off"] is True


def test_unrecognised_with_no_history_goes_straight_to_triage(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="NeverSeen")
    r = flow(db, registry, settings).say("not mine", ext("unrecognised"), txn_id="T1")
    assert r.status == "needs_info"
    assert not r.data.get("recognition_question")
    assert r.data["slot_ids"] == ["others_use_card", "card_in_possession"]


# --- robustness ------------------------------------------------------------------------------------------------------------

def test_unparseable_model_output_is_treated_as_unclear(db, registry, settings):
    class _Broken(ScriptedChatModel):
        def with_structured_output(self, schema, *, include_raw=False, **kw):
            from langchain_core.runnables import RunnableLambda
            from fakes import ai

            async def _run(_):
                return {"raw": ai(), "parsed": None, "parsing_error": ValueError("bad")}
            return RunnableLambda(lambda x: None, afunc=_run)

    result = asyncio.run(run_dispute_turn(
        llm=_Broken(), prompt=PROMPT, registry=registry, db_conn=db, logger=LOGGER,
        customer_id="C1", session_id="S1", user_message="??", settings=settings,
        agent_metrics={}, today=TODAY,
    ))
    assert result.status == "needs_transaction"


def test_a_model_failure_becomes_an_error_result_not_an_exception(db, registry, settings):
    llm = ScriptedChatModel(structured_script=[])          # the fake raises when asked
    result = asyncio.run(run_dispute_turn(
        llm=llm, prompt=PROMPT, registry=registry, db_conn=db, logger=LOGGER, customer_id="C1",
        session_id="S1", user_message="charged twice", settings=settings, agent_metrics={}, today=TODAY,
    ))
    assert result.status == "error"


def test_extraction_metrics_are_recorded(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.turn("x", ext(intent="unclear"))
    f.turn("y", ext(intent="unclear"))
    assert set(f.metrics) == {"disputes_extraction", "disputes_extraction_2"}


def test_the_model_sees_the_actual_message_the_payment_and_the_questions_it_asked(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    f = flow(db, registry, settings)
    f.say("wrong amount", ext("wrong_amount"), txn_id="T1")
    f.say("about 20 quid", ext(answers={"expected_amount": "20"}))
    user_block = f.last_llm.structured_seen[0][1]["content"]
    assert "customer_message: about 20 quid" in user_block
    assert "asked_last_turn: description, expected_amount" in user_block
    assert "£45.00 to Acme on 2026-11-01" in user_block
    assert "dispute_open: yes" in user_block


def test_the_model_is_given_todays_date_and_what_it_has_been_told_about_the_payment(db, registry, settings, add_txn):
    f = flow(db, registry, settings)
    f.turn("it was 45 pounds", ext(amount=45.0))
    f.turn("on the 1st", ext(on="2026-11-01"))
    block = f.last_llm.structured_seen[0][1]["content"]
    assert "today: 2026-11-10" in block                              # so "1st" and "yesterday" can be worked out
    assert "payment_details_given_so_far: amount=45.0, date=none, merchant=none" in block
    assert "stage: finding_transaction" in block and "transaction: not chosen yet" in block


def test_the_model_is_shown_the_proposed_payment_and_the_listed_ones_by_id(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 20.0, merchant="Kim Store")
    f = flow(db, registry, settings)
    f.turn("dispute it", ext(amount=20.0, on="2026-11-01", merchant="Kim Store"))
    f.turn("hm", ext(intent="unclear"))
    block = f.last_llm.structured_seen[0][1]["content"]
    assert "stage: confirming_transaction" in block
    assert "proposed_payment (shown to the customer, waiting for their yes or no): £20.00 to Kim Store on 2026-11-01" in block

    add_txn("T2", "2026-10-05", 33.5, merchant="Kim Store")
    add_txn("T3", "2026-10-05", 32.0, merchant="Kim Store")
    h = flow(db, registry, settings, session="S10")
    h.turn("dispute it", ext(amount=33.0, on="2026-10-05", merchant="Kim Store"))
    h.turn("hmm", ext(intent="unclear"))
    block = h.last_llm.structured_seen[0][1]["content"]
    assert "stage: choosing_transaction" in block
    assert "- T2: £33.50 to Kim Store on 2026-10-05" in block and "- T3: £32.00 to Kim Store on 2026-10-05" in block


# --- the description ------------------------------------------------------------------------------------------------------------

def test_the_opening_message_becomes_the_description_so_it_is_not_asked_again(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    opening = "Acme charged me the wrong amount on my last payment"
    r = f.say(opening, ext("wrong_amount"), txn_id="T1")
    assert f.draft()["answers"]["description"] == opening
    assert r.data["slot_ids"] == ["expected_amount", "amount_basis"]      # description is not among the questions


def test_a_very_short_opening_is_not_used_as_the_description(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    r = f.say("wrong amount", ext("wrong_amount"), txn_id="T1")
    assert "description" not in f.draft()["answers"]
    assert r.data["slot_ids"][0] == "description"                         # so it is asked


def test_a_description_the_model_extracted_wins_over_the_opening_message(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("Acme charged me the wrong amount on my last payment",
          ext("wrong_amount", {"description": "Charged 45 against a 30 quote"}), txn_id="T1")
    assert f.draft()["answers"]["description"] == "Charged 45 against a 30 quote"


def test_a_bare_yes_is_never_recorded_as_a_description(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("wrong amount", ext("wrong_amount"), txn_id="T1")
    for junk in ("yes", "Yes.", "ok", "No"):
        f.say(junk, ext(answers={"description": junk}))
    assert "description" not in f.draft()["answers"]


def test_a_later_reason_choice_does_not_overwrite_or_invent_a_description(db, registry, settings, add_txn):
    """The opening words only become the description the first time a reason is set."""
    add_txn("T1", "2026-11-01", 45.0)
    f = flow(db, registry, settings)
    f.say("wrong amount", ext("wrong_amount"), txn_id="T1")
    f.say("sorry, actually I never received what I paid for at all", ext("goods_not_received"))
    assert "description" not in f.draft()["answers"]


def test_unrecognised_payments_have_no_description(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="NeverSeen")
    f = flow(db, registry, settings)
    f.say("I do not recognise this payment at all", ext("unrecognised"), txn_id="T1")
    assert "description" not in f.draft()["answers"]


# --- reminders and plain wording ----------------------------------------------------------------------------------------------------

def _draft(**kw) -> DisputeDraft:
    return DisputeDraft(**kw)


def test_reminder_names_the_next_question(db, registry, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    txn = workflow.checks.get_transaction(db, "C1", "T1")

    waiting = _draft(transaction_id="T1", reason="wrong_amount", pending_slot_ids=["expected_amount"])
    text = workflow.pending_reminder(waiting, txn, registry)
    assert "£45.00 to Acme" in text and "What amount did you expect to pay?" in text and "cancel" in text

    confirm = _draft(transaction_id="T1", reason="wrong_amount", status="awaiting_confirmation")
    assert "Shall I submit it?" in workflow.pending_reminder(confirm, txn, registry)

    recognise = _draft(transaction_id="T1", reason="unrecognised", awaiting_recognition=True)
    assert "recognise" in workflow.pending_reminder(recognise, txn, registry)

    no_reason = _draft(transaction_id="T1")
    assert "best describes the problem" in workflow.pending_reminder(no_reason, txn, registry)

    shown = _draft(proposed_transaction_id="T1")
    assert "Is this the payment you want to dispute: £45.00 to Acme on 2026-11-01?" in workflow.pending_reminder(
        shown, None, registry, proposed=txn)

    choosing = _draft(candidates=[{"transaction_id": "T1"}])
    assert "Which of the payments I listed" in workflow.pending_reminder(choosing, None, registry)

    nothing_yet = _draft()
    text = workflow.pending_reminder(nothing_yet, None, registry)
    assert "Which payment would you like to dispute?" in text and "date, merchant and amount" in text


def test_plain_reply_covers_every_status():
    def r(status, **data):
        return DisputeResult(status=status, summary="SUMMARY", data=data)

    assert "1. A?" in workflow.plain_reply(r("needs_info", questions=["A?", "B?"]))
    assert "Do you recognise" in workflow.plain_reply(r("needs_info", recognition_question=True))
    assert "• Option one" in workflow.plain_reply(r("needs_reason", options=[{"text": "Option one"}]))
    confirm = workflow.plain_reply(r("needs_confirmation", transaction="£5.00 to Acme on 2026-11-01",
                                     reason_label="Wrong amount", details=[{"label": "Expected amount", "value": "3"}]))
    assert "Wrong amount" in confirm and "• Expected amount: 3" in confirm and "Shall I submit" in confirm
    shown = workflow.plain_reply(r("needs_payment_confirmation", merchant="Acme", amount="£5.00", date="2026-11-01"))
    assert "Acme" in shown and "£5.00" in shown and "2026-11-01" in shown and "Is this the payment" in shown
    choice = workflow.plain_reply(r("needs_transaction_choice", options=[{"text": "£5.00 to Acme"}]))
    assert "exact match" in choice and "1. £5.00 to Acme" in choice
    assert "the date, the merchant, the amount" in workflow.plain_reply(
        r("needs_transaction", missing=["date", "merchant", "amount"]))
    assert "the merchant of" in workflow.plain_reply(r("needs_transaction", missing=["merchant"]))
    assert "£9.00" in workflow.plain_reply(
        r("needs_transaction", not_found=True, amount=9.0, date="2026-11-01", merchant="Acme"))
    assert "DSP-1" in workflow.plain_reply(r("filed", case_id="DSP-1", created=True))
    assert "already open" in workflow.plain_reply(r("filed", case_id="DSP-1", created=False))
    assert "cancelled" in workflow.plain_reply(r("cancelled"))
    assert workflow.plain_reply(r("suspected_fraud")) == workflow.HANDOFF_MESSAGE
    assert workflow.plain_reply(r("not_eligible")) == "SUMMARY"


# --- a payment that cannot be disputed is always named ---------------------------------------------------------------

def test_not_eligible_results_name_the_payment_so_the_reply_never_has_to_guess(db, registry, settings, add_txn):
    add_txn("T1", "2026-06-13", 45.0, merchant="Acme")                        # outside the window
    add_txn("T2", "2026-11-01", 20.0, merchant="Bank Ltd", txn_type="direct_debit")
    add_txn("T3", "2026-11-02", 30.0, merchant="Shop")
    db_utils.create_dispute(db, "C1", "OTHER", "T3", "wrong_amount", {})      # already being disputed

    for txn_id, code, merchant in [("T1", "outside_window", "Acme"), ("T2", "not_card_payment", "Bank Ltd"),
                                   ("T3", "already_disputed", "Shop")]:
        r = flow(db, registry, settings, session=txn_id).say("dispute it", ext(), txn_id=txn_id)
        assert r.status == "not_eligible" and r.data["code"] == code
        assert merchant in r.summary and merchant in r.data["transaction"]
        assert "The payment in question is" in r.summary


def test_an_identical_list_from_an_older_draft_accepts_any_agreement(db, registry, settings, add_txn):
    add_txn("T1", "2026-11-01", 20.0, merchant="Acme")
    add_txn("T2", "2026-11-01", 20.0, merchant="Acme")
    f = flow(db, registry, settings)
    draft = DisputeDraft(payment_amount=20.0, payment_date="2026-11-01", merchant_hint="Acme",
                         candidates=[{"transaction_id": i, "date": "2026-11-01", "merchant": "Acme",
                                      "amount": 20.0, "type": "payment"} for i in ("T1", "T2")])
    db_utils.save_dispute_draft(db, "C1", "S1", draft.model_dump_json())
    f.turn("both are duplicates, raise case", ext(intent="provide_info", amount=20.0, on="2026-11-01", merchant="Acme"))
    assert f.draft()["transaction_id"] in ("T1", "T2")
