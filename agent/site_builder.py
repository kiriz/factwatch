"""Regenerate the static site data manifests after a fact-checking run.

Two files are written under ``site/data``:

``scores-manifest.json``
    A list of ``{"claim_id", "score_file"}`` entries for active claims only
    (paused/retired claims are excluded).

``summary.json``
    Aggregate counts: total active claims, per-verdict tallies, and the
    last-run metadata.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("factwatch.site_builder")


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


def _read_verdict(score_path: Path) -> str | None:
    """Return the verdict from a score file, or None if missing/unreadable."""
    if not score_path.exists():
        return None
    try:
        data = json.loads(score_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("score file %s unreadable; skipping in summary", score_path)
        return None
    return data.get("verdict")


def rebuild_manifests(
    active_claims: list[dict[str, Any]],
    scores_dir: Path | str = "scores",
    site_data_dir: Path | str = "site/data",
    last_run_at: str | None = None,
    last_run_id: str | None = None,
) -> dict[str, Path]:
    """Rebuild ``scores-manifest.json`` and ``summary.json``.

    Args:
        active_claims: Active claim dicts (each with an ``id``). Callers must
            pass only active claims; paused/retired claims are filtered out
            here as a defensive second pass via the ``status`` key when present.
        scores_dir: Directory holding ``<claim_id>.json`` score files.
        site_data_dir: Output directory for the manifests.
        last_run_at: ISO timestamp of the run that produced these manifests.
        last_run_id: Identifier of the run that produced these manifests.

    Returns:
        Mapping of ``{"manifest": Path, "summary": Path}`` for the files written.
        Both files are written even when ``active_claims`` is empty.
    """
    scores_path = Path(scores_dir)
    out_dir = Path(site_data_dir)

    active = [
        claim for claim in active_claims if str(claim.get("status", "active")).lower() == "active"
    ]

    manifest: list[dict[str, str]] = []
    verdict_counts: dict[str, int] = {}
    for claim in active:
        claim_id = str(claim.get("id", ""))
        if not claim_id:
            logger.warning("active claim missing id; skipping in manifest")
            continue
        score_file = f"scores/{claim_id}.json"
        manifest.append({"claim_id": claim_id, "score_file": score_file})

        verdict = _read_verdict(scores_path / f"{claim_id}.json")
        if verdict:
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    summary = {
        "total_claims": len(manifest),
        "verdicts": verdict_counts,
        "last_run_at": last_run_at,
        "last_run_id": last_run_id,
    }

    manifest_path = out_dir / "scores-manifest.json"
    summary_path = out_dir / "summary.json"
    _atomic_write_json(manifest_path, manifest)
    _atomic_write_json(summary_path, summary)
    logger.info(
        "rebuilt manifests: %d active claims, verdicts=%s",
        len(manifest),
        verdict_counts,
    )

    return {"manifest": manifest_path, "summary": summary_path}
