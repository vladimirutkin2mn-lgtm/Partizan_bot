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
  };

  const params = new URLSearchParams(window.location.search);
  const initialBudget = Number(params.get('budget'));
  if (Number.isFinite(initialBudget) && initialBudget >= 100) $('budget').value = String(initialBudget);
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
})();
