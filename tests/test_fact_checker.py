"""Tests for :mod:`agent.fact_checker`.

No real API key, no network calls: inject FakeClient and a no-op searcher.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent import fact_checker


class FakeResponse:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.candidates = []


class FakeModels:
    """Mimics client.models with a canned response queue."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate_content(self, model: str, contents: str, config: Any = None) -> Any:
        self.calls.append({"model": model, "contents": contents})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.models = FakeModels(responses)


def _no_search(query: str, max_results: int = 6) -> list[dict[str, str]]:
    return []


def _payload(verdict: str = "TRUE", confidence: int = 90) -> str:
    return json.dumps({
        "verdict": verdict,
        "confidence": confidence,
        "sources": [
            {
                "url": "https://nsidc.org/data",
                "title": "NSIDC sea ice",
                "relevance": "high",
                "supports_claim": True,
                "excerpt": "September minimum extent declining since 1979",
            }
        ],
        "reasoning": "Strong satellite record.",
        "agent_flags": {
            "conflicting_sources": False,
            "outdated_evidence": False,
            "requires_human_review": False,
            "low_source_quality": False,
        },
    })


CLAIM = {
    "id": "claim-001",
    "title": "Arctic sea ice decline",
    "claim_text": "Arctic sea ice has declined since 1979.",
    "context": "",
    "verification_notes": "",
}


def test_parses_valid_json_response() -> None:
    client = FakeClient([FakeResponse(text=_payload("TRUE", 90))])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, searcher=_no_search)
    assert result["verdict"] == "TRUE"
    assert result["confidence"] == 90
    assert result["claim_id"] == "claim-001"
    assert result["run_id"] == "run-x"
    assert result["sources"][0]["domain"] == "nsidc.org"
    assert result["agent_flags"]["requires_human_review"] is False


def test_unverified_result_on_exception() -> None:
    client = FakeClient([RuntimeError("boom"), RuntimeError("boom again")])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, sleep=lambda _: None, searcher=_no_search)
    assert result["verdict"] == "UNVERIFIED"
    assert result["confidence"] == 0
    assert result["agent_flags"]["requires_human_review"] is True
    assert result["reasoning"].startswith("Fact-check failed")
    assert len(client.models.calls) == 2


def test_requires_human_review_when_confidence_low() -> None:
    client = FakeClient([FakeResponse(text=_payload("DISPUTED", 40))])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, searcher=_no_search)
    assert result["confidence"] == 40
    assert result["agent_flags"]["requires_human_review"] is True


def test_sources_from_model_json() -> None:
    client = FakeClient([FakeResponse(text=_payload("TRUE", 85))])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, searcher=_no_search)
    assert len(result["sources"]) == 1
    assert result["sources"][0]["domain"] == "nsidc.org"
    assert result["sources"][0]["title"] == "NSIDC sea ice"


def test_search_results_injected_into_prompt() -> None:
    captured_prompts: list[str] = []

    class CapturingModels:
        def generate_content(self, model: str, contents: str, config: Any = None) -> Any:
            captured_prompts.append(contents)
            return FakeResponse(text=_payload())

    class CapturingClient:
        models = CapturingModels()

    def fake_search(query: str, max_results: int = 6) -> list[dict[str, str]]:
        return [{"url": "https://example.com", "title": "Example", "snippet": "relevant snippet"}]

    fact_checker.check(CLAIM, run_id="run-x", client=CapturingClient(), searcher=fake_search)
    assert "https://example.com" in captured_prompts[0]
    assert "relevant snippet" in captured_prompts[0]


def test_json_parse_retry_then_success() -> None:
    client = FakeClient([FakeResponse(text="not json"), FakeResponse(text=_payload())])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, searcher=_no_search)
    assert result["verdict"] == "TRUE"
    assert len(client.models.calls) == 2
    assert "valid JSON object" in client.models.calls[1]["contents"]


def test_json_parse_failure_after_retry_returns_error() -> None:
    client = FakeClient([FakeResponse(text="garbage"), FakeResponse(text="still garbage")])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, searcher=_no_search)
    assert result["verdict"] == "UNVERIFIED"
    assert result["agent_flags"]["requires_human_review"] is True


def test_handles_json_fenced_in_markdown() -> None:
    fenced = f"```json\n{_payload('FALSE', 75)}\n```"
    client = FakeClient([FakeResponse(text=fenced)])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, searcher=_no_search)
    assert result["verdict"] == "FALSE"
    assert result["confidence"] == 75


@pytest.mark.parametrize("bad_verdict", ["MAYBE", "", "true-ish"])
def test_unknown_verdict_coerced_to_unverified(bad_verdict: str) -> None:
    client = FakeClient([FakeResponse(text=_payload(bad_verdict, 90))])
    result = fact_checker.check(CLAIM, run_id="run-x", client=client, searcher=_no_search)
    assert result["verdict"] == "UNVERIFIED"
