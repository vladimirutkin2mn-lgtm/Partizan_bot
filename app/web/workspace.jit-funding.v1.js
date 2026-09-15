(() => {
  const PANEL_ID = 'jit-funding-panel';

  const money = (value) => `$${Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      cache: 'no-store',
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

  const fundingPath = (projectId, experimentId) => (
    `/customer/workspace/${projectId}/growth-balance/experiments/${experimentId}/funding`
  );

  const removePanel = () => {
    const existing = document.getElementById(PANEL_ID);
    if (existing) existing.remove();
  };

  const findFundingPlan = async (projectId, waitingExperiments) => {
    const candidates = (waitingExperiments || []).filter((item) => (
      item.action_type === 'PAID_CAMPAIGN'
      && item.budget_cap != null
      && item.experiment_id
    ));
    for (const candidate of candidates.slice(0, 4)) {
      try {
        const plan = await api(fundingPath(projectId, candidate.experiment_id));
        return { candidate, plan };
      } catch (error) {
        if (error.status !== 409) throw error;
      }
    }
    return null;
  };

  const renderPlan = (projectId, candidate, plan) => {
    const host = document.getElementById('experiments');
    if (!host) return;
    removePanel();

    const panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.dataset.experimentId = String(plan.experiment_id || candidate.experiment_id);

    const title = document.createElement('strong');
    title.textContent = `${plan.platform || candidate.platform} · paid test`;
    panel.appendChild(title);

    const detail = document.createElement('span');
    if (plan.funding_required) {
      detail.textContent = (
        `${money(plan.required_acquisition_usd)} test · `
        + `${money(plan.remaining_acquisition_capacity_usd)} already available · `
        + `add only ${money(plan.topup_amount_usd)} including the managed-spend fee.`
      );
    } else {
      detail.textContent = (
        `${money(plan.required_acquisition_usd)} test is already covered by the current `
        + 'Growth Balance. No additional funding is needed.'
      );
    }
    panel.appendChild(detail);

    if (plan.funding_required) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'button button-primary';
      button.textContent = `Add only ${money(plan.topup_amount_usd)} for this test →`;
      button.style.marginTop = '10px';

      const errorText = document.createElement('span');
      errorText.setAttribute('role', 'status');
      errorText.className = 'jit-funding-error hidden';
      panel.appendChild(button);
      panel.appendChild(errorText);

      button.addEventListener('click', async () => {
        button.disabled = true;
        errorText.classList.add('hidden');
        try {
          const result = await api(`${fundingPath(projectId, candidate.experiment_id)}/checkout`, {
            method: 'POST',
          });
          if (result.checkout_url) {
            window.location.assign(result.checkout_url);
            return;
          }
          button.remove();
          detail.textContent = 'This paid test is already funded. No additional top-up is needed.';
        } catch (error) {
          errorText.textContent = error.message;
          errorText.classList.remove('hidden');
          button.disabled = false;
        }
      });
    }

    host.prepend(panel);
  };

  const refresh = async (projectId) => {
    if (!projectId) return;
    removePanel();
    try {
      const workspace = await api(`/customer/workspace/${projectId}`);
      const waiting = workspace.autopilot?.waiting_experiments || [];
      const resolved = await findFundingPlan(projectId, waiting);
      if (!resolved) return;
      renderPlan(projectId, resolved.candidate, resolved.plan);
    } catch (_) {
      // JIT funding is progressive enhancement. The base workspace remains usable.
    }
  };

  window.addEventListener('partizan:workspace-ready', (event) => {
    void refresh(event.detail?.projectId);
  });
})();
