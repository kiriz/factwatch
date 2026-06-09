"""FactWatch daily agent entrypoint.

Run as a script (typically from a GitHub Actions cron job):

    uv run python agent/run_agent.py
    uv run python agent/run_agent.py --dry-run

Pipeline:
    1. Load config from ``config.yml``.
    2. Discover active claims from ``claims/*.md``.
    3. Order high -> normal -> low priority.
    4. Apply the staleness filter (and the ``CLAIM_ID_FILTER`` env override).
    5. Fact-check each surviving claim, writing its score.
    6. Write the run trace and rebuild the static-site manifests.
    7. Print a summary and exit 0 (ok) / 1 (partial failure) / 2 (total failure).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

# Allow running as a bare script (``python agent/run_agent.py``) as well as a
# module (``python -m agent.run_agent``): ensure the project root is importable.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import fact_checker, log_writer, score_writer, site_builder

logger = logging.getLogger("factwatch.run_agent")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}
"""Sort weight per priority; unknown priorities sort last."""

EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_TOTAL_FAILURE = 2


def load_config(config_path: Path) -> dict[str, Any]:
    """Load and parse the YAML config file.

    Raises:
        FileNotFoundError: if ``config_path`` does not exist.
        yaml.YAMLError: if the file is not valid YAML.
    """
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("could not read config at %s", config_path)
        raise
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config at {config_path} did not parse to a mapping")
    return data


def discover_claims(claims_dir: Path) -> list[dict[str, Any]]:
    """Load every active claim definition from ``claims/*.md``.

    Files whose name starts with ``_`` (e.g. ``_template.md``) and claims whose
    ``status`` is not ``active`` are skipped.

    Returns:
        A list of claim dicts carrying frontmatter keys plus parsed body
        sections (``claim_text``, ``context``, ``verification_notes``).
    """
    claims: list[dict[str, Any]] = []
    if not claims_dir.is_dir():
        logger.error("claims directory %s does not exist", claims_dir)
        return claims

    for path in sorted(claims_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            post = frontmatter.load(str(path))
        except (OSError, ValueError):
            logger.exception("failed to parse claim file %s; skipping", path)
            continue

        meta = dict(post.metadata)
        status = str(meta.get("status", "active")).lower()
        if status != "active":
            logger.info("skipping %s claim %s", status, meta.get("id", path.name))
            continue

        claim = dict(meta)
        claim["source_path"] = str(path)
        claim.update(_parse_body_sections(post.content))
        claims.append(claim)

    return claims


def _parse_body_sections(content: str) -> dict[str, str]:
    """Split a claim body into ``claim_text``/``context``/``verification_notes``.

    Sections are delimited by ``##`` markdown headers; matching is tolerant of
    extra words in the header text (e.g. "Verification Notes (Human)").
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            header = stripped[3:].strip().lower()
            if header.startswith("claim text"):
                current = "claim_text"
            elif header.startswith("context"):
                current = "context"
            elif header.startswith("verification notes"):
                current = "verification_notes"
            else:
                current = None
            if current is not None:
                sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)

    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def order_by_priority(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return claims ordered high -> normal -> low, stable within a priority."""
    return sorted(
        claims,
        key=lambda c: PRIORITY_ORDER.get(
            str(c.get("priority", "normal")).lower(), len(PRIORITY_ORDER)
        ),
    )


def should_check(claim_id: str, scores_dir: Path, staleness_days: int) -> bool:
    """Return True if ``claim_id`` should be re-checked given staleness policy.

    A claim is checked when no score file exists yet, or when its recorded
    ``last_checked_at`` is at least ``staleness_days`` days old.
    """
    score_path = scores_dir / f"{claim_id}.json"
    if not score_path.exists():
        return True
    score = json.loads(score_path.read_text())
    last_checked = datetime.fromisoformat(score["last_checked_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - last_checked).days >= staleness_days


def _select_claims(
    claims: list[dict[str, Any]],
    scores_dir: Path,
    staleness_days: int,
    claim_id_filter: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Partition claims into (to_check, skipped_ids) honoring filter + staleness."""
    to_check: list[dict[str, Any]] = []
    skipped: list[str] = []

    for claim in claims:
        claim_id = str(claim.get("id", ""))
        if claim_id_filter and claim_id != claim_id_filter:
            skipped.append(claim_id)
            continue
        # An explicit single-claim dispatch is a deliberate "re-check now" — it
        # bypasses the staleness window. Scheduled runs still honor staleness.
        if claim_id_filter == claim_id or should_check(claim_id, scores_dir, staleness_days):
            to_check.append(claim)
        else:
            logger.info("skipping fresh claim %s (within staleness window)", claim_id)
            skipped.append(claim_id)

    return to_check, skipped


def _prior_verdict(scores_dir: Path, claim_id: str) -> str | None:
    """Return the previously recorded verdict for a claim, or None if absent."""
    path = scores_dir / f"{claim_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("verdict")
    except (json.JSONDecodeError, OSError):
        logger.exception("prior score for %s unreadable", claim_id)
        return None


def _split_reasoning_steps(reasoning: str) -> list[str]:
    """Split a reasoning string into discrete steps.

    Splits on sentence boundaries (". ") and keeps only substantive fragments
    (>= 20 chars). Falls back to the whole string when nothing qualifies so the
    Observatory never renders an empty reasoning block.
    """
    text = (reasoning or "").strip()
    if not text:
        return []
    steps = [s.strip() for s in text.split(". ") if len(s.strip()) >= 20]
    return steps or [text]


def _check_one(
    claim: dict[str, Any],
    run_id: str,
    scores_dir: Path,
    client: Any | None,
    max_history: int,
    compiled_path: Path | None = None,
) -> dict[str, Any]:
    """Fact-check and persist a single claim, returning a trace entry.

    Never raises: any unexpected error is captured into the trace with
    ``error`` set so the caller can record a partial-failure exit code.
    """
    claim_id = str(claim.get("id", ""))
    trace: dict[str, Any] = {
        "claim_id": claim_id,
        "claim_title": str(claim.get("title", "")),
        "claim_text": str(claim.get("claim_text", "")),
        "category": str(claim.get("category", "")),
    }
    try:
        # Capture the prior verdict BEFORE writing so a first-ever check is not
        # miscounted as a change (a new claim has no previous verdict).
        previous_verdict = _prior_verdict(scores_dir, claim_id)
        result = fact_checker.check(
            claim,
            run_id=run_id,
            client=client,
            compiled_path=compiled_path,
        )
        llm_trace = result.pop("_trace", {})

        # Resilience: a transient fact-check failure (UNVERIFIED + "Fact-check
        # failed") must NOT clobber a previously good verdict. Keep the prior
        # score so a flaky API run can't blank the site; the claim re-checks on
        # the next run. Only write the error result for never-verified claims.
        is_error = result.get("verdict") == "UNVERIFIED" and str(
            result.get("reasoning", "")
        ).startswith("Fact-check failed")
        if is_error and previous_verdict not in (None, "UNVERIFIED"):
            logger.warning(
                "claim %s: fact-check failed (%s); keeping prior verdict %s",
                claim_id,
                result.get("reasoning"),
                previous_verdict,
            )
            trace.update(
                {
                    "verdict": previous_verdict,
                    "new_verdict": previous_verdict,
                    "verdict_changed": False,
                    "error": True,
                    "error_detail": result.get("reasoning"),
                    "reasoning_steps": [f"Re-check failed; kept prior verdict {previous_verdict}."],
                }
            )
            return trace

        result["title"] = str(claim.get("title", ""))
        result["category"] = str(claim.get("category", ""))
        result["trending_score"] = fact_checker.compute_trending_score(
            str(claim.get("title", "") or claim.get("claim_text", ""))
        )
        written = score_writer.write(
            claim_id, result, scores_dir=scores_dir, max_history=max_history
        )
        verdict_changed = previous_verdict is not None and previous_verdict != written.get(
            "verdict"
        )
        trace.update(
            {
                "verdict": written.get("verdict"),
                "confidence": written.get("confidence"),
                "verdict_changed": verdict_changed,
                "previous_verdict": previous_verdict,
                "new_verdict": written.get("verdict"),
                "requires_human_review": written.get("agent_flags", {}).get(
                    "requires_human_review", False
                ),
                "agent_flags": written.get("agent_flags", {}),
                "sources_found": len(llm_trace.get("search_results", [])),
                "sources_used": len(written.get("sources", [])),
                "reasoning_steps": _split_reasoning_steps(written.get("reasoning", "")),
                "error": result.get("verdict") == "UNVERIFIED"
                and result.get("reasoning", "").startswith("Fact-check failed"),
            }
        )
        trace.update(
            {
                "model_name": llm_trace.get("model_name"),
                "model_version": llm_trace.get("model_version"),
                "prompt": llm_trace.get("prompt"),
                "raw_response": llm_trace.get("raw_response"),
                "prompt_tokens": llm_trace.get("prompt_tokens"),
                "response_tokens": llm_trace.get("response_tokens"),
                "total_tokens": llm_trace.get("total_tokens"),
                "dspy_used": llm_trace.get("dspy_used", False),
                "search_queries": llm_trace.get("search_queries", []),
                "search_results": llm_trace.get("search_results", []),
            }
        )
    except Exception as exc:  # defensive: one bad claim must not abort the run
        logger.exception("unexpected failure checking claim %s", claim_id)
        trace.update({"verdict": None, "error": True, "error_detail": str(exc)})
    return trace


def run(
    project_root: Path = PROJECT_ROOT,
    dry_run: bool = False,
    client: Any | None = None,
    compiled_path: Path | None = None,
) -> int:
    """Execute one full agent run and return the process exit code.

    Args:
        project_root: Root containing ``config.yml``, ``claims/``, ``scores/``,
            ``logs/`` and ``site/``.
        dry_run: When True, run discovery + staleness filtering and log the
            claims that would be checked, but make no API calls and write
            nothing to disk.
        client: Optional injected google-genai Client passed through to the checker
            (used by tests and dry-runs).
        compiled_path: Optional compiled DSPy program path threaded to the checker.

    Returns:
        ``0`` on full success, ``1`` if some claims errored, ``2`` on total
        failure (config/discovery error, or every checked claim errored).
    """
    config_path = project_root / "config.yml"
    claims_dir = project_root / "claims"
    scores_dir = project_root / "scores"

    try:
        config = load_config(config_path)
    except (OSError, ValueError, yaml.YAMLError):
        logger.exception("config load failed; aborting run")
        return EXIT_TOTAL_FAILURE

    staleness_days = int(config.get("staleness_days", 7))
    max_history = int(config.get("max_verdict_history", score_writer.DEFAULT_MAX_HISTORY))
    retention = int(config.get("log_retention_runs", log_writer.DEFAULT_RETENTION))

    claims = order_by_priority(discover_claims(claims_dir))
    if not claims:
        logger.error("no active claims discovered; nothing to do")
        return EXIT_TOTAL_FAILURE

    claim_id_filter = os.environ.get("CLAIM_ID_FILTER") or None
    to_check, skipped = _select_claims(claims, scores_dir, staleness_days, claim_id_filter)

    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = log_writer.make_run_id()

    if dry_run:
        print("DRY RUN — no API calls, no writes")
        print(f"  active claims: {len(claims)}")
        print(f"  would check:   {[c.get('id') for c in to_check]}")
        print(f"  would skip:    {skipped}")
        return EXIT_OK

    claim_traces: list[dict[str, Any]] = []
    errors = 0
    verdicts_changed = 0
    for claim in to_check:
        trace = _check_one(claim, run_id, scores_dir, client, max_history, compiled_path)
        if trace.get("error"):
            errors += 1
        if trace.get("verdict_changed"):
            verdicts_changed += 1
        claim_traces.append(trace)

    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_results = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "claims_processed": len(claim_traces),
        "verdicts_changed": verdicts_changed,
        "errors": errors,
        "claim_traces": claim_traces,
    }

    try:
        log_writer.write_run(run_results, logs_dir=project_root / "logs", retention=retention)
    except OSError:
        logger.exception("failed to write run logs")

    try:
        site_builder.rebuild_manifests(
            claims,
            scores_dir=scores_dir,
            site_data_dir=project_root / "site" / "data",
            last_run_at=finished_at,
            last_run_id=run_id,
        )
        site_builder.mirror_scores_and_logs(
            scores_dir=scores_dir,
            logs_dir=project_root / "logs",
            site_dir=project_root / "site",
        )
    except OSError:
        logger.exception("failed to rebuild site manifests")

    _print_summary(run_id, len(claims), to_check, skipped, verdicts_changed, errors)

    if to_check and errors == len(to_check):
        return EXIT_TOTAL_FAILURE
    if errors:
        return EXIT_PARTIAL_FAILURE
    return EXIT_OK


def _print_summary(
    run_id: str,
    active_count: int,
    to_check: list[dict[str, Any]],
    skipped: list[str],
    verdicts_changed: int,
    errors: int,
) -> None:
    """Print a human-readable run summary to stdout."""
    print(f"FactWatch run {run_id}")
    print(f"  active claims:    {active_count}")
    print(f"  checked:          {len(to_check)}")
    print(f"  skipped (fresh):  {len(skipped)}")
    print(f"  verdicts changed: {verdicts_changed}")
    print(f"  errors:           {errors}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parse args, configure logging, run, and return exit code."""
    parser = argparse.ArgumentParser(description="FactWatch daily fact-checking agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report which claims would be checked without API calls or writes",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="project root containing config.yml, claims/, scores/, logs/, site/",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Run DSPy BootstrapFewShot optimization before fact-checking",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    compiled_path: Path | None = None
    if args.optimize:
        from agent import optimize

        compiled_path = optimize.run_optimization(args.project_root)
        if compiled_path is None:
            print("DSPy optimization failed; continuing without a compiled program")
        else:
            print(f"DSPy optimization output: {compiled_path}")

    return run(project_root=args.project_root, dry_run=args.dry_run, compiled_path=compiled_path)


if __name__ == "__main__":
    sys.exit(main())
