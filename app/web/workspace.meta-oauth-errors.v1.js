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
    if (details && details.url.origin === window.location.origin && details.method === 'POST' && match) {
      const nextUrl = new URL(details.url.toString());
      nextUrl.pathname = `/customer/workspace/${match[1]}/meta-guided/connect`;
      if (details.request) return nativeFetch(new Request(nextUrl.toString(), details.request), options);
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
    if (!response.ok) throw new Error(String(payload?.detail || `Request failed (${response.status})`));
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
    node.style.gridTemplateColumns = '1fr';
    node.style.alignItems = 'start';
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
    anchor.style.display = 'inline-flex';
    anchor.style.alignItems = 'center';
    anchor.style.justifyContent = 'center';
    anchor.style.width = 'auto';
    anchor.style.textDecoration = 'none';
    anchor.style.whiteSpace = 'nowrap';
    return anchor;
  };

  const button = (label, onClick, primary = false) => {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = `button ${primary ? 'button-primary' : 'button-secondary'}`;
    node.textContent = label;
    node.style.width = 'auto';
    node.style.whiteSpace = 'nowrap';
    node.addEventListener('click', onClick);
    return node;
  };

  const copyButton = (value) => button('Copy', async (event) => {
    const control = event.currentTarget;
    try {
      await navigator.clipboard.writeText(value);
      control.textContent = 'Copied';
      window.setTimeout(() => { control.textContent = 'Copy'; }, 1200);
    } catch (_) {
      showNotice('Could not copy automatically. Select the suggested text manually.', true);
    }
  });

  const addSteps = (guide, steps) => {
    if (!steps.length) return;
    const heading = document.createElement('strong');
    heading.textContent = 'What to do in Meta';
    heading.style.display = 'block';
    heading.style.marginTop = '14px';
    guide.appendChild(heading);
    const list = document.createElement('ol');
    list.className = 'note';
    list.style.margin = '8px 0 0';
    list.style.paddingLeft = '20px';
    list.style.display = 'grid';
    list.style.gap = '6px';
    steps.forEach((step) => {
      const item = document.createElement('li');
      item.textContent = step;
      list.appendChild(item);
    });
    guide.appendChild(list);
  };

  const addSuggestion = (container, label, value, copyable = true) => {
    const row = document.createElement('div');
    row.style.display = 'grid';
    row.style.gridTemplateColumns = 'minmax(110px, .35fr) minmax(0, 1fr) auto';
    row.style.gap = '10px';
    row.style.alignItems = 'center';
    row.style.padding = '10px 0';
    row.style.borderTop = '1px solid var(--line)';
    const name = document.createElement('span');
    name.className = 'note';
    name.style.margin = '0';
    name.textContent = label;
    const text = document.createElement('strong');
    text.style.fontSize = '12px';
    text.style.lineHeight = '1.45';
    text.textContent = value;
    row.appendChild(name);
    row.appendChild(text);
    if (copyable) row.appendChild(copyButton(value));
    else row.appendChild(document.createElement('span'));
    container.appendChild(row);
  };

  const addPageCreationHelp = (guide) => {
    const box = document.createElement('div');
    box.style.marginTop = '16px';
    box.style.padding = '14px';
    box.style.border = '1px solid var(--line)';
    box.style.borderRadius = '14px';
    box.style.background = 'rgba(255,255,255,.02)';

    const heading = document.createElement('strong');
    heading.textContent = 'Suggested Page details';
    box.appendChild(heading);
    const intro = document.createElement('p');
    intro.className = 'note';
    intro.style.margin = '6px 0 10px';
    intro.textContent = 'Use the same brand name customers see on your product or website. These category suggestions work well for many software products; choose the closest option Meta actually offers.';
    box.appendChild(intro);

    addSuggestion(box, 'Page name', 'Use your product or company brand name', false);
    addSuggestion(box, 'Category', 'Software Company');
    addSuggestion(box, 'Alternatives', 'Internet Company · Business Service', false);
    addSuggestion(box, 'Bio formula', 'What you sell + who it helps + the main outcome', false);
    addSuggestion(box, 'Bio example', 'AI software that helps teams find and acquire customers through measurable growth experiments.');

    const optional = document.createElement('p');
    optional.className = 'note';
    optional.style.margin = '10px 0 0';
    optional.textContent = 'If you already created the Page, do not create another one. In Meta Business Settings, confirm that this Page belongs to the same Business Portfolio and that your Facebook profile has access to both the Page and the ad account. Optional Page onboarding such as profile image, cover, WhatsApp and extra contact details can be skipped for now. Then return here and click Check again.';
    box.appendChild(optional);
    guide.appendChild(box);
  };

  const addStatusInstructions = (guide, setup) => {
    const steps = {
      NO_META_BUSINESS: [
        'Open Meta Business setup and create a Business Portfolio for the business you want Partizan to advertise.',
        'Use the real business or brand name customers recognize.',
        'When Meta finishes creating it, return to Partizan and click Check again.',
      ],
      BUSINESS_NEEDS_AD_ACCOUNT: [
        'If you already use Ads Manager, assign this Facebook profile access to the ad account in the Business Portfolio.',
        'If you do not have an ad account yet, create one in Meta Ads Manager under this Business Portfolio.',
        'Return to Partizan and click Check again.',
      ],
      AD_ACCOUNT_NEEDS_PAGE: [
        'If you have not created a Facebook Page yet, open Page settings and create one or add an existing Page to this Business Portfolio.',
        'If the Page already exists, do not create another one. Confirm it belongs to this same Business Portfolio.',
        'Confirm your Facebook profile has access to both the Page and the ad account. Partizan waits until Meta returns the Page as available for promotion from that ad account.',
        'Return to Partizan and click Check again. You do not need to reconnect Meta.',
      ],
      PAGE_NEEDS_AD_ACCOUNT_ACCESS: [
        'Do not create another Facebook Page. Partizan can already see a Page inside your Meta Business Portfolio.',
        'Open Page settings and confirm your Facebook profile has access to that Page.',
        'Open Ad Account settings and confirm the same profile has permission to manage advertising for the ad account.',
        'Confirm the Page and ad account belong to the same Business Portfolio, then return here and click Check again.',
      ],
    }[setup.status] || [];
    addSteps(guide, steps);
    if (setup.status === 'AD_ACCOUNT_NEEDS_PAGE') addPageCreationHelp(guide);
  };

  const renderGuidedSetup = (setup) => {
    const guide = ensureGuide();
    if (!guide) return;
    const metaStateNode = document.getElementById('meta-state');
    const metaDetail = document.getElementById('meta-detail');
    const metaConnect = document.getElementById('meta-connect');

    if (!setup || ['NOT_STARTED', 'READY'].includes(setup.status)) {
      guide.classList.add('hidden');
      metaConnect?.classList.remove('hidden');
      return;
    }

    guide.replaceChildren();
    guide.classList.remove('hidden');
    metaConnect?.classList.add('hidden');
    if (metaStateNode) metaStateNode.textContent = 'Setup needed';
    if (metaDetail) metaDetail.textContent = 'Finish the Meta setup below, then Partizan can check again without another login.';

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
    if (setup.business_page_count) facts.push(`${setup.business_page_count} Facebook Page${setup.business_page_count === 1 ? '' : 's'} found in Business`);
    if (setup.promotable_page_count) facts.push(`${setup.promotable_page_count} promotable Page${setup.promotable_page_count === 1 ? '' : 's'} found`);
    if (facts.length) {
      const factNode = document.createElement('p');
      factNode.className = 'note';
      factNode.textContent = facts.join(' · ');
      guide.appendChild(factNode);
    }
    if (setup.business_page_names?.length) {
      const pageNames = document.createElement('p');
      pageNames.className = 'note';
      pageNames.textContent = `Page${setup.business_page_names.length === 1 ? '' : 's'}: ${setup.business_page_names.join(', ')}`;
      guide.appendChild(pageNames);
    }

    if (setup.status === 'BUSINESS_NEEDS_AD_ACCOUNT') {
      const question = document.createElement('p');
      question.className = 'note';
      question.textContent = 'Do you already use Meta Ads Manager for this business?';
      guide.appendChild(question);
    }

    addStatusInstructions(guide, setup);

    const actions = document.createElement('div');
    actions.className = 'hero-actions';
    actions.style.marginTop = '14px';

    if (setup.primary_url) {
      const labels = {
        NO_META_BUSINESS: 'Open Meta Business setup →',
        BUSINESS_NEEDS_AD_ACCOUNT: 'Yes — fix ad account access →',
        AD_ACCOUNT_NEEDS_PAGE: 'Open Page settings →',
        PAGE_NEEDS_AD_ACCOUNT_ACCESS: 'Open Page access →',
      };
      actions.appendChild(linkButton(labels[setup.status] || 'Open Meta Business Settings →', setup.primary_url, true));
    }
    if (setup.secondary_url && setup.status === 'BUSINESS_NEEDS_AD_ACCOUNT') {
      actions.appendChild(linkButton('No — open Ads Manager →', setup.secondary_url));
    }
    if (setup.secondary_url && setup.status === 'PAGE_NEEDS_AD_ACCOUNT_ACCESS') {
      actions.appendChild(linkButton('Open Ad Account access →', setup.secondary_url));
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
          showNotice(next.message || 'Partizan checked Meta again. One setup step is still required.', true);
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
      if (!['NOT_STARTED', 'READY'].includes(setup.status) && metaState === 'connected') {
        showNotice(setup.message, true);
        cleanMetaCallbackQuery();
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
    const detail = [reason ? `reason ${reason}` : '', code ? `code ${code}` : ''].filter(Boolean).join(', ');
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
