"""Tests for :mod:`agent.site_builder`."""

from __future__ import annotations

import json
from pathlib import Path

from agent import site_builder


def _write_score(scores_dir: Path, claim_id: str, verdict: str) -> None:
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / f"{claim_id}.json").write_text(
        json.dumps({"claim_id": claim_id, "verdict": verdict})
    )


def test_only_active_claims_in_manifest(tmp_path: Path) -> None:
    scores_dir = tmp_path / "scores"
    site_data = tmp_path / "site" / "data"
    _write_score(scores_dir, "claim-001", "TRUE")
    _write_score(scores_dir, "claim-002", "FALSE")

    claims = [
        {"id": "claim-001", "status": "active"},
        {"id": "claim-002", "status": "paused"},
        {"id": "claim-003", "status": "retired"},
    ]

    site_builder.rebuild_manifests(claims, scores_dir=scores_dir, site_data_dir=site_data)

    manifest = json.loads((site_data / "scores-manifest.json").read_text())
    ids = [m["claim_id"] for m in manifest]
    assert ids == ["claim-001"]
    assert manifest[0]["score_file"] == "scores/claim-001.json"


def test_summary_has_correct_counts(tmp_path: Path) -> None:
    scores_dir = tmp_path / "scores"
    site_data = tmp_path / "site" / "data"
    _write_score(scores_dir, "claim-001", "TRUE")
    _write_score(scores_dir, "claim-002", "TRUE")
    _write_score(scores_dir, "claim-003", "FALSE")

    claims = [
        {"id": "claim-001", "status": "active"},
        {"id": "claim-002", "status": "active"},
        {"id": "claim-003", "status": "active"},
    ]

    site_builder.rebuild_manifests(
        claims,
        scores_dir=scores_dir,
        site_data_dir=site_data,
        last_run_at="2026-06-01T06:00:00Z",
        last_run_id="run-2026-06-01T060000Z",
    )

    summary = json.loads((site_data / "summary.json").read_text())
    assert summary["total_claims"] == 3
    assert summary["verdicts"] == {"TRUE": 2, "FALSE": 1}
    assert summary["last_run_at"] == "2026-06-01T06:00:00Z"
    assert summary["last_run_id"] == "run-2026-06-01T060000Z"


def test_handles_empty_scores_dir(tmp_path: Path) -> None:
    scores_dir = tmp_path / "scores"  # never created
    site_data = tmp_path / "site" / "data"

    result = site_builder.rebuild_manifests([], scores_dir=scores_dir, site_data_dir=site_data)

    manifest = json.loads(result["manifest"].read_text())
    summary = json.loads(result["summary"].read_text())
    assert manifest == []
    assert summary["total_claims"] == 0
    assert summary["verdicts"] == {}


def test_active_claim_without_score_file_omitted_from_verdicts(tmp_path: Path) -> None:
    # Active claim listed but no score file written yet: it appears in the
    # manifest but contributes no verdict count.
    scores_dir = tmp_path / "scores"
    site_data = tmp_path / "site" / "data"
    _write_score(scores_dir, "claim-001", "TRUE")

    claims = [
        {"id": "claim-001", "status": "active"},
        {"id": "claim-002", "status": "active"},  # no score yet
    ]

    site_builder.rebuild_manifests(claims, scores_dir=scores_dir, site_data_dir=site_data)

    manifest = json.loads((site_data / "scores-manifest.json").read_text())
    summary = json.loads((site_data / "summary.json").read_text())
    assert len(manifest) == 2
    assert summary["total_claims"] == 2
    assert summary["verdicts"] == {"TRUE": 1}
