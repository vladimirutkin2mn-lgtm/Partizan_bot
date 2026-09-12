(() => {
  const $ = (id) => document.getElementById(id);
  const snapshot = $('channel-snapshot');
  const tableBody = $('channels-table-body');
  const workspace = $('workspace');
  if (!snapshot || !tableBody || !workspace) return;

  const MODE_LABELS = {
    MANUAL: "I'll do it myself",
    CLIENT_OWNED: 'Use my account',
    PARTIZAN_MANAGED: 'Let Partizan handle it',
  };
  const CAPABILITY_LABELS = {
    SEARCH: 'Research',
    DRAFT: 'Draft',
    PUBLISH: 'Publish',
    MEASURE: 'Measure',
  };

  let renderTimer = null;
  let syncing = false;
  let latestChannels = [];
  let focusedPlatform = null;
  let telegramChallenge = null;
  const connectionState = new Map();

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]));
  const money = (value) => `$${Number(value || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
  const roas = (value) => value == null ? '—' : `${Number(value).toFixed(2)}×`;
  const currentProjectId = () => new URLSearchParams(window.location.search).get('project');
  const channelEnabled = (channel) => channel.mode !== 'OFF';
  const capability = (channel, name) => (channel.capabilities || []).find((item) => item.capability === name) || null;
  const publisherMode = (channel, name) => (channel.publisher_modes || []).find((item) => item.mode === name) || null;
  const clientOwnedMode = (channel) => publisherMode(channel, 'CLIENT_OWNED');
  const managedMode = (channel) => publisherMode(channel, 'PARTIZAN_MANAGED');
  const canPublish = (channel) => Boolean(capability(channel, 'PUBLISH')?.ready);
  const enabledMode = (channel) => channel.publisher_mode !== 'MANUAL' && canPublish(channel) ? 'AUTO' : 'RESEARCH_ONLY';

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        message = payload.detail || payload.message || message;
      } catch (_) {
        // Keep the HTTP fallback.
      }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return response.json();
  };

  const showNotice = (message, isError = false) => {
    const notice = $('notice');
    if (!notice) return;
    notice.textContent = message;
    notice.classList.remove('hidden', 'error');
    if (isError) notice.classList.add('error');
    window.setTimeout(() => notice.classList.add('hidden'), 4400);
  };

  const ensureStyles = () => {
    if ($('community-channel-styles')) return;
    const style = document.createElement('style');
    style.id = 'community-channel-styles';
    style.textContent = `
      .channels-summary{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 14px}.channels-summary span{padding:7px 9px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:10px}.channels-summary b{color:var(--soft)}
      .channel-toggle-control{display:inline-flex;align-items:center;gap:7px;font-size:10px;color:var(--soft);cursor:pointer}.channel-toggle-control input{position:absolute;opacity:0;pointer-events:none}.channel-toggle-track{width:32px;height:18px;border-radius:999px;background:rgba(255,255,255,.11);border:1px solid var(--line-strong);position:relative}.channel-toggle-track:after{content:"";position:absolute;width:12px;height:12px;border-radius:50%;background:var(--muted);top:2px;left:2px;transition:.16s}.channel-toggle:checked+.channel-toggle-track{background:rgba(201,255,101,.14);border-color:rgba(201,255,101,.3)}.channel-toggle:checked+.channel-toggle-track:after{left:16px;background:var(--accent)}
      .channel-detail-status{display:inline-flex;padding:5px 8px;border-radius:999px;border:1px solid var(--line-strong);font-size:9px;color:var(--muted);white-space:nowrap}.channel-detail-status.connected,.channel-detail-status.enabled{color:var(--accent);border-color:rgba(201,255,101,.3);background:rgba(201,255,101,.05)}.channel-detail-status.needs{color:var(--warn);border-color:rgba(255,212,121,.27)}.channel-detail-status.off{opacity:.7}
      .channel-execution-row td{padding:0!important;background:rgba(255,255,255,.012)}.channel-execution-panel{padding:14px 16px 16px;display:grid;grid-template-columns:minmax(220px,.8fr) minmax(280px,1.2fr);gap:14px;border-bottom:1px solid var(--line)}.channel-execution-panel.is-focused{outline:1px solid rgba(201,255,101,.34);outline-offset:-1px;background:rgba(201,255,101,.025)}.channel-execution-panel h4{font-size:11px;margin:0 0 8px}.channel-execution-panel p{font-size:10px;line-height:1.5;color:var(--muted);margin:6px 0}.channel-mode-box,.channel-connection-box{padding:12px;border:1px solid var(--line);border-radius:13px;background:rgba(255,255,255,.018)}
      .channel-publisher-select{width:100%;height:38px;border:1px solid var(--line-strong);border-radius:10px;background:#11141b;color:var(--text);padding:0 9px;font:inherit;font-size:11px}.channel-mode-blocker{min-height:15px}.channel-capabilities{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}.channel-capability{padding:4px 7px;border:1px solid var(--line);border-radius:999px;font-size:9px;color:var(--muted)}.channel-capability.ready{color:var(--accent);border-color:rgba(201,255,101,.24)}
      .channel-connection-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.channel-connection-head strong,.channel-connection-head small{display:block}.channel-connection-head small{font-size:9px;color:var(--muted);margin-top:4px}.channel-inline-form{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:10px}.channel-inline-form.telegram-confirm{grid-template-columns:1fr 1fr auto}.channel-inline-form input{min-width:0;height:38px;border:1px solid var(--line-strong);border-radius:10px;background:#11141b;color:var(--text);padding:0 10px;font:inherit;font-size:11px}.channel-inline-form .button,.channel-connection-box>.button{padding:9px 12px;font-size:11px}.channel-connection-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:9px}.channel-safe-note{font-size:9px!important;color:var(--muted);margin-top:9px!important}.channel-connect-button{border:1px solid var(--line-strong);border-radius:9px;background:rgba(255,255,255,.04);color:var(--soft);font:inherit;font-size:10px;padding:7px 9px;cursor:pointer}.channel-connect-button:disabled{opacity:.5;cursor:not-allowed}
      @media(max-width:760px){.channel-execution-panel{grid-template-columns:1fr}.channel-inline-form,.channel-inline-form.telegram-confirm{grid-template-columns:1fr}.channel-inline-form .button{width:100%}}
    `;
    document.head.append(style);
  };

  const publisherModeLabel = (mode) => MODE_LABELS[mode] || mode || 'Manual';

  const connectionFor = (platform) => connectionState.get(platform) || null;
  const connectionActive = (platform) => connectionFor(platform)?.status === 'ACTIVE';

  const detailStatus = (channel) => {
    if (!channelEnabled(channel)) return ['Off', 'off'];
    if (channel.publisher_mode === 'PARTIZAN_MANAGED') {
      return managedMode(channel)?.available ? ['Partizan managed', 'connected'] : ['Managed unavailable', 'needs'];
    }
    if (channel.publisher_mode === 'CLIENT_OWNED') {
      if (channel.connected === false || connectionFor(channel.platform)?.status === 'DISCONNECTED') return ['Needs connection', 'needs'];
      if (canPublish(channel)) return ['Client account ready', 'connected'];
      return ['Connected · gated', 'needs'];
    }
    return canPublish(channel) ? ['Manual · publish capable', 'enabled'] : ['Research + manual', 'enabled'];
  };

  const channelSubline = (channel) => {
    if (!channelEnabled(channel)) return 'Off · history stays visible';
    if (channel.execution_blocker && channel.publisher_mode !== 'MANUAL') return channel.execution_blocker;
    if (channel.publisher_mode === 'PARTIZAN_MANAGED') {
      return managedMode(channel)?.available ? 'Partizan can fulfill an eligible approved action' : (managedMode(channel)?.blocker || 'Managed execution is not available');
    }
    if (channel.publisher_mode === 'CLIENT_OWNED') {
      const connected = connectionActive(channel.platform) || channel.connected === true;
      if (!connected) return `${publisherModeLabel(channel.publisher_mode)} · connection required`;
      return canPublish(channel) ? `${publisherModeLabel(channel.publisher_mode)} · execution ready` : (capability(channel, 'PUBLISH')?.blocker || 'Connected · publish gate not ready');
    }
    return 'Research here · execute manually when you choose';
  };

  const setupNeeded = (channel) => channel.publisher_mode === 'CLIENT_OWNED'
    && ['TELEGRAM', 'REDDIT'].includes(channel.platform)
    && !(connectionActive(channel.platform) || channel.connected === true);

  const overviewControl = (channel) => {
    if (setupNeeded(channel) && clientOwnedMode(channel)?.available) {
      return `<button class="channel-connect-button" type="button" data-focus-channel="${escapeHtml(channel.platform)}">Set up</button>`;
    }
    if (channel.platform === 'INSTAGRAM' && channel.publisher_mode === 'CLIENT_OWNED' && !channel.connected) {
      if (!channel.autonomous_execution_available) return '<span class="channel-detail-status needs">Unavailable</span>';
      return '<button class="channel-connect-button" type="button" data-channel-connect="INSTAGRAM">Connect</button>';
    }
    const checked = channelEnabled(channel) ? ' checked' : '';
    const label = channelEnabled(channel) ? 'On' : 'Off';
    return `<label class="channel-toggle-control"><input class="channel-toggle" type="checkbox" data-platform="${escapeHtml(channel.platform)}" data-on-mode="${escapeHtml(enabledMode(channel))}" aria-label="${escapeHtml(channel.label)} enabled"${checked}><span class="channel-toggle-track" aria-hidden="true"></span><span class="channel-toggle-label">${label}</span></label>`;
  };

  const syncMetaSettingsControl = (channels) => {
    const meta = channels.find((channel) => channel.platform === 'INSTAGRAM');
    const button = $('meta-connect');
    const detail = $('meta-detail');
    if (!meta || !button || meta.connected) return;
    if (!meta.autonomous_execution_available) {
      button.dataset.partizanMetaUnavailable = 'true';
      button.disabled = true;
      button.textContent = 'Meta activation pending';
      if (detail) detail.textContent = meta.execution_blocker || 'Meta customer connection is temporarily unavailable';
      return;
    }
    if (button.dataset.partizanMetaUnavailable === 'true') {
      delete button.dataset.partizanMetaUnavailable;
      button.disabled = false;
      button.textContent = 'Connect Meta →';
      if (detail) detail.textContent = 'No account access yet.';
    }
  };

  const modeOptions = (channel) => {
    const returned = channel.publisher_modes || [];
    const modes = returned.length ? returned : [{ mode: channel.publisher_mode || 'MANUAL', available: true, blocker: null }];
    return modes.map((item) => {
      const selected = item.mode === channel.publisher_mode ? ' selected' : '';
      const disabled = item.available ? '' : ' disabled';
      const suffix = item.available ? '' : ' — unavailable';
      return `<option value="${escapeHtml(item.mode)}"${selected}${disabled}>${escapeHtml(publisherModeLabel(item.mode) + suffix)}</option>`;
    }).join('');
  };

  const modeBlocker = (channel) => {
    const selected = publisherMode(channel, channel.publisher_mode);
    if (selected && !selected.available) return selected.blocker || 'This execution mode is not available.';
    if (channel.publisher_mode !== 'MANUAL' && channel.execution_blocker) return channel.execution_blocker;
    return channel.publisher_mode === 'MANUAL'
      ? 'No account connection is required. Partizan will not publish on your behalf in this mode.'
      : 'Mode availability comes from the live backend capability and policy gates.';
  };

  const capabilitiesHtml = (channel) => (channel.capabilities || []).map((item) => {
    const ready = item.ready ? ' ready' : '';
    const title = item.blocker ? ` title="${escapeHtml(item.blocker)}"` : '';
    return `<span class="channel-capability${ready}"${title}>${escapeHtml(CAPABILITY_LABELS[item.capability] || item.capability)} ${item.ready ? '✓' : '·'}</span>`;
  }).join('');

  const telegramConnectionHtml = (channel) => {
    const mode = clientOwnedMode(channel);
    if (!mode?.available) {
      return `<div class="channel-connection-box"><h4>Telegram customer account</h4><p>${escapeHtml(mode?.blocker || 'Client-owned Telegram publishing is not available yet.')}</p></div>`;
    }
    const connection = connectionFor('TELEGRAM');
    if (connection?.status === 'ACTIVE') {
      const label = connection.display_name || (connection.username ? `@${connection.username}` : 'Telegram account');
      return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>${escapeHtml(label)}</strong><small>Connected for client-owned Telegram execution</small></div><span class="channel-detail-status connected">Connected</span></div><div class="channel-connection-actions"><button class="button button-secondary" type="button" data-disconnect-channel="TELEGRAM">Disconnect</button></div><p class="channel-safe-note">Connection grants access only. A real publish still requires a prepared approved action and the existing Telegram governance gates.</p></div>`;
    }
    if (telegramChallenge) {
      const passwordField = telegramChallenge.status === 'PASSWORD_REQUIRED'
        ? '<input name="password" type="password" autocomplete="current-password" placeholder="2FA password" required>'
        : '<input name="password" type="password" autocomplete="current-password" placeholder="2FA password (only if requested)">';
      return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>Confirm Telegram login</strong><small>${escapeHtml(telegramChallenge.phone_hint || 'Code sent')}</small></div><span class="channel-detail-status needs">${escapeHtml(telegramChallenge.status)}</span></div><form class="channel-inline-form telegram-confirm" data-telegram-confirm><input name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="Telegram code" required>${passwordField}<button class="button button-primary" type="submit">Confirm</button></form><p class="channel-safe-note">The code and 2FA password are sent only to the connection endpoint and are never persisted in this page.</p></div>`;
    }
    return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>Connect Telegram</strong><small>Use your own Telegram account only when a recommended action needs it.</small></div><span class="channel-detail-status needs">Not connected</span></div><form class="channel-inline-form" data-telegram-start><input name="phone_number" type="tel" autocomplete="tel" placeholder="+15551234567" required><button class="button button-primary" type="submit">Send code</button></form><p class="channel-safe-note">Connecting does not authorize a publish. Partizan still needs a prepared approved action before using this account.</p></div>`;
  };

  const redditConnectionHtml = (channel) => {
    const mode = clientOwnedMode(channel);
    if (!mode?.available) {
      return `<div class="channel-connection-box"><h4>Reddit customer account</h4><p>${escapeHtml(mode?.blocker || 'Client-owned Reddit publishing is not available yet.')}</p></div>`;
    }
    const connection = connectionFor('REDDIT');
    if (connection?.status === 'ACTIVE') {
      const scopes = Array.isArray(connection.scopes) && connection.scopes.length ? connection.scopes.join(', ') : 'verified scopes';
      return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>${escapeHtml(connection.username ? `u/${connection.username}` : 'Reddit account')}</strong><small>${escapeHtml(scopes)}</small></div><span class="channel-detail-status connected">Connected</span></div><div class="channel-connection-actions"><button class="button button-secondary" type="button" data-disconnect-channel="REDDIT">Disconnect</button></div><p class="channel-safe-note">OAuth access never overrides subreddit policy. Ambiguous or stale policy remains fail-closed, and each publish still needs explicit confirmation.</p></div>`;
    }
    return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>Connect Reddit</strong><small>Authorize the customer-owned OAuth scopes required by the backend.</small></div><span class="channel-detail-status needs">Not connected</span></div><div class="channel-connection-actions"><button class="button button-primary" type="button" data-connect-channel="REDDIT">Connect Reddit →</button></div><p class="channel-safe-note">Connection does not mean permission to post. Community policy and explicit publish confirmation remain separate gates.</p></div>`;
  };

  const connectionHtml = (channel) => {
    if (channel.publisher_mode === 'PARTIZAN_MANAGED') {
      const managed = managedMode(channel);
      return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>Partizan Managed Distribution</strong><small>Customer-safe managed fulfillment</small></div><span class="channel-detail-status ${managed?.available ? 'connected' : 'needs'}">${managed?.available ? 'Available' : 'Unavailable'}</span></div><p>${escapeHtml(managed?.available ? 'Partizan can fulfill an eligible approved action through authorized managed inventory. Internal publisher identity stays private.' : (managed?.blocker || 'Managed distribution is not available for this channel.'))}</p></div>`;
    }
    if (channel.publisher_mode !== 'CLIENT_OWNED') {
      return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>Manual execution</strong><small>No account access required</small></div><span class="channel-detail-status enabled">Manual</span></div><p>Research remains available. You perform the external action yourself; Partizan does not publish from an account in this mode.</p></div>`;
    }
    if (channel.platform === 'TELEGRAM') return telegramConnectionHtml(channel);
    if (channel.platform === 'REDDIT') return redditConnectionHtml(channel);
    if (channel.platform === 'INSTAGRAM') {
      const connected = channel.connected === true;
      return `<div class="channel-connection-box"><div class="channel-connection-head"><div><strong>Instagram & Facebook</strong><small>${escapeHtml(connected ? 'Customer Meta access connected' : (channel.execution_blocker || 'Connect Meta when an eligible action needs it'))}</small></div><span class="channel-detail-status ${connected ? 'connected' : 'needs'}">${connected ? 'Connected' : 'Not connected'}</span></div>${connected ? '' : '<div class="channel-connection-actions"><button class="button button-primary" type="button" data-connect-channel="INSTAGRAM">Connect Meta →</button></div>'}</div>`;
    }
    return `<div class="channel-connection-box"><h4>${escapeHtml(channel.label)} account</h4><p>${escapeHtml(channel.execution_blocker || 'Customer-owned execution is not configured for this channel.')}</p></div>`;
  };

  const renderOverview = (channels) => {
    const ordered = [...channels].sort((a, b) => (b.spend_usd - a.spend_usd) || a.label.localeCompare(b.label));
    snapshot.innerHTML = ordered.length
      ? ordered.map((channel) => `<div class="channel-snapshot-row" data-platform="${escapeHtml(channel.platform)}"><div class="channel-snapshot-name"><strong>${escapeHtml(channel.label)}</strong><small>${escapeHtml(channelSubline(channel))}</small></div><div>${overviewControl(channel)}</div><span>${money(channel.spend_usd)} spent</span><span>${channel.cac_usd == null ? 'CAC —' : `CAC ${money(channel.cac_usd)}`}</span></div>`).join('')
      : '<div class="channel-snapshot-empty">No channel data yet.</div>';
  };

  const renderDetails = (channels) => {
    tableBody.innerHTML = channels.map((channel) => {
      const [status, statusClass] = detailStatus(channel);
      const spendClass = channel.spend_usd ? 'channel-metric' : 'channel-zero';
      const focused = focusedPlatform === channel.platform ? ' is-focused' : '';
      return `<tr class="${channelEnabled(channel) ? '' : 'channel-off'}" data-platform="${escapeHtml(channel.platform)}"><td class="channel-name"><strong>${escapeHtml(channel.label)}</strong><small>${escapeHtml(channelSubline(channel))}</small></td><td><span class="channel-detail-status ${statusClass}">${escapeHtml(status)}</span></td><td class="${spendClass}">${money(channel.spend_usd)}</td><td class="${channel.paid_customers ? 'channel-metric' : 'channel-zero'}">${channel.paid_customers}</td><td class="${channel.cac_usd == null ? 'channel-zero' : 'channel-metric'}">${channel.cac_usd == null ? '—' : money(channel.cac_usd)}</td><td class="${channel.revenue_usd ? 'channel-metric' : 'channel-zero'}">${money(channel.revenue_usd)}</td><td class="${channel.roas == null ? 'channel-zero' : 'channel-metric'}">${roas(channel.roas)}</td></tr><tr class="channel-execution-row" data-execution-platform="${escapeHtml(channel.platform)}"><td colspan="7"><div class="channel-execution-panel${focused}"><div class="channel-mode-box"><h4>How this channel should execute</h4><select class="channel-publisher-select" data-platform="${escapeHtml(channel.platform)}" aria-label="${escapeHtml(channel.label)} execution mode">${modeOptions(channel)}</select><p class="channel-mode-blocker">${escapeHtml(modeBlocker(channel))}</p><div class="channel-capabilities">${capabilitiesHtml(channel)}</div></div>${connectionHtml(channel)}</div></td></tr>`;
    }).join('');
  };

  const polishDetailsCopy = () => {
    const card = document.querySelector('.channels-card');
    if (!card) return;
    const title = card.querySelector('h2');
    const copy = card.querySelector('.section-copy');
    const legend = card.querySelector('.channel-legend, .channels-summary');
    const header = card.querySelector('thead th:nth-child(2)');
    const note = card.querySelector('.note');
    if (title) title.textContent = 'Choose where Partizan researches — and who executes';
    if (copy) copy.textContent = 'Research can stay on without account access. For execution, choose manual, your connected account, or Partizan Managed only when the backend says that mode is available.';
    if (header) header.textContent = 'Status';
    if (legend) {
      legend.className = 'channels-summary';
      legend.innerHTML = '<span><b>Manual</b> you execute</span><span><b>Use my account</b> client-owned access</span><span><b>Partizan Managed</b> only when live inventory is eligible</span>';
    }
    if (note) note.textContent = 'Connecting an account grants access only. Publishing still requires the existing action approval, platform policy and confirmation gates; turning a channel off keeps historical results visible.';
    const integrationNote = document.querySelector('.integrations-card > .note');
    if (integrationNote) integrationNote.textContent = 'Meta access is managed here. Telegram and Reddit customer-owned access is managed in Channels when a recommended action needs it.';
  };

  const fetchConnectionStates = async (channels) => {
    const projectId = currentProjectId();
    if (!projectId) return;
    const tasks = [];
    for (const channel of channels) {
      if (!['TELEGRAM', 'REDDIT'].includes(channel.platform)) continue;
      if (!clientOwnedMode(channel)) continue;
      const platform = channel.platform;
      const endpoint = `/customer/workspace/${encodeURIComponent(projectId)}/${platform.toLowerCase()}/connection`;
      tasks.push(api(endpoint)
        .then((value) => connectionState.set(platform, value))
        .catch(() => connectionState.delete(platform)));
    }
    await Promise.all(tasks);
  };

  const dataObserver = new MutationObserver(() => scheduleRefresh());
  const connectDataObserver = () => {
    dataObserver.observe(snapshot, { childList: true });
    dataObserver.observe(tableBody, { childList: true });
  };

  const render = (channels) => {
    latestChannels = channels;
    dataObserver.disconnect();
    syncing = true;
    try {
      renderOverview(channels);
      renderDetails(channels);
      syncMetaSettingsControl(channels);
      polishDetailsCopy();
    } finally {
      syncing = false;
      connectDataObserver();
    }
    if (focusedPlatform) {
      const panel = tableBody.querySelector(`[data-execution-platform="${CSS.escape(focusedPlatform)}"] .channel-execution-panel`);
      if (panel) window.setTimeout(() => panel.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
    }
  };

  const refresh = async () => {
    if (syncing || workspace.classList.contains('hidden')) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    try {
      const channels = await api(`/customer/workspace/${encodeURIComponent(projectId)}/channels`);
      await fetchConnectionStates(channels || []);
      render(channels || []);
    } catch (error) {
      showNotice(error.message, true);
    }
  };

  function scheduleRefresh() {
    window.clearTimeout(renderTimer);
    renderTimer = window.setTimeout(() => refresh(), 0);
  }

  const updateChannel = async (platform, change) => {
    const projectId = currentProjectId();
    if (!projectId) return;
    await api(`/customer/workspace/${encodeURIComponent(projectId)}/channels`, {
      method: 'PUT',
      body: JSON.stringify({ channels: [{ platform, ...change }] }),
    });
  };

  const beginMetaConnect = async (button) => {
    const projectId = currentProjectId();
    if (!projectId) return;
    button.disabled = true;
    try {
      const payload = await api(`/customer/workspace/${encodeURIComponent(projectId)}/meta/connect`, { method: 'POST' });
      window.location.href = payload.authorization_url;
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
    }
  };

  const beginRedditConnect = async (button) => {
    const projectId = currentProjectId();
    if (!projectId) return;
    button.disabled = true;
    try {
      const payload = await api(`/customer/workspace/${encodeURIComponent(projectId)}/reddit/connect`, { method: 'POST' });
      window.location.href = payload.authorization_url;
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
    }
  };

  const focusChannel = async (platform) => {
    focusedPlatform = platform;
    document.querySelector('.tab-button[data-tab="channels"]')?.click();
    await refresh();
  };

  const platformFromOpportunity = (opportunity) => {
    if (!opportunity || opportunity.surface !== 'COMMUNITY') return null;
    const candidate = opportunity.url || (Array.isArray(opportunity.provenance) ? opportunity.provenance[0]?.url : null);
    if (!candidate) return null;
    try {
      const hostname = new URL(candidate, window.location.origin).hostname.toLowerCase();
      if (hostname === 't.me' || hostname === 'telegram.me') return 'TELEGRAM';
      if (hostname === 'reddit.com' || hostname.endsWith('.reddit.com')) return 'REDDIT';
    } catch (_) {
      return null;
    }
    return null;
  };

  const handleActivationOpportunity = async (event) => {
    const button = event.target.closest('#activation-primary');
    if (!button || !/^Open where to do this/.test(button.textContent || '')) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    button.disabled = true;
    try {
      const data = await api(`/customer/workspace/${encodeURIComponent(projectId)}`);
      const opportunity = data.preview_opportunity || null;
      const platform = platformFromOpportunity(opportunity);
      if (platform) {
        showNotice(`Choose how to execute this ${platform === 'TELEGRAM' ? 'Telegram' : 'Reddit'} opportunity. Connecting an account is optional until you choose client-owned execution.`);
        await focusChannel(platform);
        return;
      }
      const destination = opportunity?.url || (Array.isArray(opportunity?.provenance) ? opportunity.provenance[0]?.url : null);
      if (destination) window.open(destination, '_blank', 'noopener,noreferrer');
      else showNotice('This opportunity has no safe destination URL yet.', true);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
  };

  document.addEventListener('click', handleActivationOpportunity, true);

  snapshot.addEventListener('click', async (event) => {
    const focus = event.target.closest('[data-focus-channel]');
    if (focus) {
      await focusChannel(focus.dataset.focusChannel);
      return;
    }
    const button = event.target.closest('[data-channel-connect="INSTAGRAM"]');
    if (button) beginMetaConnect(button);
  });

  snapshot.addEventListener('change', async (event) => {
    const toggle = event.target.closest('.channel-toggle');
    if (!toggle) return;
    const mode = toggle.checked ? toggle.dataset.onMode : 'OFF';
    const platform = toggle.dataset.platform;
    toggle.disabled = true;
    const label = toggle.closest('.channel-toggle-control')?.querySelector('.channel-toggle-label');
    if (label) label.textContent = toggle.checked ? 'On' : 'Off';
    try {
      await updateChannel(platform, { mode });
      showNotice(`${platform === 'INSTAGRAM' ? 'Instagram & Facebook' : platform} ${toggle.checked ? 'enabled' : 'turned off'}.`);
      await refresh();
    } catch (error) {
      showNotice(error.message, true);
      await refresh();
    } finally {
      toggle.disabled = false;
    }
  });

  tableBody.addEventListener('change', async (event) => {
    const select = event.target.closest('.channel-publisher-select');
    if (!select) return;
    const platform = select.dataset.platform;
    const publisher_mode = select.value;
    select.disabled = true;
    focusedPlatform = platform;
    try {
      await updateChannel(platform, { publisher_mode });
      showNotice(`${platform}: ${publisherModeLabel(publisher_mode)} selected.`);
      await refresh();
    } catch (error) {
      showNotice(error.message, true);
      await refresh();
    } finally {
      select.disabled = false;
    }
  });

  tableBody.addEventListener('submit', async (event) => {
    const startForm = event.target.closest('[data-telegram-start]');
    const confirmForm = event.target.closest('[data-telegram-confirm]');
    if (!startForm && !confirmForm) return;
    event.preventDefault();
    const projectId = currentProjectId();
    if (!projectId) return;
    const submit = event.target.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      if (startForm) {
        const phone = new FormData(startForm).get('phone_number');
        telegramChallenge = await api(`/customer/workspace/${encodeURIComponent(projectId)}/telegram/connection/start`, {
          method: 'POST',
          body: JSON.stringify({ phone_number: String(phone || '').trim() }),
        });
        showNotice('Telegram sent a login code. Enter it here to finish the connection.');
      } else if (confirmForm && telegramChallenge) {
        const form = new FormData(confirmForm);
        const password = String(form.get('password') || '');
        const payload = await api(`/customer/workspace/${encodeURIComponent(projectId)}/telegram/connection/confirm`, {
          method: 'POST',
          body: JSON.stringify({
            challenge_id: telegramChallenge.challenge_id,
            code: String(form.get('code') || '').trim(),
            ...(password ? { password } : {}),
          }),
        });
        if (payload.status === 'PASSWORD_REQUIRED') {
          telegramChallenge = payload;
          showNotice('Telegram requires your 2FA password to finish this login.');
        } else {
          telegramChallenge = null;
          connectionState.set('TELEGRAM', payload);
          showNotice('Telegram connected. No publish was triggered.');
        }
      }
      await refresh();
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      if (submit) submit.disabled = false;
    }
  });

  tableBody.addEventListener('click', async (event) => {
    const connect = event.target.closest('[data-connect-channel]');
    if (connect) {
      if (connect.dataset.connectChannel === 'REDDIT') await beginRedditConnect(connect);
      if (connect.dataset.connectChannel === 'INSTAGRAM') await beginMetaConnect(connect);
      return;
    }
    const disconnect = event.target.closest('[data-disconnect-channel]');
    if (!disconnect) return;
    const platform = disconnect.dataset.disconnectChannel;
    if (!['TELEGRAM', 'REDDIT'].includes(platform)) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    disconnect.disabled = true;
    try {
      await api(`/customer/workspace/${encodeURIComponent(projectId)}/${platform.toLowerCase()}/connection`, { method: 'DELETE' });
      if (platform === 'TELEGRAM') telegramChallenge = null;
      connectionState.delete(platform);
      showNotice(`${platform === 'TELEGRAM' ? 'Telegram' : 'Reddit'} disconnected.`);
      await refresh();
    } catch (error) {
      showNotice(error.message, true);
      disconnect.disabled = false;
    }
  });

  const handleRedditCallback = () => {
    const url = new URL(window.location.href);
    const state = url.searchParams.get('reddit');
    if (!state) return;
    if (state === 'connected') showNotice('Reddit connected. Publishing still requires an approved action, policy clearance and explicit confirmation.');
    if (state === 'error') showNotice(`Reddit connection failed${url.searchParams.get('reason') ? `: ${url.searchParams.get('reason')}` : '.'}`, true);
    url.searchParams.delete('reddit');
    url.searchParams.delete('reason');
    window.history.replaceState({}, '', `${url.pathname}?${url.searchParams.toString()}`.replace(/\?$/, ''));
    focusedPlatform = 'REDDIT';
    document.querySelector('.tab-button[data-tab="channels"]')?.click();
  };

  const workspaceObserver = new MutationObserver(() => {
    if (!workspace.classList.contains('hidden')) scheduleRefresh();
  });
  workspaceObserver.observe(workspace, { attributes: true, attributeFilter: ['class'] });
  ensureStyles();
  connectDataObserver();
  polishDetailsCopy();
  handleRedditCallback();
  scheduleRefresh();
})();
