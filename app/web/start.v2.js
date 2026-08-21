(() => {
  const TOKEN_PREFIX = 'partizan.customer.token.';
  const PREVIEW_PREFIX = 'partizan.customer.preview.';
  const PROJECT_KEY = 'partizan.customer.project';
  const $ = (id) => document.getElementById(id);
  const stageInput = $('stage-input');
  const stagePreview = $('stage-preview');
  const stageUnlocked = $('stage-unlocked');
  const notice = $('notice');
  let currentProjectId = null;
  let currentToken = null;
  let metaOptions = null;
  let managementFeePct = 10;

  const showNotice = (message, error = false) => {
    notice.textContent = message;
    notice.classList.toggle('error', error);
    notice.classList.remove('hidden');
    window.setTimeout(() => notice.classList.add('hidden'), 5200);
  };

  const api = async (path, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('Content-Type', 'application/json');
    if (currentToken) headers.set('X-Partizan-Customer-Token', currentToken);
    const response = await fetch(path, { ...options, headers });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  };

  const tokenKey = (projectId) => `${TOKEN_PREFIX}${projectId}`;
  const previewKey = (projectId) => `${PREVIEW_PREFIX}${projectId}`;

  const rememberProject = (projectId, token, preview = null) => {
    currentProjectId = projectId;
    currentToken = token;
    localStorage.setItem(PROJECT_KEY, projectId);
    localStorage.setItem(tokenKey(projectId), token);
    if (preview) localStorage.setItem(previewKey(projectId), JSON.stringify(preview));
  };

  const loadProjectToken = (projectId) => {
    const token = localStorage.getItem(tokenKey(projectId));
    if (!token) return false;
    currentProjectId = projectId;
    currentToken = token;
    return true;
  };

  const storedPreview = (projectId) => {
    const raw = localStorage.getItem(previewKey(projectId));
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (_) { return null; }
  };

  const clearStoredProject = (projectId) => {
    localStorage.removeItem(tokenKey(projectId));
    localStorage.removeItem(previewKey(projectId));
    if (localStorage.getItem(PROJECT_KEY) === projectId) localStorage.removeItem(PROJECT_KEY);
    if (currentProjectId === projectId) {
      currentProjectId = null;
      currentToken = null;
    }
  };

  const recoverProjectToken = async (projectId, sessionId) => {
    const previousProjectId = currentProjectId;
    const previousToken = currentToken;
    currentProjectId = projectId;
    currentToken = null;
    try {
      const recovered = await api(`/v1/customer-projects/${projectId}/recover-access`, {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId }),
      });
      rememberProject(projectId, recovered.customer_token);
      return recovered;
    } catch (error) {
      currentProjectId = previousProjectId;
      currentToken = previousToken;
      throw error;
    }
  };

  const showStage = (stage) => {
    [stageInput, stagePreview, stageUnlocked].forEach((node) => node.classList.add('hidden'));
    stage.classList.remove('hidden');
    $('progress-2').classList.toggle('on', stage !== stageInput);
    $('progress-3').classList.toggle('on', stage === stageUnlocked);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

  const money = (value) => `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;

  const updateGrowthBalanceBreakdown = () => {
    const amount = Number($('growth-balance-amount').value || 0);
    const box = $('growth-balance-breakdown');
    if (!Number.isFinite(amount) || amount <= 0) {
      box.innerHTML = '<strong>Enter an amount</strong>Partizan will show how much acquisition spend that all-in balance can support.';
      return;
    }
    const capacity = amount / (1 + (managementFeePct / 100));
    const feeAtFullUse = amount - capacity;
    box.innerHTML = `<strong>Up to ${money(capacity)} acquisition spend</strong>At full use, up to ${money(feeAtFullUse)} is the ${managementFeePct}% Partizan fee. If Partizan spends less, the unused money stays in Growth Balance.`;
  };

  const renderPreview = (data) => {
    managementFeePct = Number(data.managed_spend_fee_pct || 10);
    $('preview-summary').textContent = `The free scan sees ${data.channel_count} directions worth testing. Strongest starting hypothesis: ${data.fastest_signal}. This is not deep research yet; the paid research step verifies the audience and searches for the actual places, creators and partners.`;
    $('scope-title').textContent = `Deep research can investigate ~${data.opportunity_scope_estimate} concrete opportunities`;
    $('unlock-price').textContent = `Unlock Acquisition Plan — $${data.launch_price_usd}`;
    $('autopilot-direct-price').textContent = `${managementFeePct}% of acquisition spend`;
    $('autopilot-direct-fee').textContent = 'from Growth Balance · no monthly fee';
    $('spend-fee').textContent = `${managementFeePct}% of acquisition spend`;
    $('direction-grid').innerHTML = data.directions.map((item) => {
      const label = item.potential === 'HIGH' ? 'STRONG HYPOTHESIS' : 'WORTH TESTING';
      return `<article class="direction-card"><header><h3>${escapeHtml(item.name)}</h3><span class="potential ${item.potential === 'MEDIUM' ? 'medium' : ''}">${label}</span></header><p>${escapeHtml(item.rationale)}</p></article>`;
    }).join('');
    updateGrowthBalanceBreakdown();
    showStage(stagePreview);
  };

  $('preview-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    button.textContent = 'Running pre-scan…';
    try {
      const website = $('website').value.trim();
      const data = await api('/v1/customer-projects/preview', {
        method: 'POST',
        body: JSON.stringify({
          brief: $('brief').value.trim(),
          website_url: website || null,
          market: $('market').value.trim(),
          goal: $('goal').value,
          budget_usd: Number($('budget').value),
        }),
      });
      rememberProject(data.project_id, data.customer_token, data);
      renderPreview(data);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Run free pre-scan →';
    }
  });

  const openExecutionSetup = async () => {
    if (!currentProjectId || !currentToken) {
      showNotice('Project session is missing. Run the pre-scan again.', true);
      return;
    }
    $('autopilot-card').classList.remove('hidden');
    try {
      await loadAutopilot();
    } catch (error) {
      showNotice(error.message, true);
    }
    $('autopilot-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  $('autopilot-direct-button').addEventListener('click', openExecutionSetup);
  $('inspect-first-button').addEventListener('click', () => $('acquisition-plan-card').scrollIntoView({ behavior: 'smooth', block: 'start' }));

  $('checkout-button').addEventListener('click', async () => {
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Run the pre-scan again.', true);
    const button = $('checkout-button');
    button.disabled = true;
    button.textContent = 'Opening secure checkout…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/checkout`, { method: 'POST' });
      if (data.already_unlocked) {
        showUnlocked();
        await startResearch(true);
        return;
      }
      window.location.assign(data.checkout_url);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
      button.textContent = 'Unlock & research →';
    }
  });

  const showUnlocked = () => {
    showStage(stageUnlocked);
    $('research-button').disabled = false;
    $('autopilot-card').classList.remove('hidden');
  };

  const pollEntitlement = async () => {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const project = await api(`/v1/customer-projects/${currentProjectId}`);
      if (project.launch_unlocked) {
        showUnlocked();
        await startResearch(true);
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    showNotice('Payment received. Confirmation is taking a little longer than usual — refresh this page in a few seconds.', true);
  };

  $('research-button').addEventListener('click', () => startResearch(true));

  const startResearch = async (reveal = false) => {
    const button = $('research-button');
    button.disabled = true;
    $('research-progress').classList.remove('hidden');
    $('clarification-box').classList.add('hidden');
    try {
      const result = await api(`/v1/customer-projects/${currentProjectId}/deep-research`, { method: 'POST' });
      renderResearch(result, reveal);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
    } finally {
      $('research-progress').classList.add('hidden');
    }
  };

  const renderResearch = (result, reveal = false) => {
    if (result.state === 'NEEDS_INPUT') {
      $('research-button').disabled = true;
      const question = result.clarifications[0];
      const box = $('clarification-box');
      box.innerHTML = `<span class="eyebrow">One useful clarification</span><h3>${escapeHtml(question.question)}</h3><p>${escapeHtml(question.rationale)}</p><form id="clarification-form"><input id="clarification-answer" required placeholder="Your answer"><button class="button button-primary" type="submit">Continue →</button></form>`;
      box.classList.remove('hidden');
      $('payment-status').querySelector('strong').textContent = 'One detail needed';
      $('payment-status').querySelector('small').textContent = 'Answer once; Partizan will continue the research itself.';
      $('clarification-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const submit = event.submitter;
        submit.disabled = true;
        try {
          const next = await api(`/v1/customer-projects/${currentProjectId}/clarifications`, {
            method: 'POST',
            body: JSON.stringify({ question_id: question.question_id, answer: $('clarification-answer').value.trim() }),
          });
          renderResearch(next, reveal);
        } catch (error) {
          showNotice(error.message, true);
          submit.disabled = false;
        }
      });
      return;
    }

    $('clarification-box').classList.add('hidden');
    $('research-button').classList.add('hidden');
    const results = $('research-results');
    results.innerHTML = `
      <div class="results-head"><span class="eyebrow">Research ready</span><h2>Who Partizan will target first</h2><p>Your highest-value customer segments, ranked by fit and buying potential.</p></div>
      <div class="icp-grid">${result.icps.map((item) => `<article class="icp-card"><header><h3>${escapeHtml(item.title)}</h3><span class="score">${Math.round(item.score)}/100</span></header><p>${escapeHtml(item.description)}</p><div class="hook">${escapeHtml(item.message_hook)}</div></article>`).join('')}</div>
      <div class="results-head"><span class="eyebrow">Distribution map</span><h2>Where Partizan sees opportunity</h2><p>Named places, audiences and partners returned by the current deep-research engine.</p></div>
      <div class="opportunity-list">${result.opportunities.map((item) => `<article class="opp-card"><header><h3>${escapeHtml(item.title)}</h3><span class="platform">${escapeHtml(item.platform)} · ${escapeHtml(item.kind)}</span></header><p>${escapeHtml(item.rationale || 'Relevant distribution opportunity')}</p>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Open opportunity ↗</a>` : ''}</article>`).join('')}</div>`;
    results.classList.toggle('hidden', !reveal);
    const toggle = $('view-strategy-button');
    toggle.classList.remove('hidden');
    toggle.textContent = reveal ? 'Hide strategy & audience' : 'View strategy & audience';
    $('payment-status').querySelector('strong').textContent = 'Research mapped';
    $('payment-status').querySelector('small').textContent = 'Partizan has enough research to ask only for execution access it can actually use.';
    $('autopilot-card').classList.remove('hidden');
    loadAutopilot().catch(() => {});
  };

  $('view-strategy-button').addEventListener('click', () => {
    const results = $('research-results');
    if (!results.children.length) return;
    const opening = results.classList.contains('hidden');
    results.classList.toggle('hidden', !opening);
    $('view-strategy-button').textContent = opening ? 'Hide strategy & audience' : 'View strategy & audience';
    if (opening) results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  const ensureUnderHoodResearch = async () => {
    try {
      showUnlocked();
      $('payment-status').querySelector('strong').textContent = 'Growth Balance funded';
      $('payment-status').querySelector('small').textContent = 'Deep research is starting now. Paid execution remains subject to its launch prerequisites.';
      await startResearch(false);
    } catch (error) {
      showNotice(error.message, true);
    }
  };

  const loadAutopilot = async () => {
    if (!currentProjectId || !currentToken) return;
    const overview = await api(`/v1/customer-projects/${currentProjectId}/autopilot`);
    renderAutopilot(overview);
  };

  const renderFriendlyBlockers = (overview, researchReady) => {
    const balance = overview.growth_balance;
    const items = [];
    if (!researchReady) {
      items.push(balance.funded_usd > 0 ? 'Partizan is mapping the audience and acquisition strategy.' : 'Fund Growth Balance to start the included deep research.');
      return items.join(' · ');
    }
    if (overview.blockers.some((item) => item.includes('Website or landing page'))) items.push('Add a live website or landing page before Partizan sends paid traffic.');
    if (balance.funded_usd <= 0) items.push('Growth Balance is not funded.');
    if (!balance.settlement_ready) items.push('Paid experiments are paused until Partizan’s ad-spend rail is ready.');
    if (overview.blockers.some((item) => item.includes('guardrails'))) items.push('Save the maximum cost per customer.');
    if (!overview.meta.connected) items.push('Connect Meta only if you want Partizan to run an eligible Meta paid test.');
    if (!items.length) items.push(`Growth Balance available: ${money(balance.available_usd)}.`);
    return items.join(' · ');
  };

  const renderAutopilot = (overview) => {
    const researching = overview.autopilot_status === 'RESEARCHING';
    const balance = overview.growth_balance;
    const researchReady = Boolean(overview.product_id);
    managementFeePct = Number(balance.management_fee_pct || managementFeePct);
    $('spend-fee').textContent = `${managementFeePct}% of acquisition spend`;
    updateGrowthBalanceBreakdown();
    $('autopilot-setup').classList.remove('hidden');
    $('execution-access-step').classList.toggle('hidden', !researchReady);
    $('autopilot-dashboard').classList.toggle('hidden', researching);

    if (overview.meta.connected) {
      $('meta-connection-status').textContent = `Connected: ad account ${overview.meta.ad_account_id}`;
      $('meta-connect-button').textContent = 'Reconnect Meta';
      $('meta-connect-button').disabled = !researchReady;
    } else if (!researchReady) {
      $('meta-connection-status').textContent = 'Partizan researches first. Access appears after the research is ready.';
      $('meta-connect-button').textContent = 'Connect Meta →';
      $('meta-connect-button').disabled = true;
    } else {
      $('meta-connection-status').textContent = 'Research is ready. Connect only if Meta execution is useful.';
      $('meta-connect-button').textContent = 'Connect Meta →';
      $('meta-connect-button').disabled = false;
    }

    const checkoutUnavailable = balance.settlement_status === 'STRIPE_NOT_CONFIGURED';
    if (balance.funded_usd > 0) {
      $('growth-balance-status').textContent = `${money(balance.funded_usd)} funded · ${money(balance.remaining_acquisition_capacity_usd)} acquisition capacity remains.${balance.settlement_ready ? ' Paid execution rail is ready.' : ' Research is available now; paid experiments remain paused until the ad-spend rail is ready.'}`;
    } else if (checkoutUnavailable) {
      $('growth-balance-status').textContent = 'Secure funding is temporarily unavailable because Stripe Checkout is not configured.';
    } else if (!balance.settlement_ready) {
      $('growth-balance-status').textContent = 'Fund securely with Stripe. Funding unlocks deep research now; paid experiments stay paused until Partizan’s ad-spend rail is ready.';
    } else {
      $('growth-balance-status').textContent = 'Ready to fund. Growth Balance is the hard money boundary for autonomous paid experiments.';
    }
    $('growth-balance-status').classList.toggle('settlement-warning', checkoutUnavailable || (balance.funded_usd > 0 && !balance.settlement_ready));
    $('growth-balance-button').disabled = checkoutUnavailable;
    $('growth-balance-button').textContent = checkoutUnavailable ? 'Stripe Checkout unavailable' : (balance.funded_usd > 0 ? 'Add to Growth Balance →' : 'Fund Growth Balance →');
    $('autopilot-config-button').disabled = false;

    if (researching) return;
    $('metric-balance').textContent = money(balance.available_usd);
    $('metric-spent').textContent = money(balance.acquisition_spend_usd);
    $('metric-fee').textContent = money(balance.management_fee_usd);
    $('metric-customers').textContent = String(overview.paid_customers);
    $('metric-cac').textContent = overview.cac_usd == null ? '—' : money(overview.cac_usd);
    $('metric-revenue').textContent = money(overview.revenue_usd);
    $('autopilot-live-status').textContent = overview.setup_complete ? 'Autopilot active' : 'Waiting on launch prerequisites';
    $('autopilot-blockers').textContent = renderFriendlyBlockers(overview, researchReady);

    const paused = overview.autopilot_status === 'PAUSED';
    $('autopilot-pause-button').classList.toggle('hidden', paused || overview.autopilot_status === 'NOT_CONFIGURED');
    $('autopilot-resume-button').classList.toggle('hidden', !paused);
    const experiments = [...overview.running_experiments, ...overview.waiting_experiments];
    $('autopilot-experiments').innerHTML = experiments.length
      ? experiments.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.platform)} · ${escapeHtml(item.action_type)}</strong><span>${escapeHtml(item.status)}${item.budget_cap == null ? '' : ` · ${money(item.budget_cap)}`}</span></div>`).join('')
      : '<div><strong>No experiment yet</strong><span>Partizan will create the first eligible test after research and launch prerequisites are complete.</span></div>';
    $('autopilot-decisions').innerHTML = overview.recent_decisions.length
      ? overview.recent_decisions.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.decision || item.outcome)}</strong><span>${escapeHtml(item.reasons[0] || item.kind)}</span></div>`).join('')
      : '<div><strong>Waiting for first signal</strong><span>Launch and learning decisions will appear here.</span></div>';
  };

  $('growth-balance-amount').addEventListener('input', updateGrowthBalanceBreakdown);
  $('growth-balance-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Run the pre-scan again.', true);
    const button = $('growth-balance-button');
    button.disabled = true;
    const original = button.textContent;
    button.textContent = 'Opening secure checkout…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/growth-balance/checkout`, {
        method: 'POST',
        body: JSON.stringify({ amount_usd: Number($('growth-balance-amount').value) }),
      });
      window.location.assign(data.checkout_url);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
      button.textContent = original;
    }
  });

  $('autopilot-config-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Run the pre-scan again.', true);
    const button = event.submitter;
    button.disabled = true;
    try {
      const overview = await api(`/v1/customer-projects/${currentProjectId}/autopilot`, {
        method: 'PUT',
        body: JSON.stringify({
          target_max_cac: Number($('autopilot-cac').value),
          confirm_autonomous_spend: $('autopilot-confirm-spend').checked,
        }),
      });
      renderAutopilot(overview);
      showNotice('Limits saved. Partizan will apply them to eligible paid experiments.');
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $('meta-connect-button').addEventListener('click', async () => {
    const button = $('meta-connect-button');
    button.disabled = true;
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/autopilot/meta/connect`, { method: 'POST' });
      window.location.assign(data.authorization_url);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
    }
  });

  const loadMetaOptions = async () => {
    metaOptions = await api(`/v1/customer-projects/${currentProjectId}/autopilot/meta/options`);
    if (!metaOptions.ad_accounts.length) {
      showNotice('Meta connected, but no manageable ad accounts were returned.', true);
      return;
    }
    $('meta-account').innerHTML = metaOptions.ad_accounts.map((item) => `<option value="${escapeHtml(item.account_id)}">${escapeHtml(item.name)}${item.currency ? ` · ${escapeHtml(item.currency)}` : ''}</option>`).join('');
    renderMetaPages();
    $('meta-options-form').classList.remove('hidden');
  };

  const renderMetaPages = () => {
    if (!metaOptions) return;
    const accountId = $('meta-account').value;
    const pages = metaOptions.pages_by_ad_account[accountId] || [];
    $('meta-page').innerHTML = pages.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('');
  };
  $('meta-account').addEventListener('change', renderMetaPages);

  $('meta-options-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      const overview = await api(`/v1/customer-projects/${currentProjectId}/autopilot/meta/connection`, {
        method: 'POST',
        body: JSON.stringify({
          ad_account_id: $('meta-account').value,
          page_id: $('meta-page').value,
          country_codes: [$('meta-country').value.trim().toUpperCase()],
        }),
      });
      $('meta-options-form').classList.add('hidden');
      renderAutopilot(overview);
      showNotice('Meta connected. The account stays yours; Partizan now has scoped execution access.');
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  const setAutopilotStatus = async (statusValue) => {
    try {
      const overview = await api(`/v1/customer-projects/${currentProjectId}/autopilot/status`, {
        method: 'POST',
        body: JSON.stringify({ status: statusValue }),
      });
      renderAutopilot(overview);
      showNotice(statusValue === 'PAUSED' ? 'Autopilot paused.' : 'Autopilot resumed.');
    } catch (error) {
      showNotice(error.message, true);
    }
  };
  $('autopilot-pause-button').addEventListener('click', () => setAutopilotStatus('PAUSED'));
  $('autopilot-resume-button').addEventListener('click', () => setAutopilotStatus('ACTIVE'));

  const resumeStoredProject = async () => {
    const storedProjectId = localStorage.getItem(PROJECT_KEY);
    if (!storedProjectId || !loadProjectToken(storedProjectId)) return;
    try {
      const project = await api(`/v1/customer-projects/${storedProjectId}`);
      managementFeePct = Number(project.managed_spend_fee_pct || managementFeePct);
      $('growth-balance-amount').value = String(project.budget_usd || $('growth-balance-amount').value);
      updateGrowthBalanceBreakdown();
      if (project.launch_unlocked) {
        showUnlocked();
        await startResearch(false);
        await loadAutopilot();
        showNotice('Welcome back. Your Partizan project is restored.');
        return;
      }
      const preview = storedPreview(storedProjectId);
      if (preview) {
        renderPreview(preview);
        showNotice('Welcome back. Your acquisition hypotheses are restored.');
        return;
      }
      $('brief').value = project.brief || '';
      $('website').value = project.website_url || '';
      $('market').value = project.market || 'United States';
      $('goal').value = project.goal || 'Get paying customers';
      $('budget').value = String(project.budget_usd || 1000);
      showNotice('Welcome back. Your project details are restored; run the free scan to refresh the hypotheses.');
    } catch (_) {
      clearStoredProject(storedProjectId);
    }
  };

  const params = new URLSearchParams(window.location.search);
  const initialBudget = Number(params.get('budget'));
  if (Number.isFinite(initialBudget) && initialBudget >= 1) {
    $('budget').value = String(initialBudget);
    $('growth-balance-amount').value = String(initialBudget);
    updateGrowthBalanceBreakdown();
  }

  const checkoutState = params.get('checkout');
  const projectId = params.get('project');
  const checkoutSessionId = params.get('session_id');
  if (checkoutState && projectId) {
    if (checkoutState === 'success') {
      const hadStoredAccess = loadProjectToken(projectId);
      showUnlocked();
      $('payment-status').querySelector('strong').textContent = 'Confirming payment…';
      $('research-button').disabled = true;
      const resumePurchase = async () => {
        if (checkoutSessionId) {
          try {
            await recoverProjectToken(projectId, checkoutSessionId);
          } catch (error) {
            if (!hadStoredAccess || !loadProjectToken(projectId)) throw error;
          }
        } else if (!hadStoredAccess) {
          throw new Error('Purchase session is missing. Open the successful Stripe return link again.');
        }
        await pollEntitlement();
      };
      resumePurchase().catch((error) => showNotice(error.message, true));
    } else if (checkoutState === 'cancelled') {
      if (loadProjectToken(projectId)) {
        const preview = storedPreview(projectId);
        if (preview) renderPreview(preview);
      }
      showNotice('Checkout cancelled. Your pre-scan is still here whenever you’re ready.');
    }
  }

  const growthBalanceState = params.get('growth_balance');
  if (growthBalanceState && projectId) {
    if (!loadProjectToken(projectId)) {
      showNotice('This browser no longer has access to the customer project.', true);
    } else {
      showUnlocked();
      if (growthBalanceState === 'success' && checkoutSessionId) {
        api(`/v1/customer-projects/${projectId}/growth-balance/verify`, {
          method: 'POST',
          body: JSON.stringify({ session_id: checkoutSessionId }),
        }).then(async (overview) => {
          renderAutopilot(overview);
          showNotice(`Growth Balance funded. Available: ${money(overview.growth_balance.available_usd)}.`);
          await ensureUnderHoodResearch();
        }).catch((error) => showNotice(error.message, true));
      } else if (growthBalanceState === 'cancelled') {
        showNotice('Growth Balance checkout cancelled. No funds were added.');
        loadAutopilot().catch(() => {});
      }
    }
  }

  const metaState = params.get('meta');
  if (metaState && projectId) {
    if (!loadProjectToken(projectId)) {
      showNotice('This browser no longer has access to the customer project.', true);
    } else {
      showUnlocked();
      if (metaState === 'connected') {
        loadAutopilot().then(() => loadMetaOptions()).catch((error) => showNotice(error.message, true));
      } else if (metaState === 'error') {
        showNotice('Meta connection was not completed.', true);
      }
    }
  }

  const callbackActive = Boolean(checkoutState || growthBalanceState || metaState);
  if (!callbackActive) resumeStoredProject().catch(() => {});
})();
