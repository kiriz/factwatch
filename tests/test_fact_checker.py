"""Tests for :mod:`agent.fact_checker` using a fully mocked Gemini model.

No real API key is required and no network call is made: every test injects a
fake model object exposing ``generate_content``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent import fact_checker


class FakeResponse:
    """Minimal stand-in for a Gemini response object."""

    def __init__(self, text: str = "", candidates: list | None = None) -> None:
        self.text = text
        self.candidates = candidates or []


class FakeModel:
    """Records prompts and returns canned responses (or raises) per call."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate_content(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _payload(verdict: str = "TRUE", confidence: int = 90) -> str:
    return json.dumps(
        {
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
        }
    )


CLAIM = {
    "id": "claim-001",
    "title": "Arctic sea ice decline",
    "claim_text": "Arctic sea ice has declined since 1979.",
    "context": "",
    "verification_notes": "",
}


def test_parses_valid_json_response() -> None:
    model = FakeModel([FakeResponse(text=_payload("TRUE", 90))])

    result = fact_checker.check(CLAIM, run_id="run-x", model=model)

    assert result["verdict"] == "TRUE"
    assert result["confidence"] == 90
    assert result["claim_id"] == "claim-001"
    assert result["run_id"] == "run-x"
    assert result["sources"][0]["domain"] == "nsidc.org"
    assert result["agent_flags"]["requires_human_review"] is False


def test_unverified_result_on_exception() -> None:
    # Both the initial call and the retry raise -> UNVERIFIED with error flag.
    model = FakeModel([RuntimeError("boom"), RuntimeError("boom again")])

    # Inject a no-op sleep so the 30s backoff does not actually block.
    result = fact_checker.check(CLAIM, run_id="run-x", model=model, sleep=lambda _: None)

    assert result["verdict"] == "UNVERIFIED"
    assert result["confidence"] == 0
    assert result["agent_flags"]["requires_human_review"] is True
    assert result["reasoning"].startswith("Fact-check failed")
    assert len(model.prompts) == 2  # initial + one retry


def test_requires_human_review_when_confidence_low() -> None:
    model = FakeModel([FakeResponse(text=_payload("DISPUTED", 40))])

    result = fact_checker.check(CLAIM, run_id="run-x", model=model)

    assert result["confidence"] == 40
    assert result["agent_flags"]["requires_human_review"] is True


def test_sources_extracted_from_grounding_metadata() -> None:
    # Model returns valid JSON but with NO sources -> fall back to grounding.
    payload = json.dumps(
        {
            "verdict": "TRUE",
            "confidence": 85,
            "sources": [],
            "reasoning": "ok",
            "agent_flags": {
                "conflicting_sources": False,
                "outdated_evidence": False,
                "requires_human_review": False,
                "low_source_quality": False,
            },
        }
    )
    web = SimpleNamespace(uri="https://www.noaa.gov/report", title="NOAA report")
    chunk = SimpleNamespace(web=web)
    grounding = SimpleNamespace(grounding_chunks=[chunk])
    candidate = SimpleNamespace(grounding_metadata=grounding)
    response = FakeResponse(text=payload, candidates=[candidate])
    model = FakeModel([response])

    result = fact_checker.check(CLAIM, run_id="run-x", model=model)

    assert len(result["sources"]) == 1
    src = result["sources"][0]
    assert src["url"] == "https://www.noaa.gov/report"
    assert src["domain"] == "noaa.gov"  # www. stripped
    assert src["title"] == "NOAA report"


def test_json_parse_retry_then_success() -> None:
    # First response is garbage; strict-prompt retry returns valid JSON.
    model = FakeModel([FakeResponse(text="not json at all"), FakeResponse(text=_payload())])

    result = fact_checker.check(CLAIM, run_id="run-x", model=model)

    assert result["verdict"] == "TRUE"
    assert len(model.prompts) == 2
    assert "valid JSON object" in model.prompts[1]


def test_json_parse_failure_after_retry_returns_error() -> None:
    model = FakeModel([FakeResponse(text="garbage"), FakeResponse(text="still garbage")])

    result = fact_checker.check(CLAIM, run_id="run-x", model=model)

    assert result["verdict"] == "UNVERIFIED"
    assert result["agent_flags"]["requires_human_review"] is True


def test_handles_json_fenced_in_markdown() -> None:
    fenced = f"```json\n{_payload('FALSE', 75)}\n```"
    model = FakeModel([FakeResponse(text=fenced)])

    result = fact_checker.check(CLAIM, run_id="run-x", model=model)

    assert result["verdict"] == "FALSE"
    assert result["confidence"] == 75


@pytest.mark.parametrize("bad_verdict", ["MAYBE", "", "true-ish"])
def test_unknown_verdict_coerced_to_unverified(bad_verdict: str) -> None:
    model = FakeModel([FakeResponse(text=_payload(bad_verdict, 90))])

    result = fact_checker.check(CLAIM, run_id="run-x", model=model)

    assert result["verdict"] == "UNVERIFIED"
