(() => {
  const button = document.getElementById('activation-primary');
  const note = document.getElementById('activation-note');
  const card = document.getElementById('activation-card');
  const heading = document.getElementById('activation-heading');
  const copy = document.getElementById('activation-copy');
  const channelTitle = document.getElementById('activation-channel-title');
  const channelCopy = document.getElementById('activation-channel-copy');
  if (!button || !note || !card || !heading || !copy || !channelTitle || !channelCopy) return;

  let pendingProposal = null;
  let pendingProjectId = null;
  let recommendationMode = null;
  let recommendedTargetUrl = null;
  let activeProjectId = null;
  let recommendationGeneration = 0;
  let busy = false;

  const projectId = () => activeProjectId || new URLSearchParams(window.location.search).get('project');
  const money = (value) => `$${Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;

  const requestJson = async (path, options = {}) => {
    const response = await fetch(path, {
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
    if (!response.ok) {
      const error = new Error(payload.detail || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  };

  const legacyPaidFundingAction = () => /^Set up this /.test(button.textContent || '');
  const ownsAction = () => (
    pendingProposal !== null
    || recommendationMode === 'paid'
    || recommendationMode === 'manual'
    || legacyPaidFundingAction()
  );

  const resetProposal = () => {
    pendingProposal = null;
    pendingProjectId = null;
  };

  const resetRecommendation = () => {
    resetProposal();
    recommendationMode = null;
    recommendedTargetUrl = null;
  };

  const failClosed = (message, label = 'Move not ready') => {
    resetProposal();
    note.textContent = message;
    button.textContent = label;
    button.disabled = false;
  };

  const renderRecommendation = (recommendation, currentProjectId) => {
    if (currentProjectId !== projectId() || card.classList.contains('hidden')) return;
    resetProposal();
    recommendedTargetUrl = recommendation.target_url || null;
    channelTitle.textContent = recommendation.opportunity_title
      ? `Do this next · ${recommendation.platform}`
      : 'Recommended first move';
    channelCopy.textContent = recommendation.signal_to_watch
      ? `${recommendation.recommended_action} Watch: ${recommendation.signal_to_watch}`
      : recommendation.recommended_action;

    if (recommendation.state !== 'ACTIONABLE') {
      recommendationMode = null;
      heading.textContent = 'Partizan is still finding the next actionable move.';
      copy.textContent = 'Research evidence stays separate from execution. Partizan will not ask for acquisition money until a current ranked action is ready.';
      note.textContent = recommendation.permission_text;
      return;
    }

    heading.textContent = 'Partizan picked the next useful move.';
    copy.textContent = 'This recommendation comes from the current ranked acquisition portfolio. It does not grant publishing or spend permission.';

    if (recommendation.action_type === 'PAID_CAMPAIGN') {
      recommendationMode = 'paid';
      note.textContent = (
        `Bounded test cap ${money(recommendation.required_acquisition_usd)}. `
        + `${money(recommendation.remaining_acquisition_capacity_usd)} of acquisition capacity is already available; `
        + `${money(recommendation.topup_amount_usd)} is the current exact shortfall. `
        + recommendation.permission_text
      );
      button.textContent = recommendation.funding_required
        ? 'Review exact funding need →'
        : 'Funding covered · approval separate';
      button.disabled = !recommendation.funding_required;
      return;
    }

    recommendationMode = 'manual';
    note.textContent = `Acquisition cash required to start: ${money(0)}. ${recommendation.permission_text}`;
    button.textContent = recommendedTargetUrl ? 'Open this move →' : 'Target not ready';
    button.disabled = !recommendedTargetUrl;
  };

  const loadRecommendation = async (currentProjectId) => {
    const generation = ++recommendationGeneration;
    activeProjectId = currentProjectId;
    resetRecommendation();
    const recommendation = await requestJson(
      `/customer/workspace/${encodeURIComponent(currentProjectId)}/recommended-action`,
    );
    if (generation !== recommendationGeneration || currentProjectId !== projectId()) return;
    renderRecommendation(recommendation, currentProjectId);
  };

  const reviewProposal = (proposal, currentProjectId) => {
    pendingProposal = proposal;
    pendingProjectId = currentProjectId;
    note.textContent = (
      `Concrete ${proposal.platform} paid test: cap ${money(proposal.required_acquisition_usd)}. `
      + `${money(proposal.remaining_acquisition_capacity_usd)} of acquisition capacity is already available; `
      + `only ${money(proposal.topup_amount_usd)} needs to be added. `
      + 'Funding does not start spend and execution still requires separate approval.'
    );
    button.textContent = `Add only ${money(proposal.topup_amount_usd)} →`;
    button.disabled = false;
  };

  const sufficientFunding = (proposal) => {
    resetProposal();
    note.textContent = (
      `Your current acquisition budget already covers this ${proposal.platform} test `
      + `(cap ${money(proposal.required_acquisition_usd)}). `
      + 'No additional top-up is required. Paid execution still requires separate approval.'
    );
    button.textContent = 'Funding covered · approval separate';
    button.disabled = true;
  };

  button.addEventListener('click', async (event) => {
    if (!ownsAction() || busy) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const currentProjectId = projectId();
    const originalLabel = button.textContent;
    if (!currentProjectId) {
      failClosed('Open a customer project before taking the recommended move.', originalLabel);
      return;
    }

    if (recommendationMode === 'manual') {
      if (!recommendedTargetUrl) {
        failClosed('The recommended manual move no longer has a concrete target.', originalLabel);
        return;
      }
      window.open(recommendedTargetUrl, '_blank', 'noopener,noreferrer');
      return;
    }

    if (pendingProposal && pendingProjectId !== currentProjectId) resetProposal();
    const resolvingProposal = pendingProposal === null;

    busy = true;
    button.disabled = true;
    try {
      if (pendingProposal) {
        button.textContent = 'Opening secure checkout…';
        const checkout = await requestJson(
          `/customer/workspace/${encodeURIComponent(currentProjectId)}/recommended-action/paid-proposals/${encodeURIComponent(pendingProposal.proposal_id)}/checkout`,
          { method: 'POST' },
        );
        if (checkout.checkout_url) {
          window.location.assign(checkout.checkout_url);
          return;
        }
        sufficientFunding(checkout);
        return;
      }

      button.textContent = 'Checking exact test budget…';
      const proposal = await requestJson(
        `/customer/workspace/${encodeURIComponent(currentProjectId)}/recommended-action/paid-proposal`,
        { method: 'POST' },
      );
      if (!proposal.funding_required) {
        sufficientFunding(proposal);
        return;
      }
      reviewProposal(proposal, currentProjectId);
    } catch (error) {
      if (error.status === 409 && resolvingProposal) {
        failClosed(
          'The current ranked move no longer needs paid funding. Refreshing the recommendation without charging anything.',
        );
        loadRecommendation(currentProjectId).catch(() => {});
      } else {
        failClosed(
          error.message || 'Could not prepare the exact paid-test funding amount.',
          originalLabel,
        );
        if (error.status === 409) loadRecommendation(currentProjectId).catch(() => {});
      }
    } finally {
      busy = false;
      if (button.textContent === 'Checking exact test budget…' || button.textContent === 'Opening secure checkout…') {
        button.textContent = originalLabel;
        button.disabled = false;
      }
    }
  }, true);

  window.addEventListener('partizan:workspace-ready', (event) => {
    const nextProjectId = event.detail && event.detail.projectId;
    if (!nextProjectId) return;
    if (activeProjectId && activeProjectId !== nextProjectId) recommendationGeneration += 1;
    loadRecommendation(nextProjectId).catch(() => {
      if (nextProjectId !== projectId()) return;
      resetRecommendation();
      note.textContent = 'Partizan could not refresh the ranked next action. The research view remains available and no acquisition funding was requested.';
    });
  });
})();
