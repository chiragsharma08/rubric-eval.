"""Judges that cost nothing to run: regex rules and stored human labels."""

import re
import time

import yaml

from judges.base import Judge
from models import ABSTAIN, Case, CaseVerdict, Criterion, CriterionVerdict, Rubric


class RuleJudge(Judge):
    """Deterministic baseline.

    This exists so that every LLM number has something to beat. A criterion
    that a dozen lines of regex answers as well as a model does not need a
    model, and the only way to know which criteria those are is to run both.
    """

    name = "rules"

    @property
    def fingerprint(self) -> str:
        return "rules/v1"

    async def score(self, case: Case, rubric: Rubric) -> CaseVerdict:
        started = time.perf_counter()
        verdicts = [self._criterion(case.content, c) for c in rubric.criteria]
        return CaseVerdict(
            case_id=case.id,
            judge=self.name,
            rubric_hash=rubric.hash,
            verdicts=verdicts,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _criterion(self, content: str, c: Criterion) -> CriterionVerdict:
        for pattern in c.rules:
            hit = self._match(content, pattern)
            if hit is not None:
                return CriterionVerdict(
                    criterion_id=c.id,
                    level=pattern.level,
                    evidence=hit,
                    rationale="matched a declared pattern",
                )
        level = c.rule_default or ABSTAIN
        return CriterionVerdict(
            criterion_id=c.id,
            level=level,
            rationale="no pattern matched",
        )

    @staticmethod
    def _match(content: str, pattern) -> str | None:
        """Return the matched span, or None. Empty clauses are satisfied."""
        for p in pattern.none_of:
            if re.search(p, content, re.I):
                return None
        spans = []
        for p in pattern.all_of:
            m = re.search(p, content, re.I)
            if not m:
                return None
            spans.append(m.group(0))
        if pattern.any_of:
            for p in pattern.any_of:
                m = re.search(p, content, re.I)
                if m:
                    spans.append(m.group(0))
                    break
            else:
                return None
        return " | ".join(spans) if spans else "(no positive clause)"


class HumanJudge(Judge):
    """Stored human labels, loaded from YAML.

    Shape:
        case_id:
          criterion_id: level
    A missing entry becomes an abstain rather than a silent pass, so partial
    labelling does not quietly inflate agreement.
    """

    name = "human"

    def __init__(self, labels: dict[str, dict[str, str]], source: str = "labels"):
        self.labels = labels
        self.source = source

    @classmethod
    def from_yaml(cls, path) -> "HumanJudge":
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data, source=str(path))

    @property
    def fingerprint(self) -> str:
        return f"human/{self.source}"

    @property
    def is_reference(self) -> bool:
        return True

    async def score(self, case: Case, rubric: Rubric) -> CaseVerdict:
        case_labels = self.labels.get(case.id, {})
        verdicts = []
        for c in rubric.criteria:
            raw = case_labels.get(c.id, ABSTAIN)
            level = c.validate_level(str(raw))
            if level is None:
                verdicts.append(
                    CriterionVerdict(
                        criterion_id=c.id,
                        level=ABSTAIN,
                        rationale=f"label {raw!r} is not a declared level",
                    )
                )
            else:
                verdicts.append(CriterionVerdict(criterion_id=c.id, level=level))
        return CaseVerdict(
            case_id=case.id,
            judge=self.name,
            rubric_hash=rubric.hash,
            verdicts=verdicts,
        )
