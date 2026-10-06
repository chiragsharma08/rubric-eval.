"""Schemas for cases, rubrics and verdicts.

A judge may return ABSTAIN for any criterion instead of a level. That is a
first-class outcome, not a failure: "the evidence does not support a call" is
different from "the criterion was not met", and collapsing the two is how an
evaluation ends up reporting a confident wrong answer.
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

ABSTAIN = "insufficient_evidence"


class RulePattern(BaseModel):
    """One deterministic test. Patterns are case-insensitive regex."""

    level: str
    any_of: list[str] = Field(default_factory=list)
    all_of: list[str] = Field(default_factory=list)
    none_of: list[str] = Field(default_factory=list)


class Criterion(BaseModel):
    id: str
    question: str
    levels: list[str]
    guidance: str = ""
    weight: float = 1.0
    rules: list[RulePattern] = Field(default_factory=list)
    rule_default: str | None = None

    @field_validator("levels")
    @classmethod
    def _ordered_and_unique(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("a criterion needs at least two levels")
        if len(set(v)) != len(v):
            raise ValueError("levels must be unique")
        if ABSTAIN in v:
            raise ValueError(f"{ABSTAIN!r} is implicit, do not declare it")
        return v

    def rank(self, level: str) -> int:
        return self.levels.index(level)

    def validate_level(self, level: str) -> str | None:
        """Return the level if declared, ABSTAIN if abstained, else None."""
        if level == ABSTAIN:
            return ABSTAIN
        return level if level in self.levels else None


class Rubric(BaseModel):
    id: str
    version: str
    description: str = ""
    criteria: list[Criterion]

    @field_validator("criteria")
    @classmethod
    def _unique_ids(cls, v: list[Criterion]) -> list[Criterion]:
        ids = [c.id for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError("criterion ids must be unique")
        return v

    @property
    def hash(self) -> str:
        """Content hash over everything a judge sees, so a reworded criterion
        produces a new hash and the report refuses to compare across it."""
        payload = self.model_dump(mode="json", exclude={"description"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]

    def criterion(self, cid: str) -> Criterion:
        for c in self.criteria:
            if c.id == cid:
                return c
        raise KeyError(cid)


class Case(BaseModel):
    id: str
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)


class CriterionVerdict(BaseModel):
    criterion_id: str
    level: str
    rationale: str = ""
    evidence: str = ""

    @property
    def abstained(self) -> bool:
        return self.level == ABSTAIN


class CaseVerdict(BaseModel):
    case_id: str
    judge: str
    rubric_hash: str
    verdicts: list[CriterionVerdict] = Field(default_factory=list)
    latency_ms: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    error: str | None = None

    def level_for(self, criterion_id: str) -> str | None:
        for v in self.verdicts:
            if v.criterion_id == criterion_id:
                return v.level
        return None

    def verdict_for(self, criterion_id: str) -> CriterionVerdict | None:
        for v in self.verdicts:
            if v.criterion_id == criterion_id:
                return v
        return None
