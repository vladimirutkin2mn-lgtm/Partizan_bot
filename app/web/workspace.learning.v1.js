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

(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

  let projectId = new URLSearchParams(window.location.search).get('project');
  let loading = false;
  let refreshQueued = false;
  let workspaceSnapshot = null;
  let channelSnapshot = [];

  const syncProjectId = (candidate = null) => {
    projectId = candidate || new URLSearchParams(window.location.search).get('project');
    return projectId;
  };

  const requestJson = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  };

  const ensureChoiceCard = () => {
    if ($('channel-choice-card')) return $('channel-choice-card');
    const activation = $('activation-card');
    if (!activation || !activation.parentElement) return null;
    const card = document.createElement('section');
    card.id = 'channel-choice-card';
    card.className = 'panel activation-card hidden';
    activation.insertAdjacentElement('beforebegin', card);
    return card;
  };

  const inferredPlatform = (opportunity) => {
    const candidates = [
      opportunity && opportunity.platform,
      opportunity && opportunity.url,
      ...((opportunity && Array.isArray(opportunity.provenance)) ? opportunity.provenance.map((item) => item.url) : []),
    ].filter(Boolean).map((value) => String(value).toLowerCase());
    const joined = candidates.join(' ');
    if (joined.includes('reddit.com') || joined.includes('reddit')) return 'REDDIT';
    if (joined.includes('t.me') || joined.includes('telegram')) return 'TELEGRAM';
    if (joined.includes('instagram.com') || joined.includes('facebook.com') || joined.includes('meta.com')) return 'INSTAGRAM';
    if (joined.includes('tiktok.com') || joined.includes('tiktok')) return 'TIKTOK';
    return null;
  };

  const measuredActivity = (channels) => channels.some((channel) => (
    Number(channel.experiment_count || 0) > 0
    || Number(channel.spend_usd || 0) > 0
    || Number(channel.paid_customers || 0) > 0
    || Number(channel.revenue_usd || 0) > 0
  ));

  const nextStep = (channel) => {
    if (!channel) return 'Choose one starting channel. You can change it later.';
    const publish = (channel.capabilities || []).find((item) => item.capability === 'PUBLISH');
    if (publish && publish.ready) {
      return 'The publishing path is available. Review Channels before granting any execution permission.';
    }
    if (channel.connected === false && channel.platform === 'INSTAGRAM') {
      return 'Start with this channel, then connect Meta only when a specific move needs execution access.';
    }
    if (channel.connected === false && ['REDDIT', 'TELEGRAM'].includes(channel.platform)) {
      return 'Start with this channel, then choose a publisher mode and connect your account only when you want to publish.';
    }
    if (channel.platform === 'TIKTOK') {
      return 'Partizan can keep researching TikTok. Automatic publishing is not available yet.';
    }
    return 'Start with this channel in research mode. Execution remains separately controlled in Channels.';
  };

  const renderChoice = () => {
    const card = ensureChoiceCard();
    const activation = $('activation-card');
    if (!card || !activation || !workspaceSnapshot) return;

    const researchReady = Boolean(
      workspaceSnapshot.preview_opportunity
      || (Array.isArray(workspaceSnapshot.preview_directions) && workspaceSnapshot.preview_directions.length)
      || channelSnapshot.some((channel) => channel.selected)
    );
    if (!researchReady || measuredActivity(channelSnapshot)) {
      card.classList.add('hidden');
      return;
    }

    activation.classList.add('hidden');
    card.classList.remove('hidden');
    const recommended = inferredPlatform(workspaceSnapshot.preview_opportunity);
    const selected = channelSnapshot.find((channel) => channel.selected) || null;
    const selectedPlatform = selected && selected.platform;

    const choices = channelSnapshot.map((channel) => {
      const disabled = channel.mode === 'OFF';
      const isSelected = channel.platform === selectedPlatform;
      const isRecommended = channel.platform === recommended;
      const state = isSelected ? 'Chosen' : (isRecommended ? 'Research lead' : 'Available');
      return `<div class="activation-action">
        <div>
          <span class="eyebrow">${escapeHtml(state)}</span>
          <button class="button ${isSelected ? 'button-primary' : 'button-secondary'} channel-choice-button" type="button" data-channel-choice="${escapeHtml(channel.platform)}" ${disabled ? 'disabled' : ''}>${isSelected ? 'Starting with ' : 'Start with '}${escapeHtml(channel.label)}${isSelected ? ' ✓' : ' →'}</button>
        </div>
        <p class="note">${disabled ? 'This channel is Off in Channels. Turn it back on before selecting it.' : escapeHtml(nextStep(channel))}</p>
      </div>`;
    }).join('');

    card.innerHTML = `
      <div class="activation-head">
        <div><span class="eyebrow">Research → first move</span><h2>Choose where Partizan should start.</h2></div>
        <span class="status-pill">${selected ? '3 of 3' : '2 of 3'}</span>
      </div>
      <p class="section-copy">Research found where customers may already be. Pick the first channel to focus the next recommendation without granting execution or spend permission.</p>
      <ol class="activation-list">
        <li><span class="activation-index">1</span><div><strong>Understand your product</strong><small>Partizan started from the product you already built.</small></div><span class="activation-step-state">Done</span></li>
        <li><span class="activation-index">2</span><div><strong>Find where customers are</strong><small>Public-web research found concrete evidence before spend.</small></div><span class="activation-step-state">Done</span></li>
        <li><span class="activation-index">3</span><div><strong>Choose where to start</strong><small>This is a focus choice, not execution permission.</small></div><span class="activation-step-state">${selected ? 'Done' : 'Now'}</span></li>
      </ol>
      <div class="activation-action activation-action-primary">
        <div><span class="eyebrow">First channel</span><strong>${selected ? escapeHtml(selected.label) : 'Choose one starting channel'}</strong></div>
        <p class="note">Choosing a channel does not allow execution, connect an account, or authorize spend.</p>
      </div>
      ${choices}
      <div class="activation-action">
        <div><span class="eyebrow">Execution stays separate</span><button id="channel-choice-controls" class="button button-secondary" type="button">Review channel controls →</button></div>
        <p class="note">${escapeHtml(nextStep(selected))}</p>
      </div>`;

    card.querySelectorAll('[data-channel-choice]').forEach((button) => {
      button.addEventListener('click', async () => {
        const platform = button.getAttribute('data-channel-choice');
        if (!platform) return;
        const original = button.textContent;
        button.disabled = true;
        button.textContent = 'Saving choice…';
        try {
          channelSnapshot = await requestJson(
            `/customer/workspace/${encodeURIComponent(projectId)}/channel-selection`,
            { method: 'PUT', body: JSON.stringify({ platform }) },
          );
          renderChoice();
        } catch (error) {
          button.disabled = false;
          button.textContent = original;
          const note = card.querySelector('.activation-action-primary .note');
          if (note) note.textContent = error.message || 'Could not save the starting channel.';
        }
      });
    });

    $('channel-choice-controls')?.addEventListener('click', () => {
      document.querySelector('.tab-button[data-tab="channels"]')?.click();
    });
  };

  const loadChoice = async (force = false) => {
    if (!syncProjectId(projectId)) return;
    ensureChoiceCard();
    if (loading) {
      if (force) refreshQueued = true;
      return;
    }
    loading = true;
    try {
      [workspaceSnapshot, channelSnapshot] = await Promise.all([
        requestJson(`/customer/workspace/${encodeURIComponent(projectId)}`),
        requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/channels`),
      ]);
      renderChoice();
    } catch (_) {
      const card = ensureChoiceCard();
      if (card) card.classList.add('hidden');
    } finally {
      loading = false;
      if (refreshQueued) {
        refreshQueued = false;
        loadChoice(true).catch(() => {});
      }
    }
  };

  ensureChoiceCard();

  window.addEventListener('partizan:workspace-ready', (event) => {
    const nextProjectId = event.detail && event.detail.projectId;
    if (nextProjectId) syncProjectId(nextProjectId);
    else syncProjectId();
    loadChoice(true).catch(() => {});
  });
})();
