(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

  let projectId = new URLSearchParams(window.location.search).get('project');
  let loading = false;
  let refreshQueued = false;
  let submitting = false;

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
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      throw new Error((payload && (payload.detail || payload.message)) || `Request failed (${response.status})`);
    }
    return payload;
  };

  const ensureCard = () => {
    if ($('execution-request-card')) return $('execution-request-card');
    const activation = $('activation-card');
    if (!activation || !activation.parentElement) return null;
    const card = document.createElement('section');
    card.id = 'execution-request-card';
    card.className = 'panel activation-card hidden';
    activation.insertAdjacentElement('afterend', card);
    return card;
  };

  const requestedAt = (value) => {
    if (!value) return '';
    try {
      return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    } catch (_) {
      return '';
    }
  };

  const render = (draft, setup, executionRequest) => {
    const card = ensureCard();
    if (!card) return;
    const ready = Boolean(
      draft
      && draft.review_status === 'ACCEPTED'
      && setup
      && setup.platform === draft.platform
      && setup.state === 'READY_FOR_HANDOFF'
    );
    if (!ready) {
      card.classList.add('hidden');
      card.innerHTML = '';
      return;
    }

    card.classList.remove('hidden');
    if (executionRequest && executionRequest.status === 'REQUESTED') {
      const timestamp = requestedAt(executionRequest.requested_at);
      card.innerHTML = `
        <div class="activation-head">
          <div><span class="eyebrow">Accepted draft → preparation</span><h2>One action is queued for preparation.</h2></div>
          <span class="status-pill good">Requested</span>
        </div>
        <p class="section-copy">Partizan has a customer-requested handoff for ${escapeHtml(setup.channel_label)}. The request contains the accepted draft and source evidence for operator review.</p>
        <div class="activation-action activation-action-primary">
          <div><span class="eyebrow">Preparation request</span><strong>${escapeHtml(executionRequest.draft_title || executionRequest.source_title)}</strong></div>
          <p class="note">Nothing was approved, published or funded by this request. Final customer publish confirmation remains required.</p>
          ${timestamp ? `<p class="note">Requested ${escapeHtml(timestamp)}.</p>` : ''}
        </div>`;
      return;
    }

    card.innerHTML = `
      <div class="activation-head">
        <div><span class="eyebrow">Accepted draft → preparation</span><h2>Ask Partizan to prepare one action.</h2></div>
        <span class="status-pill">Explicit request</span>
      </div>
      <p class="section-copy">Your draft and channel setup are ready for handoff. Requesting preparation queues this exact accepted draft for operator review; it does not approve execution or authorize publishing, account access or spend.</p>
      <div class="activation-action activation-action-primary">
        <div><span class="eyebrow">Next separate step</span><button id="execution-request-submit" class="button button-primary" type="button">Request one prepared action →</button></div>
        <p id="execution-request-note" class="note">Only this click creates the request. Final customer publish confirmation remains separate.</p>
      </div>`;

    $('execution-request-submit')?.addEventListener('click', async () => {
      if (submitting || !projectId) return;
      const button = $('execution-request-submit');
      const note = $('execution-request-note');
      if (!button) return;
      submitting = true;
      button.disabled = true;
      button.textContent = 'Requesting preparation…';
      try {
        const created = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/execution-request`,
          { method: 'POST', body: JSON.stringify({ confirm_request: true }) },
        );
        render(draft, setup, created);
      } catch (error) {
        button.disabled = false;
        button.textContent = 'Request one prepared action →';
        if (note) note.textContent = error.message || 'Could not request preparation.';
      } finally {
        submitting = false;
      }
    });
  };

  const load = async (force = false) => {
    if (!syncProjectId(projectId)) return;
    ensureCard();
    if (loading) {
      if (force) refreshQueued = true;
      return;
    }
    loading = true;
    try {
      const encodedProject = encodeURIComponent(projectId);
      const [draft, setup, executionRequest] = await Promise.all([
        requestJson(`/customer/workspace/${encodedProject}/starting-move/draft`),
        requestJson(`/customer/workspace/${encodedProject}/starting-move/setup`),
        requestJson(`/customer/workspace/${encodedProject}/starting-move/execution-request`),
      ]);
      render(draft, setup, executionRequest);
    } catch (_) {
      const card = ensureCard();
      if (card) card.classList.add('hidden');
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

  window.addEventListener('partizan:workspace-state-updated', (event) => {
    const changedProjectId = event.detail && event.detail.projectId;
    if (!changedProjectId || changedProjectId === projectId) load(true).catch(() => {});
  });
})();
