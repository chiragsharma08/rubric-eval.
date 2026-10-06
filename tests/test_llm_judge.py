"""The LLM judge, verified against a fake client.

No API key and no network. This is not a convenience: the behaviour that matters
here is what happens when a model returns something unexpected, and you cannot
test that reliably by asking a real model to misbehave.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import config
import judges.llm as llm_mod
from judges.llm import LLMJudge
from models import ABSTAIN, Case, Criterion, Rubric
from runner import Runner, load_cases


class Transient(Exception):
    pass


def rubric() -> Rubric:
    return Rubric(id="r", version="1", criteria=[
        Criterion(id="c1", question="first?", levels=["fail", "pass"]),
        Criterion(id="c2", question="second?", levels=["absent", "partial", "clear"]),
    ])


class FakeClient:
    """Replays queued responses. A queued Exception is raised instead."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        item = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=item)],
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
        )


def judge_with(*responses) -> tuple[LLMJudge, FakeClient]:
    j = LLMJudge(model="claude-sonnet-5-5", api_key="test")
    client = FakeClient(*responses)
    j._client = client
    return j, client


def ok_response() -> str:
    return json.dumps({"verdicts": [
        {"criterion_id": "c1", "level": "pass", "evidence": "a quote",
         "rationale": "because"},
        {"criterion_id": "c2", "level": "clear", "evidence": "another",
         "rationale": "also because"},
    ]})


def test_a_well_formed_response_is_parsed_with_tokens_and_cost():
    j, _ = judge_with(ok_response())
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert v.error is None
    assert v.level_for("c1") == "pass"
    assert v.level_for("c2") == "clear"
    assert v.verdict_for("c1").evidence == "a quote"
    assert v.input_tokens == 1200 and v.output_tokens == 300
    assert v.cost_usd == pytest.approx(config.cost_usd("claude-sonnet-5-5", 1200, 300))


def test_prompt_offers_abstain_as_an_allowed_level():
    j, client = judge_with(ok_response())
    asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    prompt = client.last_kwargs["messages"][0]["content"]
    assert ABSTAIN in prompt
    assert "first?" in prompt and "second?" in prompt


def test_a_skipped_criterion_becomes_an_abstain_not_a_missing_row():
    body = json.dumps({"verdicts": [{"criterion_id": "c1", "level": "pass"}]})
    j, _ = judge_with(body)
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert v.level_for("c2") == ABSTAIN
    assert "no verdict" in v.verdict_for("c2").rationale


def test_an_invented_level_becomes_an_abstain_and_says_what_was_returned():
    body = json.dumps({"verdicts": [
        {"criterion_id": "c1", "level": "excellent"},
        {"criterion_id": "c2", "level": "clear"},
    ]})
    j, _ = judge_with(body)
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert v.level_for("c1") == ABSTAIN
    assert "excellent" in v.verdict_for("c1").rationale


def test_rubric_order_wins_over_response_order():
    body = json.dumps({"verdicts": [
        {"criterion_id": "c2", "level": "clear"},
        {"criterion_id": "c1", "level": "pass"},
    ]})
    j, _ = judge_with(body)
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert [x.criterion_id for x in v.verdicts] == ["c1", "c2"]


def test_a_fenced_response_with_preamble_still_parses():
    body = "Here is my assessment:\n```json\n" + ok_response() + "\n```\nHope that helps."
    j, _ = judge_with(body)
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert v.error is None and v.level_for("c1") == "pass"


def test_malformed_json_abstains_everything_and_records_the_error():
    j, _ = judge_with("{not json at all")
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert v.error and "JSON" in v.error
    assert all(x.level == ABSTAIN for x in v.verdicts)


def test_a_transport_failure_never_raises_out_of_score():
    j, _ = judge_with(RuntimeError("socket died"))
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert v.error and "socket died" in v.error
    assert all(x.level == ABSTAIN for x in v.verdicts)


def test_a_transient_failure_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(llm_mod, "TRANSIENT", (Transient,))
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0.0)
    j, client = judge_with(Transient("rate limited"), ok_response())
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert client.calls == 2
    assert v.error is None and v.level_for("c1") == "pass"


def test_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(llm_mod, "TRANSIENT", (Transient,))
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0.0)
    j, client = judge_with(Transient("always down"))
    v = asyncio.run(j.score(Case(id="a", content="x"), rubric()))
    assert client.calls == config.MAX_RETRIES
    assert v.error and "always down" in v.error


def test_fingerprint_changes_with_model_and_temperature():
    a = LLMJudge(model="claude-sonnet-5-5", temperature=0.0, api_key="t").fingerprint
    b = LLMJudge(model="claude-opus-5-5", temperature=0.0, api_key="t").fingerprint
    c = LLMJudge(model="claude-sonnet-5-5", temperature=0.7, api_key="t").fingerprint
    assert a != b and a != c


def test_the_cache_serves_the_second_run_without_calling_the_judge(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    r = rubric()
    cases = [Case(id="a", content="x")]
    j, client = judge_with(ok_response())

    first = asyncio.run(Runner(r, cases, quiet=True).run(j))[0][0]
    second = asyncio.run(Runner(r, cases, quiet=True).run(j))[0][0]

    assert client.calls == 1
    assert first.cached is False and second.cached is True
    assert second.level_for("c1") == "pass"


def test_a_changed_rubric_invalidates_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    cases = [Case(id="a", content="x")]
    j, client = judge_with(ok_response())

    asyncio.run(Runner(rubric(), cases, quiet=True).run(j))
    reworded = rubric()
    reworded.criteria[0].question = "first, but asked differently?"
    asyncio.run(Runner(reworded, cases, quiet=True).run(j))

    assert client.calls == 2


def test_a_failed_call_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0.0)
    cases = [Case(id="a", content="x")]
    j, client = judge_with("{broken")

    asyncio.run(Runner(rubric(), cases, quiet=True).run(j))
    asyncio.run(Runner(rubric(), cases, quiet=True).run(j))
    assert client.calls == 2


def test_repeats_are_independent_measurements_not_cache_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    j, client = judge_with(ok_response())
    asyncio.run(Runner(rubric(), [Case(id="a", content="x")], quiet=True).run(j, repeats=3))
    assert client.calls == 3


def test_cases_load_from_a_directory_of_text_files(tmp_path):
    (tmp_path / "one.txt").write_text("first call", encoding="utf-8")
    (tmp_path / "two.txt").write_text("second call", encoding="utf-8")
    cases = load_cases(tmp_path)
    assert [c.id for c in cases] == ["one", "two"]
    assert cases[0].content == "first call"
