'use strict';

const INDEX_URL = 'logs/index.json';
const LOG_BASE  = 'logs/';

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
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

function nextScheduledRun() {
  const now = new Date();
  const next = new Date(now);
  next.setUTCHours(6, 0, 0, 0);
  if (next <= now) next.setUTCDate(next.getUTCDate() + 1);
  return next;
}

function verdictClass(v) { return 'verdict-' + (v || 'unverified').toLowerCase(); }

const FLAG_LABELS = {
  conflicting_sources: 'Conflicting sources',
  outdated_evidence:   'Outdated evidence',
  requires_human_review: 'Needs human review',
  low_source_quality:  'Low source quality',
};

function domainOf(url) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.startsWith('www.') ? host.slice(4) : host;
  } catch {
    return '';
  }
}

function formatTokens(n) {
  if (n == null) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k tokens`;
  return `${n} tokens`;
}

function subSection(title, bodyHtml, open) {
  return `
    <details class="sub-details"${open ? ' open' : ''}>
      <summary>${escHtml(title)}</summary>
      <div class="sub-body">${bodyHtml}</div>
    </details>`;
}

function renderSearchSection(trace) {
  const queries = trace.search_queries || [];
  const results = trace.search_results || [];
  if (!queries.length && !results.length) return '';

  const chips = queries.length
    ? `<div class="queries-list">${queries.map(q => `<span class="query-chip">${escHtml(q)}</span>`).join('')}</div>`
    : '';

  const cards = results.map(r => {
    const url = r.url || '';
    const dom = domainOf(url);
    return `
      <div class="search-result">
        <div class="sr-title">${url ? `<a href="${escHtml(url)}" target="_blank" rel="noopener" style="color:inherit">${escHtml(r.title || url)}</a>` : escHtml(r.title || '—')}</div>
        ${dom ? `<div class="sr-domain">${escHtml(dom)}</div>` : ''}
        ${r.snippet ? `<div class="sr-snippet">${escHtml(r.snippet)}</div>` : ''}
      </div>`;
  }).join('');

  const body = `${chips}${cards ? `<div style="margin-top:var(--space-3)">${cards}</div>` : ''}`;
  return subSection(`Search — ${queries.length} queries · ${results.length} results found`, body, false);
}

function renderRequestSection(trace) {
  if (!trace.prompt) return '';
  const chars = trace.prompt.length;
  const kchars = chars >= 1000 ? `${(chars / 1000).toFixed(1)}k chars` : `${chars} chars`;
  const model = trace.model_name || 'model';
  const ver = trace.model_version ? ` (${escHtml(trace.model_version)})` : '';

  const tokenRow = `
    <div class="token-row">
      <span class="token-chip"><span class="label">input</span>${trace.prompt_tokens ?? '—'}</span>
      <span class="token-chip"><span class="label">output</span>${trace.response_tokens ?? '—'}</span>
      <span class="token-chip"><span class="label">total</span>${trace.total_tokens ?? '—'}</span>
    </div>`;

  const body = `${tokenRow}<pre class="code-pre">${escHtml(trace.prompt)}</pre>`;
  return subSection(`Prompt — ${kchars} · ${escHtml(model)}${ver}`, body, false);
}

function renderResponseSection(trace) {
  if (!trace.raw_response) return '';
  const body = `<pre class="code-pre">${escHtml(trace.raw_response)}</pre>`;
  return subSection('Raw Response', body, false);
}

function renderReasoningSection(trace) {
  const steps = trace.reasoning_steps || [];
  const flags = trace.agent_flags || {};
  const activeFlags = Object.keys(FLAG_LABELS).filter(k => flags[k]);

  const verdictLine = trace.verdict_changed
    ? `<div class="verdict-change" style="margin-bottom:var(--space-3)">
         <span class="verdict-badge ${verdictClass(trace.previous_verdict)}">${escHtml(trace.previous_verdict || '—')}</span>
         <span class="arrow-right">→</span>
         <span class="verdict-badge ${verdictClass(trace.new_verdict)}">${escHtml(trace.new_verdict || '—')}</span>
       </div>`
    : `<div style="margin-bottom:var(--space-3)"><span class="verdict-badge ${verdictClass(trace.new_verdict)}">${escHtml(trace.new_verdict || '—')}</span></div>`;

  const stepsHtml = steps.length
    ? `<div class="steps-list">${steps.map(s => `<div class="step">${escHtml(s)}</div>`).join('')}</div>`
    : '<p style="color:var(--text-muted);font-size:0.85rem">No reasoning recorded.</p>';

  const flagsHtml = activeFlags.length
    ? `<div class="queries-list" style="margin-top:var(--space-3)">${activeFlags.map(k => `<span class="query-chip" style="color:var(--verdict-misleading)">${escHtml(FLAG_LABELS[k])}</span>`).join('')}</div>`
    : '';

  return subSection('Reasoning & Verdict', `${verdictLine}${stepsHtml}${flagsHtml}`, true);
}

function renderTraceItem(trace) {
  const changed = trace.verdict_changed;
  const tokens = formatTokens(trace.total_tokens);
  const model = trace.model_name;

  const verdictBadge = changed
    ? `<span class="verdict-change">
         <span class="verdict-badge ${verdictClass(trace.previous_verdict)}">${escHtml(trace.previous_verdict || '—')}</span>
         <span class="arrow-right">→</span>
         <span class="verdict-badge ${verdictClass(trace.new_verdict)}">${escHtml(trace.new_verdict || '—')}</span>
       </span>`
    : `<span class="verdict-badge ${verdictClass(trace.new_verdict)}">${escHtml(trace.new_verdict || '—')}</span>`;

  const sections = [
    renderSearchSection(trace),
    renderRequestSection(trace),
    renderResponseSection(trace),
    renderReasoningSection(trace),
  ].join('');

  return `
    <details class="trace-item" ${changed ? 'open' : ''}>
      <summary class="trace-summary">
        <span class="arrow">▶</span>
        <strong style="flex:1">${escHtml(trace.claim_id)}</strong>
        ${verdictBadge}
        <span style="font-size:0.75rem;color:var(--text-muted)">${trace.confidence ?? '—'}%</span>
        ${model ? `<span class="model-chip">${escHtml(model)}</span>` : ''}
        ${tokens ? `<span style="font-size:0.72rem;color:var(--text-muted)">${escHtml(tokens)}</span>` : ''}
        <span style="font-size:0.75rem;color:var(--text-muted)">${trace.processing_time_seconds != null ? `${trace.processing_time_seconds}s` : ''}</span>
      </summary>
      <div class="trace-body">${sections}</div>
    </details>`;
}

function renderRunDetail(run) {
  const traces = run.claim_traces || [];
  const container = document.getElementById('run-detail-container');

  container.innerHTML = `
    <div class="run-detail" id="${escHtml(run.run_id)}">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-4)">
        <h2 style="font-size:1rem;font-weight:600">Run: <code>${escHtml(run.run_id)}</code></h2>
        <a href="${LOG_BASE}${encodeURIComponent(run.run_id)}.json" target="_blank"
           style="font-size:0.8rem;color:var(--text-muted)">View raw JSON →</a>
      </div>

      <div class="run-stats">
        <div class="stat-box">
          <div class="stat-label">Duration</div>
          <div class="stat-value">${run.duration_seconds != null ? `${run.duration_seconds}s` : '—'}</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Claims</div>
          <div class="stat-value">${run.claims_processed ?? '—'}</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Changed</div>
          <div class="stat-value" style="color:${run.verdicts_changed ? 'var(--verdict-misleading)' : 'inherit'}">${run.verdicts_changed ?? 0}</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Errors</div>
          <div class="stat-value" style="color:${run.errors ? 'var(--verdict-false)' : 'inherit'}">${run.errors ?? 0}</div>
        </div>
      </div>

      ${run.summary ? `<p style="color:var(--text-muted);font-size:0.875rem;margin-bottom:var(--space-4)">${escHtml(run.summary)}</p>` : ''}

      <div class="trace-accordion">
        ${traces.map(renderTraceItem).join('')}
        ${!traces.length ? '<p style="color:var(--text-muted);font-size:0.875rem">No claim traces in this run log.</p>' : ''}
      </div>
    </div>
  `;

  container.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function drawChart(runs) {
  const canvas = document.getElementById('changes-chart');
  if (!canvas.getContext) return;
  const ctx = canvas.getContext('2d');

  const recent = runs.slice(0, 20).reverse();
  const maxVal = Math.max(1, ...recent.map(r => r.verdicts_changed ?? 0));
  const barW   = Math.floor(canvas.width / (recent.length + 1));
  const padV   = 8;
  const h      = canvas.height;

  ctx.clearRect(0, 0, canvas.width, h);

  recent.forEach((run, i) => {
    const val     = run.verdicts_changed ?? 0;
    const barH    = Math.round(((h - padV * 2) * val) / maxVal);
    const x       = i * barW + Math.floor(barW * 0.1);
    const w       = Math.floor(barW * 0.8);
    const y       = h - padV - barH;

    ctx.fillStyle = val > 0 ? '#f97316' : '#334155';
    ctx.beginPath();
    ctx.roundRect(x, y, w, Math.max(barH, 2), 2);
    ctx.fill();
  });

  document.getElementById('chart-wrap').style.display = '';
}

async function loadRunDetail(runId) {
  try {
    const res = await fetch(`${LOG_BASE}${encodeURIComponent(runId)}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const run = await res.json();
    renderRunDetail(run);
  } catch (err) {
    document.getElementById('run-detail-container').innerHTML =
      `<div class="state-msg"><p>Could not load run log: ${escHtml(err.message)}</p></div>`;
  }
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
    row.className = 'run-row';
    if (i === 0) row.classList.add('selected');
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
      ${(run.errors ?? 0) > 0 ? `<span class="run-badge" style="color:var(--verdict-false)">${run.errors} errors</span>` : ''}
    `;

    row.addEventListener('click', () => {
      list.querySelectorAll('.run-row').forEach(r => r.classList.remove('selected'));
      row.classList.add('selected');
      loadRunDetail(run.run_id);
    });
    row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') row.click(); });

    list.appendChild(row);
  });

  container.replaceChildren(list);

  // Auto-load the first run
  if (runs.length) loadRunDetail(runs[0].run_id);

  // Handle hash anchor
  const hash = window.location.hash.slice(1);
  if (hash) {
    const target = list.querySelector(`[data-run-id="${CSS.escape(hash)}"]`);
    if (target) target.click();
  }
}

async function init() {
  const next = nextScheduledRun();
  document.getElementById('status-next-run').textContent = formatDate(next.toISOString());

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
      const ran   = runs.some(r => r.started_at?.startsWith(today));
      if (!ran) {
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
