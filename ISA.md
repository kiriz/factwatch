---
project: factwatch-agent
effort: E3
phase: build
progress: 0/24
mode: ALGORITHM
started: 2026-06-02T00:52:00Z
updated: 2026-06-02T00:52:00Z
---

# FactWatch Agent — ISA

## Problem

FactWatch is a human-founded, AI-maintained fact-checking site. There is no Python agent yet:
no entrypoint, no fact-checker, no score/log writers, no site manifest builder, and no tests.
A daily GitHub Actions job needs a deterministic agent that reads `claims/*.md`, skips fresh
claims, fact-checks stale ones via Gemini + Google Search grounding, writes atomic JSON verdict
scores, and regenerates static site manifests.

## Vision

A single `uv run python agent/run_agent.py` command (or `--dry-run`) that runs end-to-end, is
fully testable offline with zero API key, never corrupts existing verdict history, and exits with
a meaningful status code so CI can branch on partial vs total failure.

## Out of Scope

No real network calls in tests. No frontend rendering. No claim authoring. No database — flat
JSON files only. No deletion or overwrite of existing `verdict_history`.

## Constraints

- `uv` for package management; pyproject.toml exactly as specified.
- Python 3.12+; type hints throughout; docstrings on public functions.
- Score JSON schema, log schema, manifest schema fixed by spec.
- `should_check` signature fixed by spec.
- Exit codes: 0 success, 1 partial failure, 2 total failure.
- Never swallow exceptions silently — log before returning error state.
- Atomic writes via temp `.tmp` + rename.
- All code passes `ruff check` and `ruff format`.

## Goal

Ship `pyproject.toml` + 5 agent modules (`run_agent`, `fact_checker`, `score_writer`,
`log_writer`, `site_builder`) + 3 test modules, all passing `ruff check`, `ruff format`, and
`pytest` with mocked Gemini and no API key.

## Criteria

- [ ] ISC-1: `pyproject.toml` matches the spec verbatim (deps, dev-deps, ruff, pytest config)
- [ ] ISC-2: `uv sync` installs google-generativeai, pyyaml, python-frontmatter, pytest, ruff
- [ ] ISC-3: `run_agent.should_check` has the exact spec signature and staleness logic
- [ ] ISC-4: run_agent discovers active claims from `claims/*.md` via frontmatter
- [ ] ISC-5: run_agent orders claims high → normal → low priority
- [ ] ISC-6: run_agent honors `CLAIM_ID_FILTER` env var (single-claim run)
- [ ] ISC-7: run_agent `--dry-run` makes no API calls and writes no files
- [ ] ISC-8: run_agent exits 0/1/2 for success/partial/total failure
- [ ] ISC-9: fact_checker.check returns dict matching the score schema
- [ ] ISC-10: fact_checker sets requires_human_review when confidence < 50
- [ ] ISC-11: fact_checker returns UNVERIFIED with error flag on exception (after retry)
- [ ] ISC-12: fact_checker extracts sources from grounding_metadata.grounding_chunks
- [ ] ISC-13: fact_checker JSON parse failure retries once then logs+returns error
- [ ] ISC-14: score_writer creates a new score file with correct schema
- [ ] ISC-15: score_writer appends to verdict_history on re-run
- [ ] ISC-16: score_writer caps verdict_history at max (50), trimming oldest
- [ ] ISC-17: score_writer sets last_changed_at only when verdict changes
- [ ] ISC-18: score_writer writes atomically (temp file + rename, no leftover .tmp)
- [ ] ISC-19: log_writer writes logs/run-{ISO}.json with colons stripped from timestamp
- [ ] ISC-20: log_writer prepends summary to logs/index.json, keeps last 90 runs
- [ ] ISC-21: site_builder scores-manifest contains active claims only
- [ ] ISC-22: site_builder summary.json has correct verdict counts and last_run fields
- [ ] ISC-23: site_builder handles empty scores dir without crashing
- [ ] ISC-24: Anti: no test makes a real Gemini API call; suite passes with no API key set

## Test Strategy

| isc | type | check | threshold | tool |
|-----|------|-------|-----------|------|
| 1 | inspection | diff against spec | exact | Read |
| 2 | command | uv sync exit 0 | 0 | Bash |
| 3-24 | command | pytest + ruff | all pass | Bash |

## Features

| name | satisfies | depends_on | parallelizable |
|------|-----------|------------|----------------|
| pyproject + uv init | 1,2 | - | no |
| fact_checker | 9-13 | - | yes |
| score_writer | 14-18 | - | yes |
| log_writer | 19-20 | - | yes |
| site_builder | 21-23 | - | yes |
| run_agent | 3-8 | fact_checker,score_writer,log_writer,site_builder | no |
| tests | 24 + verification | all modules | no |

## Decisions

- 2026-06-02: Gemini model is dependency-injected into `fact_checker.check(claim, model=None)`
  so tests pass a fake without monkeypatching the SDK constructor. Cleaner boundary than patching.
- 2026-06-02: `should_check` kept as a module-level function with the exact spec signature; a thin
  `_now()` indirection is NOT added to it (spec gives literal body) — staleness tests use real
  timestamps written into temp score files instead.
- 2026-06-02 (show-your-math, delegation floor): Forge auto-include applies at E3 for coding. The
  spec is near-complete transcription (exact code/schemas given), so serializing primary authorship
  on a `codex exec` subprocess would not change correctness and would risk budget. Forge is run as a
  parallel read-only audit reviewer near VERIFY instead of as primary author; Research/Anvil skipped
  as the SDK call pattern is given verbatim. Delegation floor met via Forge audit.

## Changelog

## Verification
