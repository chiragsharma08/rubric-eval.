"""Claude as a rubric judge."""

import asyncio
import hashlib
import json
import time

from anthropic import AsyncAnthropic
from anthropic import APIStatusError, APIConnectionError, RateLimitError

import config
from judges import config as jcfg
from judges.base import Judge
from models import ABSTAIN, Case, CaseVerdict, CriterionVerdict, Rubric

TRANSIENT = (RateLimitError, APIConnectionError)


class LLMJudge(Judge):
    def __init__(self, model: str | None = None, temperature: float | None = None,
                 api_key: str | None = None, name: str | None = None):
        self.model = model or config.MODEL
        self.temperature = jcfg.TEMPERATURE if temperature is None else temperature
        self.name = name or f"llm:{self.model}"
        self._client = AsyncAnthropic(api_key=api_key or config.API_KEY)

    @property
    def fingerprint(self) -> str:
        prompt_hash = hashlib.sha256(
            (jcfg.SYSTEM_PROMPT + jcfg.USER_TEMPLATE + jcfg.CRITERION_TEMPLATE).encode()
        ).hexdigest()[:8]
        return f"{self.model}/t{self.temperature}/p{prompt_hash}"

    async def score(self, case: Case, rubric: Rubric) -> CaseVerdict:
        prompt = self._build_prompt(case, rubric)
        started = time.perf_counter()
        try:
            resp = await self._call(prompt)
        except Exception as exc:
            return self._failed(case, rubric, started, f"{type(exc).__name__}: {exc}")

        latency = int((time.perf_counter() - started) * 1000)
        text = "".join(b.text for b in resp.content if b.type == "text")
        parsed, parse_error = _extract_json(text)

        if parsed is None:
            verdict = self._failed(case, rubric, started, parse_error)
        else:
            verdict = CaseVerdict(
                case_id=case.id,
                judge=self.name,
                rubric_hash=rubric.hash,
                verdicts=self._reconcile(parsed, rubric),
            )
        verdict.latency_ms = latency
        verdict.input_tokens = resp.usage.input_tokens
        verdict.output_tokens = resp.usage.output_tokens
        verdict.cost_usd = config.cost_usd(
            self.model, resp.usage.input_tokens, resp.usage.output_tokens
        )
        return verdict

    async def _call(self, prompt: str):
        """Retry transient failures with exponential backoff. A 400 is not
        transient and is surfaced immediately rather than retried four times."""
        last = None
        for attempt in range(config.MAX_RETRIES):
            try:
                return await self._client.messages.create(
                    model=self.model,
                    max_tokens=jcfg.MAX_TOKENS,
                    temperature=self.temperature,
                    system=jcfg.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
            except TRANSIENT as exc:
                last = exc
            except APIStatusError as exc:
                if exc.status_code in (408, 429, 500, 502, 503, 529):
                    last = exc
                else:
                    raise
            if attempt < config.MAX_RETRIES - 1:
                await asyncio.sleep(config.RETRY_BASE_DELAY * (2 ** attempt))
        raise last

    def _build_prompt(self, case: Case, rubric: Rubric) -> str:
        blocks = []
        for c in rubric.criteria:
            guidance = f"Guidance: {c.guidance}" if c.guidance else ""
            blocks.append(
                jcfg.CRITERION_TEMPLATE.format(
                    id=c.id,
                    question=c.question,
                    levels=", ".join(c.levels + [ABSTAIN]),
                    guidance=guidance,
                ).strip()
            )
        return jcfg.USER_TEMPLATE.format(
            content=case.content, criteria_block="\n\n".join(blocks)
        )

    def _reconcile(self, parsed: dict, rubric: Rubric) -> list[CriterionVerdict]:
        """Rubric order wins, not response order. A criterion the model skipped
        becomes an abstain; a level it invented becomes an abstain and says so.
        Either way the run continues and the report counts it."""
        returned = {
            str(v.get("criterion_id")): v
            for v in parsed.get("verdicts", [])
            if isinstance(v, dict)
        }
        out = []
        for c in rubric.criteria:
            raw = returned.get(c.id)
            if raw is None:
                out.append(
                    CriterionVerdict(
                        criterion_id=c.id, level=ABSTAIN,
                        rationale="judge returned no verdict for this criterion",
                    )
                )
                continue
            level = c.validate_level(str(raw.get("level", "")))
            if level is None:
                out.append(
                    CriterionVerdict(
                        criterion_id=c.id, level=ABSTAIN,
                        rationale=f"judge returned undeclared level {raw.get('level')!r}",
                        evidence=str(raw.get("evidence", ""))[:500],
                    )
                )
                continue
            out.append(
                CriterionVerdict(
                    criterion_id=c.id,
                    level=level,
                    rationale=str(raw.get("rationale", ""))[:500],
                    evidence=str(raw.get("evidence", ""))[:500],
                )
            )
        return out

    def _failed(self, case, rubric, started, error) -> CaseVerdict:
        return CaseVerdict(
            case_id=case.id,
            judge=self.name,
            rubric_hash=rubric.hash,
            verdicts=[
                CriterionVerdict(criterion_id=c.id, level=ABSTAIN, rationale="judge failed")
                for c in rubric.criteria
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=error,
        )


def _extract_json(text: str) -> tuple[dict | None, str]:
    """Pull the first balanced JSON object out of a response. Models wrap JSON
    in fences and preamble often enough that failing on it wastes real money."""
    start = text.find("{")
    if start == -1:
        return None, "no JSON object in response"
    depth, in_string, escaped = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1]), ""
                except json.JSONDecodeError as exc:
                    return None, f"malformed JSON: {exc}"
    return None, "unterminated JSON object"
