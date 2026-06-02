"""Tests for :mod:`agent.score_writer`."""

from __future__ import annotations

import json
from pathlib import Path

from agent import score_writer


def _result(
    claim_id: str = "claim-001",
    verdict: str = "TRUE",
    checked_at: str = "2026-06-01T06:00:00Z",
) -> dict:
    """Build a minimal schema-conforming score dict for tests."""
    return {
        "schema_version": "1.0",
        "claim_id": claim_id,
        "verdict": verdict,
        "confidence": 90,
        "last_checked_at": checked_at,
        "last_changed_at": checked_at,
        "run_id": "run-test",
        "sources": [],
        "reasoning": "because evidence",
        "verdict_history": [],
        "agent_flags": {
            "conflicting_sources": False,
            "outdated_evidence": False,
            "requires_human_review": False,
            "low_source_quality": False,
        },
    }


def test_new_score_creates_file_with_schema(tmp_path: Path) -> None:
    written = score_writer.write("claim-001", _result(), scores_dir=tmp_path)

    path = tmp_path / "claim-001.json"
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["claim_id"] == "claim-001"
    assert on_disk["verdict"] == "TRUE"
    assert on_disk["schema_version"] == "1.0"
    assert on_disk["verdict_history"] == []
    assert written == on_disk


def test_rerun_appends_to_verdict_history(tmp_path: Path) -> None:
    score_writer.write("claim-001", _result(checked_at="2026-06-01T06:00:00Z"), scores_dir=tmp_path)
    merged = score_writer.write(
        "claim-001",
        _result(verdict="TRUE", checked_at="2026-06-08T06:00:00Z"),
        scores_dir=tmp_path,
    )

    assert len(merged["verdict_history"]) == 1
    entry = merged["verdict_history"][0]
    assert entry["verdict"] == "TRUE"
    assert entry["checked_at"] == "2026-06-01T06:00:00Z"


def test_verdict_history_capped_at_max(tmp_path: Path) -> None:
    # Write 55 times with a cap of 50; expect exactly 50 retained, oldest trimmed.
    for i in range(55):
        score_writer.write(
            "claim-001",
            _result(checked_at=f"2026-06-{(i % 28) + 1:02d}T06:00:00Z"),
            scores_dir=tmp_path,
            max_history=50,
        )

    on_disk = json.loads((tmp_path / "claim-001.json").read_text())
    assert len(on_disk["verdict_history"]) == 50


def test_last_changed_at_only_updates_on_verdict_change(tmp_path: Path) -> None:
    # Initial verdict TRUE.
    score_writer.write(
        "claim-001",
        _result(verdict="TRUE", checked_at="2026-06-01T06:00:00Z"),
        scores_dir=tmp_path,
    )
    # Same verdict, later check -> last_changed_at must NOT move.
    same = score_writer.write(
        "claim-001",
        _result(verdict="TRUE", checked_at="2026-06-08T06:00:00Z"),
        scores_dir=tmp_path,
    )
    assert same["last_changed_at"] == "2026-06-01T06:00:00Z"
    assert same["last_checked_at"] == "2026-06-08T06:00:00Z"

    # Verdict flips -> last_changed_at advances to the new check time.
    changed = score_writer.write(
        "claim-001",
        _result(verdict="FALSE", checked_at="2026-06-15T06:00:00Z"),
        scores_dir=tmp_path,
    )
    assert changed["last_changed_at"] == "2026-06-15T06:00:00Z"


def test_atomic_write_leaves_no_temp_file(tmp_path: Path) -> None:
    score_writer.write("claim-001", _result(), scores_dir=tmp_path)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    assert (tmp_path / "claim-001.json").exists()
