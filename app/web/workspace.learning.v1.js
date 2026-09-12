(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);
  const money = (value) => `$${Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;

  let projectId = new URLSearchParams(window.location.search).get('project');
  let loading = false;
  let refreshQueued = false;

  const syncProjectId = (candidate = null) => {
    projectId = candidate || new URLSearchParams(window.location.search).get('project');
    return projectId;
  };

  const ensureCard = () => {
    if ($('distribution-learning-card')) return $('distribution-learning-card');
    const activity = document.querySelector('[data-tab-panel="activity"]');
    if (!activity) return null;

    const card = document.createElement('section');
    card.id = 'distribution-learning-card';
    card.className = 'panel customer-results-card';
    card.innerHTML = `
      <div class="section-head">
        <div>
          <span class="eyebrow">Observed learning</span>
          <h2>Why Partizan changed course</h2>
          <p class="section-copy">Measured distribution signals are tied to the exact test that produced each SCALE, CONTINUE, MODIFY or STOP decision. Internal publisher identities, tactic IDs and operating costs stay private.</p>
        </div>
        <span id="distribution-learning-state" class="status-pill">Read only</span>
      </div>
      <div id="distribution-learning-list" class="managed-assignment-list">
        <div class="customer-results-empty">Waiting for observed distribution learning.</div>
      </div>`;

    const results = $('distribution-results-card');
    if (results && results.parentElement === activity) results.insertAdjacentElement('afterend', card);
    else activity.appendChild(card);
    return card;
  };

  const decisionLabel = (value) => ({
    SCALE: 'Scale',
    CONTINUE: 'Continue',
    MODIFY: 'Modify',
    STOP: 'Stop',
  })[value] || value || 'Decision';

  const render = (data) => {
    ensureCard();
    const list = $('distribution-learning-list');
    const state = $('distribution-learning-state');
    if (!list || !state) return;
    const entries = Array.isArray(data && data.entries) ? data.entries : [];
    state.textContent = entries.length ? 'Observed' : 'No observations yet';
    state.classList.toggle('good', entries.length > 0);

    if (!entries.length) {
      list.innerHTML = '<div class="customer-results-empty">No Growth Manager decision has enough observed distribution evidence to show yet.</div>';
      return;
    }

    list.innerHTML = entries.map((entry) => {
      const action = entry.action_type ? ` · ${escapeHtml(entry.action_type)}` : '';
      const metrics = [];
      if (Number(entry.paid_users || 0) > 0) metrics.push(`${Number(entry.paid_users)} paid customer(s)`);
      if (entry.observed_cac != null) metrics.push(`CAC ${money(entry.observed_cac)}`);
      if (Number(entry.replies || 0) > 0) metrics.push(`${Number(entry.replies)} replies`);
      if (Number(entry.removals || 0) > 0) metrics.push(`${Number(entry.removals)} removals`);
      if (Number(entry.revenue || 0) > 0) metrics.push(`${money(entry.revenue)} revenue`);
      const basis = (entry.observed_basis || []).map((item) => escapeHtml(item)).join(' ');
      const source = entry.opportunity_url
        ? `<a href="${escapeHtml(entry.opportunity_url)}" target="_blank" rel="noopener noreferrer">Open observed opportunity ↗</a>`
        : '';
      return `<article class="managed-assignment">
        <div class="managed-assignment-head">
          <div><strong>${escapeHtml(entry.opportunity_title || 'Observed opportunity')}</strong><span>${escapeHtml(entry.platform)} · ${escapeHtml(entry.publisher_mode)}${action}</span></div>
          <span class="status-pill">${escapeHtml(decisionLabel(entry.decision))}</span>
        </div>
        <div class="managed-assignment-meta">${metrics.length ? metrics.map((item) => `<span>${escapeHtml(item)}</span>`).join('') : '<span>No downstream conversion yet</span>'}</div>
        <p class="note">${basis || 'No paid conversion or community response has been observed yet.'}</p>
        ${source}
      </article>`;
    }).join('');
  };

  const renderError = (error) => {
    ensureCard();
    const list = $('distribution-learning-list');
    const state = $('distribution-learning-state');
    if (state) {
      state.textContent = 'Unavailable';
      state.classList.remove('good');
      state.classList.add('warn');
    }
    if (list) list.innerHTML = `<div class="customer-results-error">${escapeHtml(error.message || 'Observed learning is temporarily unavailable.')}</div>`;
  };

  const load = async (force = false) => {
    if (!syncProjectId(projectId)) return;
    ensureCard();
    if (loading) {
      if (force) refreshQueued = true;
      return;
    }
    loading = true;
    const state = $('distribution-learning-state');
    if (state) {
      state.textContent = 'Loading';
      state.classList.remove('good', 'warn');
    }
    try {
      const response = await fetch(
        `/customer/workspace/${encodeURIComponent(projectId)}/distribution-learning`,
        { credentials: 'same-origin', cache: 'no-store' },
      );
      let payload = {};
      try { payload = await response.json(); } catch (_) { payload = {}; }
      if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
      render(payload);
    } catch (error) {
      renderError(error);
    } finally {
      loading = false;
      if (refreshQueued) {
        refreshQueued = false;
        load(true).catch(() => {});
      }
    }
  };

  ensureCard();

  window.addEventListener('partizan:workspace-ready', (event) => {
    const nextProjectId = event.detail && event.detail.projectId;
    if (nextProjectId) syncProjectId(nextProjectId);
    else syncProjectId();
    load(true).catch(() => {});
  });

  document.querySelector('.tab-button[data-tab="activity"]')?.addEventListener('click', () => {
    load(true).catch(() => {});
  });
})();
