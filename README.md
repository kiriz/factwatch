# FactWatch

**Live site:** https://kiriz.github.io/factwatch/ &nbsp;·&nbsp; **Repo:** https://github.com/kiriz/factwatch

**Facts verified by humans. Kept current by AI.**

FactWatch is an open-source fact-checking site where humans curate the claims and an AI agent (powered by Google Gemini + Search) periodically re-verifies each one. Every reasoning step is logged publicly in the [Observatory](#observatory).

---

## How it works

1. **Humans define claims** — editors add `claims/claim-NNN.md` files with the exact statement to verify and guidance for the agent
2. **AI agent runs daily** — a GitHub Actions job runs Gemini 2.0 Flash with Google Search grounding against every stale claim
3. **Results are committed back** — verdicts, confidence scores, sources, and reasoning are written to `scores/` and `logs/`
4. **GitHub Pages serves the site** — the static site reads the JSON files directly; no server required

---

## Setup (fork and deploy)

### 1. Fork this repo

Fork to your own GitHub account and clone it locally.

### 2. Get a free Google API key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new API key (free tier: 1500 requests/day, 1M tokens/day)

### 3. Add the secret to your repo

In your forked repo: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `GOOGLE_API_KEY`
- Value: your API key from step 2

### 4. Enable GitHub Pages

In your forked repo: **Settings → Pages**

- Source: **GitHub Actions**

### 5. Trigger the first run

Go to **Actions → FactWatch AI Agent → Run workflow** to kick off the first fact-check. After it completes, push any commit to `main` to trigger the first Pages deployment.

---

## Submitting a claim

Open a [GitHub Issue](../../issues/new?template=new-claim.md) using the claim submission template.

An editor will review your claim, create the `claims/claim-NNN.md` file, and the AI will pick it up on the next scheduled run (daily at 06:00 UTC).

---

## Running the agent locally

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/kiriz/factwatch
cd factwatch
uv sync

# Set your API key
export GOOGLE_API_KEY=your-key-here

# Dry run — shows which claims would be checked without calling the API
uv run python agent/run_agent.py --dry-run

# Full run — checks all stale claims
uv run python agent/run_agent.py

# Re-check a specific claim (bypasses staleness filter)
CLAIM_ID_FILTER=claim-001 uv run python agent/run_agent.py
```

---

## Observatory

The `/observatory.html` page shows every agent run with full transparency:

- Which claims were checked and why
- What search queries the agent issued
- Step-by-step reasoning before reaching the verdict
- Previous verdict vs. new verdict (highlighted when changed)
- Link to raw JSON log for each run

---

## Verdict types

| Verdict | Meaning |
|---------|---------|
| **TRUE** | Accurate based on strong evidence |
| **FALSE** | Demonstrably incorrect |
| **MISLEADING** | Contains truth but framed to deceive or omits key context |
| **OUTDATED** | Was true but evidence no longer supports it |
| **DISPUTED** | Credible sources reach opposite conclusions |
| **UNVERIFIED** | Insufficient public evidence found |

Claims with confidence below 50% are flagged for human review and hidden from the main dashboard.

---

## Project structure

```
factwatch/
├── claims/        # Human-curated claim definitions (Markdown + frontmatter)
├── scores/        # AI-generated verdicts (JSON, auto-committed by agent)
├── logs/          # Full agent run traces (JSON, auto-committed by agent)
├── site/          # Static site served by GitHub Pages
├── agent/         # Python agent source code
└── .github/
    ├── workflows/ # agent.yml (daily) + pages.yml (deploy)
    └── ISSUE_TEMPLATE/
```

---

## Tech stack

- **Inference:** Google Gemini 2.0 Flash (free API tier)
- **Search:** Google Search grounding (built into Gemini)
- **Hosting:** GitHub Pages (free)
- **CI:** GitHub Actions (free for public repos)
- **Python tooling:** [uv](https://github.com/astral-sh/uv) + [ruff](https://github.com/astral-sh/ruff)
