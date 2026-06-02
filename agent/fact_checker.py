"""Fact-check a single claim with Google Gemini + native Google Search grounding.

The public entry point is :func:`check`, which returns a ``dict`` matching the
FactWatch score JSON schema. The function is defensive: API failures, malformed
responses, and low-confidence verdicts are all turned into well-formed score
dicts (never raised) so the orchestrator can record an outcome for every claim.

The Gemini model is dependency-injected so the function is fully testable
offline: pass a fake object with a ``generate_content`` method as ``model``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("factwatch.fact_checker")

GEMINI_MODEL_NAME = "gemini-2.0-flash-latest"
"""Gemini model used for fact-checking with Google Search grounding."""

SCHEMA_VERSION = "1.0"

VALID_VERDICTS = frozenset({"TRUE", "FALSE", "MISLEADING", "UNVERIFIED", "OUTDATED", "DISPUTED"})

CONFIDENCE_REVIEW_THRESHOLD = 50
"""Below this confidence the verdict is flagged for human review."""

RETRY_BACKOFF_SECONDS = 30
"""Seconds to wait before the single retry after an API exception."""

SYSTEM_PROMPT = """You are a rigorous, impartial fact-checker. You will be given a claim to verify.
Your task:
1. Search for evidence using web search — seek both supporting and contradicting sources
2. Evaluate source quality (peer-reviewed > government > established news > other)
3. Reason step by step before reaching a verdict
4. Return ONLY valid JSON matching the provided schema — no markdown, no preamble

Verdict definitions:
- TRUE: Claim is accurate based on strong evidence
- FALSE: Claim is demonstrably incorrect
- MISLEADING: Contains truth but is framed to deceive or omits key context
- OUTDATED: Was true but evidence no longer supports it
- DISPUTED: Credible sources reach opposite conclusions
- UNVERIFIED: Insufficient public evidence found

Be conservative: if confidence < 50, set requires_human_review to true."""

_RESPONSE_SCHEMA = """{
  "verdict": "TRUE|FALSE|MISLEADING|UNVERIFIED|OUTDATED|DISPUTED",
  "confidence": 0-100 integer,
  "sources": [
    {"url": str, "title": str, "relevance": "high|medium|low",
     "supports_claim": bool, "excerpt": "under 15 words"}
  ],
  "reasoning": str,
  "agent_flags": {
    "conflicting_sources": bool, "outdated_evidence": bool,
    "requires_human_review": bool, "low_source_quality": bool
  }
}"""


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _domain_of(url: str) -> str:
    """Extract the bare network domain from a URL, stripping a leading ``www.``."""
    try:
        netloc = urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _build_user_prompt(claim: dict[str, Any]) -> str:
    """Compose the user prompt for Gemini from a claim definition."""
    title = claim.get("title", "")
    claim_text = claim.get("claim_text", "")
    context = claim.get("context", "")
    notes = claim.get("verification_notes", "")
    return (
        f"Claim title: {title}\n\n"
        f"Claim to verify: {claim_text}\n\n"
        f"Human-provided context: {context}\n\n"
        f"Human verification notes: {notes}\n\n"
        f"Return ONLY a JSON object with exactly this shape:\n{_RESPONSE_SCHEMA}"
    )


def _build_model(api_key: str) -> Any:
    """Build a thin wrapper around the google-genai client with Search grounding."""
    from google import genai  # noqa: PLC0415 (deferred on purpose)
    from google.genai import types  # noqa: PLC0415

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )

    class _Model:
        def generate_content(self, prompt: str) -> Any:
            return client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=prompt,
                config=config,
            )

    return _Model()


def _resolve_model(model: Any | None) -> Any:
    """Return the injected model, or build a real Gemini model if none provided."""
    if model is not None:
        return model
    import os  # noqa: PLC0415

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable is not set")
    return _build_model(api_key)


def _extract_text(response: Any) -> str:
    """Pull the raw text out of a Gemini response object."""
    text = getattr(response, "text", None)
    if text:
        return text
    # Fall back to walking candidates -> content -> parts for SDK variants.
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                return part_text
    return ""


def _extract_grounding_sources(response: Any) -> list[dict[str, Any]]:
    """Extract sources from ``grounding_metadata.grounding_chunks`` if present.

    Returns an empty list when grounding metadata is absent or malformed; never
    raises, so a missing field cannot abort a fact-check.
    """
    sources: list[dict[str, Any]] = []
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return sources

    grounding = getattr(candidates[0], "grounding_metadata", None)
    chunks = getattr(grounding, "grounding_chunks", None) or []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        if web is None:
            continue
        url = getattr(web, "uri", "") or ""
        title = getattr(web, "title", "") or ""
        if not url:
            continue
        sources.append(
            {
                "url": url,
                "title": title,
                "domain": _domain_of(url),
                "relevance": "medium",
                "supports_claim": True,
                "excerpt": "",
            }
        )
    return sources


def _normalize_source(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a model-provided source into the canonical source schema."""
    url = str(raw.get("url", "") or "")
    excerpt = str(raw.get("excerpt", "") or "")
    # Keep excerpts terse per schema (under ~15 words).
    excerpt = " ".join(excerpt.split()[:15])
    relevance = str(raw.get("relevance", "medium") or "medium").lower()
    if relevance not in {"high", "medium", "low"}:
        relevance = "medium"
    return {
        "url": url,
        "title": str(raw.get("title", "") or ""),
        "domain": _domain_of(url),
        "relevance": relevance,
        "supports_claim": bool(raw.get("supports_claim", False)),
        "excerpt": excerpt,
    }


def _coerce_confidence(value: Any) -> int:
    """Clamp a model-provided confidence to an integer in ``[0, 100]``."""
    try:
        conf = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, conf))


def _parse_payload(text: str) -> dict[str, Any]:
    """Parse JSON from model text, tolerating ```` ```json ```` fences.

    Raises ``ValueError`` if no valid JSON object can be recovered.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip a leading fence line and a trailing fence.
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in model response") from None
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model response JSON is not an object")
    return payload


def _build_result(
    claim_id: str,
    run_id: str,
    payload: dict[str, Any],
    grounding_sources: list[dict[str, Any]],
    checked_at: str,
) -> dict[str, Any]:
    """Assemble a schema-conforming score dict from a parsed model payload."""
    verdict = str(payload.get("verdict", "UNVERIFIED") or "UNVERIFIED").upper()
    if verdict not in VALID_VERDICTS:
        logger.warning("claim %s: unknown verdict %r -> UNVERIFIED", claim_id, verdict)
        verdict = "UNVERIFIED"

    confidence = _coerce_confidence(payload.get("confidence", 0))

    raw_sources = payload.get("sources") or []
    sources = [_normalize_source(s) for s in raw_sources if isinstance(s, dict)]
    if not sources and grounding_sources:
        sources = grounding_sources

    raw_flags = payload.get("agent_flags") or {}
    flags = {
        "conflicting_sources": bool(raw_flags.get("conflicting_sources", False)),
        "outdated_evidence": bool(raw_flags.get("outdated_evidence", False)),
        "requires_human_review": bool(raw_flags.get("requires_human_review", False)),
        "low_source_quality": bool(raw_flags.get("low_source_quality", False)),
    }
    # Conservative rule: low confidence always escalates to human review.
    if confidence < CONFIDENCE_REVIEW_THRESHOLD:
        flags["requires_human_review"] = True

    return {
        "schema_version": SCHEMA_VERSION,
        "claim_id": claim_id,
        "verdict": verdict,
        "confidence": confidence,
        "last_checked_at": checked_at,
        "last_changed_at": checked_at,
        "run_id": run_id,
        "sources": sources,
        "reasoning": str(payload.get("reasoning", "") or ""),
        "verdict_history": [],
        "agent_flags": flags,
    }


def _error_result(claim_id: str, run_id: str, reason: str) -> dict[str, Any]:
    """Build an UNVERIFIED score dict flagged for human review after a failure."""
    checked_at = _utc_now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_id": claim_id,
        "verdict": "UNVERIFIED",
        "confidence": 0,
        "last_checked_at": checked_at,
        "last_changed_at": checked_at,
        "run_id": run_id,
        "sources": [],
        "reasoning": f"Fact-check failed: {reason}",
        "verdict_history": [],
        "agent_flags": {
            "conflicting_sources": False,
            "outdated_evidence": False,
            "requires_human_review": True,
            "low_source_quality": True,
        },
    }


def check(
    claim: dict[str, Any],
    run_id: str = "",
    model: Any | None = None,
    *,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Fact-check a single claim and return a score dict matching the schema.

    Args:
        claim: Claim definition. Must include ``id``; may include ``title``,
            ``claim_text``, ``context``, and ``verification_notes``.
        run_id: Identifier of the current run, embedded in the result.
        model: Optional pre-built Gemini model (any object exposing
            ``generate_content(prompt) -> response``). When ``None`` a real
            Gemini 2.0 Flash model with Google Search grounding is constructed.
        sleep: Injection point for the backoff sleep (testing seam).

    Returns:
        A dict conforming to the FactWatch score schema. On API failure the
        verdict is ``UNVERIFIED`` with ``requires_human_review`` set; the
        function never raises for an API or parse problem.
    """
    claim_id = str(claim.get("id", "")) or "unknown"
    prompt = _build_user_prompt(claim)

    try:
        resolved = _resolve_model(model)
    except Exception:
        logger.exception("claim %s: failed to construct Gemini model", claim_id)
        return _error_result(claim_id, run_id, "model construction error")

    response = _call_with_retry(resolved, prompt, claim_id, sleep)
    if response is None:
        return _error_result(claim_id, run_id, "API error after retry")

    grounding_sources = _extract_grounding_sources(response)
    text = _extract_text(response)

    payload = _parse_with_retry(resolved, prompt, text, claim_id)
    if payload is None:
        return _error_result(claim_id, run_id, "JSON parse error after retry")

    return _build_result(claim_id, run_id, payload, grounding_sources, _utc_now_iso())


def _call_with_retry(model: Any, prompt: str, claim_id: str, sleep: Any) -> Any | None:
    """Call ``generate_content``; retry once after backoff. Return None on failure."""
    try:
        return model.generate_content(prompt)
    except Exception:
        logger.warning(
            "claim %s: Gemini call failed; retrying after %ss",
            claim_id,
            RETRY_BACKOFF_SECONDS,
            exc_info=True,
        )
    sleep(RETRY_BACKOFF_SECONDS)
    try:
        return model.generate_content(prompt)
    except Exception:
        logger.exception("claim %s: Gemini call failed after retry", claim_id)
        return None


def _parse_with_retry(model: Any, prompt: str, text: str, claim_id: str) -> dict[str, Any] | None:
    """Parse model JSON; on failure retry once with a stricter prompt.

    Returns None (after logging) if both attempts fail to yield valid JSON.
    """
    try:
        return _parse_payload(text)
    except ValueError:
        logger.warning(
            "claim %s: could not parse model JSON; retrying with strict prompt",
            claim_id,
        )

    strict_prompt = (
        f"{prompt}\n\n"
        "Your previous response was not valid JSON. Respond with ONLY a single "
        "valid JSON object. No markdown, no code fences, no prose."
    )
    try:
        retry_response = model.generate_content(strict_prompt)
    except Exception:
        logger.exception("claim %s: strict-prompt retry call failed", claim_id)
        return None

    try:
        return _parse_payload(_extract_text(retry_response))
    except ValueError:
        logger.exception("claim %s: model JSON unparseable after strict retry", claim_id)
        return None
