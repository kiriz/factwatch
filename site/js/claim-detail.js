'use strict';

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function verdictClass(v) { return 'verdict-' + v.toLowerCase(); }
function verdictColour(v) {
  const map = { TRUE:'#22c55e', FALSE:'#ef4444', MISLEADING:'#f97316', UNVERIFIED:'#94a3b8', OUTDATED:'#eab308', DISPUTED:'#a855f7' };
  return map[v] || '#94a3b8';
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function renderClaim(claimId, score) {
  document.title = `${escHtml(score.title || claimId)} — FactWatch`;

  const flags = score.agent_flags || {};
  const history = score.verdict_history || [];
  const sources = score.sources || [];

  const content = document.getElementById('claim-content');
  content.innerHTML = `
    <div class="detail-header">
      <div class="page-header">
        <h1>${escHtml(score.title || claimId)}</h1>
        <div class="sub">ID: ${escHtml(claimId)} · Category: ${escHtml(score.category || '—')}</div>
      </div>
      <div class="detail-verdict-row">
        <span class="verdict-badge ${verdictClass(score.verdict)}">${score.verdict}</span>
        <div>
          <div class="confidence-label">Confidence: <strong>${score.confidence}%</strong></div>
          <div class="confidence-bar" style="width:200px;margin-top:4px"
               role="progressbar" aria-valuenow="${score.confidence}" aria-valuemin="0" aria-valuemax="100">
            <div class="confidence-fill" style="width:${score.confidence}%;background:${verdictColour(score.verdict)}"></div>
          </div>
        </div>
        <div style="font-size:0.85rem;color:var(--text-muted)">
          Last checked: ${formatDate(score.last_checked_at)}<br>
          ${score.last_changed_at ? `Verdict changed: ${formatDate(score.last_changed_at)}` : 'No verdict change recorded'}
        </div>
      </div>
    </div>

    <div class="section-card">
      <h2>Agent Reasoning</h2>
      <p style="line-height:1.7">${escHtml(score.reasoning || 'No reasoning recorded for this claim.')}</p>
    </div>

    <div class="section-card">
      <h2>Sources (${sources.length})</h2>
      ${sources.length ? `
        <div class="sources-list">
          ${sources.map(s => `
            <div class="source-item">
              <div class="relevance-dot relevance-${escHtml(s.relevance || 'low')}"></div>
              <div>
                <div class="source-title">
                  <a href="${escHtml(s.url)}" target="_blank" rel="noopener">${escHtml(s.title || s.domain)}</a>
                </div>
                <div class="source-domain">${escHtml(s.domain)} · ${s.supports_claim ? '✓ Supports' : '✗ Contradicts'} · Relevance: ${escHtml(s.relevance)}</div>
                ${s.excerpt ? `<div class="source-excerpt">"${escHtml(s.excerpt)}"</div>` : ''}
              </div>
            </div>
          `).join('')}
        </div>
      ` : '<p style="color:var(--text-muted)">No sources recorded.</p>'}
    </div>

    <div class="section-card">
      <h2>Agent Flags</h2>
      <div class="flags-grid">
        ${[
          ['conflicting_sources',  'Conflicting sources',   flags.conflicting_sources],
          ['outdated_evidence',    'Outdated evidence',     flags.outdated_evidence],
          ['requires_human_review','Requires human review', flags.requires_human_review],
          ['low_source_quality',   'Low source quality',    flags.low_source_quality],
        ].map(([, label, on]) => `
          <div class="flag-item ${on ? 'flag-on' : 'flag-off'}">
            <span>${on ? '⚠' : '✓'}</span>
            <span>${label}</span>
          </div>
        `).join('')}
      </div>
    </div>

    <div class="section-card">
      <h2>Verdict History (${history.length})</h2>
      ${history.length ? `
        <div class="verdict-timeline">
          ${[...history].reverse().map(h => `
            <div class="timeline-entry">
              <span class="timeline-date">${formatDate(h.recorded_at)}</span>
              <span class="verdict-badge ${verdictClass(h.verdict)}">${h.verdict}</span>
              <span class="timeline-conf">${h.confidence}% confidence</span>
              <span style="font-size:0.75rem;color:var(--text-muted);margin-left:auto">
                <a href="observatory.html#${escHtml(h.run_id)}" style="color:var(--text-muted)">Run log →</a>
              </span>
            </div>
          `).join('')}
        </div>
      ` : '<p style="color:var(--text-muted)">No history recorded yet.</p>'}
    </div>

    ${score.run_id ? `
      <div style="text-align:center;margin-top:var(--space-6)">
        <a href="observatory.html#${escHtml(score.run_id)}">View agent run that produced this verdict →</a>
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
    const res = await fetch(`scores/${encodeURIComponent(claimId)}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const score = await res.json();
    renderClaim(claimId, score);
  } catch (err) {
    content.innerHTML = `<div class="state-msg">
      <p>Could not load claim <strong>${escHtml(claimId)}</strong>.</p>
      <p style="margin-top:8px;font-size:0.875rem;color:var(--text-muted)">${escHtml(err.message)}</p>
      <p style="margin-top:16px"><a href="index.html">Back to all claims</a></p>
    </div>`;
  }
}

init();
