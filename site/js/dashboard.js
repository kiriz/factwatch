'use strict';

const MANIFEST_URL = 'data/scores-manifest.json';
const SUMMARY_URL  = 'data/summary.json';

function verdictClass(v) {
  return 'verdict-' + v.toLowerCase();
}

function verdictColour(v) {
  const map = {
    TRUE:        '#22c55e',
    FALSE:       '#ef4444',
    MISLEADING:  '#f97316',
    UNVERIFIED:  '#94a3b8',
    OUTDATED:    '#eab308',
    DISPUTED:    '#a855f7',
  };
  return map[v] || '#94a3b8';
}

function relativeTime(isoStr) {
  const ms = Date.now() - new Date(isoStr).getTime();
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function wasChangedRecently(score, days) {
  if (!score.last_changed_at) return false;
  const ms = Date.now() - new Date(score.last_changed_at).getTime();
  return ms < days * 86400000;
}

function renderCard(score, claimId, manifestTitle) {
  const recentlyChanged = wasChangedRecently(score, 7);
  const needsReview = score.agent_flags?.requires_human_review;
  const displayTitle = manifestTitle || score.title || claimId;
  const category = score.category || manifestTitle?.category || '';

  const card = document.createElement('article');
  card.className = 'claim-card';
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', `View details for ${displayTitle}`);
  card.tabIndex = 0;
  card.dataset.category = category;
  card.dataset.verdict   = score.verdict;
  card.dataset.changedAt = score.last_changed_at || '';

  card.innerHTML = `
    <div class="claim-card-header">
      <div class="claim-title">${escHtml(displayTitle)}</div>
      <div class="claim-icons">
        ${recentlyChanged ? '<span title="Verdict changed in last 7 days">⚠</span>' : ''}
        ${needsReview     ? '<span title="Requires human review">🔍</span>' : ''}
      </div>
    </div>
    <div>
      <span class="verdict-badge ${verdictClass(score.verdict)}">${score.verdict}</span>
    </div>
    <div>
      <div class="confidence-bar" role="progressbar" aria-valuenow="${score.confidence}" aria-valuemin="0" aria-valuemax="100">
        <div class="confidence-fill" style="width:${score.confidence}%;background:${verdictColour(score.verdict)}"></div>
      </div>
    </div>
    <div class="claim-meta">
      <span class="category-tag">${escHtml(score.category || 'general')}</span>
      <span title="${score.last_checked_at}">Checked ${relativeTime(score.last_checked_at)}</span>
    </div>
  `;

  card.addEventListener('click', () => { window.location.href = `claim.html?id=${encodeURIComponent(claimId)}`; });
  card.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') card.click(); });

  return card;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function applyFilters(scores) {
  const cat     = document.getElementById('filter-category').value;
  const verdict = document.getElementById('filter-verdict').value;
  const recent  = parseInt(document.getElementById('filter-recent').value, 10) || 0;

  return scores.filter(({ score, category }) => {
    const effectiveCategory = score.category || category || '';
    if (cat     && effectiveCategory !== cat)    return false;
    if (verdict && score.verdict  !== verdict)   return false;
    if (recent  && !wasChangedRecently(score, recent)) return false;
    return true;
  });
}

let allScores = [];

function renderGrid() {
  const container = document.getElementById('claims-container');
  const filtered  = applyFilters(allScores);

  if (!filtered.length) {
    container.innerHTML = '<div class="state-msg"><p>No claims match the current filters.</p></div>';
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'claims-grid';
  filtered.forEach(({ claimId, score, title }) => grid.appendChild(renderCard(score, claimId, title)));
  container.replaceChildren(grid);
}

async function loadScores() {
  const container = document.getElementById('claims-container');

  let manifest;
  try {
    const res = await fetch(MANIFEST_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    manifest = await res.json();
  } catch (err) {
    container.innerHTML = `<div class="state-msg"><p>Could not load claims manifest. ${escHtml(err.message)}</p></div>`;
    return;
  }

  if (!manifest.length) {
    container.innerHTML = '<div class="state-msg"><p>No claims have been checked yet. The agent will run soon.</p></div>';
    return;
  }

  const results = await Promise.allSettled(
    manifest.map(({ claim_id, title, category, score_file }) =>
      fetch(score_file)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(score => ({ claimId: claim_id, title: title || claim_id, category, score }))
    )
  );

  // Filter out confidence < 50 (hidden from dashboard per policy)
  allScores = results
    .filter(r => r.status === 'fulfilled')
    .map(r => r.value)
    .filter(({ score }) => (score.confidence ?? 100) >= 50);

  if (!allScores.length) {
    container.innerHTML = '<div class="state-msg"><p>No verified claims to display yet.</p></div>';
    return;
  }

  renderGrid();
}

async function loadSummary() {
  try {
    const res = await fetch(SUMMARY_URL);
    if (!res.ok) return;
    const summary = await res.json();
    const el = document.getElementById('last-updated-text');
    if (summary.last_run_at) {
      el.textContent = `Last updated by AI: ${relativeTime(summary.last_run_at)} · ${summary.total_claims} claims`;
    }
  } catch (_) { /* non-fatal */ }
}

document.getElementById('filter-category').addEventListener('change', renderGrid);
document.getElementById('filter-verdict').addEventListener('change', renderGrid);
document.getElementById('filter-recent').addEventListener('change', renderGrid);

loadSummary();
loadScores();
