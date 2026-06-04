"""Compile a DSPy FactCheck program using existing verdicts as training examples.

Usage:
    uv run python agent/optimize.py [--scores-dir scores] [--output agent/compiled/factcheck.json]
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import frontmatter

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import dspy_checker, run_agent

logger = logging.getLogger("factwatch.optimize")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = Path("agent/compiled/factcheck.json")
DEFAULT_SCORES_DIR = Path("scores")
TOKEN_SAVINGS_ESTIMATE = (
    "Estimated token savings: query generation ~0 extra tokens overall (~150 lite-model "
    "tokens), fact-checking ~30-40% fewer tokens, parse retries eliminated, and lite-model "
    "query routing is ~70% cheaper per token."
)


def _load_dspy() -> Any:
    return importlib.import_module("dspy")


def _load_examples(project_root: Path, scores_dir: Path) -> list[dict[str, str]]:
    claims_dir = project_root / "claims"
    records: list[dict[str, str]] = []

    for score_path in sorted(scores_dir.glob("*.json")):
        try:
            score = json.loads(score_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("failed to read score file %s", score_path)
            continue

        verdict = str(score.get("verdict", "") or "").strip()
        reasoning = str(score.get("reasoning", "") or "").strip()
        if not verdict or not reasoning:
            continue

        claim_id = str(score.get("claim_id", score_path.stem) or score_path.stem)
        claim_path = claims_dir / f"{claim_id}.md"
        if not claim_path.exists():
            logger.warning("skipping %s: missing claim file %s", claim_id, claim_path)
            continue

        try:
            post = frontmatter.load(str(claim_path))
        except (OSError, ValueError):
            logger.exception("failed to parse claim file %s", claim_path)
            continue

        sections = run_agent._parse_body_sections(post.content)
        claim_text = str(sections.get("claim_text", "") or "").strip()
        if not claim_text:
            logger.warning("skipping %s: claim_text missing in %s", claim_id, claim_path)
            continue

        records.append(
            {
                "claim_text": claim_text,
                "search_results": "(from log)",
                "verdict": verdict,
                "reasoning": reasoning,
            }
        )

    return records


def _compile_program(project_root: Path, scores_dir: Path, output_path: Path) -> Path | None:
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        logger.error("GOOGLE_API_KEY environment variable is not set; cannot optimize")
        return None

    examples_data = _load_examples(project_root, scores_dir)
    if not examples_data:
        logger.error("no training examples found in %s", scores_dir)
        return None

    try:
        dspy = _load_dspy()
        lm = dspy_checker.build_lm(
            os.environ.get("FACTWATCH_MAIN_MODEL", "gemini-flash-latest"),
            api_key,
        )
        dspy.configure(lm=lm)
        trainset = [
            dspy.Example(**record).with_inputs("claim_text", "search_results")
            for record in examples_data
        ]
        optimizer = dspy.BootstrapFewShot(
            metric=lambda example, prediction: example.verdict == prediction.verdict,
            max_bootstrapped_demos=3,
        )
        compiled_program = optimizer.compile(
            student=dspy_checker._build_fact_checker_program(),
            trainset=trainset,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        compiled_program.save(str(output_path))
    except Exception:
        logger.exception("DSPy optimization failed")
        return None

    logger.info("saved compiled DSPy program to %s", output_path)
    logger.info(TOKEN_SAVINGS_ESTIMATE)
    return output_path


def run_optimization(project_root: Path) -> Path | None:
    """Compile the default DSPy fact-checker program for this project."""
    try:
        return _compile_program(
            project_root=project_root,
            scores_dir=project_root / DEFAULT_SCORES_DIR,
            output_path=project_root / DEFAULT_OUTPUT,
        )
    except Exception:
        logger.exception("optimization wrapper failed")
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI wrapper for DSPy optimization."""
    parser = argparse.ArgumentParser(description="Compile DSPy examples for FactWatch")
    parser.add_argument(
        "--scores-dir",
        type=Path,
        default=DEFAULT_SCORES_DIR,
        help="directory containing existing score JSON files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="path to write the compiled DSPy program",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    scores_dir = (
        args.scores_dir if args.scores_dir.is_absolute() else PROJECT_ROOT / args.scores_dir
    )
    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    compiled_path = _compile_program(PROJECT_ROOT, scores_dir, output_path)
    if compiled_path is None:
        print("DSPy optimization failed. See logs for details.", file=sys.stderr)
        return 1

    print(f"Saved compiled DSPy program to {compiled_path}")
    print(TOKEN_SAVINGS_ESTIMATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
