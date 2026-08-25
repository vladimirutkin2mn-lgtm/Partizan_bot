(() => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(window.location.search);
  let account = null;
  let projectId = params.get('project');
  let workspace = null;
  let metaOptions = null;
  let lastResearchResult = null;
  let activeTab = 'overview';

  const api = async (path, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (options.body != null) headers.set('Content-Type', 'application/json');
    const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload.detail || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  };

  const showNotice = (message, error = false) => {
    const node = $('notice');
    node.textContent = message;
    node.classList.toggle('error', error);
    node.classList.remove('hidden');
    window.setTimeout(() => node.classList.add('hidden'), 5200);
  };

  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);
  const money = (value) => `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
  const roas = (value) => value == null ? '—' : `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}×`;

  const setActiveTab = (name) => {
    activeTab = name;
    document.querySelectorAll('.tab-button').forEach((button) => {
      button.classList.toggle('active', button.dataset.tab === name);
    });
    document.querySelectorAll('[data-tab-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.tabPanel !== name);
    });
  };

  document.querySelectorAll('.tab-button').forEach((button) => {
    button.addEventListener('click', () => setActiveTab(button.dataset.tab));
  });
  document.querySelectorAll('[data-open-tab]').forEach((button) => {
    button.addEventListener('click', () => setActiveTab(button.dataset.openTab));
  });

  const friendlyBlockers = (overview) => {
    const items = [];
    if (overview.blockers.some((item) => item.includes('No autonomous execution channel'))) items.push('Choose an Auto channel when you want Partizan to execute, or keep every channel in Research only.');
    if (overview.growth_balance.funded_usd <= 0) items.push('Fund Growth Balance to start the included research.');
    if (overview.blockers.some((item) => item.includes('Website or landing page'))) items.push('Add a live website or landing page before paid traffic starts.');
    if (overview.blockers.some((item) => item.includes('guardrails'))) items.push('Save the maximum cost per customer in Settings.');
    if (overview.blockers.some((item) => item.includes('Meta access'))) items.push('Connect Meta in Settings for Auto Instagram & Facebook execution.');
    if (overview.growth_balance.funded_usd > 0 && !overview.growth_balance.settlement_ready) items.push('Paid spend is paused until Partizan’s ad-spend rail is ready. Research and planning remain available.');
    return items.join(' ');
  };

  const statusText = (overview) => {
    if (overview.autopilot_status === 'RESEARCHING') return 'Partizan is mapping your market.';
    if (overview.autopilot_status === 'ACTIVE') return 'Partizan is working on getting you customers.';
    if (overview.autopilot_status === 'PAUSED') return 'Partizan is paused.';
    if (overview.product_id) return 'Partizan is ready for the next acquisition step.';
    return 'Fund the learning loop to start.';
  };

  const renderAccountNav = () => {
    $('account-email').textContent = account.email;
    $('account-nav').classList.remove('hidden');
    const switcher = $('project-switcher');
    switcher.innerHTML = account.projects.map((item) => {
      const label = `${item.market} · ${item.goal}`;
      const compact = label.length > 54 ? `${label.slice(0, 54)}…` : label;
      return `<option value="${escapeHtml(item.project_id)}">${escapeHtml(compact)}</option>`;
    }).join('');
    switcher.value = projectId || '';
    switcher.classList.toggle('hidden', account.projects.length < 2);
  };

  const showLoginGate = (message = '') => {
    $('loading').classList.add('hidden');
    $('workspace').classList.add('hidden');
    $('account-nav').classList.add('hidden');
    $('login-gate').classList.remove('hidden');
    if (message) showNotice(message, true);
  };

  const hideLoginGate = () => {
    $('login-gate').classList.add('hidden');
    $('loading').classList.remove('hidden');
  };

  const renderActivity = (overview) => {
    const work = [];
    overview.running_experiments.slice(0, 4).forEach((item) => work.push({
      title: `${item.platform} · ${item.action_type}`,
      detail: `${item.status}${item.budget_cap == null ? '' : ` · cap ${money(item.budget_cap)}`}`,
    }));
    overview.waiting_experiments.slice(0, 4).forEach((item) => work.push({
      title: `${item.platform} · ${item.action_type}`,
      detail: `${item.status}${item.budget_cap == null ? '' : ` · cap ${money(item.budget_cap)}`}`,
    }));
    if (!work.length) {
      const autoEnabled = (workspace.channels || []).some((item) => item.mode === 'AUTO');
      work.push({
        title: !autoEnabled ? 'No Auto channel enabled' : (overview.product_id ? 'Comparing the first executable paths' : 'Waiting for deep research'),
        detail: !autoEnabled
          ? 'Partizan can keep researching. Choose Auto in Channels when you want execution.'
          : (overview.growth_balance.funded_usd > 0 ? 'Partizan will surface the first eligible experiment here.' : 'Funding unlocks the included deep research.'),
      });
    }
    $('current-work').innerHTML = work.map((item) => `<div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span></div>`).join('');

    const experiments = [...overview.running_experiments, ...overview.waiting_experiments];
    $('experiments').innerHTML = experiments.length
      ? experiments.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.platform)} · ${escapeHtml(item.action_type)}</strong><span>${escapeHtml(item.status)}${item.budget_cap == null ? '' : ` · ${money(item.budget_cap)}`}</span></div>`).join('')
      : '<div><strong>No experiment yet</strong><span>Partizan will create the first eligible test after research and launch prerequisites are complete.</span></div>';
    $('decisions').innerHTML = overview.recent_decisions.length
      ? overview.recent_decisions.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.decision || item.outcome)}</strong><span>${escapeHtml(item.reasons[0] || item.kind)}</span></div>`).join('')
      : '<div><strong>Waiting for the first signal</strong><span>Acquisition and learning decisions will appear here.</span></div>';
  };

  const channelModeLabel = (mode) => ({ AUTO: 'Auto', RESEARCH_ONLY: 'Research only', OFF: 'Off' })[mode] || mode;

  const channelModeOptions = (channel) => {
    const modes = channel.autonomous_execution_available
      ? [['AUTO', 'Auto'], ['RESEARCH_ONLY', 'Research only'], ['OFF', 'Off']]
      : [['RESEARCH_ONLY', 'Research only'], ['OFF', 'Off']];
    return modes.map(([value, label]) => `<option value="${value}"${channel.mode === value ? ' selected' : ''}>${label}</option>`).join('');
  };

  const renderChannels = (channels) => {
    const body = $('channels-table-body');
    body.innerHTML = channels.map((channel) => {
      const access = channel.platform === 'INSTAGRAM'
        ? (channel.connected ? 'Meta connected' : 'Meta not connected')
        : (channel.autonomous_execution_available ? 'Execution available' : 'Research surface');
      const spendClass = channel.spend_usd ? 'channel-metric' : 'channel-zero';
      return `<tr class="${channel.mode === 'OFF' ? 'channel-off' : ''}" data-platform="${escapeHtml(channel.platform)}">
        <td class="channel-name"><strong>${escapeHtml(channel.label)}</strong><small>${escapeHtml(access)}</small></td>
        <td><select class="channel-mode-select" data-platform="${escapeHtml(channel.platform)}" aria-label="${escapeHtml(channel.label)} mode">${channelModeOptions(channel)}</select></td>
        <td class="${spendClass}">${money(channel.spend_usd)}</td>
        <td class="${channel.paid_customers ? 'channel-metric' : 'channel-zero'}">${channel.paid_customers}</td>
        <td class="${channel.cac_usd == null ? 'channel-zero' : 'channel-metric'}">${channel.cac_usd == null ? '—' : money(channel.cac_usd)}</td>
        <td class="${channel.revenue_usd ? 'channel-metric' : 'channel-zero'}">${money(channel.revenue_usd)}</td>
        <td class="${channel.roas == null ? 'channel-zero' : 'channel-metric'}">${roas(channel.roas)}</td>
      </tr>`;
    }).join('');

    const snapshot = $('channel-snapshot');
    const ordered = [...channels].sort((a, b) => (b.spend_usd - a.spend_usd) || a.label.localeCompare(b.label));
    snapshot.innerHTML = ordered.length
      ? ordered.map((channel) => `<div class="channel-snapshot-row"><strong>${escapeHtml(channel.label)}</strong><span class="mode">${escapeHtml(channelModeLabel(channel.mode))}</span><span>${money(channel.spend_usd)} spent</span><span>${channel.cac_usd == null ? 'CAC —' : `CAC ${money(channel.cac_usd)}`}</span></div>`).join('')
      : '<div class="channel-snapshot-empty">No channel data yet.</div>';
  };

  const renderResearchStatus = (project, overview) => {
    $('research-state').classList.remove('good', 'warn');
    if (project.research_state === 'READY') {
      $('research-state').textContent = 'Ready';
      $('research-state').classList.add('good');
      $('research-title').textContent = 'Market mapped';
      $('research-copy').textContent = 'Partizan has mapped customer segments and acquisition opportunities. Disabled channels are excluded from what you see and from new autonomous execution.';
      $('research-button').classList.add('hidden');
      return;
    }
    if (project.research_state === 'NEEDS_INPUT') {
      $('research-state').textContent = 'Needs one detail';
      $('research-state').classList.add('warn');
      $('research-title').textContent = 'One useful clarification is needed';
      $('research-copy').textContent = 'Partizan asks only when one product detail materially changes the acquisition plan.';
      $('research-button').classList.remove('hidden');
      return;
    }
    $('research-state').textContent = overview.growth_balance.funded_usd > 0 ? 'Ready to start' : 'Not started';
    $('research-title').textContent = overview.growth_balance.funded_usd > 0 ? 'Partizan can start mapping the market now' : 'Partizan will map the market after funding';
    $('research-copy').textContent = 'Funding Growth Balance includes the deep research. There is no separate $49 charge for autonomous execution.';
    $('research-button').classList.toggle('hidden', overview.growth_balance.funded_usd <= 0);
  };

  const renderWorkspace = (data) => {
    workspace = data;
    account = data.account;
    const project = data.project;
    const overview = data.autopilot;
    const balance = overview.growth_balance;

    renderAccountNav();
    $('workspace-status').textContent = statusText(overview);
    $('workspace-summary').textContent = '';
    $('project-market').textContent = project.market;
    $('project-goal').textContent = project.goal;

    $('metric-balance').textContent = money(balance.available_usd);
    $('metric-spend').textContent = money(balance.acquisition_spend_usd);
    $('metric-customers').textContent = String(overview.paid_customers);
    $('metric-cac').textContent = overview.cac_usd == null ? '—' : money(overview.cac_usd);
    $('metric-revenue').textContent = money(overview.revenue_usd);
    $('metric-fee').textContent = money(balance.management_fee_usd);
    $('metric-fee-note').textContent = `${balance.management_fee_pct}% of actual spend`;

    $('balance-available').textContent = money(balance.available_usd);
    $('balance-capacity').textContent = money(balance.remaining_acquisition_capacity_usd);
    $('balance-used').textContent = money(balance.used_usd);
    $('fund-amount').value = String(project.budget_usd || 1000);
    const funded = balance.funded_usd > 0;
    $('balance-state').textContent = funded ? 'Funded' : 'Not funded';
    $('balance-state').classList.toggle('good', funded);
    $('fund-button').textContent = funded ? 'Add to Growth Balance →' : 'Fund Growth Balance →';
    $('balance-note').textContent = funded
      ? `${money(balance.funded_usd)} funded. Unused money remains in Growth Balance.${balance.settlement_ready ? ' Paid execution rail is ready.' : ' Paid experiments are still paused until the ad-spend rail is ready.'}`
      : 'Funding unlocks deep research immediately. Partizan charges its fee only on acquisition money actually spent.';
    $('balance-note').classList.toggle('warning', funded && !balance.settlement_ready);

    $('work-state').textContent = overview.autopilot_status === 'ACTIVE' ? 'Working' : (overview.autopilot_status === 'PAUSED' ? 'Paused' : 'Preparing');
    $('work-state').classList.toggle('good', overview.autopilot_status === 'ACTIVE');
    $('blockers').textContent = friendlyBlockers(overview);

    if (data.target_max_cac != null) $('max-cac').value = String(data.target_max_cac);
    $('spend-confirm').checked = Boolean(data.autonomous_spend_confirmed);

    $('meta-state').textContent = overview.meta.connected ? 'Connected' : 'Not connected';
    $('meta-state').classList.toggle('good', overview.meta.connected);
    $('meta-detail').textContent = overview.meta.connected
      ? `Ad account ${overview.meta.ad_account_id}`
      : 'Connect Meta if Instagram & Facebook is set to Auto. Access alone never starts spend.';
    $('meta-connect').textContent = overview.meta.connected ? 'Reconnect Meta' : 'Connect Meta →';
    $('meta-connect').disabled = false;

    renderChannels(data.channels || []);
    renderActivity(overview);

    const paused = overview.autopilot_status === 'PAUSED';
    $('pause-button').classList.toggle('hidden', paused || overview.autopilot_status === 'NOT_CONFIGURED' || overview.autopilot_status === 'RESEARCHING');
    $('resume-button').classList.toggle('hidden', !paused);

    renderResearchStatus(project, overview);
    if (lastResearchResult) renderResearch(lastResearchResult);
    $('loading').classList.add('hidden');
    $('login-gate').classList.add('hidden');
    $('workspace').classList.remove('hidden');
    setActiveTab(activeTab);
  };

  const loadWorkspace = async () => {
    const [data, channels] = await Promise.all([
      api(`/customer/workspace/${projectId}`),
      api(`/customer/workspace/${projectId}/channels`),
    ]);
    data.channels = channels;
    renderWorkspace(data);
    return data;
  };

  const refreshWorkspaceWithoutResearch = async () => loadWorkspace();

  const visibleResearchOpportunities = (result) => {
    const channels = workspace && workspace.channels ? workspace.channels : [];
    const modes = new Map(channels.map((item) => [item.platform, item.mode]));
    return result.opportunities.filter((item) => modes.get(String(item.platform).toUpperCase()) !== 'OFF');
  };

  const renderResearch = (result) => {
    lastResearchResult = result;
    if (result.state === 'NEEDS_INPUT') {
      const question = result.clarifications[0];
      const box = $('clarification');
      box.innerHTML = `<span class="eyebrow">One useful clarification</span><h3>${escapeHtml(question.question)}</h3><p>${escapeHtml(question.rationale)}</p><form id="clarification-form"><input id="clarification-answer" required placeholder="Your answer"><button class="button button-primary" type="submit">Continue →</button></form>`;
      box.classList.remove('hidden');
      $('research-results').classList.add('hidden');
      $('clarification-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const button = event.submitter;
        button.disabled = true;
        try {
          const next = await api(`/customer/workspace/${projectId}/clarifications`, {
            method: 'POST',
            body: JSON.stringify({ question_id: question.question_id, answer: $('clarification-answer').value.trim() }),
          });
          renderResearch(next);
          await refreshWorkspaceWithoutResearch();
        } catch (error) {
          showNotice(error.message, true);
        } finally {
          button.disabled = false;
        }
      });
      return;
    }

    $('clarification').classList.add('hidden');
    const opportunities = visibleResearchOpportunities(result);
    const results = $('research-results');
    results.innerHTML = `<div><h3>Who Partizan will target first</h3><div class="result-grid">${result.icps.map((item) => `<article class="result-card"><strong>${escapeHtml(item.title)}</strong><span>${Math.round(item.score)}/100 fit</span><p>${escapeHtml(item.description)}</p></article>`).join('')}</div></div><div><h3>Where Partizan sees opportunity</h3><div class="result-grid">${opportunities.slice(0, 12).map((item) => `<article class="result-card"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.platform)} · ${escapeHtml(item.kind)}</span><p>${escapeHtml(item.rationale || 'Relevant acquisition opportunity')}</p>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Open ↗</a>` : ''}</article>`).join('') || '<article class="result-card"><strong>No enabled channel opportunities yet</strong><p>Change channel preferences if you want Partizan to include additional surfaces.</p></article>'}</div></div>`;
    results.classList.remove('hidden');
  };

  const loadResearch = async (showProgress = true) => {
    if (showProgress) $('research-progress').classList.remove('hidden');
    $('research-button').disabled = true;
    try {
      const result = await api(`/customer/workspace/${projectId}/deep-research`, { method: 'POST' });
      renderResearch(result);
      await refreshWorkspaceWithoutResearch();
      return result;
    } catch (error) {
      showNotice(error.message, true);
      throw error;
    } finally {
      $('research-progress').classList.add('hidden');
      $('research-button').disabled = false;
    }
  };

  const handleCallbacks = async (initial) => {
    const growthState = params.get('growth_balance');
    const sessionId = params.get('session_id');
    if (growthState === 'success' && sessionId) {
      try {
        const overview = await api(`/customer/workspace/${projectId}/growth-balance/verify`, {
          method: 'POST',
          body: JSON.stringify({ session_id: sessionId }),
        });
        showNotice(`Growth Balance funded. Available: ${money(overview.growth_balance.available_usd)}.`);
        await loadWorkspace();
        await loadResearch(true);
        setActiveTab('overview');
        window.history.replaceState({}, '', `/workspace?project=${encodeURIComponent(projectId)}`);
      } catch (error) {
        showNotice(error.message, true);
      }
    } else if (growthState === 'cancelled') {
      showNotice('Growth Balance checkout cancelled. No funds were added.');
      window.history.replaceState({}, '', `/workspace?project=${encodeURIComponent(projectId)}`);
    } else if (initial.project.launch_unlocked && initial.project.research_state !== 'NOT_STARTED') {
      loadResearch(false).catch(() => {});
    }

    const metaState = params.get('meta');
    if (metaState === 'connected') {
      try {
        setActiveTab('settings');
        await loadMetaOptions();
        showNotice('Meta authorized. Choose the ad account Partizan should use.');
      } catch (error) {
        showNotice(error.message, true);
      }
    } else if (metaState === 'error') {
      setActiveTab('settings');
      showNotice('Meta connection was not completed.', true);
    }
  };

  const openAccount = async (accountData) => {
    account = accountData;
    if (!account.projects.length) {
      window.location.replace('/start');
      return;
    }
    if (!projectId) projectId = account.projects[0].project_id;
    if (!account.projects.some((item) => item.project_id === projectId)) {
      projectId = account.projects[0].project_id;
    }
    hideLoginGate();
    renderAccountNav();
    window.history.replaceState({}, '', `/workspace?project=${encodeURIComponent(projectId)}${window.location.search.includes('growth_balance=') || window.location.search.includes('meta=') ? `&${window.location.search.slice(1).replace(/^project=[^&]*&?/, '')}` : ''}`);
    const initial = await loadWorkspace();
    await handleCallbacks(initial);
  };

  $('workspace-login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    button.textContent = 'Signing in…';
    try {
      const accountData = await api('/customer/account/login', {
        method: 'POST',
        body: JSON.stringify({
          email: $('workspace-login-email').value.trim(),
          password: $('workspace-login-password').value,
        }),
      });
      await openAccount(accountData);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Sign in →';
    }
  });

  $('project-switcher').addEventListener('change', (event) => {
    const nextProject = event.target.value;
    if (!nextProject || nextProject === projectId) return;
    window.location.assign(`/workspace?project=${encodeURIComponent(nextProject)}`);
  });

  $('channels-table-body').addEventListener('change', async (event) => {
    const select = event.target.closest('.channel-mode-select');
    if (!select) return;
    const platform = select.dataset.platform;
    const mode = select.value;
    select.disabled = true;
    try {
      await api(`/customer/workspace/${projectId}/channels`, {
        method: 'PUT',
        body: JSON.stringify({ channels: [{ platform, mode }] }),
      });
      showNotice(`${platform === 'INSTAGRAM' ? 'Instagram & Facebook' : platform} set to ${channelModeLabel(mode)}.`);
      await loadWorkspace();
    } catch (error) {
      showNotice(error.message, true);
      await loadWorkspace().catch(() => {});
    } finally {
      select.disabled = false;
    }
  });

  $('fund-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('fund-button');
    const original = button.textContent;
    button.disabled = true;
    button.textContent = 'Opening secure checkout…';
    try {
      const result = await api(`/customer/workspace/${projectId}/growth-balance/checkout`, {
        method: 'POST',
        body: JSON.stringify({ amount_usd: Number($('fund-amount').value) }),
      });
      window.location.assign(result.checkout_url);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
      button.textContent = original;
    }
  });

  $('guardrail-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      await api(`/customer/workspace/${projectId}/autopilot`, {
        method: 'PUT',
        body: JSON.stringify({
          target_max_cac: Number($('max-cac').value),
          confirm_autonomous_spend: $('spend-confirm').checked,
        }),
      });
      showNotice('Maximum cost per customer saved.');
      await loadWorkspace();
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  const setStatus = async (status) => {
    try {
      await api(`/customer/workspace/${projectId}/autopilot/status`, {
        method: 'POST',
        body: JSON.stringify({ status }),
      });
      showNotice(status === 'PAUSED' ? 'Partizan paused.' : 'Partizan resumed.');
      await loadWorkspace();
    } catch (error) {
      showNotice(error.message, true);
    }
  };
  $('pause-button').addEventListener('click', () => setStatus('PAUSED'));
  $('resume-button').addEventListener('click', () => setStatus('ACTIVE'));
  $('research-button').addEventListener('click', () => loadResearch(true).catch(() => {}));

  $('meta-connect').addEventListener('click', async () => {
    const button = $('meta-connect');
    button.disabled = true;
    try {
      const result = await api(`/customer/workspace/${projectId}/meta/connect`, { method: 'POST' });
      window.location.assign(result.authorization_url);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
    }
  });

  const loadMetaOptions = async () => {
    metaOptions = await api(`/customer/workspace/${projectId}/meta/options`);
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
    const pages = metaOptions.pages_by_ad_account[$('meta-account').value] || [];
    $('meta-page').innerHTML = pages.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('');
  };
  $('meta-account').addEventListener('change', renderMetaPages);
  $('meta-options-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      await api(`/customer/workspace/${projectId}/meta/connection`, {
        method: 'POST',
        body: JSON.stringify({
          ad_account_id: $('meta-account').value,
          page_id: $('meta-page').value,
          country_codes: [$('meta-country').value.trim().toUpperCase()],
        }),
      });
      $('meta-options-form').classList.add('hidden');
      showNotice('Meta connected to this Partizan project.');
      await loadWorkspace();
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $('logout-button').addEventListener('click', async () => {
    try { await api('/customer/account/logout', { method: 'POST' }); } catch (_) { /* ignore */ }
    account = null;
    workspace = null;
    projectId = null;
    lastResearchResult = null;
    window.history.replaceState({}, '', '/workspace');
    showLoginGate();
  });

  const bootstrap = async () => {
    try {
      const accountData = await api('/customer/account/me');
      await openAccount(accountData);
    } catch (error) {
      if (error.status === 401) {
        showLoginGate();
        return;
      }
      showLoginGate(error.message);
    }
  };

  bootstrap().catch((error) => showLoginGate(error.message));
})();
