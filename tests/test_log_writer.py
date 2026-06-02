"""Tests for :mod:`agent.log_writer`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent import log_writer


def test_make_run_id_strips_colons() -> None:
    moment = datetime(2026, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
    assert log_writer.make_run_id(moment) == "run-2026-06-01T060000Z"


def test_write_run_creates_trace_file(tmp_path: Path) -> None:
    run_results = {
        "run_id": "run-2026-06-01T060000Z",
        "started_at": "2026-06-01T06:00:00Z",
        "finished_at": "2026-06-01T06:01:00Z",
        "claims_processed": 2,
        "verdicts_changed": 1,
        "errors": 0,
        "claim_traces": [{"claim_id": "claim-001", "reasoning_steps": ["x"]}],
    }
    run_file = log_writer.write_run(run_results, logs_dir=tmp_path)

    assert run_file.name == "run-2026-06-01T060000Z.json"
    trace = json.loads(run_file.read_text())
    assert trace["run_id"] == "run-2026-06-01T060000Z"
    assert trace["claim_traces"][0]["reasoning_steps"] == ["x"]


def test_index_prepends_newest_first(tmp_path: Path) -> None:
    log_writer.write_run(
        {"run_id": "run-A", "started_at": "2026-06-01T06:00:00Z", "claim_traces": []},
        logs_dir=tmp_path,
    )
    log_writer.write_run(
        {"run_id": "run-B", "started_at": "2026-06-02T06:00:00Z", "claim_traces": []},
        logs_dir=tmp_path,
    )

    index = json.loads((tmp_path / "index.json").read_text())
    ids = [r["run_id"] for r in index["runs"]]
    assert ids == ["run-B", "run-A"]  # newest first


def test_index_retention_caps_runs(tmp_path: Path) -> None:
    for i in range(5):
        log_writer.write_run(
            {"run_id": f"run-{i}", "started_at": "t", "claim_traces": []},
            logs_dir=tmp_path,
            retention=3,
        )
    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index["runs"]) == 3
    assert index["runs"][0]["run_id"] == "run-4"  # most recent retained
