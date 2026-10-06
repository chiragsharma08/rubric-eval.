"""Markdown reporting and the human review queue."""

from datetime import datetime, timezone

from agreement import JudgeReport
from models import Case, Rubric

# Landis and Koch read kappa above 0.61 as substantial and 0.41 to 0.60 as
# moderate. Those labels are conventions, not laws, and where the automation
# line sits is a business decision about the cost of a wrong call. They are
# constants here so that decision is visible and arguable instead of buried.
AUTOMATE_FLOOR = 0.60
RULE_TOLERANCE = 0.05
MAX_DISAGREEMENTS = 25


def _fmt(x: float | None, places: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{places}f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def recommend(rule_kappa: float | None, llm_kappa: float | None) -> tuple[str, str]:
    """Per criterion, what should actually ship."""
    if llm_kappa is None and rule_kappa is None:
        return "human", "neither judge produced a comparable result"
    if llm_kappa is None or llm_kappa < AUTOMATE_FLOOR:
        if rule_kappa is not None and rule_kappa >= AUTOMATE_FLOOR:
            return "rule", (
                f"the rule clears the floor (k={rule_kappa:.2f}) and the model does not"
            )
        return "human", (
            f"no judge reaches k={AUTOMATE_FLOOR:.2f}; automating this criterion "
            "would push a known error rate into production"
        )
    if rule_kappa is not None and rule_kappa >= llm_kappa - RULE_TOLERANCE:
        return "rule", (
            f"the rule matches the model within tolerance ({rule_kappa:.2f} vs "
            f"{llm_kappa:.2f}) at no cost and no latency"
        )
    gain = llm_kappa - (rule_kappa if rule_kappa is not None else 0.0)
    return "model", f"the model beats the rule by {gain:.2f} kappa"


def build_report(
    rubric: Rubric,
    cases: list[Case],
    reports: dict[str, JudgeReport],
    reference_name: str,
    rule_name: str = "rules",
    model_name: str | None = None,
) -> str:
    model_name = model_name or next(
        (n for n in reports if n not in (rule_name, reference_name)), None
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = []

    L += [
        "# Evaluation report",
        "",
        f"- Rubric: `{rubric.id}` version `{rubric.version}` (hash `{rubric.hash}`)",
        f"- Cases: {len(cases)}",
        f"- Reference judge: `{reference_name}`",
        f"- Generated: {now}",
        "",
        "Every verdict in this report was produced against the rubric hash above. "
        "Numbers from a different hash are not comparable and the tool will not "
        "mix them.",
        "",
        "## Headline",
        "",
        "Exact agreement is the share of cases a judge matched the reference on. "
        "Kappa is the same thing corrected for the agreement you would get by "
        "chance given each judge's own label distribution. Where the two diverge, "
        "trust kappa: a wide gap means the criterion is skewed and exact agreement "
        "is flattering the judge.",
        "",
    ]

    L += [
        "| Criterion | Rule exact | Rule k | Model exact | Model k | Ship |",
        "|---|---|---|---|---|---|",
    ]
    decisions = {}
    for c in rubric.criteria:
        rule_a = _find(reports.get(rule_name), c.id)
        llm_a = _find(reports.get(model_name), c.id) if model_name else None
        rk = rule_a.kappa if rule_a else None
        lk = llm_a.kappa if llm_a else None
        choice, why = recommend(rk, lk)
        decisions[c.id] = (choice, why)
        L.append(
            f"| `{c.id}` | {_pct(rule_a.exact if rule_a else None)} | {_fmt(rk, 2)} "
            f"| {_pct(llm_a.exact if llm_a else None)} | {_fmt(lk, 2)} | **{choice}** |"
        )

    L += ["", "### What to ship, and why", ""]
    for cid, (choice, why) in decisions.items():
        L.append(f"- `{cid}` -> **{choice}**: {why}")
    L += [
        "",
        f"The automation floor is kappa {AUTOMATE_FLOOR:.2f} and the rule is "
        f"preferred when it comes within {RULE_TOLERANCE:.2f} of the model. Both "
        "are set in `report.py` and both are judgement calls about how much a "
        "wrong verdict costs in this particular process. Change them and the "
        "recommendations change, which is the point.",
        "",
        "## Judges",
        "",
        "| Judge | Macro exact | Macro k | Abstain rate | Errors | Cost "
        "| Mean latency | p95 | Self-consistency |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, rep in reports.items():
        if name == reference_name:
            continue
        L.append(
            f"| `{name}` | {_pct(rep.macro_exact)} | {_fmt(rep.macro_kappa, 2)} "
            f"| {_pct(rep.abstain_rate)} | {rep.n_errors} | ${rep.total_cost_usd:.4f} "
            f"| {rep.mean_latency_ms:.0f} ms | {rep.p95_latency_ms:.0f} ms "
            f"| {_pct(rep.self_consistency)} |"
        )

    L += ["", "### Cost at volume", ""]
    for name, rep in reports.items():
        if name == reference_name or not cases:
            continue
        per_case = rep.total_cost_usd / len(cases)
        L.append(
            f"- `{name}`: ${per_case:.5f} per case, ${per_case * 1000:.2f} per "
            f"thousand, ${per_case * 100_000:.0f} per hundred thousand."
        )
    L += [
        "",
        "Cached calls cost nothing on a rerun, so a cost figure is only "
        "meaningful on a cold cache. Run with `--no-cache` before quoting one.",
        "",
    ]

    if model_name and reports.get(model_name) is not None:
        rep = reports[model_name]
        if rep.self_consistency is not None:
            L += [
                "### Self-consistency",
                "",
                f"Across repeat passes, `{model_name}` returned the same level on "
                f"{_pct(rep.self_consistency)} of criterion slots. Temperature zero "
                "reduces variation but does not remove it, so this is measured. It "
                "is also a ceiling: a judge that cannot reproduce its own verdict "
                "cannot agree with a human more reliably than it agrees with itself.",
                "",
            ]

    L += ["## Where the model and the reference parted", ""]
    if model_name and reports.get(model_name) is not None:
        ds = reports[model_name].disagreements
        if not ds:
            L.append("No disagreements.")
        else:
            L += [
                f"{len(ds)} disagreements, worst first by distance on the level "
                "scale. The evidence column is the span the judge quoted, which is "
                "usually enough to tell a bad rubric from a bad judge.",
                "",
                "| Case | Criterion | Model | Reference | Dist | Evidence |",
                "|---|---|---|---|---|---|",
            ]
            for d in ds[:MAX_DISAGREEMENTS]:
                ev = d.evidence.replace("|", "\\|").replace("\n", " ")[:130]
                L.append(
                    f"| `{d.case_id}` | `{d.criterion_id}` | {d.judge_level} "
                    f"| {d.reference_level} | {d.distance} | {ev} |"
                )
            if len(ds) > MAX_DISAGREEMENTS:
                L += ["", f"{len(ds) - MAX_DISAGREEMENTS} more in the review queue."]

    L += ["", "## Confusion", ""]
    for name, rep in reports.items():
        if name == reference_name:
            continue
        L += [f"### `{name}`", ""]
        for agr in rep.criteria:
            c = rubric.criterion(agr.criterion_id)
            bits = [f"{agr.n_compared} compared"]
            if agr.judge_abstained:
                bits.append(f"{agr.judge_abstained} abstained")
            if agr.note:
                bits.append(agr.note)
            L += [f"**`{agr.criterion_id}`** ({', '.join(bits)})", ""]
            if not agr.n_compared:
                L += ["_nothing to show_", ""]
                continue
            L.append("| judge \\ ref | " + " | ".join(c.levels) + " |")
            L.append("|---" * (len(c.levels) + 1) + "|")
            for jl in c.levels:
                row = [str(agr.confusion.get((jl, rl), 0)) for rl in c.levels]
                L.append(f"| **{jl}** | " + " | ".join(row) + " |")
            L.append("")

    return "\n".join(L) + "\n"


def build_review_queue(
    rubric: Rubric,
    cases: list[Case],
    reports: dict[str, JudgeReport],
    model_name: str,
    reference_name: str,
    excerpt: int = 700,
) -> str:
    """The fallback flow, as a file a person can actually work through.

    Two things land here: disagreements with the reference, and abstains. The
    abstains matter as much as the errors. A judge that declines to answer is
    telling you the rubric is ambiguous or the content is thin, and that is
    information about the process, not noise to be suppressed.
    """
    rep = reports.get(model_name)
    by_id = {c.id: c for c in cases}
    L = [
        "# Review queue",
        "",
        f"Rubric `{rubric.id}` version `{rubric.version}` (hash `{rubric.hash}`), "
        f"judge `{model_name}` against `{reference_name}`.",
        "",
    ]
    if rep is None:
        return "\n".join(L + ["Nothing to review."]) + "\n"

    L += ["## Disagreements", ""]
    if not rep.disagreements:
        L += ["None.", ""]
    for d in rep.disagreements:
        case = by_id.get(d.case_id)
        L += [
            f"### `{d.case_id}` / `{d.criterion_id}`",
            "",
            f"- Judge said **{d.judge_level}**, reference said "
            f"**{d.reference_level}** (distance {d.distance})",
            f"- Judge's reasoning: {d.rationale or '_none given_'}",
            f"- Judge quoted: {d.evidence or '_nothing_'}",
            f"- Criterion asks: {rubric.criterion(d.criterion_id).question}",
            "",
        ]
        if case:
            tail = "..." if len(case.content) > excerpt else ""
            L += ["```", case.content[:excerpt] + tail, "```", ""]
        L += [
            "Decision: [ ] judge was right  [ ] reference was right  "
            "[ ] rubric is ambiguous",
            "",
            "---",
            "",
        ]

    abstained = [
        (agr.criterion_id, agr.judge_abstained)
        for agr in rep.criteria
        if agr.judge_abstained
    ]
    L += ["## Abstains", ""]
    if not abstained:
        L += [
            "None. Worth a glance: a judge that never abstains on a real corpus "
            "is usually guessing rather than confident.",
            "",
        ]
    else:
        L += [
            "The judge declined to decide these. Treat a cluster on one criterion "
            "as a rubric problem before treating it as a judge problem.",
            "",
        ]
        for cid, n in abstained:
            L.append(f"- `{cid}`: {n} case(s)")
        L.append("")
    return "\n".join(L) + "\n"


def _find(rep: JudgeReport | None, criterion_id: str):
    if rep is None:
        return None
    for agr in rep.criteria:
        if agr.criterion_id == criterion_id:
            return agr
    return None
