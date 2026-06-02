"""Run trace logging for the FactWatch agent.

Each run writes a full trace to ``logs/run-<timestamp>.json`` and prepends a
compact summary to ``logs/index.json`` (newest first), keeping only the most
recent ``retention`` runs.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("factwatch.log_writer")

DEFAULT_RETENTION = 90
"""Number of run summaries retained in ``logs/index.json``."""


def make_run_id(now: datetime | None = None) -> str:
    """Return a run id like ``run-2026-06-01T060000Z`` (colons stripped).

    Args:
        now: Optional timestamp (UTC). Defaults to the current UTC time.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    iso = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"run-{iso.replace(':', '')}"


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON atomically (temp file + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        logger.exception("atomic write to %s failed", path)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.exception("failed to clean up temp file %s", tmp_path)
        raise


def _load_index(path: Path) -> dict[str, Any]:
    """Load ``index.json`` or return a fresh empty index on absence/corruption."""
    if not path.exists():
        return {"runs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("log index %s unreadable; starting fresh", path)
        return {"runs": []}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        logger.warning("log index %s malformed; starting fresh", path)
        return {"runs": []}
    return data


def write_run(
    run_results: dict[str, Any],
    logs_dir: Path | str = "logs",
    retention: int = DEFAULT_RETENTION,
) -> Path:
    """Write a full run trace and prepend its summary to the run index.

    Args:
        run_results: Run trace. Recognized keys: ``run_id``, ``started_at``,
            ``finished_at``, ``claim_traces`` (each may carry a
            ``reasoning_steps`` list), ``verdicts_changed``, ``errors``.
        logs_dir: Directory for log files.
        retention: Number of run summaries to keep in ``index.json``.

    Returns:
        Path to the full run-trace file that was written.
    """
    logs_path = Path(logs_dir)
    run_id = run_results.get("run_id") or make_run_id()

    full_trace = {
        "run_id": run_id,
        "started_at": run_results.get("started_at"),
        "finished_at": run_results.get("finished_at"),
        "claim_traces": run_results.get("claim_traces", []),
        "verdicts_changed": run_results.get("verdicts_changed", 0),
        "errors": run_results.get("errors", 0),
    }

    run_file = logs_path / f"{run_id}.json"
    _atomic_write_json(run_file, full_trace)
    logger.info("wrote run trace %s", run_file)

    claim_traces = run_results.get("claim_traces") or []
    summary = {
        "run_id": run_id,
        "started_at": run_results.get("started_at"),
        "claims_processed": run_results.get("claims_processed", len(claim_traces)),
        "verdicts_changed": run_results.get("verdicts_changed", 0),
        "errors": run_results.get("errors", 0),
    }

    index_path = logs_path / "index.json"
    index = _load_index(index_path)
    runs = [summary, *index["runs"]]
    index["runs"] = runs[:retention]
    _atomic_write_json(index_path, index)
    logger.info("prepended run %s to index (%d retained)", run_id, len(index["runs"]))

    return run_file
