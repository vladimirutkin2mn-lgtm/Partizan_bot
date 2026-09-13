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
  let startingMove = null;
  let startingMoveDraft = null;
  let startingMoveSetup = null;

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
    if (!response.ok) throw new Error((payload && payload.detail) || `Request failed (${response.status})`);
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

  const moveSourceLabel = (move) => ({
    PREVIEW_RESEARCH: 'Free research evidence',
    CHANNEL_RESEARCH: 'Selected-channel research',
    FULL_RESEARCH: 'Full research evidence',
    SELECTED_CHANNEL: 'Research gap',
  })[move && move.source] || 'Next recommendation';

  const draftReviewLabel = (draft) => ({
    DRAFT: 'Needs your review',
    ACCEPTED: 'Accepted for next setup step',
    REJECTED: 'Rejected',
  })[draft && draft.review_status] || 'Review only';

  const setupStateLabel = (setup) => ({
    READY_FOR_HANDOFF: 'Ready for handoff',
    NEEDS_SETUP: 'Setup needed',
    UNAVAILABLE: 'Automation unavailable',
  })[setup && setup.state] || 'Setup';

  const setupStepStateLabel = (value) => ({
    READY: 'Ready',
    NEEDS_ACTION: 'Needs action',
    UNAVAILABLE: 'Unavailable',
  })[value] || value || 'Status';

  const renderSetup = (selected) => {
    if (!startingMoveSetup || startingMoveSetup.platform !== selected.platform) return '';
    const steps = Array.isArray(startingMoveSetup.steps) ? startingMoveSetup.steps : [];
    const stepRows = steps.map((step) => `<div class="activation-action">
      <div><span class="eyebrow">${escapeHtml(step.title)}</span><span class="status-pill">${escapeHtml(setupStepStateLabel(step.state))}</span></div>
      <p class="note">${escapeHtml(step.detail)}</p>
    </div>`).join('');
    return `<div class="activation-action activation-action-primary">
      <div><span class="eyebrow">Accepted draft → setup</span><strong>${escapeHtml(startingMoveSetup.channel_label)} setup plan</strong><span class="status-pill">${escapeHtml(setupStateLabel(startingMoveSetup))}</span></div>
      <p class="note">${escapeHtml(startingMoveSetup.next_step)}</p>
      <p class="note"><strong>Execution permission:</strong> Not granted by this plan.</p>
      ${stepRows}
      <div><button id="starting-move-setup-controls" class="button button-secondary" type="button">Open Channels →</button></div>
    </div>`;
  };

  const renderDraft = (selected) => {
    if (!startingMoveDraft || startingMoveDraft.platform !== selected.platform) return '';
    const reviewable = startingMoveDraft.review_status === 'DRAFT';
    const source = `<a href="${escapeHtml(startingMoveDraft.source_url)}" target="_blank" rel="noopener noreferrer">Source evidence ↗</a>`;
    const content = reviewable
      ? `<label><span class="eyebrow">Draft title</span><input id="starting-move-draft-title" type="text" maxlength="300" value="${escapeHtml(startingMoveDraft.title || '')}"></label>
        <label><span class="eyebrow">Draft content</span><textarea id="starting-move-draft-content" rows="7" maxlength="12000">${escapeHtml(startingMoveDraft.content_text)}</textarea></label>
        <div>
          <button id="channel-choice-draft-save" class="button button-secondary" type="button">Save changes</button>
          <button id="channel-choice-draft-accept" class="button button-primary" type="button">Accept for next setup step →</button>
          <button id="channel-choice-draft-reject" class="button button-secondary" type="button">Reject draft</button>
        </div>`
      : `<p>${escapeHtml(startingMoveDraft.content_text)}</p>`;
    const setup = startingMoveDraft.review_status === 'ACCEPTED' ? renderSetup(selected) : '';
    return `<div class="activation-action">
      <div><span class="eyebrow">Review-only test draft</span><strong>${escapeHtml(startingMoveDraft.title || 'First test draft')}</strong><span class="status-pill">${escapeHtml(draftReviewLabel(startingMoveDraft))}</span></div>
      ${content}
      <p class="note"><strong>Why this draft:</strong> ${escapeHtml(startingMoveDraft.rationale)}</p>
      <p class="note"><strong>Watch:</strong> ${escapeHtml(startingMoveDraft.signal_to_watch)}</p>
      <p class="note">${escapeHtml(startingMoveDraft.execution_requirement)}</p>
      <p class="note">${source}</p>
    </div>${setup}`;
  };

  const renderStartingMove = (selected) => {
    if (!selected) {
      return `<div class="activation-action activation-action-primary">
        <div><span class="eyebrow">First channel</span><strong>Choose one starting channel</strong></div>
        <p class="note">Choosing a channel does not allow execution, connect an account, or authorize spend.</p>
      </div>`;
    }
    if (!startingMove || startingMove.platform !== selected.platform) {
      return `<div class="activation-action activation-action-primary">
        <div><span class="eyebrow">Next recommendation</span><strong>Updating the first move for ${escapeHtml(selected.label)}…</strong></div>
        <p class="note">Partizan only shows a concrete move when it can tie that move to source evidence.</p>
      </div>`;
    }

    const sourceUrl = startingMove.url
      || (Array.isArray(startingMove.provenance) ? startingMove.provenance[0]?.url : null);
    const sourceLink = startingMove.state === 'READY' && sourceUrl
      ? `<a class="button button-secondary" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Open researched opportunity ↗</a>`
      : '';
    const researchButton = startingMove.state === 'NEEDS_RESEARCH'
      ? `<button id="channel-choice-research" class="button button-secondary" type="button">Research ${escapeHtml(selected.label)} now →</button>`
      : '';
    const draftButton = startingMove.state === 'READY'
      && (!startingMoveDraft || startingMoveDraft.review_status === 'DRAFT')
      ? `<button id="channel-choice-draft" class="button button-secondary" type="button">${startingMoveDraft ? 'Refresh review draft' : 'Prepare review draft'} →</button>`
      : '';
    const stateLabel = startingMove.state === 'READY' ? 'Evidence ready' : 'More research needed';

    return `<div class="activation-action activation-action-primary">
      <div>
        <span class="eyebrow">${escapeHtml(moveSourceLabel(startingMove))}</span>
        <strong>${escapeHtml(startingMove.title)}</strong>
        <span class="status-pill">${escapeHtml(stateLabel)}</span>
      </div>
      <p>${escapeHtml(startingMove.rationale)}</p>
      <p class="note"><strong>Next:</strong> ${escapeHtml(startingMove.recommended_action)}</p>
      <p class="note"><strong>Watch:</strong> ${escapeHtml(startingMove.signal_to_watch)}</p>
      <p class="note">${escapeHtml(startingMove.execution_requirement)}</p>
      <div>${sourceLink}${researchButton}${draftButton}</div>
      ${renderDraft(selected)}
    </div>`;
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
      ${renderStartingMove(selected)}
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
          [startingMove, startingMoveDraft, startingMoveSetup] = await Promise.all([
            requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move`),
            requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move/draft`),
            requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move/setup`),
          ]);
          renderChoice();
        } catch (error) {
          button.disabled = false;
          button.textContent = original;
          const note = card.querySelector('.activation-action-primary .note');
          if (note) note.textContent = error.message || 'Could not save the starting channel.';
        }
      });
    });

    $('channel-choice-research')?.addEventListener('click', async () => {
      const button = $('channel-choice-research');
      if (!button) return;
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Researching selected channel…';
      try {
        startingMove = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/research`,
          { method: 'POST' },
        );
        [startingMoveDraft, startingMoveSetup] = await Promise.all([
          requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move/draft`),
          requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move/setup`),
        ]);
        renderChoice();
      } catch (error) {
        button.disabled = false;
        button.textContent = original;
        const note = card.querySelector('.activation-action-primary .note');
        if (note) note.textContent = error.message || 'Selected-channel research is unavailable.';
      }
    });

    $('channel-choice-draft')?.addEventListener('click', async () => {
      const button = $('channel-choice-draft');
      if (!button) return;
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Preparing review draft…';
      try {
        startingMoveDraft = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/draft`,
          { method: 'POST' },
        );
        startingMoveSetup = null;
        renderChoice();
      } catch (error) {
        button.disabled = false;
        button.textContent = original;
        const note = card.querySelector('.activation-action-primary .note');
        if (note) note.textContent = error.message || 'Could not prepare the review draft.';
      }
    });

    $('channel-choice-draft-save')?.addEventListener('click', async () => {
      const button = $('channel-choice-draft-save');
      const content = $('starting-move-draft-content');
      const title = $('starting-move-draft-title');
      if (!button || !content) return;
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Saving…';
      try {
        startingMoveDraft = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/draft`,
          {
            method: 'PATCH',
            body: JSON.stringify({ title: title?.value || null, content_text: content.value }),
          },
        );
        renderChoice();
      } catch (error) {
        button.disabled = false;
        button.textContent = original;
        const note = card.querySelector('.activation-action-primary .note');
        if (note) note.textContent = error.message || 'Could not save the review draft.';
      }
    });

    $('channel-choice-draft-accept')?.addEventListener('click', async () => {
      const button = $('channel-choice-draft-accept');
      if (!button) return;
      button.disabled = true;
      button.textContent = 'Accepting…';
      try {
        startingMoveDraft = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/draft/accept`,
          { method: 'POST' },
        );
        startingMoveSetup = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/setup`,
        );
        renderChoice();
      } catch (error) {
        button.disabled = false;
        button.textContent = 'Accept for next setup step →';
        const note = card.querySelector('.activation-action-primary .note');
        if (note) note.textContent = error.message || 'Could not accept the review draft.';
      }
    });

    $('channel-choice-draft-reject')?.addEventListener('click', async () => {
      const button = $('channel-choice-draft-reject');
      if (!button) return;
      button.disabled = true;
      button.textContent = 'Rejecting…';
      try {
        startingMoveDraft = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/draft/reject`,
          { method: 'POST' },
        );
        startingMoveSetup = null;
        renderChoice();
      } catch (error) {
        button.disabled = false;
        button.textContent = 'Reject draft';
        const note = card.querySelector('.activation-action-primary .note');
        if (note) note.textContent = error.message || 'Could not reject the review draft.';
      }
    });

    const openChannelControls = () => {
      document.querySelector('.tab-button[data-tab="channels"]')?.click();
    };
    $('channel-choice-controls')?.addEventListener('click', openChannelControls);
    $('starting-move-setup-controls')?.addEventListener('click', openChannelControls);
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
      [workspaceSnapshot, channelSnapshot, startingMove, startingMoveDraft, startingMoveSetup] = await Promise.all([
        requestJson(`/customer/workspace/${encodeURIComponent(projectId)}`),
        requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/channels`),
        requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move`),
        requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move/draft`),
        requestJson(`/customer/workspace/${encodeURIComponent(projectId)}/starting-move/setup`),
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