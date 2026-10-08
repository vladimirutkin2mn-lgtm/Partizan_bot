(() => {
  const STYLE_ID = 'partizan-live-opportunities-style';
  const PANEL_ID = 'live-opportunities-panel';
  let lastProjectId = null;
  let refreshTimer = null;

  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

  const currentProjectId = () => {
    const fromUrl = new URLSearchParams(window.location.search).get('project');
    if (fromUrl) return fromUrl;
    const switcher = document.getElementById('project-switcher');
    return switcher && switcher.value ? switcher.value : null;
  };

  const ensureStyles = () => {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      .live-opportunities-panel{margin:0 0 18px;padding:18px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.018)}
      .live-opportunities-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px}
      .live-opportunities-head h2{margin:2px 0 5px;font-size:18px}.live-opportunities-head p{margin:0;color:var(--muted);font-size:11px;line-height:1.5}
      .live-opportunities-count{flex:0 0 auto;padding:5px 8px;border:1px solid var(--line);border-radius:999px;color:var(--soft);font-size:9px;font-weight:700}
      .live-opportunities-list{display:grid;gap:10px}.live-opportunity{padding:14px;border:1px solid var(--line);border-radius:13px;background:rgba(0,0,0,.12)}
      .live-opportunity-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.live-opportunity-top strong{font-size:12px;line-height:1.4}.live-opportunity-badges{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:5px}
      .live-opportunity-badge{padding:3px 6px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:8px;font-weight:700;white-space:nowrap}.live-opportunity-badge.ready{color:var(--accent)}.live-opportunity-badge.warn{color:var(--warn)}
      .live-opportunity-meta{margin:7px 0 0;color:var(--muted);font-size:9px}.live-opportunity p{margin:9px 0 0;color:var(--soft);font-size:10px;line-height:1.55}.live-opportunity-action{margin-top:10px;padding-top:9px;border-top:1px solid var(--line)}.live-opportunity-action b{display:block;margin-bottom:3px;font-size:9px;color:var(--muted)}
      .live-opportunity-links{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}.live-opportunity-links a{font-size:10px;color:var(--accent);text-decoration:none}.live-opportunity details{margin-top:10px}.live-opportunity summary{cursor:pointer;color:var(--muted);font-size:9px}.live-opportunity pre{margin:7px 0 0;padding:10px;border:1px solid var(--line);border-radius:9px;background:rgba(0,0,0,.18);color:var(--soft);white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:10px;line-height:1.5}
      .live-opportunity-stale{opacity:.68}.live-opportunities-history{margin-top:12px}.live-opportunities-history summary{cursor:pointer;color:var(--muted);font-size:10px}.live-opportunities-empty{color:var(--muted);font-size:10px;line-height:1.55}.live-opportunities-error{color:var(--warn);font-size:10px;line-height:1.55}
      @media(max-width:700px){.live-opportunities-head,.live-opportunity-top{display:block}.live-opportunity-badges{justify-content:flex-start;margin-top:8px}}
    `;
    document.head.appendChild(style);
  };

  const ensurePanel = () => {
    let panel = document.getElementById(PANEL_ID);
    if (panel) return panel;
    const activity = document.querySelector('[data-tab-panel="activity"]');
    if (!activity) return null;
    panel = document.createElement('section');
    panel.id = PANEL_ID;
    panel.className = 'live-opportunities-panel';
    panel.innerHTML = '<div class="live-opportunities-head"><div><span class="eyebrow">Live discovery</span><h2>Opportunities Partizan is watching now</h2><p>Fresh places, posts and communities found after the original market map. Nothing here is published without the normal review and execution checks.</p></div><span class="live-opportunities-count">Loading…</span></div><div class="live-opportunities-list"><div class="live-opportunities-empty">Checking the latest opportunities…</div></div>';
    activity.insertBefore(panel, activity.firstChild);
    return panel;
  };

  const freshnessLabel = (value) => ({
    NEW: 'New', FRESH: 'Fresh', EXPIRING_SOON: 'Expires soon', STALE: 'Stale',
  })[value] || value;

  const publishabilityLabel = (value) => ({
    READY: 'Ready to publish',
    JOIN_REQUIRED: 'Need to join group',
    NO_WRITE_ACCESS: 'No write access',
    NO_DISCUSSION: 'No discussion thread',
    PRECHECK_FAILED: 'Access check failed',
    JOIN_FAILED: 'Could not join group',
    NEEDS_REVIEW: 'Needs review',
    MANUAL: 'Manual action',
    UNKNOWN: 'Access not checked',
  })[value] || value;

  const badgeClass = (value) => {
    if (value === 'READY') return 'ready';
    if (['JOIN_REQUIRED', 'NO_WRITE_ACCESS', 'PRECHECK_FAILED', 'JOIN_FAILED'].includes(value)) return 'warn';
    return '';
  };

  const formatDate = (value) => {
    if (!value) return '';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };

  const card = (item, stale = false) => {
    const expires = formatDate(item.expires_at);
    const checked = formatDate(item.last_checked_at);
    return `<article class="live-opportunity${stale ? ' live-opportunity-stale' : ''}">
      <div class="live-opportunity-top"><strong>${escapeHtml(item.title)}</strong><div class="live-opportunity-badges"><span class="live-opportunity-badge">${escapeHtml(freshnessLabel(item.freshness))}</span><span class="live-opportunity-badge ${badgeClass(item.publishability)}">${escapeHtml(publishabilityLabel(item.publishability))}</span>${item.status === 'PUBLISHED' ? '<span class="live-opportunity-badge ready">Published</span>' : ''}</div></div>
      <div class="live-opportunity-meta">${escapeHtml(item.platform)} · ${escapeHtml(item.kind)}${expires ? ` · useful until ${escapeHtml(expires)}` : ''}${checked ? ` · checked ${escapeHtml(checked)}` : ''}</div>
      <p>${escapeHtml(item.rationale)}</p>
      <div class="live-opportunity-action"><b>Partizan recommends</b><p>${escapeHtml(item.recommended_action)}</p></div>
      ${item.publishability_detail ? `<p>${escapeHtml(item.publishability_detail)}</p>` : ''}
      <div class="live-opportunity-links"><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">Open target ↗</a></div>
      ${item.suggested_content ? `<details><summary>Draft Partizan would review before publishing</summary><pre>${escapeHtml(item.suggested_content)}</pre></details>` : ''}
    </article>`;
  };

  const render = (items) => {
    const panel = ensurePanel();
    if (!panel) return;
    const list = panel.querySelector('.live-opportunities-list');
    const count = panel.querySelector('.live-opportunities-count');
    const active = items.filter((item) => item.freshness !== 'STALE' && !['DISMISSED', 'STALE'].includes(item.status));
    const history = items.filter((item) => item.freshness === 'STALE' || ['DISMISSED', 'STALE'].includes(item.status));
    count.textContent = active.length ? `${active.length} active` : 'No active';
    if (!active.length && !history.length) {
      list.innerHTML = '<div class="live-opportunities-empty">No live opportunities yet. New operational discoveries will appear here automatically.</div>';
      return;
    }
    list.innerHTML = active.slice(0, 8).map((item) => card(item)).join('')
      + (history.length ? `<details class="live-opportunities-history"><summary>History · ${history.length}</summary><div class="live-opportunities-list">${history.slice(0, 12).map((item) => card(item, true)).join('')}</div></details>` : '');
  };

  const renderError = (message) => {
    const panel = ensurePanel();
    if (!panel) return;
    const list = panel.querySelector('.live-opportunities-list');
    const count = panel.querySelector('.live-opportunities-count');
    count.textContent = 'Unavailable';
    list.innerHTML = `<div class="live-opportunities-error">${escapeHtml(message)}</div>`;
  };

  const load = async () => {
    const projectId = currentProjectId();
    const panel = ensurePanel();
    if (!panel || !projectId) return;
    lastProjectId = projectId;
    try {
      const response = await fetch(`/customer/workspace/${encodeURIComponent(projectId)}/live-opportunities`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (response.status === 401 || response.status === 403) {
        panel.classList.add('hidden');
        return;
      }
      if (!response.ok) throw new Error(`Live opportunities unavailable (${response.status})`);
      const payload = await response.json();
      panel.classList.remove('hidden');
      render(Array.isArray(payload) ? payload : []);
    } catch (error) {
      renderError(error && error.message ? error.message : 'Live opportunities are temporarily unavailable.');
    }
  };

  const refreshIfProjectChanged = () => {
    const current = currentProjectId();
    if (current && current !== lastProjectId) load().catch(() => {});
  };

  const boot = () => {
    ensureStyles();
    ensurePanel();
    load().catch(() => {});
    const switcher = document.getElementById('project-switcher');
    if (switcher) switcher.addEventListener('change', () => window.setTimeout(() => load().catch(() => {}), 120));
    window.addEventListener('popstate', () => load().catch(() => {}));
    window.setInterval(refreshIfProjectChanged, 1500);
    refreshTimer = window.setInterval(() => {
      if (!document.hidden && currentProjectId()) load().catch(() => {});
    }, 60000);
  };

  window.addEventListener('beforeunload', () => {
    if (refreshTimer) window.clearInterval(refreshTimer);
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
