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

  const renderPrepared = (draft, setup, executionRequest, preparedAction) => {
    const card = ensureCard();
    if (!card || !preparedAction) return;
    const confirmed = Boolean(
      (executionRequest.status === 'PUBLISH_CONFIRMED' || executionRequest.status === 'OPERATOR_APPROVED')
      && preparedAction.customer_publish_confirmed
    );
    const executed = Boolean(
      preparedAction.action_status === 'EXECUTED'
      || preparedAction.published === true
    );
    const approved = Boolean(
      executed
      || executionRequest.status === 'OPERATOR_APPROVED'
      || preparedAction.action_status === 'APPROVED'
      || preparedAction.operator_approval_required === false
    );
    const confirmationTime = requestedAt(preparedAction.customer_publish_confirmed_at);
    const approvalTime = requestedAt(preparedAction.operator_approved_at || executionRequest.operator_approved_at);
    const title = preparedAction.draft_title
      ? `<div><span class="eyebrow">Title</span><strong>${escapeHtml(preparedAction.draft_title)}</strong></div>`
      : '';
    const creative = preparedAction.creative_asset_url
      ? `<div>
           <span class="eyebrow">Exact video</span>
           <video controls preload="metadata" src="${escapeHtml(preparedAction.creative_asset_url)}"></video>
           <p class="note"><a href="${escapeHtml(preparedAction.creative_asset_url)}" target="_blank" rel="noopener">Open the exact video →</a></p>
         </div>`
      : '';
    const headline = executed
      ? 'This exact action has been executed.'
      : approved
        ? 'Operator approved this exact action.'
        : confirmed
          ? 'You confirmed this exact action.'
          : 'Review the exact action before confirming.';
    const status = executed ? 'Executed' : approved ? 'Operator approved' : confirmed ? 'Confirmed' : 'Needs confirmation';
    const stateNote = executed
      ? 'The exact action was executed after your confirmation and separate operator approval. This customer view remains read-only.'
      : approved
        ? 'The exact action is APPROVED for the separate execution step. Nothing has been published or funded yet.'
        : 'The action is still PREPARED, operator approval is still required, and nothing has been published or funded.';

    card.innerHTML = `
      <div class="activation-head">
        <div><span class="eyebrow">Prepared action → exact customer review</span><h2>${headline}</h2></div>
        <span class="status-pill ${(confirmed || approved || executed) ? 'good' : ''}">${status}</span>
      </div>
      <p class="section-copy">This is the exact ${escapeHtml(setup.channel_label)} action prepared from your accepted draft. Confirming records your approval of this exact target, copy${preparedAction.creative_asset_url ? ' and video' : ''} for the separate operator approval step; it does not publish anything.</p>
      <div class="activation-action activation-action-primary">
        <div><span class="eyebrow">Source evidence</span><a href="${escapeHtml(preparedAction.source_url)}" target="_blank" rel="noopener">${escapeHtml(preparedAction.source_title)}</a></div>
        <div><span class="eyebrow">Exact target</span><a href="${escapeHtml(preparedAction.target_url)}" target="_blank" rel="noopener">${escapeHtml(preparedAction.target_url)}</a></div>
        ${title}
        <div><span class="eyebrow">Context</span><p>${escapeHtml(preparedAction.context_text)}</p></div>
        <div><span class="eyebrow">Exact content</span><p>${escapeHtml(preparedAction.content_text)}</p></div>
        ${creative}
        <p class="note">${stateNote}</p>
        ${confirmed
          ? `<p class="note">Confirmed${confirmationTime ? ` ${escapeHtml(confirmationTime)}` : ''}. Any changed copy or confirmed video must return through a new customer review.</p>
             ${approved ? `<p class="note">Operator approval recorded${approvalTime ? ` ${escapeHtml(approvalTime)}` : ''}. ${executed ? 'Execution status is shown read-only above.' : 'Execution remains a separate protected step.'}</p>` : ''}`
          : `<div><button id="execution-confirm-submit" class="button button-primary" type="button">Confirm this exact action →</button></div>
             <p id="execution-confirm-note" class="note">Only this click records confirmation. It does not call approve, execute or publishing endpoints.</p>`}
      </div>`;

    if (confirmed) return;
    $('execution-confirm-submit')?.addEventListener('click', async () => {
      if (submitting || !projectId) return;
      const button = $('execution-confirm-submit');
      const note = $('execution-confirm-note');
      if (!button) return;
      submitting = true;
      button.disabled = true;
      button.textContent = 'Confirming exact action…';
      try {
        const confirmedAction = await requestJson(
          `/customer/workspace/${encodeURIComponent(projectId)}/starting-move/execution-request/confirmation`,
          {
            method: 'POST',
            body: JSON.stringify({
              confirm_publish: true,
              creative_asset_id: preparedAction.creative_asset_id || null,
            }),
          },
        );
        renderPrepared(
          draft,
          setup,
          { ...executionRequest, status: 'PUBLISH_CONFIRMED' },
          confirmedAction,
        );
      } catch (error) {
        button.disabled = false;
        button.textContent = 'Confirm this exact action →';
        if (note) note.textContent = error.message || 'Could not confirm this exact action.';
      } finally {
        submitting = false;
      }
    });
  };

  const render = (draft, setup, executionRequest, preparedAction = null) => {
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
    if (
      executionRequest
      && ['ACTION_PREPARED', 'PUBLISH_CONFIRMED', 'OPERATOR_APPROVED'].includes(executionRequest.status)
    ) {
      renderPrepared(draft, setup, executionRequest, preparedAction);
      return;
    }

    if (
      executionRequest
      && ['REQUESTED', 'PREPARATION_READY'].includes(executionRequest.status)
    ) {
      const timestamp = requestedAt(executionRequest.requested_at);
      const linked = executionRequest.status === 'PREPARATION_READY';
      card.innerHTML = `
        <div class="activation-head">
          <div><span class="eyebrow">Accepted draft → preparation</span><h2>One action is queued for preparation.</h2></div>
          <span class="status-pill good">${linked ? 'Preparation ready' : 'Requested'}</span>
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
      let preparedAction = null;
      if (
        executionRequest
        && ['ACTION_PREPARED', 'PUBLISH_CONFIRMED', 'OPERATOR_APPROVED'].includes(executionRequest.status)
      ) {
        preparedAction = await requestJson(
          `/customer/workspace/${encodedProject}/starting-move/execution-request/prepared-action`,
        );
      }
      render(draft, setup, executionRequest, preparedAction);
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
