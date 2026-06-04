'use strict';

const INDEX_URL = 'logs/index.json';
const LOG_BASE  = 'logs/';

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
function relativeTime(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
function nextScheduledRun() {
  const next = new Date();
  next.setUTCHours(6, 0, 0, 0);
  if (next <= new Date()) next.setUTCDate(next.getUTCDate() + 1);
  return next;
}
function verdictClass(v) { return 'verdict-' + (v || 'unverified').toLowerCase(); }
function domainOf(url) {
  try { const h = new URL(url).hostname; return h.startsWith('www.') ? h.slice(4) : h; }
  catch { return url; }
}
function fmtTokens(n) {
  if (n == null) return null;
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

/* ── Pipeline step renderer ─────────────────────────────── */

function pipelineStep(num, label, meta, bodyHtml, accent) {
  return `
    <div class="ps-step">
      <div class="ps-left">
        <div class="ps-num" style="${accent ? `background:${accent};color:#fff` : ''}">${num}</div>
        <div class="ps-line"></div>
      </div>
      <div class="ps-right">
        <div class="ps-label">
          <span class="ps-label-title">${label}</span>
          ${meta ? `<span class="ps-label-meta">${meta}</span>` : ''}
        </div>
        <div class="ps-body">${bodyHtml}</div>
      </div>
    </div>`;
}

function renderClaimTrace(trace) {
  const steps = [];

  /* ① Claim Input */
  const claimTitle = trace.claim_title || trace.claim_id || '—';
  const claimText  = trace.claim_text  || '';
  const category   = trace.category    || '';
  steps.push(pipelineStep('①', 'Claim Input', category || null, `
    <div class="ps-claim-box">
      <div class="ps-claim-id">${escHtml(claimTitle)}</div>
      ${claimText ? `<div class="ps-claim-text">"${escHtml(claimText)}"</div>` : ''}
    </div>
  `, '#3b82f6'));

  /* ② Search */
  const queries  = trace.search_queries  || [];
  const results  = trace.search_results  || [];
  const hasSearch = queries.length || results.length;
  if (hasSearch) {
    const qChips = queries.map(q =>
      `<span class="ps-query-chip">${escHtml(q)}</span>`
    ).join('');

    const rCards = results.map(r => `
      <div class="ps-result-card">
        <div class="ps-result-title">
          ${r.url
            ? `<a href="${escHtml(r.url)}" target="_blank" rel="noopener">${escHtml(r.title || r.url)}</a>`
            : escHtml(r.title || '—')}
        </div>
        <div class="ps-result-domain">${escHtml(domainOf(r.url || ''))}</div>
        ${r.snippet ? `<div class="ps-result-snippet">${escHtml(r.snippet.slice(0, 200))}</div>` : ''}
      </div>`).join('');

    steps.push(pipelineStep(
      '②', 'Web Search',
      `${queries.length} quer${queries.length === 1 ? 'y' : 'ies'} · ${results.length} results`,
      `<div class="ps-query-list">${qChips}</div>
       ${rCards ? `<div class="ps-result-grid">${rCards}</div>` : ''}`,
      '#8b5cf6'
    ));
  }

  /* ③ LLM Request */
  if (trace.prompt) {
    const model   = trace.model_name    || 'model';
    const version = trace.model_version || '';
    const inTok   = fmtTokens(trace.prompt_tokens);
    const outTok  = fmtTokens(trace.response_tokens);
    const totTok  = fmtTokens(trace.total_tokens);

    const tokenRow = (inTok || outTok || totTok) ? `
      <div class="ps-token-row">
        ${inTok  ? `<span class="ps-token-chip"><span class="ps-tok-label">input</span>${inTok}</span>`  : ''}
        ${outTok ? `<span class="ps-token-chip"><span class="ps-tok-label">output</span>${outTok}</span>` : ''}
        ${totTok ? `<span class="ps-token-chip ps-tok-total"><span class="ps-tok-label">total</span>${totTok}</span>` : ''}
      </div>` : '';

    const modelBadge = `<span class="ps-model-badge">${escHtml(model)}${version ? ` · <span style="opacity:.7">${escHtml(version)}</span>` : ''}</span>`;

    steps.push(pipelineStep(
      '③', 'LLM Request',
      null,
      `<div class="ps-req-header">${modelBadge}${tokenRow}</div>
       <pre class="ps-code">${escHtml(trace.prompt)}</pre>`,
      '#f59e0b'
    ));
  }

  /* ④ LLM Response */
  if (trace.raw_response) {
    let pretty = trace.raw_response;
    try { pretty = JSON.stringify(JSON.parse(trace.raw_response), null, 2); } catch { /* keep raw */ }
    steps.push(pipelineStep(
      '④', 'LLM Response', null,
      `<pre class="ps-code ps-response">${escHtml(pretty)}</pre>`,
      '#10b981'
    ));
  }

  /* ⑤ Verdict */
  const verdict    = trace.new_verdict || trace.verdict || '—';
  const confidence = trace.confidence  != null ? `${trace.confidence}%` : '—';
  const prevVerdict = trace.previous_verdict;
  const changed    = trace.verdict_changed;
  const reasoning  = (trace.reasoning_steps || []).join(' ');
  const flags      = trace.agent_flags || {};
  const activeFlags = Object.entries({
    conflicting_sources:   'Conflicting sources',
    outdated_evidence:     'Outdated evidence',
    requires_human_review: 'Needs human review',
    low_source_quality:    'Low source quality',
  }).filter(([k]) => flags[k]);

  const verdictFlow = changed && prevVerdict
    ? `<span class="verdict-badge ${verdictClass(prevVerdict)}">${escHtml(prevVerdict)}</span>
       <span class="ps-arrow">→</span>
       <span class="verdict-badge ${verdictClass(verdict)}">${escHtml(verdict)}</span>`
    : `<span class="verdict-badge ${verdictClass(verdict)}">${escHtml(verdict)}</span>`;

  const flagsHtml = activeFlags.length
    ? `<div class="ps-flags">${activeFlags.map(([, label]) =>
        `<span class="ps-flag-chip">${escHtml(label)}</span>`).join('')}</div>` : '';

  steps.push(pipelineStep(
    '⑤', 'Verdict',
    confidence,
    `<div class="ps-verdict-row">${verdictFlow}</div>
     ${reasoning ? `<p class="ps-reasoning">${escHtml(reasoning)}</p>` : ''}
     ${flagsHtml}`,
    '#ef4444'
  ));

  /* ── Assemble trace card ── */
  const inTok  = fmtTokens(trace.prompt_tokens);
  const totTok = fmtTokens(trace.total_tokens);
  const model  = trace.model_name;
  const displayTitle = trace.claim_title || trace.claim_id || '—';

  return `
    <div class="trace-card" data-claim="${escHtml(trace.claim_id || '')}">
      <div class="trace-card-header" role="button" tabindex="0" aria-expanded="true">
        <div class="trace-card-title">
          <span class="trace-card-id">${escHtml(displayTitle)}</span>
          ${changed
            ? `<span class="verdict-badge ${verdictClass(trace.previous_verdict)}">${escHtml(trace.previous_verdict || '—')}</span>
               <span class="ps-arrow">→</span>
               <span class="verdict-badge ${verdictClass(verdict)}">${escHtml(verdict)}</span>`
            : `<span class="verdict-badge ${verdictClass(verdict)}">${escHtml(verdict)}</span>`}
          <span style="font-size:.8rem;color:var(--text-muted)">${escHtml(confidence)}</span>
        </div>
        <div class="trace-card-meta">
          ${model ? `<span class="ps-model-badge">${escHtml(model)}</span>` : ''}
          ${totTok ? `<span class="ps-token-chip ps-tok-total"><span class="ps-tok-label">tokens</span>${totTok}</span>` : ''}
          ${inTok  ? `<span class="ps-token-chip"><span class="ps-tok-label">in</span>${inTok}</span>`  : ''}
          <span class="trace-toggle-icon">▾</span>
        </div>
      </div>
      <div class="trace-card-body">
        <div class="ps-pipeline">${steps.join('')}</div>
      </div>
    </div>`;
}

/* ── Run detail ───────────────────────────────────────────── */

function renderRunDetail(run) {
  const traces  = run.claim_traces || [];
  const container = document.getElementById('run-detail-container');

  container.innerHTML = `
    <div class="run-detail" id="${escHtml(run.run_id)}">
      <div class="run-detail-top">
        <div>
          <h2 class="run-detail-title">Run <code>${escHtml(run.run_id)}</code></h2>
          <div style="font-size:.8rem;color:var(--text-muted);margin-top:2px">${formatDate(run.started_at)}</div>
        </div>
        <div style="display:flex;gap:var(--space-3);align-items:center">
          ${traces.length > 1
            ? `<button class="obs-btn" id="btn-expand-all">Expand all</button>
               <button class="obs-btn" id="btn-collapse-all">Collapse all</button>`
            : ''}
          <a href="${LOG_BASE}${encodeURIComponent(run.run_id)}.json" target="_blank" class="obs-btn">Raw JSON ↗</a>
        </div>
      </div>

      <div class="run-stats">
        <div class="stat-box"><div class="stat-label">Duration</div><div class="stat-value">${run.duration_seconds != null ? `${run.duration_seconds}s` : '—'}</div></div>
        <div class="stat-box"><div class="stat-label">Claims</div><div class="stat-value">${run.claims_processed ?? traces.length}</div></div>
        <div class="stat-box"><div class="stat-label">Changed</div><div class="stat-value" style="color:${(run.verdicts_changed || 0) > 0 ? 'var(--verdict-misleading)' : 'inherit'}">${run.verdicts_changed ?? 0}</div></div>
        <div class="stat-box"><div class="stat-label">Errors</div><div class="stat-value" style="color:${(run.errors || 0) > 0 ? 'var(--verdict-false)' : 'inherit'}">${run.errors ?? 0}</div></div>
      </div>

      ${traces.length
        ? `<div class="trace-list" id="trace-list">${traces.map(renderClaimTrace).join('')}</div>`
        : '<p style="color:var(--text-muted)">No claim traces in this run log.</p>'}
    </div>`;

  /* toggle logic */
  container.querySelectorAll('.trace-card-header').forEach(hdr => {
    hdr.addEventListener('click', () => toggleCard(hdr.closest('.trace-card')));
    hdr.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') toggleCard(hdr.closest('.trace-card')); });
  });

  const btnExpand   = container.querySelector('#btn-expand-all');
  const btnCollapse = container.querySelector('#btn-collapse-all');
  if (btnExpand)   btnExpand.addEventListener('click',   () => setAll(true));
  if (btnCollapse) btnCollapse.addEventListener('click', () => setAll(false));

  container.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function toggleCard(card) {
  const open = card.classList.toggle('trace-open');
  card.querySelector('.trace-card-header').setAttribute('aria-expanded', open);
}
function setAll(open) {
  document.querySelectorAll('.trace-card').forEach(c => {
    c.classList.toggle('trace-open', open);
    c.querySelector('.trace-card-header').setAttribute('aria-expanded', open);
  });
}

/* ── Run list ─────────────────────────────────────────────── */

function drawChart(runs) {
  const canvas = document.getElementById('changes-chart');
  if (!canvas || !canvas.getContext) return;
  const ctx    = canvas.getContext('2d');
  const recent = runs.slice(0, 20).reverse();
  const maxVal = Math.max(1, ...recent.map(r => r.verdicts_changed ?? 0));
  const barW   = Math.floor(canvas.width / (recent.length + 1));
  const padV   = 8;
  const h      = canvas.height;
  ctx.clearRect(0, 0, canvas.width, h);
  recent.forEach((run, i) => {
    const val  = run.verdicts_changed ?? 0;
    const barH = Math.round(((h - padV * 2) * val) / maxVal);
    const x    = i * barW + Math.floor(barW * 0.1);
    const w    = Math.floor(barW * 0.8);
    ctx.fillStyle = val > 0 ? '#f97316' : '#334155';
    ctx.beginPath();
    ctx.roundRect(x, h - padV - Math.max(barH, 2), w, Math.max(barH, 2), 2);
    ctx.fill();
  });
  document.getElementById('chart-wrap').style.display = '';
}

function renderRunList(runs) {
  const container = document.getElementById('runs-container');
  if (!runs.length) {
    container.innerHTML = '<div class="state-msg"><p>No agent runs recorded yet.</p></div>';
    return;
  }

  const list = document.createElement('div');
  list.className = 'runs-list';

  runs.slice(0, 10).forEach((run, i) => {
    const row = document.createElement('div');
    row.className = 'run-row' + (i === 0 ? ' selected' : '');
    row.setAttribute('role', 'button');
    row.tabIndex = 0;
    row.dataset.runId = run.run_id;
    row.innerHTML = `
      <span class="run-id">${escHtml(run.run_id)}</span>
      <span title="${escHtml(run.started_at)}">${formatDate(run.started_at)}</span>
      <span class="run-badge">${run.claims_processed ?? '—'} claims</span>
      ${(run.verdicts_changed ?? 0) > 0
        ? `<span class="run-badge changed">${run.verdicts_changed} changed</span>`
        : '<span class="run-badge">no changes</span>'}
      ${(run.errors ?? 0) > 0 ? `<span class="run-badge" style="color:var(--verdict-false)">${run.errors} errors</span>` : ''}`;

    const select = () => {
      list.querySelectorAll('.run-row').forEach(r => r.classList.remove('selected'));
      row.classList.add('selected');
      loadRunDetail(run.run_id);
    };
    row.addEventListener('click', select);
    row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') select(); });
    list.appendChild(row);
  });

  container.replaceChildren(list);
  if (runs.length) loadRunDetail(runs[0].run_id);

  const hash = window.location.hash.slice(1);
  if (hash) {
    const target = list.querySelector(`[data-run-id="${CSS.escape(hash)}"]`);
    if (target) target.click();
  }
}

async function loadRunDetail(runId) {
  const container = document.getElementById('run-detail-container');
  container.innerHTML = '<div class="state-msg"><div class="spinner"></div><p style="margin-top:12px">Loading trace…</p></div>';
  try {
    const res = await fetch(`${LOG_BASE}${encodeURIComponent(runId)}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const run = await res.json();
    renderRunDetail(run);
    /* auto-expand first claim */
    const first = document.querySelector('.trace-card');
    if (first && !first.classList.contains('trace-open')) toggleCard(first);
  } catch (err) {
    container.innerHTML = `<div class="state-msg"><p>Could not load run log: ${escHtml(err.message)}</p></div>`;
  }
}

/* ── Init ─────────────────────────────────────────────────── */

async function init() {
  document.getElementById('status-next-run').textContent = formatDate(nextScheduledRun().toISOString());

  try {
    const res = await fetch(INDEX_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { runs } = await res.json();

    document.getElementById('status-total-runs').textContent = runs.length;
    if (runs.length) {
      const last = runs[0];
      document.getElementById('status-last-run').textContent =
        `${relativeTime(last.started_at)} (${formatDate(last.started_at)})`;
      const today = new Date().toISOString().slice(0, 10);
      if (!runs.some(r => r.started_at?.startsWith(today))) {
        document.getElementById('status-last-run').textContent += ' · Run in progress…';
      }
    } else {
      document.getElementById('status-last-run').textContent = 'No runs yet';
    }

    drawChart(runs);
    renderRunList(runs);
  } catch (err) {
    document.getElementById('runs-container').innerHTML =
      `<div class="state-msg"><p>Could not load run history: ${escHtml(err.message)}</p></div>`;
  }
}

init();
