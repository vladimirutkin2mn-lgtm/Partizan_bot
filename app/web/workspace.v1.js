(() => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(window.location.search);
  let account = null;
  let projectId = params.get('project');
  let workspace = null;
  let metaOptions = null;
  let lastResearchResult = null;
  let activeTab = 'overview';
  let activationAction = null;
  let activationOpportunityUrl = null;
  let researchAction = 'full';

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

  const openFundingControls = () => {
    setActiveTab('settings');
    const form = $('fund-form');
    if (form) form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const amount = $('fund-amount');
    if (amount) {
      window.setTimeout(() => {
        amount.focus();
        amount.select();
      }, 180);
    }
  };


  const openResearchControls = () => {
    setActiveTab('activity');
    const card = document.querySelector('.research-card');
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const button = $('research-button');
    if (button && !button.classList.contains('hidden') && !button.disabled) button.click();
  };

  const setActivationStep = (id, complete, current, stateText) => {
    const node = $(id);
    node.classList.toggle('complete', complete);
    node.classList.toggle('current', current);
    const state = node.querySelector('.activation-step-state');
    state.textContent = stateText;
  };

  const renderActivationPreview = (directions, opportunity, researchStatus = 'NOT_RUN', researchMessage = '') => {
    const preview = $('activation-preview');
    const opportunityContainer = $('activation-preview-opportunity');
    const directionsContainer = $('activation-preview-directions');
    activationOpportunityUrl = null;

    if (opportunity) {
      const maxCost = Number(opportunity.estimated_cost_max_usd || 0);
      const minCost = Number(opportunity.estimated_cost_min_usd || 0);
      const cost = maxCost === 0
        ? '$0'
        : (minCost === maxCost ? money(maxCost) : `${money(minCost)}–${money(maxCost)}`);
      const evidence = Array.isArray(opportunity.provenance) ? opportunity.provenance[0] : null;
      activationOpportunityUrl = opportunity.url || (evidence && evidence.url) || null;
      $('activation-preview-label').textContent = 'Real public-web research · not a conversion claim';
      opportunityContainer.innerHTML = `
        <article>
          <span>${escapeHtml(opportunity.surface.replaceAll('_', ' ').toLowerCase())}</span>
          <strong>${escapeHtml(opportunity.title)}</strong>
          <p>${escapeHtml(opportunity.rationale)}</p>
          <div><b>Recommended move</b><small>${escapeHtml(opportunity.recommended_action)}</small></div>
          <div><b>Estimated cash cost</b><small>${escapeHtml(cost)}</small></div>
          <div><b>Signal to watch</b><small>${escapeHtml(opportunity.signal_to_watch)}</small></div>
          ${evidence ? `<a href="${escapeHtml(evidence.url)}" target="_blank" rel="noopener noreferrer">Evidence · ${escapeHtml(evidence.title || 'public source')} ↗</a>` : ''}
        </article>`;
      directionsContainer.innerHTML = '';
      opportunityContainer.classList.remove('hidden');
      preview.classList.remove('hidden');
      return;
    }

    const items = Array.isArray(directions) ? directions.slice(0, 3) : [];
    const researchPending = researchStatus === 'NEEDS_MORE_RESEARCH' || researchStatus === 'UNAVAILABLE';
    $('activation-preview-label').textContent = researchPending
      ? (researchStatus === 'UNAVAILABLE'
        ? 'Public-web research unavailable · no synthetic fallback'
        : 'Evidence threshold not met · hypotheses only')
      : 'Starting hypotheses · not public-web proof yet';
    opportunityContainer.innerHTML = '';
    opportunityContainer.classList.add('hidden');
    preview.classList.toggle('hidden', items.length === 0);
    directionsContainer.innerHTML = items.map((item) => `
      <article>
        <span>Hypothesis · ${escapeHtml(item.potential === 'HIGH' ? 'strong starting direction' : 'worth investigating')}</span>
        <strong>${escapeHtml(item.name)}</strong>
        <p>${escapeHtml(item.rationale)}</p>
      </article>
    `).join('') + (researchPending && researchMessage
      ? `<article class="research-state-card"><span>Research status</span><strong>Partizan will not invent an opportunity.</strong><p>${escapeHtml(researchMessage)}</p></article>`
      : '');
  };

  const renderActivation = (project, overview, data) => {
    const funded = overview.growth_balance.funded_usd > 0;
    const researchReady = project.research_state === 'READY';
    const needsInput = project.research_state === 'NEEDS_INPUT';
    const previewOpportunity = data.preview_opportunity || null;
    const previewResearchStatus = data.preview_research_status || 'NOT_RUN';
    const previewResearchMessage = data.preview_research_message || '';
    const testStarted = overview.running_experiments.length > 0 || overview.waiting_experiments.length > 0;
    const noMeasuredActivity = overview.growth_balance.acquisition_spend_usd <= 0
      && overview.paid_customers <= 0;
    const showActivation = noMeasuredActivity && !testStarted;

    $('activation-card').classList.toggle('hidden', !showActivation);
    ['performance-metrics', 'performance-overview', 'performance-finance'].forEach((id) => {
      $(id).classList.toggle('hidden', showActivation);
    });
    if (!showActivation) {
      activationAction = null;
      return;
    }

    const button = $('activation-primary');
    button.disabled = false;
    setActivationStep('activation-product', true, false, 'Done');
    setActivationStep('activation-direction', true, false, 'Done');

    if (previewOpportunity) {
      $('activation-research-title').textContent = 'One real opportunity researched';
      $('activation-research-copy').textContent = 'Public-web evidence appeared before Partizan asked you to fund anything.';
      setActivationStep('activation-budget', true, false, 'Done');
      setActivationStep('activation-channel', false, true, 'Now');
      setActivationStep('activation-limit', false, false, 'If needed');
      setActivationStep('activation-test', false, false, 'Next');
      $('activation-progress').textContent = '3 of 6';
      const maxCost = Number(previewOpportunity.estimated_cost_max_usd || 0);
      if (maxCost <= 0) {
        activationAction = 'opportunity';
        button.textContent = 'Open this $0 move →';
        $('activation-note').textContent = `Recommended first move: ${previewOpportunity.recommended_action} No acquisition funding is required for this step.`;
      } else {
        activationAction = 'fund';
        button.textContent = `Run this ${money(maxCost)} test →`;
        $('activation-note').textContent = `This specific move needs up to ${money(maxCost)}. Partizan will ask you to fund the test because the action is now concrete.`;
      }
      return;
    }

    if (
      previewResearchStatus === 'NEEDS_MORE_RESEARCH'
      || previewResearchStatus === 'UNAVAILABLE'
    ) {
      $('activation-research-title').textContent = 'Researching first opportunity';
      $('activation-research-copy').textContent = previewResearchStatus === 'UNAVAILABLE'
        ? 'Public-web evidence is temporarily unavailable. Partizan will not invent a result.'
        : 'Partizan has hypotheses, but it is waiting for stronger public evidence before naming an opportunity.';
      setActivationStep('activation-budget', false, true, 'Researching');
      setActivationStep('activation-channel', false, false, 'Waiting');
      setActivationStep('activation-limit', false, false, 'If needed');
      setActivationStep('activation-test', false, false, 'Later');
      $('activation-progress').textContent = '2 of 6';
      activationAction = 'preview-research';
      button.textContent = 'Keep researching →';
      $('activation-note').textContent = previewResearchMessage || 'Partizan needs stronger public evidence before it can recommend a concrete opportunity. No acquisition funding is required.';
      return;
    }

    $('activation-research-title').textContent = researchReady
      ? 'Market research ready'
      : 'Researching first opportunity';
    $('activation-research-copy').textContent = researchReady
      ? 'The broader research is ready to review.'
      : 'This older project predates evidence-first free research; Partizan will not treat its hypotheses as proof.';
    const current = !researchReady ? 'research' : 'recommendation';
    setActivationStep('activation-budget', researchReady, current === 'research', researchReady ? 'Done' : 'Next');
    setActivationStep('activation-channel', false, current === 'recommendation', researchReady ? 'Review' : 'Waiting');
    setActivationStep('activation-limit', false, false, 'If needed');
    setActivationStep('activation-test', false, false, 'Partizan');
    $('activation-progress').textContent = `${2 + Number(researchReady)} of 6`;

    if (needsInput) {
      activationAction = 'research';
      button.textContent = 'Answer one question →';
      $('activation-note').textContent = 'Partizan only asks when one product detail materially changes the research.';
      return;
    }
    if (!researchReady) {
      if (project.launch_unlocked) {
        activationAction = 'research';
        button.textContent = 'Start full market research →';
        $('activation-note').textContent = 'Your research-only Acquisition Plan already unlocks the full research.';
        return;
      }
      if (funded) {
        activationAction = 'research';
        button.textContent = 'Open included full market map →';
        $('activation-note').textContent = 'The full market map is included after a paid move funds the workspace. The acquisition budget itself remains for paid execution, not for buying research.';
        return;
      }
      activationAction = 'research';
      button.textContent = 'Review product analysis →';
      $('activation-note').textContent = 'This older project does not have the new free researched opportunity yet. Funding is not shown as the default next step.';
      return;
    }

    activationAction = 'research';
    button.textContent = 'See what Partizan found →';
    $('activation-note').textContent = 'Use the research to choose the smallest useful move. Add budget or access only if that specific move needs it.';
  };

  const friendlyBlockers = (overview, data) => {
    const items = [];
    if (overview.blockers.some((item) => item.includes('No autonomous execution channel'))) items.push('No execution channel is currently available. Partizan can keep researching; grant channel access only when you want to run a recommended action.');

    if (overview.blockers.some((item) => item.includes('Website or landing page'))) items.push('Add a live website or landing page before paid traffic starts.');
    if (overview.blockers.some((item) => item.includes('guardrails'))) items.push('Save the maximum cost per customer in Settings.');
    if (overview.blockers.some((item) => item.includes('Meta access'))) items.push('Connect Meta only if a recommended Instagram & Facebook action needs execution access.');
    if (overview.growth_balance.funded_usd > 0 && !overview.growth_balance.settlement_ready) items.push('Paid tests are paused until Partizan’s payment path is ready. Research and planning remain available.');
    return items.join(' ');
  };

  const statusText = (overview) => {
    if (overview.autopilot_status === 'RESEARCHING') return 'Partizan is mapping your market.';
    if (overview.autopilot_status === 'ACTIVE') return 'Partizan is working on getting you customers.';
    if (overview.autopilot_status === 'PAUSED') return 'Partizan is paused.';
    if (overview.product_id) return 'Partizan is ready for the next acquisition step.';
    return 'Partizan is ready to keep researching before spend.';
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
        title: !autoEnabled ? 'No Auto channel enabled' : (overview.product_id ? 'Comparing the first executable paths' : 'Waiting for market research'),
        detail: !autoEnabled
          ? 'Partizan can keep researching. Choose Auto in Channels when you want execution.'
          : (overview.growth_balance.funded_usd > 0 ? 'Partizan will surface the first eligible test here.' : 'Partizan keeps researching before spend. Add budget only when a concrete paid move needs it.'),
      });
    }
    $('current-work').innerHTML = work.map((item) => `<div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span></div>`).join('');

    const experiments = [...overview.running_experiments, ...overview.waiting_experiments];
    $('experiments').innerHTML = experiments.length
      ? experiments.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.platform)} · ${escapeHtml(item.action_type)}</strong><span>${escapeHtml(item.status)}${item.budget_cap == null ? '' : ` · ${money(item.budget_cap)}`}</span></div>`).join('')
      : '<div><strong>No test yet</strong><span>Partizan will create the first eligible test after research and launch prerequisites are complete.</span></div>';
    $('decisions').innerHTML = overview.recent_decisions.length
      ? overview.recent_decisions.slice(0, 8).map((item) => `<div><strong>${escapeHtml(item.decision || item.outcome)}</strong><span>${escapeHtml(item.reasons[0] || item.kind)}</span></div>`).join('')
      : '<div><strong>Waiting for the first signal</strong><span>Acquisition and learning decisions will appear here.</span></div>';
  };

  const channelModeLabel = (mode) => ({ AUTO: 'Execution allowed', RESEARCH_ONLY: 'Research only', OFF: 'Off' })[mode] || mode;

  const channelModeOptions = (channel) => {
    const modes = channel.autonomous_execution_available
      ? [['AUTO', 'Allow execution'], ['RESEARCH_ONLY', 'Research only'], ['OFF', 'Off']]
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

  const renderResearchStatus = (project, overview, data) => {
    researchAction = 'full';
    $('research-state').classList.remove('good', 'warn');
    if (project.research_state === 'READY') {
      $('research-state').textContent = 'Ready';
      $('research-state').classList.add('good');
      $('research-title').textContent = 'Market mapped';
      $('research-copy').textContent = 'Partizan has mapped customer segments and broader acquisition opportunities. Research-only findings stay visible independently; execution still requires permission.';
      $('research-button').classList.add('hidden');
      return;
    }
    if (project.research_state === 'NEEDS_INPUT') {
      $('research-state').textContent = 'Needs one detail';
      $('research-state').classList.add('warn');
      $('research-title').textContent = 'One useful clarification is needed';
      $('research-copy').textContent = 'Partizan asks only when one product detail materially changes the research.';
      $('research-button').classList.remove('hidden');
      return;
    }
    const previewResearchStatus = data.preview_research_status || 'NOT_RUN';
    const previewResearchMessage = data.preview_research_message || '';
    if (
      !data.preview_opportunity
      && (previewResearchStatus === 'NEEDS_MORE_RESEARCH' || previewResearchStatus === 'UNAVAILABLE')
    ) {
      researchAction = 'preview';
      $('research-state').textContent = previewResearchStatus === 'UNAVAILABLE'
        ? 'Research unavailable'
        : 'Needs more evidence';
      $('research-state').classList.add('warn');
      $('research-title').textContent = previewResearchStatus === 'UNAVAILABLE'
        ? 'Public-web research is temporarily unavailable'
        : 'Partizan is still looking for a concrete opportunity';
      $('research-copy').textContent = previewResearchMessage || 'Partizan will not invent a named opportunity. Keep researching without adding acquisition funding.';
      $('research-button').textContent = 'Keep free research going →';
      $('research-button').classList.remove('hidden');
      return;
    }
    const researchEntitled = Boolean(project.launch_unlocked);
    $('research-state').textContent = researchEntitled
      ? 'Ready to expand'
      : (data.preview_opportunity ? 'Free proof ready' : 'Not started');
    $('research-title').textContent = researchEntitled
      ? 'Partizan can research the rest now'
      : (data.preview_opportunity ? 'One researched opportunity is already available' : 'Full market research is optional');
    $('research-copy').textContent = project.launch_unlocked
      ? (overview.growth_balance.funded_usd > 0
        ? 'Because a paid move funded the workspace, the full market map is included. Acquisition money remains for paid execution; research started before spend.'
        : 'Your research-only Acquisition Plan unlocks the full market map without an acquisition budget.')
      : 'You already received value before funding. The full market map is an optional research upgrade; add acquisition budget only when a specific recommended move needs paid budget.';
    $('research-button').textContent = 'Continue full research →';
    $('research-button').classList.toggle('hidden', !researchEntitled);
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
    $('growth-balance-metric').classList.toggle('is-funded', funded);
    $('overview-fund-label').textContent = 'Add funds';
    $('overview-balance-benefit').querySelector('span').textContent = funded
      ? 'Available for recommended paid tests'
      : 'Fund a recommended paid test when needed';
    $('balance-state').textContent = funded ? 'Funded' : 'Not funded';
    $('balance-state').classList.toggle('good', funded);
    $('fund-button').textContent = 'Add funds for a paid test →';
    $('balance-note').textContent = funded
      ? `${money(balance.funded_usd)} added. Unused money remains in your acquisition budget.${balance.settlement_ready ? ' Paid execution is available when a recommended action and permission are ready.' : ' Paid tests are still paused until the payment path is ready.'}`
      : 'Add money only when a recommended action needs paid budget. Partizan charges its fee only on acquisition money actually spent.';
    $('balance-note').classList.toggle('warning', funded && !balance.settlement_ready);

    $('work-state').textContent = overview.autopilot_status === 'ACTIVE' ? 'Working' : (overview.autopilot_status === 'PAUSED' ? 'Paused' : 'Preparing');
    $('work-state').classList.toggle('good', overview.autopilot_status === 'ACTIVE');
    $('blockers').textContent = friendlyBlockers(overview, data);

    if (data.target_max_cac != null) $('max-cac').value = String(data.target_max_cac);
    $('spend-confirm').checked = Boolean(data.autonomous_spend_confirmed);

    $('meta-state').textContent = overview.meta.connected ? 'Connected' : 'Not connected';
    $('meta-state').classList.toggle('good', overview.meta.connected);
    $('meta-detail').textContent = overview.meta.connected
      ? `Ad account ${overview.meta.ad_account_id}`
      : 'Connect Meta only when a recommended Instagram & Facebook action needs execution access. Access alone never starts spend.';
    $('meta-connect').textContent = overview.meta.connected ? 'Reconnect Meta' : 'Connect Meta →';
    $('meta-connect').disabled = false;

    renderChannels(data.channels || []);
    renderActivity(overview);
    renderActivationPreview(
      data.preview_directions || [],
      data.preview_opportunity || null,
      data.preview_research_status || 'NOT_RUN',
      data.preview_research_message || '',
    );
    renderActivation(project, overview, data);

    const paused = overview.autopilot_status === 'PAUSED';
    $('pause-button').classList.toggle('hidden', paused || overview.autopilot_status === 'NOT_CONFIGURED' || overview.autopilot_status === 'RESEARCHING');
    $('resume-button').classList.toggle('hidden', !paused);

    renderResearchStatus(project, overview, data);
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
    window.dispatchEvent(new CustomEvent('partizan:workspace-ready', {
      detail: { projectId },
    }));
    return data;
  };

  const refreshWorkspaceWithoutResearch = async () => loadWorkspace();

  const surfaceLabels = {
    EXECUTION_PLATFORM: 'Execution platform',
    CREATOR: 'Creator / influencer',
    MEDIA: 'Media / podcast / newsletter',
    PARTNERSHIP: 'Partnership / affiliate',
    SEARCH: 'Search / SEO',
    DIRECTORY: 'Directory / reviews',
    COMMUNITY: 'Public community',
  };
  const executionStatusLabels = {
    PARTIZAN_CONTROL_PLANE: 'Control-plane path',
    RESEARCH_ONLY: 'Research only',
    OUTREACH_POSSIBLE: 'Outreach possible',
    MANUAL_HANDOFF: 'Manual handoff',
  };
  const opportunitySurface = (item) => item.surface || 'EXECUTION_PLATFORM';
  const opportunityStatus = (item) => item.execution_status || (opportunitySurface(item) === 'EXECUTION_PLATFORM' ? 'PARTIZAN_CONTROL_PLANE' : 'RESEARCH_ONLY');
  const opportunityMeta = (item) => [
    surfaceLabels[opportunitySurface(item)] || opportunitySurface(item),
    item.platform,
    item.kind,
  ].filter(Boolean).join(' · ');
  const executionRequirement = (item) => item.execution_requirement || ({
    PARTIZAN_CONTROL_PLANE: 'A control-plane path is not authorization to execute. The channel still needs to be enabled, connected with the required identity/permissions, and pass normal safety checks.',
    RESEARCH_ONLY: 'Research finding only. This is not a connected channel and Partizan cannot execute it automatically.',
    OUTREACH_POSSIBLE: 'Partizan can prepare an outreach path, but sending still requires the normal outreach permissions and safety checks.',
    MANUAL_HANDOFF: 'Manual handoff is required before anything happens on this opportunity.',
  })[opportunityStatus(item)];
  const renderEvidence = (item) => {
    const evidence = Array.isArray(item.provenance) ? item.provenance.slice(0, 3) : [];
    if (!evidence.length) return '';
    return `<details><summary>Evidence · ${evidence.length}</summary>${evidence.map((source) => `<p>${source.url ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(source.title || source.query || 'Source')}</a>` : `<strong>${escapeHtml(source.title || source.query || 'Source')}</strong>`}${source.snippet ? ` — ${escapeHtml(source.snippet)}` : ''}</p>`).join('')}</details>`;
  };
  const renderOpportunity = (item) => `<article class="result-card"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(opportunityMeta(item))}</span><span>${escapeHtml(executionStatusLabels[opportunityStatus(item)] || opportunityStatus(item))}</span><p>${escapeHtml(item.rationale || 'Relevant acquisition opportunity')}</p><p>${escapeHtml(executionRequirement(item))}</p>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Open ↗</a>` : ''}${renderEvidence(item)}</article>`;

  const visibleResearchOpportunities = (result) => {
    const channels = workspace && workspace.channels ? workspace.channels : [];
    const modes = new Map(channels.map((item) => [item.platform, item.mode]));
    return result.opportunities.filter((item) => {
      const surface = opportunitySurface(item);
      if (surface !== 'EXECUTION_PLATFORM') return true;
      return modes.get(String(item.platform || '').toUpperCase()) !== 'OFF';
    });
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
    const visible = visibleResearchOpportunities(result);
    const broadResearch = visible.filter((item) => opportunitySurface(item) !== 'EXECUTION_PLATFORM');
    const executionCandidates = visible.filter((item) => opportunitySurface(item) === 'EXECUTION_PLATFORM');
    const broadCards = broadResearch.length
      ? broadResearch.slice(0, 12).map(renderOpportunity).join('')
      : '<article class="result-card"><strong>No broad public-web findings yet</strong><p>Partizan will keep the research domain separate from execution channels and only surface evidence-backed findings.</p></article>';
    const executionCards = executionCandidates.length
      ? executionCandidates.slice(0, 12).map(renderOpportunity).join('')
      : '<article class="result-card"><strong>No enabled execution-platform candidates</strong><p>Research-only surfaces above stay visible. Change channel preferences only if you want Partizan to consider execution on supported platforms.</p></article>';
    const results = $('research-results');
    results.innerHTML = `<div><h3>Who Partizan would target first</h3><div class="result-grid">${result.icps.map((item) => `<article class="result-card"><strong>${escapeHtml(item.title)}</strong><span>${Math.round(item.score)}/100 fit</span><p>${escapeHtml(item.description)}</p></article>`).join('')}</div></div><div><h3>Broad research surfaces</h3><p>These are evidence-backed findings, not connected channels and not automatic execution permission.</p><div class="result-grid">${broadCards}</div></div><div><h3>Execution-platform candidates</h3><p>Only enabled supported platforms appear here. A control-plane path still requires integration/identity/permission and normal safety checks before execution.</p><div class="result-grid">${executionCards}</div></div>`;
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
        showNotice(`Acquisition budget funded. Available: ${money(overview.growth_balance.available_usd)}.`);
        await loadWorkspace();
        await loadResearch(true);
        setActiveTab('overview');
        window.history.replaceState({}, '', `/workspace?project=${encodeURIComponent(projectId)}`);
      } catch (error) {
        showNotice(error.message, true);
      }
    } else if (growthState === 'cancelled') {
      showNotice('acquisition budget checkout cancelled. No funds were added.');
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

  $('overview-fund-button').addEventListener('click', openFundingControls);
  $('activation-primary').addEventListener('click', () => {
    if (activationAction === 'opportunity' && activationOpportunityUrl) {
      window.open(activationOpportunityUrl, '_blank', 'noopener,noreferrer');
      return;
    }
    if (activationAction === 'preview-research') {
      retryPreviewResearch().catch(() => {});
      return;
    }
    if (activationAction === 'fund') {
      const recommended = Number(workspace?.preview_opportunity?.estimated_cost_max_usd || 0);
      if (recommended > 0) $('fund-amount').value = String(recommended);
      return openFundingControls();
    }
    if (activationAction === 'research') return openResearchControls();
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
  const retryPreviewResearch = async () => {
    const button = $('research-button');
    const activationButton = $('activation-primary');
    const originalResearch = button.textContent;
    const originalActivation = activationButton.textContent;
    button.disabled = true;
    activationButton.disabled = true;
    button.textContent = 'Researching public evidence…';
    activationButton.textContent = 'Researching public evidence…';
    try {
      const result = await api(`/customer/workspace/${projectId}/preview-research`, {
        method: 'POST',
      });
      if (result.free_opportunity) {
        showNotice('Partizan found a concrete public-web opportunity with evidence.');
      } else {
        showNotice(result.research_message || 'Partizan still needs stronger public evidence.');
      }
      await loadWorkspace();
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      activationButton.disabled = false;
      button.textContent = originalResearch;
      activationButton.textContent = originalActivation;
    }
  };

  $('research-button').addEventListener('click', () => {
    if (researchAction === 'preview') {
      retryPreviewResearch().catch(() => {});
      return;
    }
    loadResearch(true).catch(() => {});
  });

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