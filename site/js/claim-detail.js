'use strict';

const MANIFEST_URL = 'data/scores-manifest.json';

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function verdictClass(v) { return 'verdict-' + String(v || 'unverified').toLowerCase(); }
function verdictColour(v) {
  const map = { TRUE:'var(--color-viz-positive)', FALSE:'var(--color-viz-negative)',
    MISLEADING:'var(--color-viz-caution)', UNVERIFIED:'var(--color-viz-neutral)',
    OUTDATED:'var(--color-viz-stale)', DISPUTED:'var(--color-viz-special)' };
  return map[v] || 'var(--color-viz-neutral)';
}
function verdictGloss(v) {
  return ({ TRUE:'Accurate based on strong evidence', FALSE:'Demonstrably incorrect',
    MISLEADING:'Contains truth but framed to deceive', UNVERIFIED:'Insufficient public evidence',
    OUTDATED:'Was true; evidence no longer supports it', DISPUTED:'Credible sources disagree' })[v] || '';
}
function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
function relativeTime(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const d = Math.floor(ms / 86400000);
  if (d === 0) return 'today';
  if (d === 1) return 'yesterday';
  return `${d} days ago`;
}

function renderClaim(claimId, score, meta) {
  const title    = meta.title || score.title || claimId;
  const category = meta.category || score.category || '';
  const claimText = meta.claim_text || '';
  document.title = `${title} — FactWatch`;

  const flags   = score.agent_flags || {};
  const history = score.verdict_history || [];
  const sources = score.sources || [];
  const vColour = verdictColour(score.verdict);
  const activeFlags = [
    ['conflicting_sources',  'Conflicting sources'],
    ['outdated_evidence',    'Outdated evidence'],
    ['requires_human_review','Needs human review'],
    ['low_source_quality',   'Low source quality'],
  ].filter(([k]) => flags[k]);

  const content = document.getElementById('claim-content');
  content.innerHTML = `
    <article class="claim-hero" style="--hero-accent:${vColour}">
      <div class="claim-hero__meta">
        ${category ? `<span class="category-tag">${escHtml(category)}</span>` : ''}
        <span class="claim-hero__id">${escHtml(claimId)}</span>
      </div>
      <h1 class="claim-hero__title">${escHtml(title)}</h1>
      ${claimText ? `<blockquote class="claim-hero__statement">${escHtml(claimText)}</blockquote>` : ''}
      <div class="claim-hero__verdict">
        <span class="verdict-badge verdict-badge--lg ${verdictClass(score.verdict)}">${escHtml(score.verdict)}</span>
        <div class="claim-hero__verdict-detail">
          <div class="claim-hero__gloss">${escHtml(verdictGloss(score.verdict))}</div>
          <div class="confidence-bar" role="progressbar" aria-valuenow="${score.confidence}" aria-valuemin="0" aria-valuemax="100">
            <div class="confidence-fill" style="width:${score.confidence}%;background:${vColour}"></div>
          </div>
          <div class="claim-hero__conf-label">${score.confidence}% confidence · checked ${relativeTime(score.last_checked_at)}</div>
        </div>
      </div>
    </article>

    <section class="section-card">
      <h2>Why this verdict</h2>
      <p class="claim-reasoning">${escHtml(score.reasoning || 'No reasoning recorded for this claim.')}</p>
    </section>

    <section class="section-card">
      <h2>Sources <span class="count-pill">${sources.length}</span></h2>
      ${sources.length ? `
        <div class="source-grid">
          ${sources.map(s => `
            <a class="source-card" href="${escHtml(s.url)}" target="_blank" rel="noopener">
              <div class="source-card__head">
                <span class="relevance-dot relevance-${escHtml(s.relevance || 'low')}" title="Relevance: ${escHtml(s.relevance || 'low')}"></span>
                <span class="source-card__domain">${escHtml(s.domain || '')}</span>
                <span class="source-card__stance ${s.supports_claim ? 'stance-support' : 'stance-contra'}">${s.supports_claim ? 'supports' : 'contradicts'}</span>
              </div>
              <div class="source-card__title">${escHtml(s.title || s.domain || s.url)}</div>
              ${s.excerpt ? `<div class="source-card__excerpt">“${escHtml(s.excerpt)}”</div>` : ''}
            </a>
          `).join('')}
        </div>
      ` : '<p class="muted">No sources recorded.</p>'}
    </section>

    ${activeFlags.length ? `
      <section class="section-card section-card--flagged">
        <h2>⚠ Flags</h2>
        <div class="flag-chips">
          ${activeFlags.map(([, label]) => `<span class="flag-chip">${escHtml(label)}</span>`).join('')}
        </div>
      </section>
    ` : ''}

    <section class="section-card">
      <h2>Verdict history <span class="count-pill">${history.length}</span></h2>
      ${history.length ? `
        <div class="verdict-timeline">
          ${[...history].reverse().map(h => `
            <div class="timeline-entry">
              <span class="verdict-badge ${verdictClass(h.verdict)}">${escHtml(h.verdict)}</span>
              <span class="timeline-conf">${h.confidence}%</span>
              <span class="timeline-date">${formatDate(h.checked_at || h.recorded_at)}</span>
              ${h.run_id ? `<a class="timeline-run" href="observatory.html#${escHtml(h.run_id)}">run log →</a>` : ''}
            </div>
          `).join('')}
        </div>
      ` : '<p class="muted">First check — no prior verdicts yet.</p>'}
    </section>

    ${score.run_id ? `
      <div class="claim-footer-link">
        <a class="btn-link" href="observatory.html#${escHtml(score.run_id)}">🔭 See the full agent trace that produced this verdict →</a>
      </div>
    ` : ''}
  `;
}

async function init() {
  const params  = new URLSearchParams(window.location.search);
  const claimId = params.get('id');
  const content = document.getElementById('claim-content');

  if (!claimId) {
    content.innerHTML = '<div class="state-msg"><p>No claim ID provided. <a href="index.html">Go back</a></p></div>';
    return;
  }

  try {
    // Fetch score + manifest together; manifest carries title/category/claim_text.
    const [scoreRes, manifestRes] = await Promise.all([
      fetch(`scores/${encodeURIComponent(claimId)}.json`),
      fetch(MANIFEST_URL, { cache: 'no-cache' }).catch(() => null),
    ]);
    if (!scoreRes.ok) throw new Error(`HTTP ${scoreRes.status}`);
    const score = await scoreRes.json();

    let meta = {};
    if (manifestRes && manifestRes.ok) {
      const manifest = await manifestRes.json();
      meta = manifest.find(m => m.claim_id === claimId) || {};
    }
    renderClaim(claimId, score, meta);
  } catch (err) {
    content.innerHTML = `<div class="state-msg">
      <p>Could not load claim <strong>${escHtml(claimId)}</strong>.</p>
      <p style="margin-top:8px;font-size:0.875rem;color:var(--text-muted)">${escHtml(err.message)}</p>
      <p style="margin-top:16px"><a href="index.html">Back to all claims</a></p>
    </div>`;
  }
}

init();
