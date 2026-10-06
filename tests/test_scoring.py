import asyncio

import pytest

from agreement import cohens_kappa, compare, self_consistency
from judges.local import HumanJudge, RuleJudge
from models import ABSTAIN, Case, CaseVerdict, Criterion, CriterionVerdict, Rubric
from report import recommend


def binary_rubric(**kw) -> Rubric:
    return Rubric(
        id="r", version="1",
        criteria=[Criterion(id="c1", question="q?", levels=["fail", "pass"], **kw)],
    )


def test_kappa_punishes_a_judge_that_always_says_pass():
    """The headline claim of the whole project, as a test. On a 90/10 split a
    constant judge scores 90% raw agreement and must score ~0 kappa."""
    pairs = [("pass", "pass")] * 9 + [("pass", "fail")]
    raw = sum(1 for a, b in pairs if a == b) / len(pairs)
    kappa, _ = cohens_kappa(pairs)
    assert raw == pytest.approx(0.9)
    assert kappa == pytest.approx(0.0)


def test_kappa_undefined_when_only_one_level_was_ever_used():
    kappa, note = cohens_kappa([("pass", "pass")] * 10)
    assert kappa is None
    assert "degenerate" in note


def test_rubric_hash_changes_when_a_criterion_is_reworded():
    a = binary_rubric()
    b = Rubric(
        id="r", version="1",
        criteria=[Criterion(id="c1", question="q, but reworded?", levels=["fail", "pass"])],
    )
    assert a.hash != b.hash


def test_rubric_hash_ignores_the_human_description():
    a = Rubric(id="r", version="1", description="one",
               criteria=[Criterion(id="c1", question="q?", levels=["fail", "pass"])])
    b = Rubric(id="r", version="1", description="two",
               criteria=[Criterion(id="c1", question="q?", levels=["fail", "pass"])])
    assert a.hash == b.hash


def test_abstain_cannot_be_declared_as_a_level():
    with pytest.raises(ValueError):
        Criterion(id="c", question="q?", levels=["fail", ABSTAIN])


def test_rule_none_of_vetoes_a_match():
    rubric = binary_rubric(
        rule_default="fail",
        rules=[{"level": "pass", "any_of": ["fee"], "none_of": ["no fee"]}],
    )
    judge = RuleJudge()
    hit = asyncio.run(judge.score(Case(id="a", content="our fee is 1%"), rubric))
    miss = asyncio.run(judge.score(Case(id="b", content="there is no fee at all"), rubric))
    assert hit.level_for("c1") == "pass"
    assert miss.level_for("c1") == "fail"


def test_rule_all_of_requires_every_pattern():
    rubric = binary_rubric(
        rule_default="fail",
        rules=[{"level": "pass", "all_of": ["income", "outgoings"]}],
    )
    judge = RuleJudge()
    both = asyncio.run(judge.score(Case(id="a", content="income and outgoings"), rubric))
    one = asyncio.run(judge.score(Case(id="b", content="income only"), rubric))
    assert both.level_for("c1") == "pass"
    assert one.level_for("c1") == "fail"


def test_rule_without_a_default_abstains_rather_than_guessing():
    rubric = binary_rubric(rules=[{"level": "pass", "any_of": ["fee"]}])
    v = asyncio.run(RuleJudge().score(Case(id="a", content="nothing here"), rubric))
    assert v.level_for("c1") == ABSTAIN


def test_rules_are_tried_in_order_and_first_match_wins():
    rubric = Rubric(id="r", version="1", criteria=[Criterion(
        id="c1", question="q?", levels=["absent", "partial", "clear"],
        rule_default="absent",
        rules=[
            {"level": "clear", "any_of": ["can go down"]},
            {"level": "partial", "any_of": ["risk"]},
        ],
    )])
    content = "there is risk and the value can go down"
    v = asyncio.run(RuleJudge().score(Case(id="a", content=content), rubric))
    assert v.level_for("c1") == "clear"


def test_missing_human_label_becomes_an_abstain_not_a_pass():
    rubric = binary_rubric()
    judge = HumanJudge({"a": {}})
    v = asyncio.run(judge.score(Case(id="a", content="x"), rubric))
    assert v.level_for("c1") == ABSTAIN


def test_undeclared_human_label_is_rejected_and_explained():
    rubric = binary_rubric()
    judge = HumanJudge({"a": {"c1": "excellent"}})
    v = asyncio.run(judge.score(Case(id="a", content="x"), rubric))
    assert v.level_for("c1") == ABSTAIN
    assert "not a declared level" in v.verdict_for("c1").rationale


def _verdict(case_id, judge, level, rubric):
    return CaseVerdict(
        case_id=case_id, judge=judge, rubric_hash=rubric.hash,
        verdicts=[CriterionVerdict(criterion_id="c1", level=level)],
    )


def test_abstains_are_excluded_from_agreement_but_still_counted():
    rubric = binary_rubric()
    judged = [_verdict("a", "j", "pass", rubric), _verdict("b", "j", ABSTAIN, rubric)]
    reference = [_verdict("a", "human", "pass", rubric),
                 _verdict("b", "human", "fail", rubric)]
    rep = compare(judged, reference, rubric, "j")
    agr = rep.criteria[0]
    assert agr.n_cases == 2
    assert agr.n_compared == 1
    assert agr.judge_abstained == 1
    assert agr.exact == pytest.approx(1.0)


def test_a_judge_that_abstains_on_everything_gets_no_score():
    rubric = binary_rubric()
    judged = [_verdict("a", "j", ABSTAIN, rubric)]
    reference = [_verdict("a", "human", "pass", rubric)]
    rep = compare(judged, reference, rubric, "j")
    assert rep.criteria[0].exact is None
    assert rep.macro_kappa is None
    assert "no comparable pairs" in rep.criteria[0].note


def test_bias_shows_which_direction_a_judge_leans():
    rubric = binary_rubric()
    judged = [_verdict(str(i), "j", "pass", rubric) for i in range(4)]
    reference = [_verdict(str(i), "human", "fail", rubric) for i in range(4)]
    agr = compare(judged, reference, rubric, "j").criteria[0]
    assert agr.bias_ranks == pytest.approx(1.0)
    assert agr.mae_ranks == pytest.approx(1.0)


def test_disagreements_are_sorted_worst_first():
    rubric = Rubric(id="r", version="1", criteria=[Criterion(
        id="c1", question="q?", levels=["absent", "partial", "clear"])])
    judged = [_verdict("near", "j", "partial", rubric),
              _verdict("far", "j", "clear", rubric)]
    reference = [_verdict("near", "human", "absent", rubric),
                 _verdict("far", "human", "absent", rubric)]
    ds = compare(judged, reference, rubric, "j").disagreements
    assert [d.case_id for d in ds] == ["far", "near"]
    assert ds[0].distance == 2


def test_self_consistency_detects_a_flapping_judge():
    rubric = binary_rubric()
    run_a = [_verdict("x", "j", "pass", rubric), _verdict("y", "j", "pass", rubric)]
    run_b = [_verdict("x", "j", "pass", rubric), _verdict("y", "j", "fail", rubric)]
    assert self_consistency([run_a, run_b], rubric) == pytest.approx(0.5)
    assert self_consistency([run_a], rubric) is None


def test_recommend_prefers_the_rule_when_the_model_adds_nothing():
    assert recommend(0.90, 0.92)[0] == "rule"


def test_recommend_prefers_the_model_when_it_clearly_wins():
    assert recommend(0.30, 0.85)[0] == "model"


def test_recommend_refuses_to_automate_below_the_floor():
    assert recommend(0.20, 0.25)[0] == "human"
    assert recommend(None, None)[0] == "human"
