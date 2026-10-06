"""Loading, fan-out and caching."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import yaml

import config
from judges.base import Judge
from models import Case, CaseVerdict, Rubric


def load_rubric(path) -> Rubric:
    with open(path, encoding="utf-8") as fh:
        return Rubric(**yaml.safe_load(fh))


def load_cases(path) -> list[Case]:
    """Accepts a YAML list of cases, or a directory of .txt files whose stems
    become case ids. The directory form exists because the first thing anyone
    does is drop a folder of transcripts in and expect it to work."""
    p = Path(path)
    if p.is_dir():
        return [
            Case(id=f.stem, content=f.read_text(encoding="utf-8"))
            for f in sorted(p.glob("*.txt"))
        ]
    with open(p, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or []
    return [Case(**c) for c in data]


class Runner:
    def __init__(self, rubric: Rubric, cases: list[Case], use_cache: bool = True,
                 concurrency: int | None = None, quiet: bool = False):
        self.rubric = rubric
        self.cases = cases
        self.use_cache = use_cache
        self.quiet = quiet
        self._sem = asyncio.Semaphore(concurrency or config.MAX_CONCURRENCY)

    async def run(self, judge: Judge, repeats: int = 1) -> list[list[CaseVerdict]]:
        out = []
        for r in range(repeats):
            self._log(f"{judge.name}: pass {r + 1}/{repeats}")
            out.append(await self._one_pass(judge, r))
        return out

    async def _one_pass(self, judge: Judge, repeat: int) -> list[CaseVerdict]:
        done = 0
        total = len(self.cases)

        async def worker(case: Case) -> CaseVerdict:
            nonlocal done
            async with self._sem:
                verdict = await self._scored(judge, case, repeat)
            done += 1
            self._log(f"  {done}/{total} {case.id}", end="\r")
            return verdict

        results = await asyncio.gather(*(worker(c) for c in self.cases))
        self._log(f"  {total}/{total} done        ")
        return list(results)

    async def _scored(self, judge: Judge, case: Case, repeat: int) -> CaseVerdict:
        path = self._cache_path(judge, case, repeat) if self.use_cache else None
        if path and path.exists():
            verdict = CaseVerdict(**json.loads(path.read_text(encoding="utf-8")))
            verdict.cached = True
            return verdict
        verdict = await judge.score(case, self.rubric)
        # A failed call is never cached: the next run would inherit the failure.
        if path and not verdict.error:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(verdict.model_dump_json(indent=2), encoding="utf-8")
        return verdict

    def _cache_path(self, judge: Judge, case: Case, repeat: int) -> Path:
        """Keyed on everything that could change the verdict. The repeat index
        is in the key so reruns of an experiment are free while repeats inside
        one experiment stay independent measurements."""
        key = "|".join([
            judge.fingerprint,
            self.rubric.hash,
            case.id,
            hashlib.sha256(case.content.encode()).hexdigest()[:16],
            str(repeat),
        ])
        return config.CACHE_DIR / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"

    def _log(self, msg: str, end: str = "\n") -> None:
        if not self.quiet:
            print(msg, file=sys.stderr, end=end, flush=True)
