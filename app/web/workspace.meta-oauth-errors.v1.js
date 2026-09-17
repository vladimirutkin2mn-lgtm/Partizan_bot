(() => {
  const params = new URLSearchParams(window.location.search);
  const metaState = params.get('meta') || '';
  const projectIdFromUrl = params.get('project') || '';
  const nativeFetch = window.fetch.bind(window);

  const requestDetails = (input, options) => {
    const request = input instanceof Request ? input : null;
    const method = String(options?.method || request?.method || 'GET').toUpperCase();
    let url;
    try {
      url = new URL(request?.url || String(input), window.location.origin);
    } catch (_) {
      return null;
    }
    return { request, method, url };
  };

  window.fetch = async (input, options = {}) => {
    const details = requestDetails(input, options);
    const match = details?.url?.pathname.match(/^\/customer\/workspace\/([^/]+)\/meta\/connect$/);
    if (
      details
      && details.url.origin === window.location.origin
      && details.method === 'POST'
      && match
    ) {
      const nextUrl = new URL(details.url.toString());
      nextUrl.pathname = `/customer/workspace/${match[1]}/meta-guided/connect`;
      if (details.request) {
        return nativeFetch(new Request(nextUrl.toString(), details.request), options);
      }
      return nativeFetch(nextUrl.toString(), options);
    }
    return nativeFetch(input, options);
  };

  const errorMessages = {
    access_denied: 'Meta did not grant the requested access. Open Connect Meta again and approve the requested permissions.',
    missing_oauth_response: 'Meta returned without completing authorization. Start Connect Meta again.',
    no_manageable_ad_accounts: 'Meta authorized, but no manageable ad accounts were returned for this Facebook user.',
    no_promotable_pages: 'Meta authorized the ad account, but no Facebook Page available for promotion was returned.',
    oauth_state_invalid: 'This Meta authorization attempt expired or became invalid. Start Connect Meta again.',
    meta_api_rejected: 'Meta rejected an API request after authorization. Partizan did not save the connection.',
    partizan_meta_oauth_failed: 'Partizan could not complete the Meta authorization flow.',
  };

  const currentProjectId = () => (
    new URLSearchParams(window.location.search).get('project')
    || document.getElementById('project-switcher')?.value
    || projectIdFromUrl
  );

  const showNotice = (message, isError = false) => {
    const node = document.getElementById('notice');
    if (!node) return;
    node.textContent = message;
    node.classList.toggle('error', isError);
    node.classList.remove('hidden');
    window.setTimeout(() => node.classList.add('hidden'), 9000);
  };

  const apiJson = async (path, options = {}) => {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(options.headers || {}) },
      ...options,
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      throw new Error(String(payload?.detail || `Request failed (${response.status})`));
    }
    return payload;
  };

  const cleanMetaCallbackQuery = () => {
    const projectId = currentProjectId();
    if (!projectId) return;
    window.history.replaceState({}, '', `/workspace?project=${encodeURIComponent(projectId)}`);
  };

  const ensureGuide = () => {
    let node = document.getElementById('meta-guided-setup');
    if (node) return node;
    const form = document.getElementById('meta-options-form');
    const connect = document.getElementById('meta-connect');
    const integration = connect?.closest('.integration-card');
    const parent = form?.parentElement || integration?.parentElement;
    if (!parent) return null;
    node = document.createElement('div');
    node.id = 'meta-guided-setup';
    node.className = 'meta-options hidden';
    if (form) parent.insertBefore(node, form);
    else parent.appendChild(node);
    return node;
  };

  const linkButton = (label, url, primary = false) => {
    const anchor = document.createElement('a');
    anchor.className = `button ${primary ? 'button-primary' : 'button-secondary'}`;
    anchor.textContent = label;
    anchor.href = url;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    return anchor;
  };

  const button = (label, onClick, primary = false) => {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = `button ${primary ? 'button-primary' : 'button-secondary'}`;
    node.textContent = label;
    node.addEventListener('click', onClick);
    return node;
  };

  const renderGuidedSetup = (setup) => {
    const guide = ensureGuide();
    if (!guide) return;
    const metaStateNode = document.getElementById('meta-state');
    const metaDetail = document.getElementById('meta-detail');
    const metaConnect = document.getElementById('meta-connect');

    if (!setup || ['NOT_STARTED', 'READY'].includes(setup.status)) {
      guide.classList.add('hidden');
      return;
    }

    guide.replaceChildren();
    guide.classList.remove('hidden');
    if (metaStateNode) metaStateNode.textContent = 'Setup needed';
    if (metaDetail) metaDetail.textContent = setup.message;

    const title = document.createElement('strong');
    title.textContent = 'Meta setup needs one more step';
    guide.appendChild(title);

    const copy = document.createElement('p');
    copy.className = 'note';
    copy.textContent = setup.message;
    guide.appendChild(copy);

    const facts = [];
    if (setup.business_count) facts.push(`${setup.business_count} Business Portfolio${setup.business_count === 1 ? '' : 's'} found`);
    if (setup.ad_account_count) facts.push(`${setup.ad_account_count} ad account${setup.ad_account_count === 1 ? '' : 's'} found`);
    if (setup.promotable_page_count) facts.push(`${setup.promotable_page_count} promotable Page${setup.promotable_page_count === 1 ? '' : 's'} found`);
    if (facts.length) {
      const factNode = document.createElement('p');
      factNode.className = 'note';
      factNode.textContent = facts.join(' · ');
      guide.appendChild(factNode);
    }

    if (setup.status === 'BUSINESS_NEEDS_AD_ACCOUNT') {
      const question = document.createElement('p');
      question.className = 'note';
      question.textContent = 'Do you already use Meta Ads Manager for this business?';
      guide.appendChild(question);
    }

    const actions = document.createElement('div');
    actions.className = 'inline-form';

    if (setup.primary_url) {
      const labels = {
        NO_META_BUSINESS: 'Open Meta Business setup →',
        BUSINESS_NEEDS_AD_ACCOUNT: 'Yes — fix ad account access →',
        AD_ACCOUNT_NEEDS_PAGE: 'Open Page settings →',
      };
      actions.appendChild(linkButton(labels[setup.status] || 'Open Meta Business Settings →', setup.primary_url, true));
    }
    if (setup.secondary_url && setup.status === 'BUSINESS_NEEDS_AD_ACCOUNT') {
      actions.appendChild(linkButton('No — open Ads Manager →', setup.secondary_url));
    }

    if (setup.can_check_again) {
      actions.appendChild(button('Check again', async (event) => {
        const control = event.currentTarget;
        const original = control.textContent;
        control.disabled = true;
        control.textContent = 'Checking Meta…';
        try {
          const projectId = currentProjectId();
          const next = await apiJson(`/customer/workspace/${encodeURIComponent(projectId)}/meta-guided/check`, { method: 'POST' });
          if (next.status === 'READY') {
            showNotice('Meta setup is ready. Choose the ad account and Facebook Page Partizan should use.');
            window.location.assign(`/workspace?project=${encodeURIComponent(projectId)}&meta=connected`);
            return;
          }
          renderGuidedSetup(next);
          showNotice('Partizan checked Meta again. One setup step is still required.', true);
        } catch (error) {
          showNotice(error.message, true);
        } finally {
          control.disabled = false;
          control.textContent = original;
        }
      }));
    }

    if (setup.status === 'REAUTHORIZE_REQUIRED') {
      if (metaConnect) metaConnect.textContent = 'Reconnect Meta →';
      actions.appendChild(button('Reconnect Meta →', () => metaConnect?.click(), true));
    }

    guide.appendChild(actions);
  };

  const loadGuidedSetup = async () => {
    const projectId = currentProjectId();
    if (!projectId) return;
    try {
      const setup = await apiJson(`/customer/workspace/${encodeURIComponent(projectId)}/meta-guided/setup`);
      renderGuidedSetup(setup);
      if (!['NOT_STARTED', 'READY'].includes(setup.status)) {
        if (metaState === 'connected') {
          showNotice(setup.message, true);
          cleanMetaCallbackQuery();
        }
      }
    } catch (_) {
      // Guided setup is supplemental. The existing Meta connection UI remains usable.
    }
  };

  const renderCallbackError = () => {
    if (metaState !== 'error') return;
    const error = params.get('meta_error') || '';
    const reason = params.get('meta_reason') || '';
    const code = params.get('meta_code') || '';
    const detail = [
      reason ? `reason ${reason}` : '',
      code ? `code ${code}` : '',
    ].filter(Boolean).join(', ');
    const base = errorMessages[error] || 'Meta connection was not completed.';
    showNotice(detail ? `${base} (${detail})` : base, true);
  };

  let initialized = false;
  const initialize = () => {
    if (initialized) return;
    initialized = true;
    window.setTimeout(renderCallbackError, 0);
    window.setTimeout(() => loadGuidedSetup(), 0);
  };

  window.addEventListener('partizan:workspace-ready', initialize, { once: true });
  window.setTimeout(initialize, 1200);
})();
