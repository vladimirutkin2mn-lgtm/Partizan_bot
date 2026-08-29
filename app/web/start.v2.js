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
  let managementFeePct = 10;
  let pendingUnderstanding = null;

  const showNotice = (message, error = false) => {
    notice.textContent = message;
    notice.classList.toggle('error', error);
    notice.classList.remove('hidden');
    window.setTimeout(() => notice.classList.add('hidden'), 5200);
  };

  const api = async (path, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (options.body != null) headers.set('Content-Type', 'application/json');
    if (currentToken) headers.set('X-Partizan-Customer-Token', currentToken);
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

  const accountApi = async (path, options = {}) => {
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

  const tokenKey = (projectId) => `${TOKEN_PREFIX}${projectId}`;
  const previewKey = (projectId) => `${PREVIEW_PREFIX}${projectId}`;
  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

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

  const setProgress = (step) => {
    $('progress-2').classList.toggle('on', step >= 2);
    $('progress-3').classList.toggle('on', step >= 3);
    $('progress-4').classList.toggle('on', step >= 4);
  };

  const showStage = (stage) => {
    [stageInput, stagePreview, stageUnlocked].forEach((node) => node.classList.add('hidden'));
    stage.classList.remove('hidden');
    if (stage === stagePreview || stage === stageUnlocked) setProgress(4);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const intakeSteps = [
    $('intake-product-step'),
    $('intake-clarification-step'),
    $('intake-understanding-step'),
    $('intake-goal-step'),
    $('intake-budget-step'),
  ];
  const intakeProgress = [1, 1, 2, 3, 4];

  const showIntakeStep = (step) => {
    showStage(stageInput);
    intakeSteps.forEach((node, index) => node.classList.toggle('hidden', index + 1 !== step));
    setProgress(intakeProgress[step - 1] || 1);
  };

  const renderProductClarification = (data) => {
    managementFeePct = Number(data.managed_spend_fee_pct || managementFeePct);
    pendingUnderstanding = { ...data.understanding };
    const clarification = data.clarification;
    $('product-clarification-question').textContent = clarification.question;
    $('product-clarification-rationale').textContent = clarification.rationale
      || 'Partizan only asks when this detail materially changes the product understanding.';
    $('product-clarification-answer').value = '';
    showIntakeStep(2);
  };

  const renderUnderstanding = (data) => {
    managementFeePct = Number(data.managed_spend_fee_pct || managementFeePct);
    pendingUnderstanding = { ...data.understanding };
    const values = {
      product: pendingUnderstanding.product,
      for_whom: pendingUnderstanding.for_whom,
      likely_customer: pendingUnderstanding.likely_customer,
      likely_first_audiences: Array.isArray(pendingUnderstanding.likely_first_audiences)
        ? pendingUnderstanding.likely_first_audiences
        : [],
      market: pendingUnderstanding.market,
    };
    $('understanding-source-label').textContent = data.source_label || 'Product source';
    $('understanding-product-type').textContent = [
      pendingUnderstanding.product_type,
      pendingUnderstanding.language,
      pendingUnderstanding.business_model,
    ].filter(Boolean).join(' · ');
    $('understanding-product').textContent = values.product;
    $('understanding-for').textContent = values.for_whom;
    $('understanding-customer').textContent = values.likely_customer;
    $('understanding-audiences').textContent = values.likely_first_audiences.length
      ? values.likely_first_audiences.join(' · ')
      : 'Not determined yet';
    $('understanding-market').textContent = values.market;
    $('understanding-product-input').value = values.product;
    $('understanding-for-input').value = values.for_whom;
    $('understanding-customer-input').value = values.likely_customer;
    $('understanding-audiences-input').value = values.likely_first_audiences.join('\n');
    $('understanding-market-input').value = values.market;
    $('understanding-edit').classList.add('hidden');
    $('understanding-edit-button').textContent = 'Edit';
    $('understanding-confirm').textContent = 'Looks right →';
    showIntakeStep(3);
  };

  const surfaceLabel = (surface) => ({
    COMMUNITY: 'Public community',
    DIRECTORY: 'Directory / review site',
    CREATOR: 'Creator',
    MEDIA: 'Media / podcast / newsletter',
    PARTNERSHIP: 'Partnership / affiliate',
    SEARCH: 'Search / SEO',
  })[surface] || surface;

  const resetResearchOutcomeCopy = () => {
    $('preview-eyebrow').textContent = 'Real research · before funding';
    $('preview-title').innerHTML = 'Partizan found a place<br><em>worth investigating.</em>';
    $('preview-summary').textContent = 'This is public-web research evidence, not a conversion claim. No acquisition budget was required to see it.';
    $('value-first-title').textContent = 'Keep this opportunity and let Partizan continue from here.';
    $('value-first-copy').textContent = 'Create a workspace around the researched opportunity. Partizan will only ask for money or channel access when a specific recommended action actually needs it.';
    $('value-first-note').textContent = 'Research evidence is not a proven acquisition result. Real visits, signups and customers are what Partizan learns from next.';
    $('autonomous-button').textContent = 'Continue with this move →';
    $('preview-research-retry').classList.add('hidden');
  };

  const renderFreeOpportunity = (data) => {
    resetResearchOutcomeCopy();
    managementFeePct = Number(data.managed_spend_fee_pct || managementFeePct);
    pendingUnderstanding = { ...data.understanding };
    const item = data.free_opportunity;
    const maxCost = Number(item.estimated_cost_max_usd || 0);
    const minCost = Number(item.estimated_cost_min_usd || 0);
    const cost = maxCost === 0 ? '$0' : (minCost === maxCost ? `$${maxCost}` : `$${minCost}–$${maxCost}`);
    const evidence = Array.isArray(item.provenance) ? item.provenance.slice(0, 3) : [];
    $('free-opportunity').innerHTML = `
      <div class="free-opportunity-head">
        <div><span class="eyebrow">${escapeHtml(surfaceLabel(item.surface))}</span><h2>${escapeHtml(item.title)}</h2></div>
        <span class="cost-pill">${escapeHtml(cost)} first move</span>
      </div>
      <div class="opportunity-detail"><span>Why Partizan selected it</span><p>${escapeHtml(item.rationale)}</p></div>
      <div class="opportunity-detail"><span>Recommended first move</span><p>${escapeHtml(item.recommended_action)}</p></div>
      <div class="opportunity-detail"><span>Signal to watch</span><p>${escapeHtml(item.signal_to_watch)}</p></div>
      <div class="opportunity-evidence"><span>Evidence</span>${evidence.map((source) => `
        <a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">
          <strong>${escapeHtml(source.title || 'Public source')}</strong>
          ${source.snippet ? `<small>${escapeHtml(source.snippet)}</small>` : ''}
        </a>`).join('')}</div>
      <div class="opportunity-boundary">${escapeHtml(item.execution_requirement)}</div>
      ${item.url ? `<a class="text-button opportunity-open" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">Open opportunity ↗</a>` : ''}
    `;
    $('scope-title').textContent = 'Want Partizan to research the rest?';
    $('unlock-price').textContent = `Full Acquisition Plan — $${data.launch_price_usd} once`;
    $('autonomous-price').textContent = `No monthly fee · ${managementFeePct}% only on acquisition spend`;
    $('account-gate').classList.add('hidden');
    const existing = storedPreview(currentProjectId) || {};
    rememberProject(currentProjectId, currentToken, { ...existing, ...data });
    showStage(stagePreview);
  };

  const renderResearchPending = (data) => {
    managementFeePct = Number(data.managed_spend_fee_pct || managementFeePct);
    pendingUnderstanding = { ...data.understanding };
    const unavailable = data.research_status === 'UNAVAILABLE';
    const directions = Array.isArray(data.directions) ? data.directions.slice(0, 3) : [];

    $('preview-eyebrow').textContent = unavailable
      ? 'Research status · no synthetic results'
      : 'Research before spend · evidence threshold not met';
    $('preview-title').innerHTML = unavailable
      ? 'Public-web research is<br><em>temporarily unavailable.</em>'
      : 'Partizan found hypotheses,<br><em>but not enough evidence yet.</em>';
    $('preview-summary').textContent = data.research_message || (
      unavailable
        ? 'Partizan will not invent an opportunity. Retry public-web research without adding acquisition funds.'
        : 'Keep researching without funding. A hypothesis is not presented as a real opportunity until public evidence supports it.'
    );
    $('free-opportunity').innerHTML = `
      <div class="research-pending-head">
        <span class="eyebrow">${unavailable ? 'No fabricated fallback' : 'Starting hypotheses · not researched opportunities'}</span>
        <h2>${unavailable ? 'Evidence is unavailable right now.' : 'These are directions to investigate next.'}</h2>
      </div>
      <div class="research-hypotheses">${directions.map((item) => `
        <article>
          <span>Hypothesis</span>
          <strong>${escapeHtml(item.name)}</strong>
          <p>${escapeHtml(item.rationale)}</p>
        </article>
      `).join('')}</div>
      <div class="opportunity-boundary">No acquisition funding is required to continue this research. Partizan will only show a researched opportunity when it has real public-web evidence.</div>
    `;
    $('preview-research-retry').classList.remove('hidden');
    $('value-first-title').textContent = 'Keep this project and continue research in your workspace.';
    $('value-first-copy').textContent = 'Save the product understanding and research state, then keep looking for evidence when you are ready. Acquisition funding is not required.';
    $('value-first-note').textContent = 'Partizan will not turn a channel hypothesis into a named opportunity unless the public evidence supports it.';
    $('autonomous-button').textContent = 'Continue without funding →';
    $('scope-title').textContent = 'Prefer a full research-only pass?';
    $('unlock-price').textContent = `Full Acquisition Plan — ${data.launch_price_usd} once`;
    $('autonomous-price').textContent = 'No acquisition funding required';
    $('account-gate').classList.add('hidden');
    const existing = storedPreview(currentProjectId) || {};
    rememberProject(currentProjectId, currentToken, { ...existing, ...data });
    showStage(stagePreview);
  };

  const renderResearchOutcome = (data) => {
    if (data.free_opportunity) renderFreeOpportunity(data);
    else renderResearchPending(data);
  };

  const renderPreview = (data) => {
    if (data.research_status) renderResearchOutcome(data);
    else if (data.free_opportunity) renderFreeOpportunity(data);
    else if (data.clarification) renderProductClarification(data);
    else renderUnderstanding(data);
  };

  const showBriefFallback = () => {
    $('brief-fallback').classList.remove('hidden');
    $('no-public-link-button').classList.add('hidden');
    $('brief').focus();
  };

  $('no-public-link-button').addEventListener('click', showBriefFallback);
  $('product-link').addEventListener('input', () => {
    if ($('product-link').value.trim()) $('brief-fallback').classList.add('hidden');
  });

  const budgetInput = $('budget');
  const budgetPresets = [...document.querySelectorAll('.budget-preset')];

  const selectBudgetPreset = (button) => {
    budgetPresets.forEach((item) => item.classList.toggle('active', item === button));
    const value = button.dataset.budget;
    if (value === 'custom') {
      budgetInput.focus();
      budgetInput.select();
      return;
    }
    budgetInput.value = value;
  };

  budgetPresets.forEach((button) => {
    button.addEventListener('click', () => selectBudgetPreset(button));
  });
  budgetInput.addEventListener('input', () => {
    const matching = budgetPresets.find((button) => button.dataset.budget === budgetInput.value);
    budgetPresets.forEach((button) => button.classList.toggle('active', button === matching));
  });

  $('preview-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const productLink = $('product-link').value.trim();
    const brief = $('brief').value.trim();
    if (!productLink && !brief) {
      showBriefFallback();
      showNotice('Paste a product link or describe what you built.', true);
      return;
    }
    const button = event.submitter;
    button.disabled = true;
    button.textContent = productLink ? 'Analyzing your product…' : 'Understanding your product…';
    try {
      const data = await api('/v1/customer-projects/preview', {
        method: 'POST',
        body: JSON.stringify({
          brief: brief || null,
          product_link: productLink || null,
        }),
      });
      rememberProject(data.project_id, data.customer_token, data);
      renderPreview(data);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Analyze my product →';
    }
  });

  $('product-clarification-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentProjectId || !currentToken) {
      showNotice('Product session is missing. Analyze the product again.', true);
      return;
    }
    const answer = $('product-clarification-answer').value.trim();
    if (!answer) return;
    const button = event.submitter;
    button.disabled = true;
    button.textContent = 'Updating product understanding…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/product-clarification`, {
        method: 'POST',
        body: JSON.stringify({ answer }),
      });
      const existing = storedPreview(currentProjectId) || {};
      rememberProject(currentProjectId, currentToken, { ...existing, ...data });
      renderPreview(data);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Continue analysis →';
    }
  });

  $('understanding-edit-button').addEventListener('click', () => {
    const edit = $('understanding-edit');
    const opening = edit.classList.contains('hidden');
    edit.classList.toggle('hidden', !opening);
    $('understanding-edit-button').textContent = opening ? 'Cancel edit' : 'Edit';
    $('understanding-confirm').textContent = opening ? 'Save & continue →' : 'Looks right →';
  });

  $('understanding-confirm').addEventListener('click', () => {
    pendingUnderstanding = {
      product: $('understanding-product-input').value.trim(),
      for_whom: $('understanding-for-input').value.trim(),
      likely_customer: $('understanding-customer-input').value.trim(),
      likely_first_audiences: $('understanding-audiences-input').value
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean)
        .slice(0, 5),
      market: $('understanding-market-input').value.trim(),
    };
    if (Object.values(pendingUnderstanding).some((value) => !value)) {
      showNotice('Confirm the product, audience and market before continuing.', true);
      return;
    }
    showIntakeStep(4);
  });

  $('goal-continue').addEventListener('click', () => showIntakeStep(5));

  $('budget-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentProjectId || !currentToken || !pendingUnderstanding) {
      showNotice('Product understanding is missing. Analyze the product again.', true);
      return;
    }
    const button = event.submitter;
    button.disabled = true;
    button.textContent = 'Researching one real opportunity…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/confirm-preview`, {
        method: 'POST',
        body: JSON.stringify({
          ...pendingUnderstanding,
          goal: $('goal').value,
          budget_usd: Number($('budget').value),
        }),
      });
      renderResearchOutcome(data);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Find one real opportunity →';
    }
  });

  $('preview-research-retry').addEventListener('click', async () => {
    if (!currentProjectId || !currentToken) {
      showNotice('Project session is missing. Analyze the product again.', true);
      return;
    }
    const button = $('preview-research-retry');
    button.disabled = true;
    button.textContent = 'Researching public evidence…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/preview-research`, {
        method: 'POST',
      });
      renderResearchOutcome(data);
      if (!data.free_opportunity) {
        showNotice(data.research_message || 'Partizan still needs stronger public evidence.');
      }
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Keep researching →';
    }
  });

  const redirectWorkspace = (projectId) => {
    clearStoredProject(projectId);
    window.location.assign(`/workspace?project=${encodeURIComponent(projectId)}`);
  };

  const claimIntoCurrentAccount = async () => {
    if (!currentProjectId || !currentToken) throw new Error('Project session is missing. Analyze the product again.');
    const account = await accountApi('/customer/account/me');
    if (account.projects.some((item) => item.project_id === currentProjectId)) {
      redirectWorkspace(currentProjectId);
      return true;
    }
    await accountApi('/customer/account/projects/claim', {
      method: 'POST',
      body: JSON.stringify({ project_id: currentProjectId, customer_token: currentToken }),
    });
    redirectWorkspace(currentProjectId);
    return true;
  };

  const openAccountGate = async () => {
    if (!currentProjectId || !currentToken) {
      showNotice('Project session is missing. Analyze the product again.', true);
      return;
    }
    try {
      await claimIntoCurrentAccount();
      return;
    } catch (error) {
      if (error.status !== 401) {
        showNotice(error.message, true);
        return;
      }
    }
    $('account-gate').classList.remove('hidden');
    $('account-gate').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  $('autonomous-button').addEventListener('click', openAccountGate);
  $('research-only-link').addEventListener('click', () => $('acquisition-plan-card').scrollIntoView({ behavior: 'smooth', block: 'start' }));
  $('show-login').addEventListener('click', () => {
    $('register-form').classList.add('hidden');
    $('login-form').classList.remove('hidden');
  });
  $('show-register').addEventListener('click', () => {
    $('login-form').classList.add('hidden');
    $('register-form').classList.remove('hidden');
  });

  $('register-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Analyze the product again.', true);
    const button = event.submitter;
    button.disabled = true;
    button.textContent = 'Creating workspace…';
    try {
      await accountApi('/customer/account/register', {
        method: 'POST',
        body: JSON.stringify({
          email: $('register-email').value.trim(),
          password: $('register-password').value,
          project_id: currentProjectId,
          customer_token: currentToken,
        }),
      });
      redirectWorkspace(currentProjectId);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
      button.textContent = 'Create workspace →';
    }
  });

  $('login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Analyze the product again.', true);
    const button = event.submitter;
    button.disabled = true;
    button.textContent = 'Signing in…';
    try {
      await accountApi('/customer/account/login', {
        method: 'POST',
        body: JSON.stringify({
          email: $('login-email').value.trim(),
          password: $('login-password').value,
        }),
      });
      await claimIntoCurrentAccount();
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
      button.textContent = 'Sign in & continue →';
    }
  });

  $('checkout-button').addEventListener('click', async () => {
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Analyze the product again.', true);
    const button = $('checkout-button');
    button.disabled = true;
    button.textContent = 'Opening secure checkout…';
    try {
      const data = await api(`/v1/customer-projects/${currentProjectId}/checkout`, { method: 'POST' });
      if (data.already_unlocked) {
        showUnlocked();
        await startResearch();
        return;
      }
      window.location.assign(data.checkout_url);
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
      button.textContent = 'Get research-only plan →';
    }
  });

  const showUnlocked = () => {
    showStage(stageUnlocked);
    $('research-button').disabled = false;
  };

  const pollEntitlement = async () => {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const project = await api(`/v1/customer-projects/${currentProjectId}`);
      if (project.launch_unlocked) {
        showUnlocked();
        await startResearch();
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    showNotice('Payment received. Confirmation is taking a little longer than usual — refresh in a few seconds.', true);
  };

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
  $('research-button').addEventListener('click', startResearch);

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
  const renderOpportunity = (item) => `<article class="opp-card"><header><h3>${escapeHtml(item.title)}</h3><span class="platform">${escapeHtml(opportunityMeta(item))} · ${escapeHtml(executionStatusLabels[opportunityStatus(item)] || opportunityStatus(item))}</span></header><p>${escapeHtml(item.rationale || 'Relevant distribution opportunity')}</p><div class="hook">${escapeHtml(executionRequirement(item))}</div>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Open opportunity ↗</a>` : ''}${renderEvidence(item)}</article>`;

  const renderResearch = (result) => {
    if (result.state === 'NEEDS_INPUT') {
      const question = result.clarifications[0];
      const box = $('clarification-box');
      box.innerHTML = `<span class="eyebrow">One useful clarification</span><h3>${escapeHtml(question.question)}</h3><p>${escapeHtml(question.rationale)}</p><form id="clarification-form"><input id="clarification-answer" required placeholder="Your answer"><button class="button button-primary" type="submit">Continue →</button></form>`;
      box.classList.remove('hidden');
      $('research-button').classList.add('hidden');
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
    const opportunities = Array.isArray(result.opportunities) ? result.opportunities : [];
    const broadResearch = opportunities.filter((item) => opportunitySurface(item) !== 'EXECUTION_PLATFORM');
    const executionCandidates = opportunities.filter((item) => opportunitySurface(item) === 'EXECUTION_PLATFORM');
    const broadSection = broadResearch.length
      ? `<div class="results-head"><span class="eyebrow">Broad research</span><h2>Research surfaces beyond connected channels</h2><p>Creators, media, partnerships, search demand, directories and public communities are findings — not connected execution channels.</p></div><div class="opportunity-list">${broadResearch.map(renderOpportunity).join('')}</div>`
      : '';
    const executionSection = executionCandidates.length
      ? `<div class="results-head"><span class="eyebrow">Execution-platform candidates</span><h2>Where Partizan also has a control-plane path</h2><p>Discovery alone never authorizes posting or spend. Enabled channel state, integration/identity/permission and the normal safety checks still apply.</p></div><div class="opportunity-list">${executionCandidates.map(renderOpportunity).join('')}</div>`
      : '';
    const results = $('research-results');
    results.innerHTML = `<div class="results-head"><span class="eyebrow">Research ready</span><h2>Who Partizan would target first</h2><p>Your highest-value customer segments, ranked by fit and buying potential.</p></div><div class="icp-grid">${result.icps.map((item) => `<article class="icp-card"><header><h3>${escapeHtml(item.title)}</h3><span class="score">${Math.round(item.score)}/100</span></header><p>${escapeHtml(item.description)}</p><div class="hook">${escapeHtml(item.message_hook)}</div></article>`).join('')}</div>${broadSection}${executionSection}`;
    results.classList.remove('hidden');
    $('payment-status').querySelector('strong').textContent = 'Research mapped';
    $('payment-status').querySelector('small').textContent = 'Your Acquisition Plan is ready. Research findings remain separate from execution permission.';
  };

  const accountOwnsProject = async (projectId) => {
    try {
      const account = await accountApi('/customer/account/me');
      return account.projects.some((item) => item.project_id === projectId);
    } catch (_) {
      return false;
    }
  };

  const resumeStoredProject = async () => {
    const storedProjectId = localStorage.getItem(PROJECT_KEY);
    if (!storedProjectId || !loadProjectToken(storedProjectId)) return;
    try {
      const project = await api(`/v1/customer-projects/${storedProjectId}`);
      managementFeePct = Number(project.managed_spend_fee_pct || managementFeePct);
      if (project.launch_unlocked) {
        showUnlocked();
        await startResearch();
        showNotice('Welcome back. Your Acquisition Plan is restored.');
        return;
      }
      const preview = storedPreview(storedProjectId);
      if (preview) {
        renderPreview(preview);
        showNotice(preview.free_opportunity
          ? 'Welcome back. Your researched opportunity is restored.'
          : 'Welcome back. Your product understanding is restored.');
      }
    } catch (_) {
      if (await accountOwnsProject(storedProjectId)) {
        clearStoredProject(storedProjectId);
        window.location.replace(`/workspace?project=${encodeURIComponent(storedProjectId)}`);
      } else {
        clearStoredProject(storedProjectId);
      }
    }
  };

  const params = new URLSearchParams(window.location.search);
  const initialProductLink = String(params.get('product') || params.get('website') || '').trim();
  if (initialProductLink) $('product-link').value = initialProductLink;
  const initialBudget = Number(params.get('budget'));
  if (Number.isFinite(initialBudget) && initialBudget >= 1) {
    $('budget').value = String(initialBudget);
    const matchingPreset = budgetPresets.find(
      (button) => button.dataset.budget === String(initialBudget),
    );
    budgetPresets.forEach((button) => button.classList.toggle('active', button === matchingPreset));
  }

  const bootstrapCallbacks = async () => {
    const projectId = params.get('project');
    const checkoutState = params.get('checkout');
    const checkoutSessionId = params.get('session_id');
    const growthBalanceState = params.get('growth_balance');
    const metaState = params.get('meta');

    if (projectId && (growthBalanceState || metaState)) {
      if (await accountOwnsProject(projectId)) {
        const query = new URLSearchParams();
        query.set('project', projectId);
        if (growthBalanceState) query.set('growth_balance', growthBalanceState);
        if (metaState) query.set('meta', metaState);
        if (checkoutSessionId) query.set('session_id', checkoutSessionId);
        window.location.replace(`/workspace?${query.toString()}`);
        return true;
      }
    }
    if (!projectId && metaState === 'error') {
      try {
        const account = await accountApi('/customer/account/me');
        if (account.projects.length) {
          window.location.replace(`/workspace?meta=error&project=${encodeURIComponent(account.projects[0].project_id)}`);
          return true;
        }
      } catch (_) { /* no signed-in account */ }
    }

    if (checkoutState && projectId) {
      if (checkoutState === 'success') {
        const hadStoredAccess = loadProjectToken(projectId);
        showUnlocked();
        $('payment-status').querySelector('strong').textContent = 'Confirming payment…';
        $('research-button').disabled = true;
        try {
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
        } catch (error) {
          showNotice(error.message, true);
        }
        return true;
      }
      if (checkoutState === 'cancelled') {
        if (loadProjectToken(projectId)) {
          const preview = storedPreview(projectId);
          if (preview) renderPreview(preview);
        }
        showNotice('Checkout cancelled. Your product analysis is still here whenever you’re ready.');
        return true;
      }
    }
    return false;
  };

  const bootstrap = async () => {
    if (params.get('login') === 'required') showNotice('Sign in or create a workspace after analyzing your product.');
    const callbackHandled = await bootstrapCallbacks();
    if (!callbackHandled) await resumeStoredProject();
  };

  bootstrap().catch((error) => showNotice(error.message, true));
})();