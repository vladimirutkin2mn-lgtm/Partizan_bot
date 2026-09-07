(() => {
  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

  const projectTypeLabels = {
    WEBSITE_PRODUCT: 'Website / product',
    TELEGRAM_COMMUNITY: 'Telegram channel / group',
    SOCIAL_ACCOUNT: 'Social account',
    APP: 'App',
    BUSINESS_SERVICE: 'Business / service',
    OTHER: 'Other',
  };

  const acquisitionChannelCopy = {
    INSTAGRAM: {
      label: 'Instagram & Facebook',
      description: 'Meta audiences and paid or organic distribution.',
    },
    TIKTOK: {
      label: 'TikTok',
      description: 'Short-form discovery, creators and audience testing.',
    },
    REDDIT: {
      label: 'Reddit',
      description: 'High-intent communities and relevant discussion threads.',
    },
    TELEGRAM: {
      label: 'Telegram',
      description: 'Communities, groups and public conversations.',
    },
  };

  const api = async (path, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (options.body != null) headers.set('Content-Type', 'application/json');
    const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  };

  const normalizeUrl = (value) => {
    const trimmed = value.trim();
    if (!trimmed) return null;
    return /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  };

  const currentProjectId = () => new URLSearchParams(window.location.search).get('project');

  const syncRecommendedMoveCta = () => {
    const channelStep = document.getElementById('activation-channel');
    const channelTitle = document.getElementById('activation-channel-title');
    const primary = document.getElementById('activation-primary');
    if (!channelStep || !channelTitle || !primary) return;

    const body = channelStep.querySelector(':scope > div') || channelStep.querySelector('div');
    if (!body) return;
    const actionable = channelStep.classList.contains('current')
      && channelTitle.textContent.trim() === 'Do this now';
    let inline = document.getElementById('activation-inline-primary');

    if (!actionable) {
      if (inline) inline.remove();
      return;
    }

    if (!inline) {
      inline = document.createElement('button');
      inline.id = 'activation-inline-primary';
      inline.className = 'button button-primary';
      inline.type = 'button';
      inline.style.marginTop = '8px';
      inline.style.justifySelf = 'start';
      inline.style.maxWidth = '100%';
      inline.addEventListener('click', () => primary.click());
      body.appendChild(inline);
    }

    if (inline.textContent !== primary.textContent) inline.textContent = primary.textContent;
    if (inline.disabled !== primary.disabled) inline.disabled = primary.disabled;
    inline.setAttribute('aria-label', `Next step: ${primary.textContent.replace(/→/g, '').trim()}`);
  };

  const installRecommendedMoveCta = () => {
    const activationCard = document.getElementById('activation-card');
    if (!activationCard || activationCard.dataset.inlineCtaInstalled === 'true') return;
    activationCard.dataset.inlineCtaInstalled = 'true';
    const observer = new MutationObserver(syncRecommendedMoveCta);
    observer.observe(activationCard, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['class', 'disabled'],
    });
    syncRecommendedMoveCta();
  };

  const opportunityUrls = (opportunity) => {
    if (!opportunity) return [];
    const evidence = Array.isArray(opportunity.provenance) ? opportunity.provenance : [];
    return [opportunity.url, ...evidence.map((item) => item && item.url)].filter(Boolean);
  };

  const inferResearchedChannel = (opportunity) => {
    for (const raw of opportunityUrls(opportunity)) {
      try {
        const host = new URL(String(raw)).hostname.toLowerCase().replace(/^www\./, '');
        if (host === 't.me' || host === 'telegram.me' || host.endsWith('.telegram.me')) return 'TELEGRAM';
        if (host === 'reddit.com' || host.endsWith('.reddit.com')) return 'REDDIT';
        if (host === 'tiktok.com' || host.endsWith('.tiktok.com')) return 'TIKTOK';
        if (
          host === 'instagram.com' || host.endsWith('.instagram.com')
          || host === 'facebook.com' || host.endsWith('.facebook.com')
          || host === 'fb.com' || host.endsWith('.fb.com')
        ) return 'INSTAGRAM';
      } catch (_) {
        // Research URLs are display evidence only. Unknown hosts stay unclassified.
      }
    }
    return null;
  };

  const openWorkspaceTab = (name) => {
    const button = document.querySelector(`.tab-button[data-tab="${name}"]`);
    if (button) button.click();
  };

  const acquisitionStage = (number, title, copy, state, current = false) => `
    <li class="acquisition-stage ${current ? 'current' : ''} ${state === 'Done' ? 'complete' : ''}">
      <span class="acquisition-stage-index">${number}</span>
      <div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(copy)}</small></div>
      <span class="acquisition-stage-state">${escapeHtml(state)}</span>
    </li>`;

  const ensureAcquisitionFlow = () => {
    const activationCard = document.getElementById('activation-card');
    if (!activationCard) return null;
    const legacyList = activationCard.querySelector('.activation-list');
    if (legacyList) legacyList.classList.add('hidden');
    let flow = document.getElementById('acquisition-path');
    if (!flow) {
      flow = document.createElement('section');
      flow.id = 'acquisition-path';
      flow.className = 'acquisition-path';
      const action = activationCard.querySelector('.activation-action-primary');
      activationCard.insertBefore(flow, action || activationCard.firstChild);
    }
    let choice = document.getElementById('acquisition-channel-choice');
    if (!choice) {
      choice = document.createElement('section');
      choice.id = 'acquisition-channel-choice';
      choice.className = 'acquisition-channel-choice hidden';
      const preview = document.getElementById('activation-preview');
      if (preview && preview.parentNode === activationCard) {
        preview.insertAdjacentElement('afterend', choice);
      } else {
        activationCard.appendChild(choice);
      }
    }
    return { activationCard, flow, choice };
  };

  const channelCards = (channels, recommendedPlatform) => channels.map((channel) => {
    const copy = acquisitionChannelCopy[channel.platform] || {
      label: channel.label || channel.platform,
      description: 'Supported acquisition surface.',
    };
    const recommended = channel.platform === recommendedPlatform;
    const off = channel.mode === 'OFF';
    return `
      <article class="acquisition-channel-card ${recommended ? 'recommended' : ''} ${off ? 'is-off' : ''}">
        <div class="acquisition-channel-card-head">
          <strong>${escapeHtml(copy.label)}</strong>
          <span>${recommended ? 'Recommended · researched' : (off ? 'Turned off' : 'Available channel')}</span>
        </div>
        <p>${escapeHtml(copy.description)}</p>
        <small>${recommended
          ? 'The free research evidence maps to this channel.'
          : 'Available to choose. The free preview has not proved this channel yet.'}</small>
        <button class="button ${recommended ? 'button-primary' : 'button-secondary'}" type="button" data-select-acquisition-channel="${escapeHtml(channel.platform)}" ${off ? 'disabled' : ''}>
          ${off ? 'Turn it on in Channels first' : `Choose ${escapeHtml(copy.label)} →`}
        </button>
      </article>`;
  }).join('');

  const selectedChannelSetup = (selected, data, recommendedPlatform) => {
    const copy = acquisitionChannelCopy[selected.platform] || {
      label: selected.label || selected.platform,
      description: 'Selected acquisition channel.',
    };
    const opportunity = data.preview_opportunity || null;
    const opportunityUrl = selected.platform === recommendedPlatform ? opportunityUrls(opportunity)[0] : null;
    if (selected.platform === 'INSTAGRAM') {
      if (!selected.connected) {
        return `
          <section class="acquisition-channel-setup">
            <span class="eyebrow">Channel setup</span>
            <h3>Connect Meta for ${escapeHtml(copy.label)}.</h3>
            <p>Your channel choice is saved. Connecting Meta grants account access only; it does not authorize spend or enable autonomous execution.</p>
            <div class="acquisition-setup-actions">
              <button class="button button-primary" type="button" data-acquisition-connect-meta>Connect Meta →</button>
              <button class="button button-secondary" type="button" data-acquisition-change-channel>Choose a different channel</button>
            </div>
          </section>`;
      }
      return `
        <section class="acquisition-channel-setup">
          <span class="eyebrow">Channel setup</span>
          <h3>${escapeHtml(copy.label)} selected · Meta connected.</h3>
          <p>Account access is ready. Execution permission and acquisition budget remain separate controls and are never granted by choosing this channel.</p>
          ${selected.execution_blocker ? `<p class="acquisition-setup-note">Current execution blocker: ${escapeHtml(selected.execution_blocker)}.</p>` : ''}
          <div class="acquisition-setup-actions">
            <button class="button button-primary" type="button" data-acquisition-open-channels>Review channel controls →</button>
            <button class="button button-secondary" type="button" data-acquisition-change-channel>Choose a different channel</button>
          </div>
        </section>`;
    }
    return `
      <section class="acquisition-channel-setup">
        <span class="eyebrow">Channel selected</span>
        <h3>${escapeHtml(copy.label)} is where you want to start.</h3>
        <p>Partizan saved this choice separately from execution permission. ${escapeHtml(copy.label)} stays Research only until a customer-facing connection and execution path is production-ready.</p>
        <p class="acquisition-setup-note">Automatic account connection for ${escapeHtml(copy.label)} is not available in the customer workspace yet. Partizan will not pretend the channel is connected or authorize spend.</p>
        <div class="acquisition-setup-actions">
          ${opportunityUrl ? `<a class="button button-secondary" href="${escapeHtml(opportunityUrl)}" target="_blank" rel="noopener noreferrer">Open researched opportunity ↗</a>` : ''}
          <button class="button button-secondary" type="button" data-acquisition-open-channels>Review channel controls →</button>
          <button class="button button-secondary" type="button" data-acquisition-change-channel>Choose a different channel</button>
        </div>
      </section>`;
  };

  const bindAcquisitionFlowActions = (data, channels, recommendedPlatform) => {
    document.querySelectorAll('[data-select-acquisition-channel]').forEach((button) => {
      button.addEventListener('click', async () => {
        const projectId = currentProjectId();
        if (!projectId || button.disabled) return;
        const original = button.textContent;
        button.disabled = true;
        button.textContent = 'Saving choice…';
        try {
          const updated = await api(`/customer/workspace/${encodeURIComponent(projectId)}/channel-selection`, {
            method: 'PUT',
            body: JSON.stringify({ platform: button.dataset.selectAcquisitionChannel }),
          });
          renderAcquisitionFlow(data, updated);
        } catch (error) {
          const errorNode = document.getElementById('acquisition-flow-error');
          if (errorNode) {
            errorNode.textContent = error.message;
            errorNode.classList.remove('hidden');
          }
          button.disabled = false;
          button.textContent = original;
        }
      });
    });
    document.querySelectorAll('[data-acquisition-change-channel]').forEach((button) => {
      button.addEventListener('click', () => {
        const picker = document.getElementById('acquisition-channel-picker');
        if (picker) {
          picker.classList.remove('hidden');
          picker.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    });
    document.querySelectorAll('[data-acquisition-open-channels]').forEach((button) => {
      button.addEventListener('click', () => openWorkspaceTab('channels'));
    });
    document.querySelectorAll('[data-acquisition-connect-meta]').forEach((button) => {
      button.addEventListener('click', () => {
        openWorkspaceTab('settings');
        window.setTimeout(() => {
          const meta = document.getElementById('meta-connect');
          if (meta && !meta.disabled) meta.click();
        }, 0);
      });
    });
  };

  const renderAcquisitionFlow = (data, channels) => {
    const nodes = ensureAcquisitionFlow();
    if (!nodes || nodes.activationCard.classList.contains('hidden')) return;
    const project = data.project || {};
    const researchDone = Boolean(data.preview_opportunity) || project.research_state === 'READY';
    const selected = channels.find((item) => item.selected) || null;
    const recommendedPlatform = inferResearchedChannel(data.preview_opportunity || null);
    const action = nodes.activationCard.querySelector('.activation-action-primary');
    const progress = document.getElementById('activation-progress');
    const heading = document.getElementById('activation-heading');
    const copy = document.getElementById('activation-copy');

    if (progress) progress.textContent = selected ? 'Channel selected' : (researchDone ? 'Choose channel' : 'Research');
    if (heading) heading.textContent = selected
      ? `${selected.label} selected. Set up only what this channel needs.`
      : (researchDone ? 'Research is ready. Choose where to start.' : 'Find where your customers already are.');
    if (copy) copy.textContent = selected
      ? 'Choosing a channel does not grant account access, execution permission or acquisition spend. Those are requested separately only when this channel needs them.'
      : (researchDone
        ? 'Review the evidence, then choose the channel where you want Partizan to help you promote this product.'
        : 'Partizan researches real distribution opportunities before asking you to choose a channel, connect an account or add acquisition budget.');

    nodes.flow.innerHTML = `
      <ol class="acquisition-path-stages">
        ${acquisitionStage(1, 'Understand your product', 'Product understanding is ready for acquisition research.', 'Done')}
        ${acquisitionStage(2, 'Find where customers are', researchDone
          ? 'Partizan found evidence-backed distribution opportunities.'
          : 'Partizan researches real places where potential customers already gather.', researchDone ? 'Done' : 'Now', !researchDone)}
        ${acquisitionStage(3, 'Choose where to start', selected
          ? `${selected.label} selected. This choice grants no execution permission.`
          : (researchDone ? 'Choose the channel you want to use after reviewing the research.' : 'Channel choice appears after research.'), selected ? 'Done' : (researchDone ? 'Now' : 'Waiting'), researchDone && !selected)}
      </ol>`;

    if (action) action.classList.toggle('hidden', researchDone);
    const primary = document.getElementById('activation-primary');
    if (!researchDone && primary && primary.textContent.includes('first real opportunity')) {
      primary.textContent = 'Find distribution opportunities →';
    }

    if (!researchDone) {
      nodes.choice.classList.add('hidden');
      nodes.choice.innerHTML = '';
      return;
    }

    nodes.choice.classList.remove('hidden');
    nodes.choice.innerHTML = `
      ${selectedChannelSetup(selected || {}, data, recommendedPlatform)}
      <section id="acquisition-channel-picker" class="acquisition-channel-picker ${selected ? 'hidden' : ''}">
        <div class="acquisition-choice-head">
          <span class="eyebrow">Choose your channel</span>
          <h3>Where do you want Partizan to help you acquire customers?</h3>
          <p>The researched recommendation is highlighted when the free evidence maps cleanly to a supported channel. Other channels remain choices, not research claims.</p>
        </div>
        <div class="acquisition-channel-grid">${channelCards(channels, recommendedPlatform)}</div>
        <p id="acquisition-flow-error" class="project-form-error hidden"></p>
        <p class="acquisition-research-note">Research access and acquisition budget are separate. A larger paid research report can expand the market map; choosing a channel never spends acquisition money.</p>
      </section>`;
    if (!selected) {
      const setup = nodes.choice.querySelector('.acquisition-channel-setup');
      if (setup) setup.remove();
    }
    bindAcquisitionFlowActions(data, channels, recommendedPlatform);
  };

  let acquisitionFlowRequest = 0;
  const refreshAcquisitionFlow = async (projectId) => {
    if (!projectId) return;
    const request = ++acquisitionFlowRequest;
    const [data, channels] = await Promise.all([
      api(`/customer/workspace/${encodeURIComponent(projectId)}`),
      api(`/customer/workspace/${encodeURIComponent(projectId)}/channels`),
    ]);
    if (request !== acquisitionFlowRequest || currentProjectId() !== String(projectId)) return;
    renderAcquisitionFlow(data, channels);
  };

  const installAcquisitionFlow = () => {
    window.addEventListener('partizan:workspace-ready', (event) => {
      const projectId = event.detail && event.detail.projectId;
      refreshAcquisitionFlow(projectId).catch(() => {});
    });
  };

  const renderModal = () => {
    if (document.getElementById('new-project-modal')) return;
    const modal = document.createElement('div');
    modal.id = 'new-project-modal';
    modal.className = 'project-modal hidden';
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = `
      <div class="project-modal-backdrop" data-close-new-project></div>
      <section class="project-modal-card" role="dialog" aria-modal="true" aria-labelledby="new-project-title">
        <div class="project-modal-head">
          <div><span class="eyebrow">New project</span><h2 id="new-project-title">What are you growing?</h2></div>
          <button class="project-modal-close" type="button" data-close-new-project aria-label="Close">×</button>
        </div>
        <p class="project-modal-copy">Create a separate workspace with its own channels, budget, research and performance.</p>
        <form id="new-project-form" class="project-form">
          <div class="project-form-grid two">
            <label><span>Project name</span><input id="new-project-name" maxlength="120" required placeholder="My Telegram channel"></label>
            <label><span>What is it?</span><select id="new-project-type" required>${Object.entries(projectTypeLabels).map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('')}</select></label>
          </div>
          <label><span>Link <small>optional</small></span><input id="new-project-url" inputmode="url" placeholder="https://example.com or t.me/channel"></label>
          <label><span>What do you offer and to whom?</span><textarea id="new-project-brief" minlength="20" maxlength="6000" required placeholder="Describe the product, channel, community or service and who should care about it."></textarea></label>
          <div class="project-form-grid two">
            <label><span>Market</span><input id="new-project-market" maxlength="160" required placeholder="United States"></label>
            <label><span>Goal</span><select id="new-project-goal" required><option>Get paying customers</option><option>Grow subscribers</option><option>Generate qualified leads</option><option>Drive purchases</option><option>Grow an audience</option></select></label>
          </div>
          <label class="project-budget-field"><span>Test budget</span><div><b>$</b><input id="new-project-budget" type="number" min="1" max="100000" step="1" value="1000" required></div></label>
          <div id="new-project-error" class="project-form-error hidden"></div>
          <div class="project-modal-actions"><button type="button" class="button button-secondary" data-close-new-project>Cancel</button><button id="new-project-submit" type="submit" class="button button-primary">Create project →</button></div>
        </form>
      </section>`;
    document.body.appendChild(modal);
  };

  const openModal = () => {
    const modal = document.getElementById('new-project-modal');
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('project-modal-open');
    window.setTimeout(() => document.getElementById('new-project-name').focus(), 0);
  };

  const closeModal = () => {
    const modal = document.getElementById('new-project-modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('project-modal-open');
    const error = document.getElementById('new-project-error');
    error.textContent = '';
    error.classList.add('hidden');
  };

  const ensureProjectDetailsCard = () => {
    if (document.getElementById('project-details-card')) return;
    const settings = document.querySelector('[data-tab-panel="settings"]');
    if (!settings) return;
    const card = document.createElement('section');
    card.id = 'project-details-card';
    card.className = 'panel project-details-card';
    card.innerHTML = `
      <div class="section-head project-details-head"><div><span class="eyebrow">Project</span><h2 id="project-details-name">Project details</h2></div><span id="project-details-type" class="status-pill">Project</span></div>
      <div class="project-detail-grid">
        <div><span>Link</span><strong id="project-details-link">—</strong></div>
        <div><span>Market</span><strong id="project-details-market">—</strong></div>
        <div><span>Goal</span><strong id="project-details-goal">—</strong></div>
        <div><span>Test budget</span><strong id="project-details-budget">—</strong></div>
      </div>
      <div class="project-description-block"><span>Description</span><p id="project-details-brief">—</p></div>
      <div class="project-danger-zone">
        <div><strong>Delete project</strong><span>Remove this project from your workspace. Financial and experiment records are retained for audit integrity.</span></div>
        <button id="delete-project-button" class="project-delete-button" type="button">Delete project</button>
      </div>
      <div id="project-delete-confirm" class="project-delete-confirm hidden">
        <div><strong id="project-delete-confirm-title">Delete this project?</strong><span>This cannot be undone from the customer workspace.</span></div>
        <div><button id="project-delete-cancel" class="button button-secondary" type="button">Cancel</button><button id="project-delete-confirm-button" class="project-delete-confirm-button" type="button">Yes, delete project</button></div>
      </div>`;
    settings.appendChild(card);

    document.getElementById('delete-project-button').addEventListener('click', () => {
      const confirm = document.getElementById('project-delete-confirm');
      const name = document.getElementById('project-details-name').textContent;
      document.getElementById('project-delete-confirm-title').textContent = `Delete “${name}”?`;
      confirm.classList.remove('hidden');
      document.getElementById('delete-project-button').classList.add('hidden');
    });
    document.getElementById('project-delete-cancel').addEventListener('click', () => {
      document.getElementById('project-delete-confirm').classList.add('hidden');
      document.getElementById('delete-project-button').classList.remove('hidden');
    });
    document.getElementById('project-delete-confirm-button').addEventListener('click', async (event) => {
      const button = event.currentTarget;
      const projectId = document.getElementById('project-details-card').dataset.projectId;
      if (!projectId) return;
      button.disabled = true;
      button.textContent = 'Deleting…';
      try {
        await api(`/customer/account/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' });
        const projects = await api('/customer/account/projects');
        if (projects.length) {
          window.location.assign(`/workspace?project=${encodeURIComponent(projects[0].project_id)}`);
        } else {
          window.location.assign('/start');
        }
      } catch (error) {
        button.disabled = false;
        button.textContent = 'Yes, delete project';
        const errorNode = document.getElementById('new-project-error');
        errorNode.textContent = error.message;
        errorNode.classList.remove('hidden');
      }
    });
  };

  const renderProjectDetails = (project) => {
    ensureProjectDetailsCard();
    const card = document.getElementById('project-details-card');
    if (!card || !project) return;
    card.dataset.projectId = String(project.project_id);
    document.getElementById('project-details-name').textContent = project.name || 'Project details';
    document.getElementById('project-details-type').textContent = projectTypeLabels[project.project_type] || 'Project';
    const link = document.getElementById('project-details-link');
    if (project.reference_url) {
      link.innerHTML = `<a href="${escapeHtml(project.reference_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(project.reference_url)}</a>`;
    } else {
      link.textContent = 'No link added';
    }
    document.getElementById('project-details-market').textContent = project.market || '—';
    document.getElementById('project-details-goal').textContent = project.goal || '—';
    document.getElementById('project-details-budget').textContent = `$${Number(project.budget_usd || 0).toLocaleString('en-US')}`;
    document.getElementById('project-details-brief').textContent = project.brief || 'No description added.';
    document.getElementById('project-delete-confirm').classList.add('hidden');
    document.getElementById('delete-project-button').classList.remove('hidden');
  };

  const refreshProjectLabels = async () => {
    const switcher = document.getElementById('project-switcher');
    if (!switcher) return;
    try {
      const projects = await api('/customer/account/projects');
      const byId = new Map(projects.map((item) => [String(item.project_id), item]));
      Array.from(switcher.options).forEach((option) => {
        const project = byId.get(option.value);
        if (!project) return;
        const label = project.name || `${project.market} · ${project.goal}`;
        option.textContent = label.length > 46 ? `${label.slice(0, 46)}…` : label;
        option.title = `${label} · ${projectTypeLabels[project.project_type] || 'Project'}`;
      });
      const selected = byId.get(currentProjectId() || switcher.value);
      if (selected) renderProjectDetails(selected);
    } catch (_) {
      // The core workspace handles authentication; project enhancements can keep their fallback.
    }
  };

  const install = () => {
    const nav = document.getElementById('account-nav');
    const email = document.getElementById('account-email');
    const switcher = document.getElementById('project-switcher');
    if (!nav || !email || !switcher || document.getElementById('new-project-button')) return;

    const button = document.createElement('button');
    button.id = 'new-project-button';
    button.className = 'new-project-button';
    button.type = 'button';
    button.textContent = '+ New project';
    nav.insertBefore(button, email);
    renderModal();
    ensureProjectDetailsCard();
    installRecommendedMoveCta();
    installAcquisitionFlow();

    button.addEventListener('click', openModal);
    document.querySelectorAll('[data-close-new-project]').forEach((node) => node.addEventListener('click', closeModal));
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeModal();
    });

    const observer = new MutationObserver(() => refreshProjectLabels());
    observer.observe(switcher, { childList: true });
    refreshProjectLabels();

    document.getElementById('new-project-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const submit = document.getElementById('new-project-submit');
      const errorNode = document.getElementById('new-project-error');
      submit.disabled = true;
      submit.textContent = 'Creating…';
      errorNode.classList.add('hidden');
      try {
        const result = await api('/customer/account/projects', {
          method: 'POST',
          body: JSON.stringify({
            name: document.getElementById('new-project-name').value.trim(),
            project_type: document.getElementById('new-project-type').value,
            reference_url: normalizeUrl(document.getElementById('new-project-url').value),
            brief: document.getElementById('new-project-brief').value.trim(),
            market: document.getElementById('new-project-market').value.trim(),
            goal: document.getElementById('new-project-goal').value,
            budget_usd: Number(document.getElementById('new-project-budget').value),
          }),
        });
        window.location.assign(`/workspace?project=${encodeURIComponent(result.project_id)}`);
      } catch (error) {
        errorNode.textContent = error.message;
        errorNode.classList.remove('hidden');
        submit.disabled = false;
        submit.textContent = 'Create project →';
      }
    });
  };

  install();
})();
