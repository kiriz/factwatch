"""Tests for :mod:`agent.dspy_checker`.

These tests never require ``dspy`` to be installed. The lazy ``_load_dspy``
helper is monkeypatched to return a minimal fake module exposing exactly the
surface the checker touches: ``LM``, ``context``, ``Predict``, ``Signature``,
``Module``, ``InputField``, ``OutputField``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from agent import dspy_checker


class _FakePrediction:
    """A stand-in for a ``dspy.Predict`` return value."""

    def __init__(self, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


class _FakePredict:
    """Mimics ``dspy.Predict(signature)`` — callable, returns a canned prediction."""

    def __init__(self, signature: Any) -> None:
        self.signature = signature

    def __call__(self, **kwargs: Any) -> _FakePrediction:
        return _FakeDspy.next_prediction


class _FakeLM:
    def __init__(self, model: str, api_key: str = "") -> None:
        self.model = model
        self.api_key = api_key


class _FakeModule:
    """Base mimicking ``dspy.Module`` — provides ``load`` and ``__call__``."""

    def load(self, path: str) -> None:  # noqa: D401 - mirrors dspy API
        _FakeDspy.loaded_paths.append(path)

    def __call__(self, **kwargs: Any) -> _FakePrediction:
        return self.forward(**kwargs)


class _FakeDspy:
    """Fake ``dspy`` module. Class attributes drive per-test behavior."""

    next_prediction: _FakePrediction
    loaded_paths: list[str] = []
    raise_in_predict: bool = False

    LM = _FakeLM
    Predict = _FakePredict
    Module = _FakeModule

    class Signature:
        pass

    @staticmethod
    def InputField(desc: str = "") -> None:
        return None

    @staticmethod
    def OutputField(desc: str = "") -> None:
        return None

    @staticmethod
    @contextmanager
    def context(lm: Any):
        if _FakeDspy.raise_in_predict:
            raise RuntimeError("boom inside dspy.context")
        yield


def _install_fake_dspy(monkeypatch: Any, **prediction_fields: Any) -> None:
    _FakeDspy.next_prediction = _FakePrediction(**prediction_fields)
    _FakeDspy.loaded_paths = []
    _FakeDspy.raise_in_predict = False
    monkeypatch.setattr(dspy_checker, "_load_dspy", lambda: _FakeDspy)


def test_generate_queries_returns_three_queries(monkeypatch: Any) -> None:
    _install_fake_dspy(monkeypatch, queries=["q1", "q2", "q3", "q4"])
    lite_lm = _FakeLM("gemini-2.0-flash-lite")
    claim = {"id": "c1", "title": "t", "claim_text": "ct", "context": ""}

    queries = dspy_checker.generate_queries(claim, lite_lm)

    assert queries == ["q1", "q2", "q3"]


def test_generate_queries_returns_empty_on_exception(monkeypatch: Any) -> None:
    _install_fake_dspy(monkeypatch, queries=["q1"])
    _FakeDspy.raise_in_predict = True
    lite_lm = _FakeLM("gemini-2.0-flash-lite")

    queries = dspy_checker.generate_queries({"id": "c1"}, lite_lm)

    assert queries == []


def test_fact_check_returns_expected_dict_shape(monkeypatch: Any) -> None:
    _install_fake_dspy(
        monkeypatch,
        verdict="true",
        confidence="87",
        reasoning="Strong evidence.",
        conflicting_sources=False,
        outdated_evidence=False,
        requires_human_review=False,
        low_source_quality=False,
    )
    main_lm = _FakeLM("gemini-flash-latest")

    result = dspy_checker.fact_check("claim", "results", "", main_lm)

    assert result is not None
    assert set(result.keys()) == {
        "verdict",
        "confidence",
        "reasoning",
        "conflicting_sources",
        "outdated_evidence",
        "requires_human_review",
        "low_source_quality",
    }
    # verdict upper-cased and validated; confidence coerced from str to int.
    assert result["verdict"] == "TRUE"
    assert result["confidence"] == 87
    assert result["requires_human_review"] is False


def test_fact_check_forces_review_when_confidence_low(monkeypatch: Any) -> None:
    _install_fake_dspy(
        monkeypatch,
        verdict="DISPUTED",
        confidence=40,
        reasoning="Thin evidence.",
        conflicting_sources=True,
        outdated_evidence=False,
        requires_human_review=False,
        low_source_quality=True,
    )
    main_lm = _FakeLM("gemini-flash-latest")

    result = dspy_checker.fact_check("claim", "results", "", main_lm)

    assert result is not None
    assert result["confidence"] == 40
    assert result["requires_human_review"] is True


def test_fact_check_coerces_unknown_verdict_to_unverified(monkeypatch: Any) -> None:
    _install_fake_dspy(
        monkeypatch,
        verdict="maybe",
        confidence=90,
        reasoning="r",
        conflicting_sources=False,
        outdated_evidence=False,
        requires_human_review=False,
        low_source_quality=False,
    )
    main_lm = _FakeLM("gemini-flash-latest")

    result = dspy_checker.fact_check("claim", "results", "", main_lm)

    assert result is not None
    assert result["verdict"] == "UNVERIFIED"


def test_fact_check_returns_none_on_exception(monkeypatch: Any) -> None:
    _install_fake_dspy(monkeypatch, verdict="TRUE", confidence=90)
    _FakeDspy.raise_in_predict = True
    main_lm = _FakeLM("gemini-flash-latest")

    result = dspy_checker.fact_check("claim", "results", "", main_lm)

    assert result is None
