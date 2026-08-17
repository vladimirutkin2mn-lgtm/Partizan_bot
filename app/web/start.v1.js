(() => {
  const TOKEN_PREFIX = 'partizan.customer.token.';
  const PROJECT_KEY = 'partizan.customer.project';
  const $ = (id) => document.getElementById(id);
  const stageInput = $('stage-input');
  const stagePreview = $('stage-preview');
  const stageUnlocked = $('stage-unlocked');
  const notice = $('notice');
  let currentProjectId = null;
  let currentToken = null;
  let metaOptions = null;

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
  const rememberProject = (projectId, token) => {
    currentProjectId = projectId;
    currentToken = token;
    localStorage.setItem(PROJECT_KEY, projectId);
    localStorage.setItem(tokenKey(projectId), token);
  };

  const loadProjectToken = (projectId) => {
    const token = localStorage.getItem(tokenKey(projectId));
    if (!token) return false;
    currentProjectId = projectId;
    currentToken = token;
    return true;
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

  const renderPreview = (data) => {
    $('preview-summary').textContent = `Partizan sees ${data.channel_count} promising acquisition directions and roughly ${data.opportunity_scope_estimate} concrete places, audiences and partners worth investigating. Best place to start: ${data.fastest_signal}.`;
    $('scope-title').textContent = `~${data.opportunity_scope_estimate} concrete opportunities to investigate`;
    $('unlock-price').textContent = `Unlock Acquisition Plan — $${data.launch_price_usd}`;
    $('autopilot-price').innerHTML = `$${data.autopilot_price_usd}<span>/mo</span>`;
    $('spend-fee').textContent = `+ ${data.managed_spend_fee_pct}% managed spend`;
    $('direction-grid').innerHTML = data.directions.map((item) => `
      <article class="direction-card"><header><h3>${escapeHtml(item.name)}</h3><span class="potential ${item.potential === 'MEDIUM' ? 'medium' : ''}">${item.potential}</span></header><p>${escapeHtml(item.rationale)}</p></article>
    `).join('');
    $('masked-list').innerHTML = data.masked_opportunities.map((item) => `
      <div class="masked-item"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.category)} · locked</span></div>
    `).join('');
    showStage(stagePreview);
  };

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

  const money = (value) => `$${Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

  $('preview-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    button.textContent = 'Running pre-scan…';
    try {
      const data = await api('/v1/customer-projects/preview', {
        method: 'POST',
        body: JSON.stringify({
          brief: $('brief').value.trim(),
          website_url: $('website').value.trim(),
          market: $('market').value.trim(),
          goal: $('goal').value,
          budget_usd: Number($('budget').value),
        }),
      });
      rememberProject(data.project_id, data.customer_token);
      renderPreview(data);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Run free pre-scan →';
    }
  });

  $('checkout-button').addEventListener('click', async () => {
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Run the pre-scan again.', true);
    const button = $('checkout-button');
    button.disabled = true;
    button.textContent = 'Opening secure checkout…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/checkout`, { method: 'POST' });
      if (data.already_unlocked) {
        showUnlocked();
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
    $('autopilot-card').classList.remove('hidden');
  };

  const pollEntitlement = async () => {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const project = await api(`/v1/customer-projects/${currentProjectId}`);
      if (project.launch_unlocked) {
        showUnlocked();
        if (project.research_state === 'READY') await startResearch();
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    showNotice('Payment received. Confirmation is taking a little longer than usual — refresh this page in a few seconds.', true);
  };

  $('research-button').addEventListener('click', () => startResearch());

  const startResearch = async () => {
    const button = $('research-button');
    button.disabled = true;
    $('research-progress').classList.remove('hidden');
    $('clarification-box').classList.add('hidden');
    try {
      const result = await api(`/v1/customer-projects/${currentProjectId}/deep-research`, { method: 'POST' });
      renderResearch(result);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
    } finally {
      $('research-progress').classList.add('hidden');
    }
  };

  const renderResearch = (result) => {
    if (result.state === 'NEEDS_INPUT') {
      $('research-button').disabled = true;
      const question = result.clarifications[0];
      const box = $('clarification-box');
      box.innerHTML = `<span class="eyebrow">One useful clarification</span><h3>${escapeHtml(question.question)}</h3><p>${escapeHtml(question.rationale)}</p><form id="clarification-form"><input id="clarification-answer" required placeholder="Your answer"><button class="button button-primary" type="submit">Continue research →</button></form>`;
      box.classList.remove('hidden');
      $('clarification-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const submit = event.submitter;
        submit.disabled = true;
        try {
          const next = await api(`/v1/customer-projects/${currentProjectId}/clarifications`, {
            method: 'POST',
            body: JSON.stringify({ question_id: question.question_id, answer: $('clarification-answer').value.trim() }),
          });
          renderResearch(next);
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
      <div class="results-head"><span class="eyebrow">Deep research ready</span><h2>Who to target first</h2><p>Your highest-value customer segments, ranked by fit and buying potential.</p></div>
      <div class="icp-grid">${result.icps.map((item) => `<article class="icp-card"><header><h3>${escapeHtml(item.title)}</h3><span class="score">${Math.round(item.score)}/100</span></header><p>${escapeHtml(item.description)}</p><div class="hook">${escapeHtml(item.message_hook)}</div></article>`).join('')}</div>
      <div class="results-head"><span class="eyebrow">Concrete distribution map</span><h2>Where to go next</h2><p>Named places, audiences and partners where Partizan sees the strongest acquisition signal.</p></div>
      <div class="opportunity-list">${result.opportunities.map((item) => `<article class="opp-card"><header><h3>${escapeHtml(item.title)}</h3><span class="platform">${escapeHtml(item.platform)} · ${escapeHtml(item.kind)}</span></header><p>${escapeHtml(item.rationale || 'Relevant distribution opportunity')}</p>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Open opportunity ↗</a>` : ''}</article>`).join('')}</div>`;
    results.classList.remove('hidden');
    $('autopilot-card').classList.remove('hidden');
    loadAutopilot().catch(() => {});
  };

  const loadAutopilot = async () => {
    if (!currentProjectId || !currentToken) return;
    const overview = await api(`/v1/customer-projects/${currentProjectId}/autopilot`);
    renderAutopilot(overview);
  };

  const renderAutopilot = (overview) => {
    const activeSubscription = overview.subscription_status === 'ACTIVE';
    $('autopilot-subscription-status').textContent = activeSubscription ? 'Subscription active' : overview.subscription_status.replaceAll('_', ' ').toLowerCase();
    $('autopilot-subscription-status').classList.toggle('active', activeSubscription);
    $('autopilot-subscribe-button').classList.toggle('hidden', activeSubscription);
    $('autopilot-setup').classList.toggle('hidden', !activeSubscription);
    $('autopilot-dashboard').classList.toggle('hidden', !activeSubscription);

    if (!activeSubscription) return;
    if (overview.marketing_budget_usd > 0) $('autopilot-budget').value = String(overview.marketing_budget_usd);
    $('meta-connection-status').textContent = overview.meta.connected
      ? `Connected: ad account ${overview.meta.ad_account_id}`
      : 'No Meta ad account connected.';
    $('meta-connect-button').textContent = overview.meta.connected ? 'Reconnect Meta' : 'Connect Meta →';

    $('metric-budget').textContent = money(overview.marketing_budget_usd);
    $('metric-spent').textContent = money(overview.spent_usd);
    $('metric-customers').textContent = String(overview.paid_customers);
    $('metric-cac').textContent = overview.cac_usd == null ? '—' : money(overview.cac_usd);
    $('metric-revenue').textContent = money(overview.revenue_usd);
    $('metric-roas').textContent = overview.roas == null ? '—' : `${Number(overview.roas).toFixed(2)}×`;
    $('autopilot-live-status').textContent = overview.setup_complete ? 'Autopilot active' : overview.autopilot_status.replaceAll('_', ' ').toLowerCase();
    $('autopilot-blockers').textContent = overview.blockers.length ? overview.blockers.join(' · ') : `Remaining delegated budget: ${money(overview.remaining_budget_usd)}.`;
    $('managed-fee-estimate').textContent = `Managed-spend fee (${overview.managed_spend_fee_pct}%): ${money(overview.estimated_managed_fee_usd)} based on recorded spend.`;

    const paused = overview.autopilot_status === 'PAUSED';
    $('autopilot-pause-button').classList.toggle('hidden', paused || overview.autopilot_status === 'NOT_CONFIGURED');
    $('autopilot-resume-button').classList.toggle('hidden', !paused);
    const experiments = [...overview.running_experiments, ...overview.waiting_experiments];
    $('autopilot-experiments').innerHTML = experiments.length
      ? experiments.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.platform)} · ${escapeHtml(item.action_type)}</strong><span>${escapeHtml(item.status)}${item.budget_cap == null ? '' : ` · ${money(item.budget_cap)}`}</span></div>`).join('')
      : '<div><strong>No experiment yet</strong><span>Partizan will create the first eligible test after setup is complete.</span></div>';
    $('autopilot-decisions').innerHTML = overview.recent_decisions.length
      ? overview.recent_decisions.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.decision || item.outcome)}</strong><span>${escapeHtml(item.reasons[0] || item.kind)}</span></div>`).join('')
      : '<div><strong>Waiting for first signal</strong><span>Launch and learning decisions will appear here.</span></div>';
  };

  $('autopilot-subscribe-button').addEventListener('click', async () => {
    const button = $('autopilot-subscribe-button');
    button.disabled = true;
    button.textContent = 'Opening secure checkout…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/autopilot/checkout`, { method: 'POST' });
      window.location.assign(data.checkout_url);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
      button.textContent = 'Start Autopilot →';
    }
  });

  $('autopilot-config-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      const overview = await api(`/v1/customer-projects/${currentProjectId}/autopilot`, {
        method: 'PUT',
        body: JSON.stringify({
          marketing_budget_usd: Number($('autopilot-budget').value),
          target_max_cac: Number($('autopilot-cac').value),
          confirm_autonomous_spend: $('autopilot-confirm-spend').checked,
        }),
      });
      renderAutopilot(overview);
      showNotice(overview.meta.connected ? 'Autopilot guardrails saved.' : 'Guardrails saved. Connect Meta to activate execution.');
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
      showNotice('Meta connected. Partizan can now execute inside your mandate.');
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

  const params = new URLSearchParams(window.location.search);
  const initialBudget = Number(params.get('budget'));
  if (Number.isFinite(initialBudget) && initialBudget >= 100) {
    $('budget').value = String(initialBudget);
    $('autopilot-budget').value = String(initialBudget);
  }
  const checkoutState = params.get('checkout');
  const projectId = params.get('project');
  const checkoutSessionId = params.get('session_id');
  if (checkoutState && projectId) {
    if (checkoutState === 'success') {
      const hadStoredAccess = loadProjectToken(projectId);
      showStage(stageUnlocked);
      $('payment-status').querySelector('strong').textContent = 'Confirming payment…';
      $('payment-status').querySelector('small').textContent = 'This usually takes only a few seconds.';
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
      loadProjectToken(projectId);
      showNotice('Checkout cancelled. Your pre-scan is still here whenever you’re ready.');
    }
  }

  const autopilotCheckout = params.get('autopilot_checkout');
  if (autopilotCheckout && projectId) {
    if (!loadProjectToken(projectId)) {
      showNotice('This browser no longer has access to the customer project.', true);
    } else {
      showUnlocked();
      if (autopilotCheckout === 'success' && checkoutSessionId) {
        api(`/v1/customer-projects/${projectId}/autopilot/verify`, {
          method: 'POST',
          body: JSON.stringify({ session_id: checkoutSessionId }),
        }).then((overview) => {
          renderAutopilot(overview);
          showNotice('Autopilot subscription active. Set the budget and connect Meta.');
        }).catch((error) => showNotice(error.message, true));
      } else if (autopilotCheckout === 'cancelled') {
        showNotice('Autopilot checkout cancelled. Your Acquisition Plan is unchanged.');
        loadAutopilot().catch(() => {});
      }
    }
  }

  const metaState = params.get('meta');
  if (metaState && projectId) {
    if (loadProjectToken(projectId)) {
      showUnlocked();
      api(`/v1/customer-projects/${projectId}`).then((project) => {
        if (project.research_state === 'READY') return startResearch();
        return null;
      }).then(() => loadAutopilot()).then(() => {
        if (metaState === 'connected') return loadMetaOptions();
        showNotice('Meta connection was not completed.', true);
        return null;
      }).catch((error) => showNotice(error.message, true));
    }
  }

  if (!checkoutState && !autopilotCheckout && !metaState) {
    const remembered = localStorage.getItem(PROJECT_KEY);
    if (remembered && loadProjectToken(remembered)) {
      api(`/v1/customer-projects/${remembered}`).then((project) => {
        if (!project.launch_unlocked) return;
        showUnlocked();
        if (project.research_state === 'READY') return startResearch();
        return null;
      }).catch(() => {});
    }
  }
})();