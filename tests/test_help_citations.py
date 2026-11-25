"""Strict citation policy: an uncited sentence is kept (and cited) only when a retrieved
excerpt supports it; anything unsupported is still removed."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.help_rag import enforce_citations, supporting_excerpt  # noqa: E402

CONTEXTS = [
    {"n": 1, "text": "Log in to your net banking portal. Navigate to the 'Cards' or 'Debit Card' section. "
                     "Select the card you want to block. Click on 'Block Card' or 'Temporary Block'."},
    {"n": 2, "text": "Most mobile banking apps have a 'Forgot MPIN' option on the login screen."},
]


def test_supported_uncited_sentence_is_cited_not_dropped():
    answer = "Log in to your net banking portal and open the Cards section, then click Block Card. It is quick [1]."
    out, rep = enforce_citations(answer, 2, "strict", contexts=CONTEXTS)
    assert "Block Card [1]" in out and not rep["dropped_sentences"]
    assert len(rep["auto_cited"]) == 1


def test_unsupported_sentence_is_still_dropped():
    answer = "Call the fraud hotline on 0800 123 456 within 24 hours. Click on Block Card in net banking [1]."
    out, rep = enforce_citations(answer, 2, "strict", contexts=CONTEXTS)
    assert "0800" not in out and rep["dropped_sentences"] == ["Call the fraud hotline on 0800 123 456 within 24 hours."]


def test_answer_with_no_support_is_refused():
    out, rep = enforce_citations("Your card will be replaced free of charge in two days.", 2, "strict", contexts=CONTEXTS)
    assert rep["refused"]


def test_without_contexts_behaviour_is_unchanged():
    out, rep = enforce_citations("Click on Block Card in net banking. Done [1].", 2, "strict")
    assert rep["dropped_sentences"] == ["Click on Block Card in net banking."] and rep["auto_cited"] == []


def test_short_sentences_are_never_auto_cited():
    assert supporting_excerpt("Submit.", CONTEXTS) is None
