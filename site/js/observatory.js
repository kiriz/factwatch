'use strict';

const MANIFEST_URL = 'data/scores-manifest.json';
const INDEX_URL    = 'logs/index.json';
const LOG_BASE     = 'logs/';
const NOCACHE      = { cache: 'no-cache' };

function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function verdictClass(v) { return 'verdict-' + String(v || 'unverified').toLowerCase(); }
function verdictColour(v) {
  const m = { TRUE:'var(--color-viz-positive)', FALSE:'var(--color-viz-negative)',
    MISLEADING:'var(--color-viz-caution)', UNVERIFIED:'var(--color-viz-neutral)',
    OUTDATED:'var(--color-viz-stale)', DISPUTED:'var(--color-viz-special)' };
  return m[v] || 'var(--color-viz-neutral)';
}
function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
function relativeTime(iso) {
  if (!iso) return '';
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  return d === 0 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`;
}
function domainOf(u) { try { const h = new URL(u).hostname; return h.startsWith('www.') ? h.slice(4) : h; } catch { return u; } }
function fmtTokens(n) { return n == null ? null : n >= 1000 ? `${(n/1000).toFixed(1)}k` : String(n); }

/* ── Pipeline step ─────────────────────────────────────────── */
function step(num, label, meta, body, accent) {
  return `
    <div class="ps-step">
      <div class="ps-rail"><div class="ps-num" style="${accent ? `background:${accent};border-color:${accent};color:#fff` : ''}">${num}</div><div class="ps-line"></div></div>
      <div class="ps-content">
        <div class="ps-label"><span class="ps-label-title">${escHtml(label)}</span>${meta ? `<span class="ps-label-meta">${meta}</span>` : ''}</div>
        <div class="ps-body">${body}</div>
      </div>
    </div>`;
}

/* Render the full agent pipeline for one claim trace. */
function renderTracePipeline(trace) {
  const steps = [];

  steps.push(step('①', 'Claim', trace.category || null,
    `<div class="ps-claim">${escHtml(trace.claim_title || trace.claim_id)}</div>
     ${trace.claim_text ? `<div class="ps-claim-text">"${escHtml(trace.claim_text)}"</div>` : ''}`,
    'var(--color-primary)'));

  const queries = trace.search_queries || [];
  const results = trace.search_results || [];
  if (queries.length || results.length) {
    steps.push(step('②', 'Web Search', `${queries.length} queries · ${results.length} results`,
      `<div class="ps-chips">${queries.map(q => `<span class="ps-chip">${escHtml(q)}</span>`).join('')}</div>
       <div class="ps-results">${results.map(r => `
         <a class="ps-result" href="${escHtml(r.url)}" target="_blank" rel="noopener">
           <div class="ps-result-title">${escHtml(r.title || r.url)}</div>
           <div class="ps-result-domain">${escHtml(domainOf(r.url || ''))}</div>
           ${r.snippet ? `<div class="ps-result-snippet">${escHtml(r.snippet.slice(0,160))}</div>` : ''}
         </a>`).join('')}</div>`,
      'var(--color-viz-special)'));
  }

  if (trace.prompt) {
    const inTok = fmtTokens(trace.prompt_tokens), outTok = fmtTokens(trace.response_tokens), totTok = fmtTokens(trace.total_tokens);
    const model = trace.model_name || 'model';
    const tokens = (inTok || outTok || totTok)
      ? `<div class="ps-tokens">${inTok?`<span class="ps-tok"><b>in</b> ${inTok}</span>`:''}${outTok?`<span class="ps-tok"><b>out</b> ${outTok}</span>`:''}${totTok?`<span class="ps-tok ps-tok-total"><b>total</b> ${totTok}</span>`:''}</div>` : '';
    steps.push(step('③', 'LLM Request', null,
      `<div class="ps-reqhead"><span class="ps-model">${escHtml(model)}${trace.model_version?` · ${escHtml(trace.model_version)}`:''}</span>${trace.dspy_used?'<span class="ps-chip">DSPy</span>':''}${tokens}</div>
       <pre class="ps-code">${escHtml(trace.prompt)}</pre>`,
      'var(--color-warning)'));
  }
  if (trace.raw_response) {
    let pretty = trace.raw_response;
    try { pretty = JSON.stringify(JSON.parse(trace.raw_response), null, 2); } catch { /* keep */ }
    steps.push(step('④', 'LLM Response', null, `<pre class="ps-code ps-code-out">${escHtml(pretty)}</pre>`, 'var(--color-viz-positive)'));
  }

  const reasoning = (trace.reasoning_steps || []).join(' ');
  const changed = trace.verdict_changed;
  const verdictRow = changed && trace.previous_verdict
    ? `<span class="verdict-badge ${verdictClass(trace.previous_verdict)}">${escHtml(trace.previous_verdict)}</span><span class="ps-arrow">→</span><span class="verdict-badge ${verdictClass(trace.new_verdict)}">${escHtml(trace.new_verdict)}</span>`
    : `<span class="verdict-badge ${verdictClass(trace.new_verdict || trace.verdict)}">${escHtml(trace.new_verdict || trace.verdict)}</span>`;
  steps.push(step('⑤', 'Verdict', trace.confidence != null ? `${trace.confidence}%` : null,
    `<div class="ps-verdict">${verdictRow}</div>${reasoning ? `<p class="ps-reasoning">${escHtml(reasoning)}</p>` : ''}`,
    'var(--color-viz-negative)'));

  return `<div class="ps-pipeline">${steps.join('')}</div>`;
}

/* ── Single-claim view (?claim=ID) ─────────────────────────── */
async function renderClaimView(claimId) {
  const el = document.getElementById('obs-content');
  try {
    const [scoreRes, manRes] = await Promise.all([
      fetch(`scores/${encodeURIComponent(claimId)}.json`, NOCACHE),
      fetch(MANIFEST_URL, NOCACHE).catch(() => null),
    ]);
    if (!scoreRes.ok) throw new Error(`score HTTP ${scoreRes.status}`);
    const score = await scoreRes.json();
    let meta = {};
    if (manRes && manRes.ok) { const m = await manRes.json(); meta = m.find(x => x.claim_id === claimId) || {}; }

    // Pull the trace for this claim from the run that produced the verdict.
    let trace = null;
    if (score.run_id) {
      try {
        const r = await fetch(`${LOG_BASE}${encodeURIComponent(score.run_id)}.json`, NOCACHE);
        if (r.ok) { const run = await r.json(); trace = (run.claim_traces || []).find(t => t.claim_id === claimId) || null; }
      } catch (_) { /* fall through */ }
    }
    // Enrich trace with manifest title/text + score fallback.
    if (trace) {
      trace.claim_title = trace.claim_title || meta.title || claimId;
      trace.claim_text = trace.claim_text || meta.claim_text || '';
      trace.category = trace.category || meta.category || '';
    }

    const title = meta.title || score.title || claimId;
    el.innerHTML = `
      <a href="observatory.html" class="back-link">← All claim traces</a>
      <div class="page-header">
        <h1>Agent trace</h1>
        <div class="sub">${escHtml(title)} · run <code>${escHtml(score.run_id || '—')}</code> · ${relativeTime(score.last_checked_at)}</div>
      </div>
      ${trace
        ? renderTracePipeline(trace)
        : `<div class="section-card"><p class="muted">The detailed trace for this run isn't available, but here is the recorded verdict:</p>
             <div class="ps-verdict" style="margin-top:12px"><span class="verdict-badge ${verdictClass(score.verdict)}">${escHtml(score.verdict)}</span> <span class="muted">${score.confidence}%</span></div>
             <p class="claim-reasoning" style="margin-top:12px">${escHtml(score.reasoning || '')}</p></div>`}
      <div class="claim-footer-link"><a class="btn-link" href="claim.html?id=${encodeURIComponent(claimId)}">← Back to claim verdict</a></div>`;
  } catch (err) {
    el.innerHTML = `<a href="observatory.html" class="back-link">← All claim traces</a>
      <div class="state-msg"><p>Could not load trace for <strong>${escHtml(claimId)}</strong>: ${escHtml(err.message)}</p></div>`;
  }
}

/* ── Index view (claim picker + run stats) ─────────────────── */
async function renderIndexView() {
  const el = document.getElementById('obs-content');
  let manifest = [], runs = [];
  try {
    const [manRes, idxRes] = await Promise.all([
      fetch(MANIFEST_URL, NOCACHE),
      fetch(INDEX_URL, NOCACHE).catch(() => null),
    ]);
    if (manRes.ok) manifest = await manRes.json();
    if (idxRes && idxRes.ok) runs = (await idxRes.json()).runs || [];
  } catch (_) { /* show what we have */ }

  // only runs that actually processed claims
  const realRuns = runs.filter(r => (r.claims_processed || 0) > 0);
  const lastRun = realRuns[0];
  const next = new Date(); next.setUTCHours(6, 0, 0, 0); if (next <= new Date()) next.setUTCDate(next.getUTCDate() + 1);

  // Load each claim's score for verdict + last-checked (small N).
  const scored = await Promise.all(manifest.map(async (m) => {
    try { const r = await fetch(m.score_file, NOCACHE); if (r.ok) return { ...m, score: await r.json() }; } catch (_) {}
    return { ...m, score: null };
  }));
  scored.sort((a, b) => new Date(b.score?.last_checked_at || 0) - new Date(a.score?.last_checked_at || 0));

  el.innerHTML = `
    <div class="page-header">
      <h1>AI Observatory</h1>
      <div class="sub">Pick a claim to watch exactly how the agent verified it — every search, prompt, and decision.</div>
    </div>
    <div class="obs-stats">
      <div class="stat-box"><div class="stat-box__label">Last run</div><div class="stat-box__value">${lastRun ? relativeTime(lastRun.started_at) : '—'}</div></div>
      <div class="stat-box"><div class="stat-box__label">Runs</div><div class="stat-box__value">${realRuns.length}</div></div>
      <div class="stat-box"><div class="stat-box__label">Claims tracked</div><div class="stat-box__value">${manifest.length}</div></div>
      <div class="stat-box"><div class="stat-box__label">Next run</div><div class="stat-box__value" style="font-size:0.95rem">${formatDate(next.toISOString())}</div></div>
    </div>
    <div class="obs-claim-list">
      ${scored.map(c => {
        const v = c.score?.verdict || 'UNVERIFIED';
        return `<a class="card-news obs-claim-row" href="observatory.html?claim=${encodeURIComponent(c.claim_id)}" style="--card-accent:${verdictColour(v)}">
          <div class="card-news__top">
            <span class="card-news__title">${escHtml(c.title || c.claim_id)}</span>
            <span class="verdict-badge ${verdictClass(v)}">${escHtml(v)}</span>
          </div>
          <div class="card-news__bottom">
            <span>${escHtml(c.category || '')}</span><span>·</span>
            <span>${c.score?.confidence != null ? c.score.confidence + '%' : '—'}</span><span>·</span>
            <span>checked ${relativeTime(c.score?.last_checked_at)}</span>
            <span class="obs-row-cta">view trace →</span>
          </div>
        </a>`;
      }).join('')}
    </div>`;
}

/* ── Init ──────────────────────────────────────────────────── */
const claimParam = new URLSearchParams(location.search).get('claim');
if (claimParam) renderClaimView(claimParam); else renderIndexView();
