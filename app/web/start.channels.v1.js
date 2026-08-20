(() => {
  const $ = (id) => document.getElementById(id);
  const TOKEN_PREFIX = 'partizan.customer.token.';
  const notice = $('notice');
  let applyingFriendlyState = false;

  const showFriendlyNotice = (message) => {
    if (!notice) return;
    notice.textContent = message;
    notice.classList.remove('error');
    notice.classList.remove('hidden');
    window.setTimeout(() => notice.classList.add('hidden'), 5200);
  };

  const technicalFundingCopy = (value) => [
    'Partizan-funded payment rail',
    'Funding rail setup required',
    'STRIPE_ISSUING',
    'STRIPE_NOT_CONFIGURED',
  ].some((fragment) => String(value || '').includes(fragment));

  const applyFriendlyState = () => {
    if (applyingFriendlyState) return;
    applyingFriendlyState = true;
    try {
      const metaButton = $('meta-connect-button');
      const metaStatus = $('meta-connection-status');
      if (metaButton && metaButton.dataset.busy !== '1') metaButton.disabled = false;
      if (metaStatus && (
        metaStatus.textContent.includes('Fund Growth Balance first')
        || metaStatus.textContent.includes('Meta connection unlocks when research is ready')
      )) {
        metaStatus.textContent = 'Connect now. Partizan will use this access only if Meta is selected for execution.';
      }

      const guardrailButton = $('autopilot-config-button');
      if (guardrailButton && guardrailButton.dataset.busy !== '1') guardrailButton.disabled = false;

      const fundingButton = $('growth-balance-button');
      const fundingStatus = $('growth-balance-status');
      if (fundingButton && fundingStatus) {
        const blocked = technicalFundingCopy(fundingButton.textContent)
          || technicalFundingCopy(fundingStatus.textContent);
        if (blocked) {
          fundingButton.dataset.fundingUnavailable = '1';
          fundingButton.disabled = false;
          fundingButton.textContent = 'Fund Growth Balance →';
          fundingStatus.textContent = 'Growth Balance is being enabled. You can connect channels and save guardrails now; funding will open here when ready.';
          fundingStatus.classList.remove('settlement-warning');
        } else if (
          fundingStatus.textContent.includes('Ready to fund')
          || fundingStatus.textContent.includes('funded')
        ) {
          fundingButton.dataset.fundingUnavailable = '0';
        }
      }
    } finally {
      applyingFriendlyState = false;
    }
  };

  const root = $('autopilot-card');
  if (root) {
    const observer = new MutationObserver(() => window.queueMicrotask(applyFriendlyState));
    observer.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['disabled'],
    });
  }

  const metaButton = $('meta-connect-button');
  if (metaButton) {
    metaButton.addEventListener('click', () => {
      metaButton.dataset.busy = '1';
      window.setTimeout(() => {
        delete metaButton.dataset.busy;
        applyFriendlyState();
      }, 5000);
    }, true);
  }

  const guardrailForm = $('autopilot-config-form');
  if (guardrailForm) {
    guardrailForm.addEventListener('submit', (event) => {
      const button = event.submitter || $('autopilot-config-button');
      if (!button) return;
      button.dataset.busy = '1';
      window.setTimeout(() => {
        delete button.dataset.busy;
        applyFriendlyState();
      }, 1800);
    }, true);
  }

  const fundingForm = $('growth-balance-form');
  if (fundingForm) {
    fundingForm.addEventListener('submit', (event) => {
      const fundingButton = $('growth-balance-button');
      if (!fundingButton || fundingButton.dataset.fundingUnavailable !== '1') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      showFriendlyNotice('Growth Balance is being enabled. Finish channel access and guardrails now; funding will become available here without changing your setup.');
    }, true);
  }

  const restoreSetupAfterEarlyMeta = async () => {
    const params = new URLSearchParams(window.location.search);
    if (!params.get('meta')) return;
    const projectId = params.get('project');
    if (!projectId) return;
    const token = localStorage.getItem(`${TOKEN_PREFIX}${projectId}`);
    if (!token) return;
    try {
      const response = await fetch(`/v1/customer-projects/${projectId}`, {
        headers: { 'X-Partizan-Customer-Token': token },
      });
      if (!response.ok) return;
      const project = await response.json();
      if (project.launch_unlocked) return;
      const stage = $('stage-unlocked');
      if (!stage) return;
      const eyebrow = stage.querySelector('.eyebrow');
      const heading = stage.querySelector('h1');
      const lede = stage.querySelector('.lede');
      if (eyebrow) eyebrow.textContent = 'Execution setup';
      if (heading) heading.innerHTML = 'Channel access connected.<br><em>Finish the setup below.</em>';
      if (lede) lede.textContent = 'You can connect channels and save guardrails before funding. Audience research starts automatically after Growth Balance is funded.';
      ['payment-status', 'research-button', 'research-progress', 'view-strategy-button', 'research-results'].forEach((id) => {
        const node = $(id);
        if (node) node.classList.add('hidden');
      });
      const autopilot = $('autopilot-card');
      if (autopilot) autopilot.classList.remove('hidden');
    } catch (_) {
      // The original onboarding script remains the source of truth if recovery fails.
    }
  };

  applyFriendlyState();
  restoreSetupAfterEarlyMeta();
})();
