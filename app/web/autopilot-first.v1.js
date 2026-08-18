(() => {
  const TOKEN_PREFIX = 'partizan.customer.token.';
  const PROJECT_KEY = 'partizan.customer.project';
  const $ = (id) => document.getElementById(id);
  let researchKicked = false;

  const showNotice = (message, error = false) => {
    const notice = $('notice');
    if (!notice) return;
    notice.textContent = message;
    notice.classList.toggle('error', error);
    notice.classList.remove('hidden');
    window.setTimeout(() => notice.classList.add('hidden'), 5200);
  };

  const projectAccess = (preferredProjectId = null) => {
    const projectId = preferredProjectId || localStorage.getItem(PROJECT_KEY);
    const token = projectId ? localStorage.getItem(`${TOKEN_PREFIX}${projectId}`) : null;
    if (!projectId || !token) throw new Error('Customer project access is missing. Run the pre-scan again.');
    return { projectId, token };
  };

  const customerApi = async (projectId, token, path = '', options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('Content-Type', 'application/json');
    headers.set('X-Partizan-Customer-Token', token);
    const response = await fetch(`/v1/customer-projects/${projectId}${path}`, { ...options, headers });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  };

  const insertAutopilotFirstChoice = () => {
    const stage = $('stage-preview');
    const locked = stage?.querySelector('.locked.panel');
    if (!stage || !locked || $('autopilot-direct-button')) return;
    const panel = document.createElement('section');
    panel.className = 'panel autopilot-first-launch';
    panel.innerHTML = `
      <div>
        <span class="eyebrow">Recommended · autonomous execution</span>
        <h2>Want customers, not another report?</h2>
        <p>Partizan can use the audience and channel analysis internally, choose the first experiments and show you only the decisions that matter. You can open the full strategy whenever you want.</p>
        <small class="autopilot-first-note">The Acquisition Plan is included with Autopilot. Your marketing budget stays separate and remains capped by your mandate.</small>
      </div>
      <div class="autopilot-first-actions">
        <div class="autopilot-first-price"><strong>$149/mo</strong><span>+ 10% managed spend</span></div>
        <button id="autopilot-direct-button" class="button button-primary" type="button">Launch Partizan →</button>
        <button id="inspect-first-button" class="autopilot-first-link" type="button">I want to inspect the strategy first</button>
      </div>`;
    stage.insertBefore(panel, locked);

    $('inspect-first-button').addEventListener('click', () => {
      locked.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $('autopilot-direct-button').addEventListener('click', async () => {
      const button = $('autopilot-direct-button');
      button.disabled = true;
      button.textContent = 'Opening secure checkout…';
      try {
        const { projectId, token } = projectAccess();
        const data = await customerApi(projectId, token, '/autopilot/checkout', { method: 'POST' });
        window.location.assign(data.checkout_url);
      } catch (error) {
        showNotice(error.message, true);
        button.disabled = false;
        button.textContent = 'Launch Partizan →';
      }
    });
  };

  const insertStrategyToggle = () => {
    const intro = document.querySelector('.autopilot-intro');
    if (!intro || $('view-strategy-button')) return;
    const button = document.createElement('button');
    button.id = 'view-strategy-button';
    button.className = 'button button-secondary autopilot-strategy-toggle hidden';
    button.type = 'button';
    button.textContent = 'View strategy & audience';
    button.addEventListener('click', () => {
      const results = $('research-results');
      if (!results || !results.children.length) return;
      const opening = !document.body.classList.contains('strategy-open');
      document.body.classList.toggle('strategy-open', opening);
      button.textContent = opening ? 'Hide strategy & audience' : 'View strategy & audience';
      if (opening) results.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    intro.appendChild(button);
  };

  const rewriteDirectAutopilotStage = () => {
    document.body.classList.add('autopilot-first-flow');
    const stage = $('stage-unlocked');
    if (!stage) return;
    const eyebrow = stage.querySelector(':scope > .eyebrow');
    const heading = stage.querySelector(':scope > h1');
    const lede = stage.querySelector(':scope > .lede');
    if (eyebrow) eyebrow.textContent = 'Autopilot launch';
    if (heading) heading.innerHTML = 'Partizan is mapping the market.<br><em>You can stay above the machinery.</em>';
    if (lede) lede.textContent = 'Audience research and channel selection now run under the hood. If Partizan needs one material product detail, it will ask — otherwise you can go straight to budget guardrails and account connections.';
    const status = $('payment-status');
    if (status) {
      const strong = status.querySelector('strong');
      const small = status.querySelector('small');
      if (strong) strong.textContent = 'Activating Autopilot…';
      if (small) small.textContent = 'Confirming the subscription before market mapping starts.';
    }
  };

  const markStrategyReady = () => {
    document.body.classList.add('strategy-ready');
    const toggle = $('view-strategy-button');
    if (toggle) toggle.classList.remove('hidden');
    const status = $('payment-status');
    if (status) {
      const strong = status.querySelector('strong');
      const small = status.querySelector('small');
      if (strong) strong.textContent = 'Strategy mapped';
      if (small) small.textContent = 'Partizan has the audience and acquisition map it needs. Review it only if you want to.';
    }
  };

  const watchResearchUi = () => {
    const results = $('research-results');
    if (results) {
      const observer = new MutationObserver(() => {
        if (results.children.length) markStrategyReady();
      });
      observer.observe(results, { childList: true, subtree: true });
      if (results.children.length) markStrategyReady();
    }
    const clarification = $('clarification-box');
    if (clarification) {
      const observer = new MutationObserver(() => {
        if (!clarification.classList.contains('hidden')) {
          const lede = $('stage-unlocked')?.querySelector(':scope > .lede');
          if (lede) lede.textContent = 'Partizan needs one product detail before it can choose the first experiments. Answer it once; you still do not need to review the audience analysis.';
        }
      });
      observer.observe(clarification, { attributes: true, attributeFilter: ['class'] });
    }
  };

  const startUnderTheHoodResearch = async (projectId) => {
    let access;
    try { access = projectAccess(projectId); } catch (error) {
      showNotice(error.message, true);
      return;
    }
    const researchButton = $('research-button');
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        const project = await customerApi(access.projectId, access.token);
        if (project.autopilot_subscription_status === 'ACTIVE') {
          const status = $('payment-status');
          if (status) {
            const strong = status.querySelector('strong');
            const small = status.querySelector('small');
            if (strong) strong.textContent = 'Autopilot subscription active';
            if (small) small.textContent = 'Partizan is mapping the audience and deciding where to start.';
          }
          if (!researchKicked && researchButton) {
            researchKicked = true;
            researchButton.click();
            window.setTimeout(async () => {
              try {
                const latest = await customerApi(access.projectId, access.token);
                if (latest.research_state === 'NOT_STARTED') {
                  researchButton.style.display = 'inline-flex';
                  researchButton.textContent = 'Retry market mapping →';
                }
              } catch (_) { /* Existing UI surfaces network errors. */ }
            }, 20000);
          }
          return;
        }
      } catch (_) { /* The regular checkout UI owns the visible verification error. */ }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
  };

  insertAutopilotFirstChoice();
  insertStrategyToggle();
  watchResearchUi();

  const params = new URLSearchParams(window.location.search);
  const autopilotCheckout = params.get('autopilot_checkout');
  const projectId = params.get('project');
  if (autopilotCheckout === 'success' && projectId) {
    rewriteDirectAutopilotStage();
    startUnderTheHoodResearch(projectId);
  }
})();
