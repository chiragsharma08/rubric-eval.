"""Judge-side tunables."""

TEMPERATURE = 0.0
MAX_TOKENS = 1600

# Low temperature is not determinism. Repeated identical calls can still differ,
# which is why repeat runs are a measurement (see --repeats) and not a formality.

SYSTEM_PROMPT = """You are grading a single piece of content against a rubric.

Rules you must follow:
- Decide each criterion independently. Do not let one criterion influence another.
- Quote the exact span of the content that drives your decision. If you cannot
  quote something specific, you do not have evidence.
- If the content does not contain enough information to decide a criterion,
  return the level "insufficient_evidence". This is a correct answer, not a
  cop-out. Guessing is worse than abstaining.
- Judge only what is present. Do not reward what the author probably meant.

Return nothing but the JSON object described in the user message."""

USER_TEMPLATE = """## Content to grade

<content>
{content}
</content>

## Criteria

{criteria_block}

## Output format

Return a single JSON object, no prose, no code fence:

{{"verdicts": [{{"criterion_id": "<id>", "level": "<one of the allowed levels \
or insufficient_evidence>", "evidence": "<exact quote from the content>", \
"rationale": "<one sentence>"}}]}}

Include exactly one entry per criterion, in the order listed."""

CRITERION_TEMPLATE = """### {id}
Question: {question}
Allowed levels: {levels}
{guidance}"""
