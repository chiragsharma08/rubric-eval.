# Evaluation report

- Rubric: `advice_compliance` version `1.2` (hash `baec398d9fe9`)
- Cases: 14
- Reference judge: `human`
- Generated: 2026-10-06 22:35 UTC

Every verdict in this report was produced against the rubric hash above. Numbers from a different hash are not comparable and the tool will not mix them.

## Headline

Exact agreement is the share of cases a judge matched the reference on. Kappa is the same thing corrected for the agreement you would get by chance given each judge's own label distribution. Where the two diverge, trust kappa: a wide gap means the criterion is skewed and exact agreement is flattering the judge.

| Criterion | Rule exact | Rule k | Model exact | Model k | Ship |
|---|---|---|---|---|---|
| `affordability_checked` | 75.0% | 0.50 | n/a | n/a | **human** |
| `no_guaranteed_returns` | 92.9% | 0.81 | n/a | n/a | **rule** |
| `fees_disclosed` | 84.6% | 0.65 | n/a | n/a | **rule** |
| `risk_explained` | 90.0% | 0.82 | n/a | n/a | **rule** |
| `tone_appropriate` | 50.0% | 0.23 | n/a | n/a | **human** |

### What to ship, and why

- `affordability_checked` -> **human**: no judge reaches k=0.60; automating this criterion would push a known error rate into production
- `no_guaranteed_returns` -> **rule**: the rule clears the floor (k=0.81) and the model does not
- `fees_disclosed` -> **rule**: the rule clears the floor (k=0.65) and the model does not
- `risk_explained` -> **rule**: the rule clears the floor (k=0.82) and the model does not
- `tone_appropriate` -> **human**: no judge reaches k=0.60; automating this criterion would push a known error rate into production

The automation floor is kappa 0.60 and the rule is preferred when it comes within 0.05 of the model. Both are set in `report.py` and both are judgement calls about how much a wrong verdict costs in this particular process. Change them and the recommendations change, which is the point.

## Judges

| Judge | Macro exact | Macro k | Abstain rate | Errors | Cost | Mean latency | p95 | Self-consistency |
|---|---|---|---|---|---|---|---|---|
| `rules` | 78.5% | 0.60 | 0.0% | 0 | $0.0000 | 0 ms | 2 ms | n/a |

### Cost at volume

- `rules`: $0.00000 per case, $0.00 per thousand, $0 per hundred thousand.

Cached calls cost nothing on a rerun, so a cost figure is only meaningful on a cold cache. Run with `--no-cache` before quoting one.

## Where the model and the reference parted


## Confusion

### `rules`

**`affordability_checked`** (12 compared)

| judge \ ref | fail | pass |
|---|---|---|
| **fail** | 5 | 2 |
| **pass** | 1 | 4 |

**`no_guaranteed_returns`** (14 compared)

| judge \ ref | fail | pass |
|---|---|---|
| **fail** | 3 | 1 |
| **pass** | 0 | 10 |

**`fees_disclosed`** (13 compared)

| judge \ ref | fail | pass |
|---|---|---|
| **fail** | 3 | 2 |
| **pass** | 0 | 8 |

**`risk_explained`** (10 compared)

| judge \ ref | absent | partial | clear |
|---|---|---|---|
| **absent** | 1 | 0 | 0 |
| **partial** | 1 | 2 | 0 |
| **clear** | 0 | 0 | 6 |

**`tone_appropriate`** (14 compared)

| judge \ ref | poor | acceptable | good |
|---|---|---|---|
| **poor** | 2 | 0 | 0 |
| **acceptable** | 2 | 5 | 5 |
| **good** | 0 | 0 | 0 |

