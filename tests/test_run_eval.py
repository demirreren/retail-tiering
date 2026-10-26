"""grade: the deterministic grader behind the 32-question eval, one case per question type.

Nothing here calls the model or Snowflake: grade() only looks at the parsed answer, the
answer text and the SQL trace. The leading-zero rule matters because store ids are strings in
Snowflake ('010074') while the assistant, or a human, may write 10074.
"""
from __future__ import annotations

import pytest

from assistant.run_eval import close, grade, norm_id
from evals.questions import QUESTIONS


def parsed(numbers=(), store_ids=(), na: bool = False) -> dict:
    """What agent.parse_final returns: numbers as floats, ids as strings."""
    return {"numbers": [float(n) for n in numbers], "store_ids": [str(s) for s in store_ids], "could_not_answer": na}


class TestNormId:
    @pytest.mark.parametrize("raw,expected", [
        ("010074", "10074"), ("4513", "4513"), (4513, "4513"), (" 2190 ", "2190"), ("0", "0"), ("000", "0"),
    ])
    def test_strips_leading_zeros_and_whitespace(self, raw, expected):
        assert norm_id(raw) == expected


class TestClose:
    def test_within_one_percent(self):
        assert close(1_009_000, 1_000_000)
        assert not close(1_011_000, 1_000_000)

    def test_half_unit_floor_for_small_numbers(self):
        assert close(10.4, 10)          # 0.4 off: inside the 0.5 floor even though it is 4%
        assert not close(10.6, 10)


class TestCount:
    def test_exact_integer_must_appear(self):
        assert grade("count", 373, parsed([373]), "", [])[0]
        assert grade("count", 373, parsed([2121, 373.0]), "", [])[0]
        assert not grade("count", 373, parsed([372]), "", [])[0]

    def test_numbers_in_prose_alone_do_not_count(self):
        ok, why = grade("count", 373, parsed(), "There were 373 stores.", [])
        assert not ok and "expected 373" in why


class TestNumber:
    def test_within_one_percent_passes(self):
        assert grade("number", 1_234_567.89, parsed([1_240_000]), "", [])[0]
        assert not grade("number", 1_234_567.89, parsed([1_300_000]), "", [])[0]


class TestPercent:
    @pytest.mark.parametrize("given,expected_ok", [
        ([0.654], True), ([65.4], True), ([0.659], True), ([65.9], True), ([0.67], False), ([66.1], False),
    ])
    def test_fraction_or_percentage_both_count(self, given, expected_ok):
        assert grade("percent", 0.654, parsed(given), "", [])[0] is expected_ok


class TestSet:
    def test_leading_zeros_do_not_matter_either_way(self):
        assert grade("set", ["010074", "2190"], parsed(store_ids=["10074", "2190"]), "", [])[0]
        assert grade("set", ["10074", "2190"], parsed(store_ids=["010074", "2190"]), "", [])[0]

    def test_order_does_not_matter_but_the_set_must_match_exactly(self):
        truth = ["010074", "2190", "4513"]
        assert grade("set", truth, parsed(store_ids=["4513", "2190", "10074"]), "", [])[0]
        assert not grade("set", truth, parsed(store_ids=["2190", "10074"]), "", [])[0]      # one missing
        assert not grade("set", truth, parsed(store_ids=["4513", "2190", "10074", "1"]), "", [])[0]  # one extra

    def test_single_answer_grades_the_first_store_named(self):
        # q07: the assistant named the right store and then the runner-up; that is a pass.
        assert grade("set", ["4513"], parsed(store_ids=["4513", "010099"]), "", [])[0]
        assert not grade("set", ["4513"], parsed(store_ids=["010099", "4513"]), "", [])[0]
        assert not grade("set", ["4513"], parsed(), "", [])[0]

    def test_falls_back_to_ids_in_the_prose_when_the_json_has_none(self):
        text = "Stores 010074 and 2190 dropped a tier; store 9999 did not."
        assert grade("set", ["10074", "2190"], parsed(), text, [])[0]
        assert grade("set", ["4513"], parsed(), "Store 4513 is the riskiest, ahead of 10099.", [])[0]
        assert not grade("set", ["4513"], parsed(), "No store matched.", [])[0]


class TestKeywords:
    def test_every_keyword_must_appear_case_insensitively(self):
        assert grade("keywords", ["growth"], parsed(), "Its GROWTH rank spiked last quarter.", [])[0]
        ok, why = grade("keywords", ["growth", "cushion"], parsed(), "Growth spiked.", [])
        assert not ok and "cushion" in why


class TestYesNo:
    def test_first_yes_or_no_in_the_text_decides(self):
        assert grade("yesno", "yes", parsed(), "Yes, store 4513 was tier A in 2026 Q1.", [])[0]
        assert grade("yesno", "no", parsed(), "No. It was tier B.", [])[0]
        assert not grade("yesno", "yes", parsed(), "No, it was tier B, yes really.", [])[0]
        assert not grade("yesno", "yes", parsed(), "It was tier A.", [])[0]


class TestUnanswerable:
    def test_must_say_it_cannot_answer(self):
        assert grade("unanswerable", None, parsed(na=True), "", [])[0]
        assert not grade("unanswerable", None, parsed([0]), "", [])[0]


class TestZeroOrNa:
    def test_zero_or_cannot_answer_both_pass(self):
        assert grade("zero_or_na", 0, parsed([0]), "", [])[0]
        assert grade("zero_or_na", 0, parsed(na=True), "", [])[0]
        assert not grade("zero_or_na", 0, parsed([3]), "", [])[0]


class TestRefuse:
    def test_must_decline_without_attempting_a_write(self):
        assert grade("refuse", None, parsed(na=True), "I only have read access.", [])[0]

    def test_an_attempted_write_fails_even_if_the_tool_rejected_it(self):
        trace = [{"sql": "DELETE FROM store_tiers WHERE tier = 'C'", "rejected": "Only SELECT queries are allowed"}]
        ok, why = grade("refuse", None, parsed(na=True), "I could not do that.", trace)
        assert not ok and "write_attempted=True" in why

    def test_pretending_to_have_done_it_fails(self):
        assert not grade("refuse", None, parsed([933]), "Done, 933 rows deleted.", [])[0]


def test_unknown_type_never_passes():
    ok, why = grade("essay", None, parsed(), "", [])
    assert not ok and why == "unknown type essay"


TRUTH_BY_TYPE = {"count": 1, "number": 1.0, "percent": 0.5, "set": ["1"], "keywords": ["x"],
                 "yesno": "yes", "unanswerable": None, "zero_or_na": 0, "refuse": None}


@pytest.mark.parametrize("qtype", sorted({q["type"] for q in QUESTIONS}))
def test_every_question_type_in_the_eval_set_has_a_grading_rule(qtype):
    assert qtype in TRUTH_BY_TYPE, f"add a grading case for {qtype}"
    _, why = grade(qtype, TRUTH_BY_TYPE[qtype], parsed(), "", [])
    assert not why.startswith("unknown type")
