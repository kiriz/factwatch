"""Atomic, history-preserving writes of verdict score files.

A score file lives at ``scores/<claim_id>.json``. Each run appends the previous
verdict to ``verdict_history`` (capped, oldest trimmed), sets ``last_changed_at``
only when the verdict actually changes, and writes atomically via a temp file +
rename so a crash mid-write can never leave a corrupt or truncated score.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("factwatch.score_writer")

DEFAULT_MAX_HISTORY = 50
"""Maximum number of entries retained in ``verdict_history``."""


def _read_existing(path: Path) -> dict[str, Any] | None:
    """Read and parse an existing score file, or return None if absent/corrupt."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("score file %s is unreadable; treating as new", path)
        return None


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as JSON to ``path`` atomically (temp file + ``os.replace``).

    The temp file is removed on any failure so no ``.tmp`` artifact survives.
    """
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


def _history_entry(score: dict[str, Any]) -> dict[str, Any]:
    """Build a compact ``verdict_history`` entry from a previous score dict."""
    return {
        "verdict": score.get("verdict"),
        "confidence": score.get("confidence"),
        "checked_at": score.get("last_checked_at"),
        "run_id": score.get("run_id"),
    }


def write(
    claim_id: str,
    result: dict[str, Any],
    scores_dir: Path | str = "scores",
    max_history: int = DEFAULT_MAX_HISTORY,
) -> dict[str, Any]:
    """Persist a fact-check ``result`` for ``claim_id``, preserving history.

    Behavior:
        - If a prior score exists, its current verdict snapshot is appended to
          ``verdict_history`` and the list is trimmed to ``max_history`` newest.
        - ``last_changed_at`` is carried forward from the prior score unless the
          verdict changed, in which case it is set to the new ``last_checked_at``.
        - The merged record is written atomically.

    Args:
        claim_id: Claim identifier; the file is ``scores/<claim_id>.json``.
        result: Score dict from :func:`agent.fact_checker.check`.
        scores_dir: Directory holding score files.
        max_history: Maximum retained ``verdict_history`` entries.

    Returns:
        The merged score dict that was written to disk.
    """
    scores_path = Path(scores_dir)
    path = scores_path / f"{claim_id}.json"

    merged = dict(result)
    merged["claim_id"] = claim_id

    existing = _read_existing(path)
    if existing is None:
        # First-ever write: history empty, last_changed_at == last_checked_at.
        merged["verdict_history"] = []
        merged["last_changed_at"] = result.get("last_changed_at", result.get("last_checked_at"))
        _atomic_write_json(path, merged)
        logger.info("wrote new score for %s (%s)", claim_id, merged.get("verdict"))
        return merged

    history = list(existing.get("verdict_history") or [])
    history.append(_history_entry(existing))
    if len(history) > max_history:
        history = history[-max_history:]
    merged["verdict_history"] = history

    verdict_changed = existing.get("verdict") != result.get("verdict")
    if verdict_changed:
        merged["last_changed_at"] = result.get("last_checked_at")
        logger.info(
            "verdict for %s changed %s -> %s",
            claim_id,
            existing.get("verdict"),
            result.get("verdict"),
        )
    else:
        merged["last_changed_at"] = existing.get("last_changed_at", result.get("last_checked_at"))

    _atomic_write_json(path, merged)
    logger.info(
        "updated score for %s (%s, %d history entries)",
        claim_id,
        merged.get("verdict"),
        len(history),
    )
    return merged
