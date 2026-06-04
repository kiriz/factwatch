'use strict';

const MANIFEST_URL  = 'data/scores-manifest.json';
const SUMMARY_URL   = 'data/summary.json';
const COUNT_BASE    = 'https://api.counterapi.dev/v1/factwatch-claims';
const LS_VIEWS_KEY  = 'fw_views'; // localStorage: {claimId: count}

/* ── View counter (CountAPI.dev — free, no auth) ──────────── */

function getLocalViews() {
  try { return JSON.parse(localStorage.getItem(LS_VIEWS_KEY) || '{}'); }
  catch { return {}; }
}
function bumpLocalView(claimId) {
  const v = getLocalViews();
  v[claimId] = (v[claimId] || 0) + 1;
  try { localStorage.setItem(LS_VIEWS_KEY, JSON.stringify(v)); } catch { /* quota */ }
}

/* Fetch live view counts from CountAPI.dev for all claim IDs. Returns {} on error. */
async function fetchViewCounts(claimIds) {
  const results = await Promise.allSettled(
    claimIds.map(id =>
      fetch(`${COUNT_BASE}/${encodeURIComponent(id)}/get`)
        .then(r => r.ok ? r.json() : null)
        .then(d => ({ id, count: d?.count ?? 0 }))
    )
  );
  const map = {};
  results.forEach(r => { if (r.status === 'fulfilled' && r.value) map[r.value.id] = r.value.count; });
  return map;
}

/* Increment view counter on CountAPI.dev (fire-and-forget). */
function trackView(claimId) {
  bumpLocalView(claimId);
  fetch(`${COUNT_BASE}/${encodeURIComponent(claimId)}/up`).catch(() => {});
}

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

function renderCard({ score, claimId, title: manifestTitle, category: manifestCategory }) {
  const recentlyChanged = wasChangedRecently(score, 7);
  const needsReview     = score.agent_flags?.requires_human_review;
  const displayTitle    = manifestTitle || score.title || claimId;
  const category        = score.category || manifestCategory || '';
  const trending        = score.trending_score ?? null;
  const views           = viewCounts[claimId] ?? getLocalViews()[claimId] ?? 0;

  const card = document.createElement('article');
  card.className = 'claim-card';
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', `View details for ${displayTitle}`);
  card.tabIndex = 0;
  card.dataset.category = category;
  card.dataset.verdict   = score.verdict;
  card.dataset.changedAt = score.last_changed_at || '';

  /* trending badge: fire (hot) if score ≥ 7, spark if 4-6 */
  const trendBadge = trending === null ? '' :
    trending >= 7 ? '<span class="trend-badge trend-hot"  title="Trending this week">🔥</span>' :
    trending >= 4 ? '<span class="trend-badge trend-warm" title="Active this week">⚡</span>' : '';

  const viewBadge = views > 0
    ? `<span class="view-count" title="${views} views">${views >= 1000 ? `${(views/1000).toFixed(1)}k` : views} views</span>`
    : '';

  /* left border colour = verdict colour */
  card.style.borderLeftColor = verdictColour(score.verdict);

  card.innerHTML = `
    <div class="claim-card-top">
      <div class="claim-title">${escHtml(displayTitle)}</div>
      <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
        <span class="verdict-badge ${verdictClass(score.verdict)}">${score.verdict}</span>
        <span class="confidence-num">${score.confidence}%</span>
        <div class="claim-icons">
          ${trendBadge}
          ${recentlyChanged ? '<span title="Verdict changed in last 7 days">⚠</span>' : ''}
          ${needsReview     ? '<span title="Requires human review">🔍</span>'         : ''}
        </div>
      </div>
    </div>
    <div class="claim-card-bottom">
      <span class="category-tag">${escHtml(category || 'general')}</span>
      <span>·</span>
      ${viewBadge ? viewBadge + '<span>·</span>' : ''}
      <span title="${escHtml(score.last_checked_at || '')}">checked ${relativeTime(score.last_checked_at)}</span>
    </div>
  `;

  card.addEventListener('click', () => {
    trackView(claimId);
    window.location.href = `claim.html?id=${encodeURIComponent(claimId)}`;
  });
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

let allScores  = [];
let viewCounts = {}; // claimId → live count from CountAPI
let currentSort = 'trending'; // trending | views | confidence | recent | az

function popularityScore(item) {
  const trending = item.score.trending_score ?? 0;          // 0-10 agent signal
  const views    = viewCounts[item.claimId] ?? getLocalViews()[item.claimId] ?? 0;
  const viewNorm = Math.min(views / 20, 10);                // normalise to 0-10
  return trending * 0.6 + viewNorm * 0.4;
}

function applySortAndFilter(scores) {
  const cat     = document.getElementById('filter-category').value;
  const verdict = document.getElementById('filter-verdict').value;
  const recent  = parseInt(document.getElementById('filter-recent').value, 10) || 0;
  const sort    = currentSort;

  let filtered = scores.filter(({ score, category }) => {
    const effectiveCategory = score.category || category || '';
    if (cat     && effectiveCategory !== cat)    return false;
    if (verdict && score.verdict  !== verdict)   return false;
    if (recent  && !wasChangedRecently(score, recent)) return false;
    return true;
  });

  filtered.sort((a, b) => {
    switch (sort) {
      case 'trending':    return popularityScore(b) - popularityScore(a);
      case 'views':       return (viewCounts[b.claimId] ?? 0) - (viewCounts[a.claimId] ?? 0);
      case 'confidence':  return (b.score.confidence ?? 0) - (a.score.confidence ?? 0);
      case 'recent':      return new Date(b.score.last_changed_at || 0) - new Date(a.score.last_changed_at || 0);
      case 'az':          return (a.title || '').localeCompare(b.title || '');
      default:            return 0;
    }
  });

  return filtered;
}

function renderGrid() {
  const container = document.getElementById('claims-container');
  const filtered  = applySortAndFilter(allScores);

  if (!filtered.length) {
    container.innerHTML = '<div class="state-msg"><p>No claims match the current filters.</p></div>';
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'claims-grid';
  filtered.forEach(item => grid.appendChild(renderCard(item)));
  container.replaceChildren(grid);
}

async function loadScores() {
  const container = document.getElementById('claims-container');

  let manifest;
  try {
    const res = await fetch(MANIFEST_URL, { cache: 'no-cache' });
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

  // Fetch live view counts in background then re-render
  fetchViewCounts(allScores.map(s => s.claimId)).then(counts => {
    viewCounts = counts;
    renderGrid();
  });

  renderGrid(); // initial render with local counts
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

document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    currentSort = btn.dataset.sort;
    document.querySelectorAll('.sort-btn').forEach(b => b.classList.toggle('sort-active', b === btn));
    renderGrid();
  });
});

loadSummary();
loadScores();
