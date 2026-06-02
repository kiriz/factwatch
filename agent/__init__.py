"""FactWatch agent package.

Modules:
    run_agent     -- daily entrypoint and orchestration
    fact_checker  -- Gemini + Google Search grounding fact-checks
    score_writer  -- atomic verdict score file writes
    log_writer    -- run trace logs and index
    site_builder  -- static site manifest regeneration
"""

__all__ = [
    "fact_checker",
    "log_writer",
    "run_agent",
    "score_writer",
    "site_builder",
]
