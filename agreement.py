"""Agreement between any two judges, per criterion.

Raw agreement on its own is close to useless. Take a pass/fail criterion where
90% of cases genuinely pass: a judge that returns "pass" unconditionally scores
90% and has learned nothing. Cohen's kappa corrects for the agreement you would
expect by chance given each judge's own label distribution, so the same judge
scores about zero. Both numbers are reported, and the gap between them is itself
the diagnostic: wide gap means the criterion is skewed and raw agreement is
flattering the judge.

Abstains are excluded from agreement and counted separately. A judge that
abstains on the hard half of the set would otherwise look excellent on the easy
half, which is the same failure in a different costume.
"""

from collections import Counter
from dataclasses import dataclass, field
from statistics import mean

from models import ABSTAIN, CaseVerdict, Rubric


@dataclass
class CriterionAgreement:
    criterion_id: str
    n_cases: int
    n_compared: int
    judge_abstained: int
    reference_abstained: int
    exact: float | None = None
    kappa: float | None = None
    mae_ranks: float | None = None
    bias_ranks: float | None = None
    confusion: Counter = field(default_factory=Counter)
    note: str = ""


@dataclass
class Disagreement:
    case_id: str
    criterion_id: str
    judge_level: str
    reference_level: str
    distance: int
    evidence: str = ""
    rationale: str = ""


@dataclass
class JudgeReport:
    judge: str
    criteria: list[CriterionAgreement]
    disagreements: list[Disagreement]
    n_errors: int = 0
    total_cost_usd: float = 0.0
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    cached_calls: int = 0
    self_consistency: float | None = None

    @property
    def macro_exact(self) -> float | None:
        vals = [c.exact for c in self.criteria if c.exact is not None]
        return mean(vals) if vals else None

    @property
    def macro_kappa(self) -> float | None:
        vals = [c.kappa for c in self.criteria if c.kappa is not None]
        return mean(vals) if vals else None

    @property
    def abstain_rate(self) -> float | None:
        total = sum(c.n_cases for c in self.criteria)
        if not total:
            return None
        return sum(c.judge_abstained for c in self.criteria) / total


def cohens_kappa(pairs: list[tuple[str, str]]) -> tuple[float | None, str]:
    """pairs are (judge_level, reference_level) with abstains already removed."""
    n = len(pairs)
    if n == 0:
        return None, "nothing to compare"
    po = sum(1 for a, b in pairs if a == b) / n
    judge_dist = Counter(a for a, _ in pairs)
    ref_dist = Counter(b for _, b in pairs)
    levels = set(judge_dist) | set(ref_dist)
    pe = sum((judge_dist[l] / n) * (ref_dist[l] / n) for l in levels)
    if pe >= 1.0:
        return None, "degenerate: both judges used a single level, kappa undefined"
    return (po - pe) / (1 - pe), ""


def compare(
    judge_verdicts: list[CaseVerdict],
    reference_verdicts: list[CaseVerdict],
    rubric: Rubric,
    judge_name: str,
) -> JudgeReport:
    by_case = {v.case_id: v for v in reference_verdicts}
    criteria_out, disagreements = [], []

    for c in rubric.criteria:
        pairs, abstains_j, abstains_r, n_cases = [], 0, 0, 0
        for jv in judge_verdicts:
            ref = by_case.get(jv.case_id)
            if ref is None:
                continue
            n_cases += 1
            jl = jv.level_for(c.id)
            rl = ref.level_for(c.id)
            if jl is None or rl is None:
                continue
            if jl == ABSTAIN:
                abstains_j += 1
            if rl == ABSTAIN:
                abstains_r += 1
            if jl == ABSTAIN or rl == ABSTAIN:
                continue
            pairs.append((jl, rl))
            if jl != rl:
                cv = jv.verdict_for(c.id)
                disagreements.append(
                    Disagreement(
                        case_id=jv.case_id,
                        criterion_id=c.id,
                        judge_level=jl,
                        reference_level=rl,
                        distance=abs(c.rank(jl) - c.rank(rl)),
                        evidence=cv.evidence if cv else "",
                        rationale=cv.rationale if cv else "",
                    )
                )

        agr = CriterionAgreement(
            criterion_id=c.id,
            n_cases=n_cases,
            n_compared=len(pairs),
            judge_abstained=abstains_j,
            reference_abstained=abstains_r,
            confusion=Counter(pairs),
        )
        if pairs:
            agr.exact = sum(1 for a, b in pairs if a == b) / len(pairs)
            agr.kappa, agr.note = cohens_kappa(pairs)
            deltas = [c.rank(a) - c.rank(b) for a, b in pairs]
            agr.mae_ranks = mean(abs(d) for d in deltas)
            agr.bias_ranks = mean(deltas)
        else:
            agr.note = "no comparable pairs: every case abstained on one side"
        criteria_out.append(agr)

    latencies = sorted(v.latency_ms for v in judge_verdicts)
    report = JudgeReport(
        judge=judge_name,
        criteria=criteria_out,
        disagreements=sorted(disagreements, key=lambda d: -d.distance),
        n_errors=sum(1 for v in judge_verdicts if v.error),
        total_cost_usd=sum(v.cost_usd for v in judge_verdicts),
        cached_calls=sum(1 for v in judge_verdicts if v.cached),
    )
    if latencies:
        report.mean_latency_ms = mean(latencies)
        report.p95_latency_ms = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
    return report


def self_consistency(repeat_verdicts: list[list[CaseVerdict]], rubric: Rubric) -> float | None:
    """Fraction of (case, criterion) slots where every repeat agreed.

    Temperature zero is not determinism, so this is measured rather than
    assumed. A judge that cannot reproduce its own verdict sets a ceiling on
    the agreement it can ever reach with a human, and that ceiling is the
    number worth knowing before anyone tunes a prompt.
    """
    if len(repeat_verdicts) < 2:
        return None
    runs = [{v.case_id: v for v in run} for run in repeat_verdicts]
    case_ids = set(runs[0])
    for r in runs[1:]:
        case_ids &= set(r)
    total = unanimous = 0
    for cid in sorted(case_ids):
        for c in rubric.criteria:
            levels = {r[cid].level_for(c.id) for r in runs}
            total += 1
            if len(levels) == 1:
                unanimous += 1
    return unanimous / total if total else None
