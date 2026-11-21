from datetime import date

import pytest

from agents.disputes import checks
from conftest import TODAY
from utils import db_utils


def txn_of(db, transaction_id="T1", customer="C1"):
    return checks.get_transaction(db, customer, transaction_id)


def test_get_transaction_only_returns_the_customers_own(db, add_txn):
    add_txn("T1", "2026-11-01", 45.0, customer="C1")
    assert txn_of(db, "T1", "C1")["merchant_brand_name"] == "Acme"
    assert txn_of(db, "T1", "C2") is None
    assert txn_of(db, "NOPE", "C1") is None


def test_card_payment_inside_window_is_eligible(db, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    result = checks.check_eligibility(txn_of(db), TODAY, 120, has_open_dispute=False)
    assert result.ok and result.code == "ok"


def test_missing_transaction_is_not_found():
    result = checks.check_eligibility(None, TODAY, 120, has_open_dispute=False)
    assert (result.ok, result.code) == (False, "not_found")


@pytest.mark.parametrize("txn_type", ["income", "direct_debit", "standing_order"])
def test_only_card_payments_can_be_disputed(db, add_txn, txn_type):
    add_txn("T1", "2026-11-01", 45.0, txn_type=txn_type)
    result = checks.check_eligibility(txn_of(db), TODAY, 120, has_open_dispute=False)
    assert (result.ok, result.code) == (False, "not_card_payment")


def test_each_non_card_type_gets_its_own_explanation(db, add_txn):
    messages = set()
    for i, txn_type in enumerate(["income", "direct_debit", "standing_order"]):
        add_txn(f"T{i}", "2026-11-01", 5.0, txn_type=txn_type)
        messages.add(checks.check_eligibility(txn_of(db, f"T{i}"), TODAY, 120, False).message)
    assert len(messages) == 3


def test_payment_older_than_the_window_is_rejected(db, add_txn):
    add_txn("T1", "2026-06-13", 45.0)   # 150 days before TODAY
    result = checks.check_eligibility(txn_of(db), TODAY, 120, has_open_dispute=False)
    assert (result.ok, result.code) == (False, "outside_window")
    assert "120 days" in result.message


def test_window_edge_is_inclusive(db, add_txn):
    add_txn("T1", "2026-07-13", 45.0)   # exactly 120 days
    assert checks.check_eligibility(txn_of(db), TODAY, 120, False).ok


def test_open_dispute_blocks_a_second_one(db, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    result = checks.check_eligibility(txn_of(db), TODAY, 120, has_open_dispute=True)
    assert (result.ok, result.code) == (False, "already_disputed")


def test_detect_duplicate_matches_same_merchant_amount_and_window(db, add_txn):
    add_txn("T1", "2026-11-01", 45.0)
    add_txn("T2", "2026-11-02", 45.0)                      # duplicate
    add_txn("T3", "2026-11-02", 46.0)                      # different amount
    add_txn("T4", "2026-11-02", 45.0, merchant="Other")    # different merchant
    add_txn("T5", "2026-11-09", 45.0)                      # outside the window
    add_txn("T6", "2026-11-02", 45.0, customer="C2")       # someone else's
    found = checks.detect_duplicate(db, "C1", txn_of(db), window_days=3)
    assert [d["transaction_id"] for d in found] == ["T2"]


def test_detect_duplicate_is_case_insensitive_on_merchant(db, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="Acme")
    add_txn("T2", "2026-11-01", 45.0, merchant="ACME")
    assert len(checks.detect_duplicate(db, "C1", txn_of(db), 3)) == 1


def test_detect_recurring_finds_a_monthly_rhythm(db, add_txn):
    for i, d in enumerate(["2026-08-01", "2026-09-01", "2026-10-01"]):
        add_txn(f"P{i}", d, 12.99, merchant="StreamCo")
    add_txn("T1", "2026-11-01", 12.99, merchant="StreamCo")
    info = checks.detect_recurring(db, "C1", txn_of(db), min_prior=2)
    assert info.prior_count == 3
    assert info.is_recurring and info.monthly
    assert info.last_date == "2026-10-01"
    assert info.typical_amount == 12.99


def test_frequent_irregular_merchant_is_recurring_but_not_monthly(db, add_txn):
    for i, d in enumerate(["2026-10-01", "2026-10-04", "2026-10-09", "2026-10-20"]):
        add_txn(f"P{i}", d, 8.0 + i, merchant="Cafe")
    add_txn("T1", "2026-11-01", 9.0, merchant="Cafe")
    info = checks.detect_recurring(db, "C1", txn_of(db), min_prior=2)
    assert info.is_recurring and not info.monthly


def test_a_first_time_merchant_is_not_recurring(db, add_txn):
    add_txn("T1", "2026-11-01", 45.0, merchant="NewShop")
    info = checks.detect_recurring(db, "C1", txn_of(db), min_prior=2)
    assert (info.prior_count, info.is_recurring, info.monthly) == (0, False, False)


def test_recurring_ignores_other_customers_and_income(db, add_txn):
    add_txn("P1", "2026-09-01", 45.0, customer="C2")
    add_txn("P2", "2026-09-02", 45.0, txn_type="income")
    add_txn("T1", "2026-11-01", 45.0)
    assert checks.detect_recurring(db, "C1", txn_of(db), 1).prior_count == 0


def test_find_refund_credit_looks_for_income_from_the_same_merchant_afterwards(db, add_txn):
    add_txn("T1", "2026-10-01", 60.0, merchant="ShopCo")
    assert checks.find_refund_credit(db, "C1", txn_of(db)) is None

    add_txn("R0", "2026-09-20", 60.0, merchant="ShopCo", txn_type="income")   # before the payment
    add_txn("R1", "2026-10-20", 60.0, merchant="Other", txn_type="income")    # other merchant
    add_txn("R2", "2026-10-21", 90.0, merchant="ShopCo", txn_type="income")   # more than was paid
    assert checks.find_refund_credit(db, "C1", txn_of(db)) is None

    add_txn("R3", "2026-10-22", 60.0, merchant="ShopCo", txn_type="income")
    credit = checks.find_refund_credit(db, "C1", txn_of(db))
    assert credit["transaction_id"] == "R3"


def test_refund_wait(db, add_txn):
    add_txn("T1", "2026-11-05", 60.0)
    wait = checks.refund_wait(txn_of(db), TODAY, wait_days=10)
    assert wait.too_early and wait.days_since == 5
    assert wait.earliest_check == date(2026, 11, 15)
    assert not checks.refund_wait(txn_of(db), date(2026, 11, 20), 10).too_early


def test_create_dispute_is_idempotent_per_transaction_and_reason(db):
    first, created = db_utils.create_dispute(db, "C1", "S1", "T1", "wrong_amount", {"a": "1"})
    again, created_again = db_utils.create_dispute(db, "C1", "S2", "T1", "wrong_amount", {"a": "2"})
    assert created and not created_again
    assert again == first
    assert db.execute("SELECT COUNT(*) FROM disputes").fetchone()[0] == 1
    # A different reason on the same payment is a different case.
    other, created_other = db_utils.create_dispute(db, "C1", "S1", "T1", "duplicate_charge", {})
    assert created_other and other != first


def test_has_open_dispute_is_scoped_to_the_customer(db):
    db_utils.create_dispute(db, "C1", "S1", "T1", "wrong_amount", {})
    assert db_utils.has_open_dispute(db, "C1", "T1")
    assert not db_utils.has_open_dispute(db, "C2", "T1")


def test_stale_drafts_are_ignored(db):
    db_utils.save_dispute_draft(db, "C1", "S1", '{"transaction_id": "T1"}')
    assert db_utils.get_dispute_draft(db, "C1", "S1", max_age_hours=24) is not None
    db.execute("UPDATE dispute_drafts SET updated_at = '2020-01-01T00:00:00'")
    assert db_utils.get_dispute_draft(db, "C1", "S1", max_age_hours=24) is None


# --- finding a payment from its date (exactly), merchant (fuzzily) and amount (within £3) ------------

ON = date(2026, 11, 1)


def ids(rows):
    return [r["transaction_id"] for r in rows]


def find(db, amount, merchant, tol=3.0, **kw):
    return checks.find_transactions(db, "C1", amount, ON, merchant, amount_tolerance=tol, **kw)


@pytest.mark.parametrize("said, actual", [
    ("Great Northern Hotel", "Great Northern Hotel"),
    ("great northern hotel", "GREAT NORTHERN HOTEL"),
    ("Northern Hotel", "Great Northern Hotel"),            # part of the name
    ("GNH", "Great Northern Hotel"),                       # initials
    ("Top-Man", "Topman"),
])
def test_merchant_score_counts_these_as_the_same_merchant(said, actual):
    assert checks.merchant_score(said, actual) == 1.0 or checks.merchant_score(said, actual) > 0.9


def test_merchant_score_forgives_typos_but_not_a_different_merchant():
    assert checks.merchant_score("Grate Nothern Hotel", "Great Northern Hotel") >= 0.6
    assert checks.merchant_score("Tesco", "Great Northern Hotel") < 0.6
    assert checks.merchant_score("", "Tesco") == 0.0


def test_the_date_must_match_exactly(db, add_txn):
    add_txn("ON", "2026-11-01", 40.0)
    add_txn("NEXT_DAY", "2026-11-02", 40.0)
    add_txn("PREV_DAY", "2026-10-31", 40.0)
    assert ids(find(db, 40.0, "Acme")) == ["ON"]


def test_the_amount_can_be_out_by_up_to_three_pounds_either_way_inclusive(db, add_txn):
    add_txn("LOW", "2026-11-01", 37.00)          # 3.00 under
    add_txn("HIGH", "2026-11-01", 43.00)         # 3.00 over
    add_txn("OUT", "2026-11-01", 43.02)          # 3.02 over
    assert set(ids(find(db, 40.0, "Acme"))) == {"LOW", "HIGH"}


def test_the_merchant_has_to_match(db, add_txn):
    add_txn("HOTEL", "2026-11-01", 38.0, merchant="Great Northern Hotel")
    add_txn("TESCO", "2026-11-01", 40.0, merchant="Tesco")
    assert ids(find(db, 40.0, "Grate Nothern")) == ["HOTEL"]
    assert find(db, 40.0, "Nowhere") == []


def test_closest_amount_first_then_best_merchant_then_id(db, add_txn):
    add_txn("FAR", "2026-11-01", 42.5, merchant="Acme")
    add_txn("NEAR", "2026-11-01", 40.5, merchant="Acme")
    add_txn("B", "2026-11-01", 39.0, merchant="Acme")
    add_txn("A", "2026-11-01", 41.0, merchant="Acme")             # 1.00 away, same as B
    add_txn("TYPO", "2026-11-01", 40.5, merchant="Acne")         # as close as NEAR, weaker name
    assert ids(find(db, 40.0, "Acme", limit=10)) == ["NEAR", "TYPO", "A", "B", "FAR"]


def test_only_the_closest_three_come_back_by_default(db, add_txn):
    for i in range(6):
        add_txn(f"T{i}", "2026-11-01", 20.0 + i * 0.4)
    assert ids(find(db, 20.0, "Acme")) == ["T0", "T1", "T2"]


def test_each_row_says_how_close_it_is(db, add_txn):
    add_txn("T1", "2026-11-01", 38.5, merchant="Acme Stores")
    row = find(db, 40.0, "acme")[0]
    assert row["amount_difference"] == 1.5 and row["merchant_score"] == 1.0


def test_exact_match_needs_the_amount_to_the_penny_and_the_merchant(db, add_txn):
    add_txn("EXACT", "2026-11-01", 16.565, merchant="Acme")      # the data stores unrounded amounts
    add_txn("NEAR", "2026-11-01", 16.60, merchant="Acme")
    add_txn("TYPO", "2026-11-01", 16.57, merchant="Acne")
    rows = {r["transaction_id"]: r for r in find(db, 16.57, "Acme")}
    assert checks.is_exact_match(rows["EXACT"])
    assert not checks.is_exact_match(rows["NEAR"])
    assert not checks.is_exact_match(rows["TYPO"])


def test_find_transactions_only_searches_the_customers_own_rows(db, add_txn):
    add_txn("T1", "2026-11-01", 40.0, customer="C2")
    assert find(db, 40.0, "Acme") == []


def test_every_transaction_type_is_found_so_ineligible_ones_can_be_explained(db, add_txn):
    for i, kind in enumerate(["payment", "direct_debit", "standing_order", "income"]):
        add_txn(f"T{i}", "2026-11-01", 20.0, txn_type=kind)
    assert len(find(db, 20.0, "Acme", limit=10)) == 4
