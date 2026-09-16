(() => {
  const button = document.getElementById('activation-primary');
  const note = document.getElementById('activation-note');
  if (!button || !note) return;

  let pendingProposal = null;
  let pendingProjectId = null;
  let busy = false;

  const projectId = () => new URLSearchParams(window.location.search).get('project');
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
  const ownsAction = () => pendingProposal !== null || legacyPaidFundingAction();

  const resetProposal = () => {
    pendingProposal = null;
    pendingProjectId = null;
  };

  const failClosed = (message, label = 'Paid move not ready') => {
    resetProposal();
    note.textContent = message;
    button.textContent = label;
    button.disabled = false;
  };

  const reviewProposal = (proposal, currentProjectId) => {
    pendingProposal = proposal;
    pendingProjectId = currentProjectId;
    note.textContent = (
      `Concrete ${proposal.platform} paid test: cap ${money(proposal.required_acquisition_usd)}. `
      + `${money(proposal.remaining_acquisition_capacity_usd)} of acquisition capacity is already available; `
      + `only ${money(proposal.topup_amount_usd)} needs to be added. `
      + 'Funding does not start spend.'
    );
    button.textContent = `Add only ${money(proposal.topup_amount_usd)} →`;
    button.disabled = false;
  };

  const sufficientFunding = (proposal) => {
    resetProposal();
    note.textContent = (
      `Your current acquisition budget already covers this ${proposal.platform} test `
      + `(cap ${money(proposal.required_acquisition_usd)}). `
      + 'No additional top-up is required and funding alone does not start spend.'
    );
    button.textContent = 'Acquisition budget already sufficient';
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
      failClosed('Open a customer project before funding a paid move.', originalLabel);
      return;
    }

    if (pendingProposal && pendingProjectId !== currentProjectId) resetProposal();

    busy = true;
    button.disabled = true;
    try {
      if (pendingProposal) {
        button.textContent = 'Opening secure checkout…';
        const checkout = await requestJson(
          `/customer/workspace/${encodeURIComponent(currentProjectId)}/growth-balance/paid-proposal/${encodeURIComponent(pendingProposal.proposal_id)}/checkout`,
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
        `/customer/workspace/${encodeURIComponent(currentProjectId)}/growth-balance/paid-proposal`,
      );
      if (!proposal.funding_required) {
        sufficientFunding(proposal);
        return;
      }
      reviewProposal(proposal, currentProjectId);
    } catch (error) {
      if (error.status === 409) {
        failClosed(
          'No executable paid move is ready yet. Partizan will not request acquisition funding from a research-only preview.',
        );
      } else {
        failClosed(
          error.message || 'Could not prepare the exact paid-test funding amount.',
          originalLabel,
        );
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
    if (pendingProjectId && nextProjectId && pendingProjectId !== nextProjectId) resetProposal();
  });
})();
