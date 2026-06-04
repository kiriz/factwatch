"""Fact-check a single claim using DuckDuckGo search + Google Gemini reasoning.

Architecture: search first (DuckDuckGo, free, no quota), then reason (Gemini plain
call, no grounding). Sources are explicit in the prompt context so the model
produces well-grounded verdicts without consuming grounding-API quota.

The public entry point is :func:`check`. It returns a ``dict`` matching the
FactWatch score schema. API failures, parse errors, and low-confidence results
are all turned into well-formed score dicts — never raised.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("factwatch.fact_checker")

GEMINI_MODEL_NAME = "gemini-flash-latest"
DSPY_LITE_MODEL_NAME = "gemini-2.0-flash-lite"
SCHEMA_VERSION = "1.0"
VALID_VERDICTS = frozenset({"TRUE", "FALSE", "MISLEADING", "UNVERIFIED", "OUTDATED", "DISPUTED"})
CONFIDENCE_REVIEW_THRESHOLD = 50
RETRY_BACKOFF_SECONDS = 15
MAX_SEARCH_RESULTS = 6

SYSTEM_PROMPT = """You are a rigorous, impartial fact-checker. You will be given a claim
and a set of web search results to verify it against.

Your task:
1. Evaluate the provided search results — seek evidence both supporting and contradicting
2. Assess source quality (peer-reviewed > government > established news > other)
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
  "confidence": <0-100 integer>,
  "sources": [
    {"url": "<url>", "title": "<title>", "relevance": "high|medium|low",
     "supports_claim": <bool>, "excerpt": "<under 15 words>"}
  ],
  "reasoning": "<multi-sentence explanation>",
  "agent_flags": {
    "conflicting_sources": <bool>, "outdated_evidence": <bool>,
    "requires_human_review": <bool>, "low_source_quality": <bool>
  }
}"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _search(query: str, max_results: int = MAX_SEARCH_RESULTS) -> list[dict[str, str]]:
    """Run a DuckDuckGo text search and return result dicts."""
    try:
        from ddgs import DDGS  # noqa: PLC0415

        results = DDGS().text(query, max_results=max_results)
        return [
            {
                "url": r.get("href", ""),
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
            }
            for r in (results or [])
            if r.get("href")
        ]
    except Exception:
        logger.warning("DuckDuckGo search failed for query: %r", query, exc_info=True)
        return []


def compute_trending_score(query: str) -> int:
    """Return 0-10 score: count of DDG results published in the past week.

    Higher = more internet discussion right now. Returns 0 on any error so a
    search failure never breaks a fact-check run.
    """
    try:
        from ddgs import DDGS  # noqa: PLC0415

        results = list(DDGS().text(query, timelimit="w", max_results=10) or [])
        return min(len(results), 10)
    except Exception:
        logger.debug("trending score search failed for %r", query, exc_info=True)
        return 0


def _build_search_queries(claim: dict[str, Any]) -> list[str]:
    """Generate 2–3 search queries from a claim definition."""
    title = claim.get("title", "")
    claim_text = claim.get("claim_text", "")
    base = claim_text or title
    queries = [base]
    if title and claim_text and title != claim_text:
        queries.append(title)
    queries.append(f"{base} fact check evidence")
    return queries[:3]


def _build_user_prompt(claim: dict[str, Any], search_results: list[dict[str, str]]) -> str:
    title = claim.get("title", "")
    claim_text = claim.get("claim_text", "")
    context = claim.get("context", "")
    notes = claim.get("verification_notes", "")

    sources_block = ""
    for i, r in enumerate(search_results, 1):
        sources_block += (
            f"\n[{i}] {r['title']}\n    URL: {r['url']}\n    Excerpt: {r['snippet'][:300]}\n"
        )
    if not sources_block:
        sources_block = "\n(No search results found — base verdict on general knowledge only)\n"

    return (
        f"Claim title: {title}\n\n"
        f"Claim to verify: {claim_text}\n\n"
        f"Human context: {context}\n\n"
        f"Verification guidance: {notes}\n\n"
        f"Web search results:\n{sources_block}\n"
        f"Return ONLY a JSON object with exactly this shape:\n{_RESPONSE_SCHEMA}"
    )


def _resolve_client() -> Any:
    """Build a google-genai Client from GOOGLE_API_KEY env var."""
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable is not set")
    return genai.Client(api_key=api_key)


def _call_gemini(client: Any, prompt: str) -> Any:
    """Call Gemini with no grounding tools (plain generation)."""
    from google.genai import types  # noqa: PLC0415

    return client.models.generate_content(
        model=GEMINI_MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text
    for candidate in getattr(response, "candidates", None) or []:
        for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return part_text
    return ""


def _extract_usage(response: Any) -> dict[str, int | None]:
    """Safely read token usage from a Gemini response.

    Never raises: missing ``usage_metadata`` or fields yield ``None`` values.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {"prompt_tokens": None, "response_tokens": None, "total_tokens": None}

    def _read(name: str) -> int | None:
        value = getattr(usage, name, None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "prompt_tokens": _read("prompt_token_count"),
        "response_tokens": _read("candidates_token_count"),
        "total_tokens": _read("total_token_count"),
    }


def _coerce_confidence(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _parse_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in model response") from None
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model response JSON is not an object")
    return payload


def _normalize_source(raw: dict[str, Any]) -> dict[str, Any]:
    url = str(raw.get("url", "") or "")
    excerpt = " ".join(str(raw.get("excerpt", "") or "").split()[:15])
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


def _build_trace(
    prompt: str,
    raw_response: str,
    usage: dict[str, int | None],
    search_queries: list[str],
    search_results: list[dict[str, str]],
    model_name: str,
    model_version: str,
    dspy_used: bool,
) -> dict[str, Any]:
    """Assemble the ``_trace`` payload for Observatory rendering.

    This is stripped before scores are written to disk (see ``score_writer``);
    it lives only in the run log trace.
    """
    return {
        "model_name": model_name,
        "model_version": model_version,
        "prompt": prompt,
        "raw_response": raw_response,
        "prompt_tokens": usage.get("prompt_tokens"),
        "response_tokens": usage.get("response_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "dspy_used": dspy_used,
        "search_queries": list(search_queries),
        "search_results": [
            {
                "url": str(r.get("url", "")),
                "title": str(r.get("title", "")),
                "snippet": str(r.get("snippet", "")),
            }
            for r in search_results[:MAX_SEARCH_RESULTS]
        ],
    }


def _build_result(
    claim_id: str,
    run_id: str,
    payload: dict[str, Any],
    checked_at: str,
) -> dict[str, Any]:
    verdict = str(payload.get("verdict", "UNVERIFIED") or "UNVERIFIED").upper()
    if verdict not in VALID_VERDICTS:
        logger.warning("claim %s: unknown verdict %r -> UNVERIFIED", claim_id, verdict)
        verdict = "UNVERIFIED"
    confidence = _coerce_confidence(payload.get("confidence", 0))
    sources = [_normalize_source(s) for s in (payload.get("sources") or []) if isinstance(s, dict)]
    raw_flags = payload.get("agent_flags") or {}
    flags = {
        "conflicting_sources": bool(raw_flags.get("conflicting_sources", False)),
        "outdated_evidence": bool(raw_flags.get("outdated_evidence", False)),
        "requires_human_review": bool(raw_flags.get("requires_human_review", False)),
        "low_source_quality": bool(raw_flags.get("low_source_quality", False)),
    }
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


def _error_result(
    claim_id: str,
    run_id: str,
    reason: str,
    *,
    prompt: str = "",
    raw_response: str = "",
    search_queries: list[str] | None = None,
    search_results: list[dict[str, str]] | None = None,
    model_name: str = GEMINI_MODEL_NAME,
    dspy_used: bool = False,
) -> dict[str, Any]:
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
        "_trace": _build_trace(
            prompt=prompt,
            raw_response=raw_response,
            usage={"prompt_tokens": None, "response_tokens": None, "total_tokens": None},
            search_queries=search_queries or [],
            search_results=search_results or [],
            model_name=model_name,
            model_version="",
            dspy_used=dspy_used,
        ),
    }


def _render_search_results_text(search_results: list[dict[str, str]]) -> str:
    if not search_results:
        return "(No search results found)"

    parts: list[str] = []
    for i, result in enumerate(search_results, 1):
        parts.append(
            (
                f"[{i}] {str(result.get('title', '') or '')}\n"
                f"URL: {str(result.get('url', '') or '')}\n"
                f"Excerpt: {str(result.get('snippet', '') or '')[:300]}"
            ).strip()
        )
    return "\n\n".join(parts)


def _generate_queries_dspy(claim: dict[str, Any]) -> list[str]:
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return []

    try:
        from agent import dspy_checker

        lite_model_name = os.environ.get("FACTWATCH_LITE_MODEL", DSPY_LITE_MODEL_NAME)
        lite_lm = dspy_checker.build_lm(lite_model_name, api_key)
        return dspy_checker.generate_queries(claim, lite_lm)
    except Exception:
        logger.exception("claim %s: DSPy query generation unavailable", claim.get("id", "unknown"))
        return []


def _fact_check_dspy(
    claim: dict[str, Any],
    search_results_text: str,
    compiled_path: Path | None = None,
) -> dict[str, Any] | None:
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return None

    try:
        from agent import dspy_checker

        main_model_name = os.environ.get("FACTWATCH_MAIN_MODEL", GEMINI_MODEL_NAME)
        main_lm = dspy_checker.build_lm(main_model_name, api_key)
        return dspy_checker.fact_check(
            claim_text=str(claim.get("claim_text", "") or ""),
            search_results_text=search_results_text,
            verification_notes=str(claim.get("verification_notes", "") or ""),
            main_lm=main_lm,
            compiled_path=compiled_path,
        )
    except Exception:
        logger.exception("claim %s: DSPy fact-check unavailable", claim.get("id", "unknown"))
        return None


def _fact_check_direct(
    claim: dict[str, Any],
    search_results: list[dict[str, str]],
    client: Any | None,
    sleep: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    claim_id = str(claim.get("id", "")) or "unknown"
    prompt = _build_user_prompt(claim, search_results)

    try:
        resolved = client if client is not None else _resolve_client()
    except Exception:
        logger.exception("claim %s: failed to build Gemini client", claim_id)
        return None, {
            "reason": "client construction error",
            "prompt": prompt,
            "raw_response": "",
            "usage": {"prompt_tokens": None, "response_tokens": None, "total_tokens": None},
            "model_version": "",
        }

    response = _call_with_retry(resolved, prompt, claim_id, sleep)
    if response is None:
        return None, {
            "reason": "API error after retry",
            "prompt": prompt,
            "raw_response": "",
            "usage": {"prompt_tokens": None, "response_tokens": None, "total_tokens": None},
            "model_version": "",
        }

    text = _extract_text(response)
    payload = _parse_with_retry(resolved, prompt, text, claim_id)
    if payload is None:
        return None, {
            "reason": "JSON parse error after retry",
            "prompt": prompt,
            "raw_response": text,
            "usage": {"prompt_tokens": None, "response_tokens": None, "total_tokens": None},
            "model_version": str(getattr(response, "model_version", "") or ""),
        }

    return payload, {
        "reason": "",
        "prompt": prompt,
        "raw_response": text,
        "usage": _extract_usage(response),
        "model_version": str(getattr(response, "model_version", "") or ""),
    }


def check(
    claim: dict[str, Any],
    run_id: str = "",
    client: Any | None = None,
    compiled_path: Path | None = None,
    *,
    sleep: Any = time.sleep,
    searcher: Any = _search,
) -> dict[str, Any]:
    """Fact-check a claim. Returns a score dict; never raises for API/parse failures.

    Args:
        claim: Claim definition with keys: id, title, claim_text, context, verification_notes.
        run_id: Current run identifier embedded in the result.
        client: Optional pre-built google-genai Client (for testing).
        compiled_path: Optional compiled DSPy program path for the production path.
        sleep: Backoff sleep injection (for testing).
        searcher: Search function injection (for testing).
    """
    claim_id = str(claim.get("id", "")) or "unknown"

    # Tests inject a client to exercise the direct Gemini path deterministically.
    # Preserve that behavior exactly by only attempting DSPy in the production path.
    if client is None:
        try:
            queries = _generate_queries_dspy(claim) or _build_search_queries(claim)
        except Exception:
            logger.exception("claim %s: DSPy query generation crashed; using fallback", claim_id)
            queries = _build_search_queries(claim)
    else:
        queries = _build_search_queries(claim)

    # Step 1: gather search results
    seen_urls: set[str] = set()
    search_results: list[dict[str, str]] = []
    for q in queries:
        for r in searcher(q, MAX_SEARCH_RESULTS):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                search_results.append(r)
        if len(search_results) >= MAX_SEARCH_RESULTS:
            break
    logger.info(
        "claim %s: %d search results from %d queries", claim_id, len(search_results), len(queries)
    )

    search_results_text = _render_search_results_text(search_results)
    dspy_payload: dict[str, Any] | None = None
    if client is None:
        try:
            dspy_payload = _fact_check_dspy(claim, search_results_text, compiled_path=compiled_path)
        except Exception:
            logger.exception("claim %s: DSPy fact-check crashed; using direct fallback", claim_id)
            dspy_payload = None

    if dspy_payload is not None:
        checked_at = _utc_now_iso()
        main_model_name = os.environ.get("FACTWATCH_MAIN_MODEL", GEMINI_MODEL_NAME)
        # DSPy returns verdict/reasoning/flags but no source list, so surface the
        # web results the agent actually consulted as the sources for this verdict.
        dspy_sources = [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "relevance": "medium",
                "supports_claim": True,
                "excerpt": r.get("snippet", ""),
            }
            for r in search_results
            if r.get("url")
        ]
        result = _build_result(
            claim_id,
            run_id,
            {
                "verdict": dspy_payload.get("verdict"),
                "confidence": dspy_payload.get("confidence"),
                "sources": dspy_sources,
                "reasoning": dspy_payload.get("reasoning"),
                "agent_flags": {
                    "conflicting_sources": dspy_payload.get("conflicting_sources", False),
                    "outdated_evidence": dspy_payload.get("outdated_evidence", False),
                    "requires_human_review": dspy_payload.get("requires_human_review", False),
                    "low_source_quality": dspy_payload.get("low_source_quality", False),
                },
            },
            checked_at,
        )
        result["_trace"] = _build_trace(
            prompt=json.dumps(
                {
                    "claim_text": str(claim.get("claim_text", "") or ""),
                    "verification_notes": str(claim.get("verification_notes", "") or ""),
                    "search_results": search_results_text,
                },
                indent=2,
                ensure_ascii=False,
            ),
            raw_response=json.dumps(dspy_payload, indent=2, ensure_ascii=False),
            usage={"prompt_tokens": None, "response_tokens": None, "total_tokens": None},
            search_queries=queries,
            search_results=search_results,
            model_name=main_model_name,
            model_version="",
            dspy_used=True,
        )
        return result

    payload, direct_meta = _fact_check_direct(claim, search_results, client, sleep)
    if payload is None:
        return _error_result(
            claim_id,
            run_id,
            str(direct_meta.get("reason", "unknown error")),
            prompt=str(direct_meta.get("prompt", "")),
            raw_response=str(direct_meta.get("raw_response", "")),
            search_queries=queries,
            search_results=search_results,
            model_name=GEMINI_MODEL_NAME,
            dspy_used=False,
        )

    result = _build_result(claim_id, run_id, payload, _utc_now_iso())
    result["_trace"] = _build_trace(
        prompt=str(direct_meta.get("prompt", "")),
        raw_response=str(direct_meta.get("raw_response", "")),
        usage=direct_meta.get("usage", {})
        if isinstance(direct_meta.get("usage"), dict)
        else {"prompt_tokens": None, "response_tokens": None, "total_tokens": None},
        search_queries=queries,
        search_results=search_results,
        model_name=GEMINI_MODEL_NAME,
        model_version=str(direct_meta.get("model_version", "")),
        dspy_used=False,
    )
    return result


def _call_with_retry(client: Any, prompt: str, claim_id: str, sleep: Any) -> Any | None:
    try:
        return _call_gemini(client, prompt)
    except Exception:
        logger.warning(
            "claim %s: Gemini call failed; retrying after %ss",
            claim_id,
            RETRY_BACKOFF_SECONDS,
            exc_info=True,
        )
    sleep(RETRY_BACKOFF_SECONDS)
    try:
        return _call_gemini(client, prompt)
    except Exception:
        logger.exception("claim %s: Gemini call failed after retry", claim_id)
        return None


def _parse_with_retry(client: Any, prompt: str, text: str, claim_id: str) -> dict[str, Any] | None:
    try:
        return _parse_payload(text)
    except ValueError:
        logger.warning("claim %s: JSON parse failed; retrying with strict prompt", claim_id)
    strict = (
        f"{prompt}\n\nYour previous response was not valid JSON. "
        "Respond with ONLY a single valid JSON object. No markdown, no prose."
    )
    try:
        r = _call_gemini(client, strict)
        return _parse_payload(_extract_text(r))
    except Exception:
        logger.exception("claim %s: JSON parse failed after strict retry", claim_id)
        return None
