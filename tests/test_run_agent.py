"""Tests for :mod:`agent.run_agent` orchestration (offline, injected model)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent import run_agent


class StubModels:
    """Mimics client.models — returns a canned verdict for every call."""

    def __init__(self, verdict: str = "TRUE", confidence: int = 90) -> None:
        self._verdict = verdict
        self._confidence = confidence

    def generate_content(self, model: str, contents: str, config: Any = None) -> Any:
        class _Resp:
            pass

        r = _Resp()
        r.text = json.dumps(  # type: ignore[attr-defined]
            {
                "verdict": self._verdict,
                "confidence": self._confidence,
                "sources": [],
                "reasoning": "stub",
                "agent_flags": {
                    "conflicting_sources": False,
                    "outdated_evidence": False,
                    "requires_human_review": False,
                    "low_source_quality": False,
                },
            }
        )
        r.candidates = []  # type: ignore[attr-defined]
        return r


class StubClient:
    """Fake google-genai Client for offline tests."""

    def __init__(self, verdict: str = "TRUE", confidence: int = 90) -> None:
        self.models = StubModels(verdict, confidence)


def _make_project(tmp_path: Path, claim_ids: list[str]) -> Path:
    """Scaffold a minimal project tree (config + claims + empty dirs)."""
    (tmp_path / "config.yml").write_text(
        "staleness_days: 7\nmax_verdict_history: 50\nlog_retention_runs: 90\n"
    )
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    for cid in claim_ids:
        (claims_dir / f"{cid}.md").write_text(
            f"---\nid: {cid}\ntitle: t\nstatus: active\npriority: normal\n---\n"
            f"## Claim Text\nA claim.\n"
        )
    (tmp_path / "scores").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "site" / "data").mkdir(parents=True)
    return tmp_path


def test_should_check_true_when_no_score(tmp_path: Path) -> None:
    assert run_agent.should_check("claim-001", tmp_path, staleness_days=7) is True


def test_should_check_false_when_fresh(tmp_path: Path) -> None:
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "claim-001.json").write_text(json.dumps({"last_checked_at": recent}))
    assert run_agent.should_check("claim-001", tmp_path, staleness_days=7) is False


def test_should_check_true_when_stale(tmp_path: Path) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "claim-001.json").write_text(json.dumps({"last_checked_at": old}))
    assert run_agent.should_check("claim-001", tmp_path, staleness_days=7) is True


def test_order_by_priority() -> None:
    claims = [
        {"id": "a", "priority": "low"},
        {"id": "b", "priority": "high"},
        {"id": "c", "priority": "normal"},
    ]
    ordered = [c["id"] for c in run_agent.order_by_priority(claims)]
    assert ordered == ["b", "c", "a"]


def test_new_claim_not_counted_as_changed(tmp_path: Path) -> None:
    # Regression: a first-ever verdict must NOT register as verdicts_changed.
    project = _make_project(tmp_path, ["claim-001"])
    code = run_agent.run(project_root=project, client=StubClient("TRUE"))

    assert code == run_agent.EXIT_OK
    index = json.loads((project / "logs" / "index.json").read_text())
    assert index["runs"][0]["verdicts_changed"] == 0
    assert index["runs"][0]["claims_processed"] == 1


def test_verdict_flip_counted_as_changed(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["claim-001"])
    # First run records TRUE.
    run_agent.run(project_root=project, client=StubClient("TRUE"))
    # Force staleness so the second run re-checks: backdate last_checked_at.
    score_path = project / "scores" / "claim-001.json"
    score = json.loads(score_path.read_text())
    score["last_checked_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    score_path.write_text(json.dumps(score))

    # Second run flips to FALSE.
    run_agent.run(project_root=project, client=StubClient("FALSE"))

    index = json.loads((project / "logs" / "index.json").read_text())
    assert index["runs"][0]["verdicts_changed"] == 1


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["claim-001", "claim-002"])
    code = run_agent.run(project_root=project, dry_run=True, client=StubClient())

    assert code == run_agent.EXIT_OK
    assert list((project / "scores").glob("*.json")) == []
    assert list((project / "logs").glob("*.json")) == []


def test_claim_id_filter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path, ["claim-001", "claim-002"])
    monkeypatch.setenv("CLAIM_ID_FILTER", "claim-002")
    run_agent.run(project_root=project, client=StubClient())

    written = list((project / "scores").glob("*.json"))
    assert [p.name for p in written] == ["claim-002.json"]


def test_total_failure_when_no_active_claims(tmp_path: Path) -> None:
    project = _make_project(tmp_path, [])
    code = run_agent.run(project_root=project, client=StubClient())
    assert code == run_agent.EXIT_TOTAL_FAILURE


def test_manifest_and_summary_written(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["claim-001"])
    run_agent.run(project_root=project, client=StubClient("TRUE"))

    manifest = json.loads((project / "site" / "data" / "scores-manifest.json").read_text())
    summary = json.loads((project / "site" / "data" / "summary.json").read_text())
    assert manifest == [{"claim_id": "claim-001", "score_file": "scores/claim-001.json"}]
    assert summary["verdicts"] == {"TRUE": 1}
    assert summary["total_claims"] == 1
