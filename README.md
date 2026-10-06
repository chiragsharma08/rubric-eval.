# rubric-eval

Decide whether an LLM should be doing a judgement task at all.

You point it at some content, a rubric and a set of human labels. It scores the
content with an LLM, scores it again with a deterministic rule baseline, and
tells you per criterion which one to ship, what the model costs you, and which
cases a person still needs to look at.

The output is a decision, not a leaderboard. Most eval tooling answers "how good
is my prompt". The question that actually comes up in a business is "does this
criterion need a model, a regex, or a human", and those have different answers
for different criteria of the same rubric.

## The output

Real numbers from the worked example in `examples/advice_compliance`, rule
baseline only:

| Criterion | Rule exact | Rule k | Ship |
|---|---|---|---|
| `affordability_checked` | 75.0% | 0.50 | human |
| `no_guaranteed_returns` | 92.9% | 0.81 | rule |
| `fees_disclosed` | 84.6% | 0.65 | rule |
| `risk_explained` | 90.0% | 0.82 | rule |
| `tone_appropriate` | 50.0% | 0.23 | human |

Two criteria a dozen lines of regex handles well enough to ship. One it cannot
touch, because no keyword list assesses whether an adviser was patient with a
worried client. That split is the finding, and you only get it by running both.

## Quickstart

```bash
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt

# deterministic baseline only, no API key needed
python -m cli rules \
  --rubric examples/advice_compliance/rubric.yaml \
  --cases  examples/advice_compliance/cases.yaml \
  --labels examples/advice_compliance/human_labels.yaml

# with the model
cp .env.example .env    # add your key
python -m cli run \
  --rubric examples/advice_compliance/rubric.yaml \
  --cases  examples/advice_compliance/cases.yaml \
  --labels examples/advice_compliance/human_labels.yaml \
  --repeats 3
```

Each run writes `report.md`, `review_queue.md` and `verdicts.json` to
`runs/<timestamp>/`.

`--cases` takes a YAML file or a directory of `.txt` files. `--repeats` runs the
model more than once over the same cases to measure self-consistency.

## Design decisions

**Every judge implements one interface, including the human labels.** `LLMJudge`,
`RuleJudge` and `HumanJudge` all expose `score(case, rubric)`. So the agreement
code in `agreement.py` is a single path that never asks what produced a verdict,
and a human reviewer is modelled as a judge with its own error rate rather than
as ground truth handed down from nowhere. Humans disagree with each other too,
and the architecture should not hide that.

**Kappa is reported next to raw agreement, and kappa wins ties.** Raw agreement
is close to useless on a skewed criterion. Take a pass/fail check where 90% of
cases genuinely pass: a judge that returns "pass" unconditionally scores 90% and
has learned nothing. Cohen's kappa corrects for chance agreement given each
judge's own label distribution, so the same judge scores 0.0. There is a test
asserting exactly that, because it is the central claim of the project. The gap
between the two numbers is itself diagnostic: wide gap means the criterion is
skewed and the headline percentage is flattering someone.

**A judge may abstain, and abstaining is a correct answer.** Any judge can
return `insufficient_evidence` for any criterion. Abstains are excluded from
agreement and counted separately, because a judge that declines on the hard half
of the set would otherwise post excellent numbers on the easy half. This is the
one idea here I did not come up with for this repo: it is lifted from my MSc
work with AstraZeneca, where conflating "no record found" with "confirmed
absent" would have produced a confident and wrong conclusion. It is the same
mistake in a different domain.

**The rubric is hashed data, not code.** Rubrics live in YAML with an id and a
version, and every verdict carries the rubric hash. Reword a criterion and the
hash changes, which invalidates the cache and marks the old numbers as not
comparable. The most common silent failure in evaluation work is comparing this
week's scores against last week's after somebody adjusted the wording.

**The deterministic baseline always runs.** It is not a flag. Without it the
report can only tell you how the model did, never whether the model was
necessary, and "we spent four figures a month on a model that a regex matched"
is a real outcome worth preventing.

**Self-consistency is measured, not assumed.** Temperature zero reduces
variation; it does not remove it. `--repeats` runs the model several times over
the same cases and reports the share of criterion slots where every pass agreed.
It is a ceiling: a judge that cannot reproduce its own verdict cannot agree with
a human more reliably than it agrees with itself, and knowing that number before
tuning a prompt saves tuning against noise.

**Disagreements and abstains are a file, not a footnote.** `review_queue.md` is
the human-in-the-loop fallback: each disagreement with the quoted evidence, the
judge's reasoning, the criterion text, the source excerpt, and tickboxes for
whether the judge was wrong, the reference was wrong, or the rubric is ambiguous.
The third box is there because it is the most common answer.

**Scoring is per criterion.** One score per case hides where a model fails. The
whole value of the headline table is that it is a table.

**Malformed model output degrades, never crashes.** A skipped criterion, an
invented level, JSON wrapped in prose or a fence, a dead socket: all become
abstains with the reason recorded, and the run continues. Failures are never
cached, so a retry does not inherit them. This is tested against a fake client
rather than a live model, because you cannot reliably ask a real model to
misbehave on demand.

## Honest limitations

**The example data flatters the rule baseline, and I wrote both halves of it.**
The 14 conversations in `examples/` are synthetic, and I wrote the regex patterns
after writing the conversations. On real transcripts, phrasing varies in ways a
keyword list does not survive, and I would expect the rule numbers to fall
noticeably. Treat the example as a demonstration of the method, not as evidence
about regex.

**14 cases is far too few to conclude anything.** Confidence intervals on kappa
at this n are wide enough to drive through. The example is sized to be readable.

**One human labeller means no inter-annotator agreement.** The reference is one
person's opinion. In production you want two or three labellers and the
agreement between *them* first, because that sets the real ceiling for every
judge measured against them. The architecture supports this (a second
`HumanJudge` is just another judge) but the example does not include it.

**The automation thresholds are judgement calls.** `AUTOMATE_FLOOR = 0.60` and
`RULE_TOLERANCE = 0.05` in `report.py` decide what the report recommends. Kappa
of 0.6 being "good enough" is a statement about how much a wrong verdict costs
in a given process, not a statistical fact. They are constants at the top of the
file so the decision is arguable instead of buried.

**Pricing is a lookup table.** `config.PRICING` is hardcoded and will drift.
Check it against current published pricing before quoting a cost to anyone.

**No real client data, by design.** This pattern comes from a production system
that reviewed regulated financial conversations, where the data could not leave
the firm. That is why the harness takes content as an input and ships no dataset
of its own.

## What I would add next

- Inter-annotator agreement between multiple human labellers, reported before
  any judge is measured, since it sets the ceiling.
- Bootstrap confidence intervals on kappa, so the report stops implying that
  0.61 and 0.58 are different.
- A rubric diff that, given two hashes, says which criteria changed and which
  historic numbers survive the change.
- Stratified sampling so the review queue prioritises the cases where a wrong
  verdict costs the most, rather than where the distance is largest.

## Layout

```
models.py       Case, Rubric, Criterion, verdicts; the ABSTAIN rule
config.py       model, concurrency, retries, pricing
judges/
  base.py       the Judge contract
  local.py      RuleJudge (regex baseline) and HumanJudge (stored labels)
  llm.py        Claude as judge: prompt, retry, tolerant JSON parse
  config.py     prompts and temperature
runner.py       loading, concurrent fan-out, content-addressed cache
agreement.py    exact agreement, Cohen's kappa, bias, confusion, consistency
report.py       report.md, review_queue.md, the ship/do-not-ship call
cli.py          run (with model) and rules (no API key)
examples/advice_compliance/   rubric, 14 synthetic cases, human labels
tests/          35 tests, no network
```

```bash
python -m pytest tests -q
```

## Background

I built and ran a system shaped like this in production for two years: it scored
recorded financial guidance conversations against a compliance rubric, replacing
most of a thirty-person manual review process. Before it replaced anyone it ran
in parallel with the human reviewers for two months, and the finding that mattered
was not that the model was accurate. It was that the human reviewers disagreed
with each other more than the model disagreed with any of them, and that several
criteria never needed a model in the first place.

This repository is that lesson as a tool. The design decisions and the
limitations above are mine, and I am happy to be questioned on any of them.
