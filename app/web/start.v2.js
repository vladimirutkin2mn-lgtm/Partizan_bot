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

  const showStage = (stage) => {
    [stageInput, stagePreview, stageUnlocked].forEach((node) => node.classList.add('hidden'));
    stage.classList.remove('hidden');
    $('progress-2').classList.toggle('on', stage !== stageInput);
    $('progress-3').classList.toggle('on', stage === stageUnlocked);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const renderPreview = (data) => {
    managementFeePct = Number(data.managed_spend_fee_pct || 10);
    $('preview-summary').textContent = `The free scan sees ${data.channel_count} directions worth testing. Strongest starting hypothesis: ${data.fastest_signal}. This is not deep research yet; the paid step verifies the audience and finds the actual opportunities.`;
    $('scope-title').textContent = `Deep research can investigate ~${data.opportunity_scope_estimate} concrete opportunities`;
    $('unlock-price').textContent = `Unlock Acquisition Plan — $${data.launch_price_usd}`;
    $('autonomous-price').textContent = `${managementFeePct}% of acquisition spend`;
    $('direction-grid').innerHTML = data.directions.map((item) => {
      const label = item.potential === 'HIGH' ? 'STRONG HYPOTHESIS' : 'WORTH TESTING';
      return `<article class="direction-card"><header><h3>${escapeHtml(item.name)}</h3><span class="potential ${item.potential === 'MEDIUM' ? 'medium' : ''}">${label}</span></header><p>${escapeHtml(item.rationale)}</p></article>`;
    }).join('');
    $('account-gate').classList.add('hidden');
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

  const redirectWorkspace = (projectId) => {
    clearStoredProject(projectId);
    window.location.assign(`/workspace?project=${encodeURIComponent(projectId)}`);
  };

  const claimIntoCurrentAccount = async () => {
    if (!currentProjectId || !currentToken) throw new Error('Project session is missing. Run the pre-scan again.');
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
      showNotice('Project session is missing. Run the pre-scan again.', true);
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
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Run the pre-scan again.', true);
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
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Run the pre-scan again.', true);
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
    if (!currentProjectId || !currentToken) return showNotice('Project session is missing. Run the pre-scan again.', true);
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
      button.textContent = 'Unlock & research →';
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
        showNotice('Welcome back. Your acquisition hypotheses are restored.');
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
  const initialBudget = Number(params.get('budget'));
  if (Number.isFinite(initialBudget) && initialBudget >= 1) $('budget').value = String(initialBudget);

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
        showNotice('Checkout cancelled. Your pre-scan is still here whenever you’re ready.');
        return true;
      }
    }
    return false;
  };

  const bootstrap = async () => {
    if (params.get('login') === 'required') showNotice('Sign in or create a workspace after running a free pre-scan.');
    const callbackHandled = await bootstrapCallbacks();
    if (!callbackHandled) await resumeStoredProject();
  };

  bootstrap().catch((error) => showNotice(error.message, true));
})();