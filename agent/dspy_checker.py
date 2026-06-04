"""Best-effort DSPy helpers for structured FactWatch fact-checking.

Expected token savings versus the direct Gemini path:
- Query generation: ~0 extra tokens overall; the lite model uses roughly 150 tokens.
- Fact-checking: ~30-40% fewer tokens by removing JSON schema boilerplate and shrinking
  the system prompt.
- Parse retries: eliminated because DSPy handles structured output internally.
- Model routing: query generation on the lite model is ~70% cheaper per token.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("factwatch.dspy_checker")

VALID_VERDICTS = frozenset({"TRUE", "FALSE", "MISLEADING", "UNVERIFIED", "OUTDATED", "DISPUTED"})
CONFIDENCE_REVIEW_THRESHOLD = 50


def _load_dspy() -> Any:
    return importlib.import_module("dspy")


# DSPy is imported lazily, so its types are never in module scope. The ``LM``
# alias keeps annotations readable while satisfying static analysis (``Any``).
LM = Any


def _build_query_generator_program() -> Any:
    # Signatures/modules are created lazily so this module remains importable when DSPy
    # is not installed; tests monkeypatch `_load_dspy()` directly.
    dspy = _load_dspy()

    class QueryGenSignature(dspy.Signature):
        """Generate 3 focused web search queries to fact-check a claim. Return diverse angles."""

        claim_title: str = dspy.InputField(desc="Short claim title")
        claim_text: str = dspy.InputField(desc="Full claim statement")
        context: str = dspy.InputField(desc="Human-provided context, may be empty")
        queries: list[str] = dspy.OutputField(desc="Exactly 3 search queries, diverse angles")

    class QueryGenerator(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self.predict = dspy.Predict(QueryGenSignature)

        def forward(self, claim_title: str, claim_text: str, context: str = "") -> Any:
            return self.predict(claim_title=claim_title, claim_text=claim_text, context=context)

    return QueryGenerator()


def _build_fact_checker_program() -> Any:
    dspy = _load_dspy()

    class FactCheckSignature(dspy.Signature):
        """Rigorous impartial fact-checker. Evaluate the claim against web evidence.

        Source quality: peer-reviewed > government > established news > other.
        """

        claim_text: str = dspy.InputField(desc="The exact claim to verify")
        search_results: str = dspy.InputField(
            desc="Numbered web search results with URL, title, excerpt"
        )
        verification_notes: str = dspy.InputField(
            desc="Human guidance for verification, may be empty"
        )
        verdict: Literal["TRUE", "FALSE", "MISLEADING", "UNVERIFIED", "OUTDATED", "DISPUTED"] = (
            dspy.OutputField()
        )
        confidence: int = dspy.OutputField(desc="0-100. Below 50 means insufficient evidence")
        reasoning: str = dspy.OutputField(desc="Multi-sentence explanation citing sources")
        conflicting_sources: bool = dspy.OutputField()
        outdated_evidence: bool = dspy.OutputField()
        requires_human_review: bool = dspy.OutputField(desc="True when confidence < 50")
        low_source_quality: bool = dspy.OutputField()

    class FactChecker(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self.predict = dspy.Predict(FactCheckSignature)

        def forward(
            self,
            claim_text: str,
            search_results: str,
            verification_notes: str = "",
        ) -> Any:
            return self.predict(
                claim_text=claim_text,
                search_results=search_results,
                verification_notes=verification_notes,
            )

    return FactChecker()


def _coerce_confidence(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    return bool(value)


def build_lm(model_name: str, api_key: str) -> LM:
    """Build a DSPy LM for the given Gemini model."""
    dspy = _load_dspy()
    return dspy.LM(f"gemini/{model_name}", api_key=api_key)


def generate_queries(claim: dict[str, Any], lite_lm: LM) -> list[str]:
    """Use the lite model to generate search queries. Returns [] on failure."""
    try:
        dspy = _load_dspy()
        predictor = _build_query_generator_program()
        with dspy.context(lm=lite_lm):
            prediction = predictor(
                claim_title=str(claim.get("title", "") or ""),
                claim_text=str(claim.get("claim_text", "") or ""),
                context=str(claim.get("context", "") or ""),
            )
    except Exception:
        logger.exception("DSPy query generation failed for claim %s", claim.get("id", "unknown"))
        return []

    raw_queries = getattr(prediction, "queries", [])
    if isinstance(raw_queries, str):
        values = [raw_queries]
    elif isinstance(raw_queries, (list, tuple)):
        values = list(raw_queries)
    else:
        values = []

    queries: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            queries.append(text)
        if len(queries) == 3:
            break
    return queries


def fact_check(
    claim_text: str,
    search_results_text: str,
    verification_notes: str,
    main_lm: LM,
    compiled_path: Path | None = None,
) -> dict[str, Any] | None:
    """Run DSPy fact-checking. Returns a normalized result dict or None on failure."""
    try:
        dspy = _load_dspy()
        checker = _build_fact_checker_program()
        if compiled_path is not None and compiled_path.exists():
            try:
                checker.load(str(compiled_path))
            except Exception:
                logger.exception("failed to load compiled DSPy program from %s", compiled_path)
                checker = _build_fact_checker_program()
        with dspy.context(lm=main_lm):
            prediction = checker(
                claim_text=claim_text,
                search_results=search_results_text,
                verification_notes=verification_notes,
            )
    except Exception:
        logger.exception("DSPy fact-check failed")
        return None

    verdict = str(getattr(prediction, "verdict", "UNVERIFIED") or "UNVERIFIED").upper()
    if verdict not in VALID_VERDICTS:
        verdict = "UNVERIFIED"

    confidence = _coerce_confidence(getattr(prediction, "confidence", 0))
    requires_human_review = _coerce_bool(getattr(prediction, "requires_human_review", False))
    if confidence < CONFIDENCE_REVIEW_THRESHOLD:
        requires_human_review = True

    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": str(getattr(prediction, "reasoning", "") or ""),
        "conflicting_sources": _coerce_bool(getattr(prediction, "conflicting_sources", False)),
        "outdated_evidence": _coerce_bool(getattr(prediction, "outdated_evidence", False)),
        "requires_human_review": requires_human_review,
        "low_source_quality": _coerce_bool(getattr(prediction, "low_source_quality", False)),
    }
