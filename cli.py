"""Command line entry point.

    python -m cli run   --rubric R --cases C --labels L [--model M] [--repeats N]
    python -m cli rules --rubric R --cases C --labels L

`rules` runs the deterministic judge only and needs no API key, so a fresh
clone can be verified before anyone spends money on it.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import agreement
import config
import report as reporting
from judges.llm import LLMJudge
from judges.local import HumanJudge, RuleJudge
from runner import Runner, load_cases, load_rubric


def _out_dir(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        p = config.RUNS_DIR / stamp
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _execute(args, with_model: bool) -> int:
    rubric = load_rubric(args.rubric)
    cases = load_cases(args.cases)
    if not cases:
        print("no cases loaded", file=sys.stderr)
        return 1

    human = HumanJudge.from_yaml(args.labels)
    runner = Runner(rubric, cases, use_cache=not args.no_cache,
                    concurrency=args.concurrency, quiet=args.quiet)

    reference = (await runner.run(human))[0]
    rule_passes = await runner.run(RuleJudge())

    reports = {}
    reports["rules"] = agreement.compare(rule_passes[0], reference, rubric, "rules")

    model_name = None
    model_passes: list = []
    if with_model:
        if not config.API_KEY:
            print("ANTHROPIC_API_KEY is not set. Use `rules` to run without it.",
                  file=sys.stderr)
            return 2
        judge = LLMJudge(model=args.model)
        model_name = judge.name
        model_passes = await runner.run(judge, repeats=args.repeats)
        rep = agreement.compare(model_passes[0], reference, rubric, model_name)
        rep.self_consistency = agreement.self_consistency(model_passes, rubric)
        reports[model_name] = rep

    out = _out_dir(args.out)
    (out / "report.md").write_text(
        reporting.build_report(rubric, cases, reports, human.name,
                               model_name=model_name),
        encoding="utf-8",
    )
    queue_judge = model_name or "rules"
    (out / "review_queue.md").write_text(
        reporting.build_review_queue(rubric, cases, reports, queue_judge, human.name),
        encoding="utf-8",
    )
    (out / "verdicts.json").write_text(
        json.dumps(
            {
                "rubric_id": rubric.id,
                "rubric_version": rubric.version,
                "rubric_hash": rubric.hash,
                "reference": [v.model_dump() for v in reference],
                "rules": [v.model_dump() for v in rule_passes[0]],
                "model": [[v.model_dump() for v in p] for p in model_passes],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    _summarise(rubric, reports, model_name, out)
    return 0


def _summarise(rubric, reports, model_name, out: Path) -> None:
    print()
    print(f"rubric {rubric.id} v{rubric.version} hash {rubric.hash}")
    for name, rep in reports.items():
        mk = rep.macro_kappa
        print(f"  {name:28s} macro kappa {'n/a' if mk is None else f'{mk:.2f}'}"
              f"  cost ${rep.total_cost_usd:.4f}  errors {rep.n_errors}")
    rule_rep = reports.get("rules")
    model_rep = reports.get(model_name) if model_name else None
    print()
    for c in rubric.criteria:
        rk = reporting._find(rule_rep, c.id)
        lk = reporting._find(model_rep, c.id)
        choice, why = reporting.recommend(
            rk.kappa if rk else None, lk.kappa if lk else None
        )
        print(f"  {c.id:26s} -> {choice:6s}  {why}")
    print()
    print(f"report       {out / 'report.md'}")
    print(f"review queue {out / 'review_queue.md'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rubric-eval")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("run", "rules"):
        p = sub.add_parser(name)
        p.add_argument("--rubric", required=True)
        p.add_argument("--cases", required=True)
        p.add_argument("--labels", required=True)
        p.add_argument("--out")
        p.add_argument("--concurrency", type=int, default=None)
        p.add_argument("--no-cache", action="store_true",
                       help="bypass the cache; required before quoting a cost")
        p.add_argument("--quiet", action="store_true")
        if name == "run":
            p.add_argument("--model", default=config.MODEL)
            p.add_argument("--repeats", type=int, default=1,
                           help="passes per case, to measure self-consistency")

    args = ap.parse_args(argv)
    if args.cmd == "rules":
        args.model, args.repeats = None, 1
    return asyncio.run(_execute(args, with_model=args.cmd == "run"))


if __name__ == "__main__":
    raise SystemExit(main())
