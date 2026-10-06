"""The judge contract.

Every scorer implements this, including the human labels. That is deliberate:
a human reviewer is a judge with its own error rate and its own disagreement
with other humans, and modelling it as something special would hide that.
Because all judges share one interface, the agreement maths in agreement.py is
a single code path and never needs to know what produced a verdict.
"""

from abc import ABC, abstractmethod

from models import Case, CaseVerdict, Rubric


class Judge(ABC):
    name: str

    @property
    @abstractmethod
    def fingerprint(self) -> str:
        """Identifies this judge's configuration for the cache key. Anything
        that could change a verdict must change the fingerprint."""

    @abstractmethod
    async def score(self, case: Case, rubric: Rubric) -> CaseVerdict:
        """Grade one case. Must never raise: return a verdict with .error set."""

    @property
    def is_reference(self) -> bool:
        """True for ground truth, which is scored against rather than measured."""
        return False
