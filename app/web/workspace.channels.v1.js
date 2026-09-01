(() => {
  const $ = (id) => document.getElementById(id);
  const snapshot = $('channel-snapshot');
  const tableBody = $('channels-table-body');
  const workspace = $('workspace');
  if (!snapshot || !tableBody || !workspace) return;

  let renderTimer = null;
  let syncing = false;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]));
  const money = (value) => `$${Number(value || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
  const roas = (value) => value == null ? '—' : `${Number(value).toFixed(2)}×`;
  const currentProjectId = () => new URLSearchParams(window.location.search).get('project');
  const channelEnabled = (channel) => channel.mode !== 'OFF';
  const enabledMode = (channel) => channel.execution_ready ? 'AUTO' : 'RESEARCH_ONLY';

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
    window.setTimeout(() => notice.classList.add('hidden'), 3600);
  };

  const detailStatus = (channel) => {
    if (channel.platform === 'INSTAGRAM') {
      if (!channel.connected) return ['Needs connection', 'needs'];
      if (!channelEnabled(channel)) return ['Connected · Off', 'off'];
      if (!channel.execution_ready) return ['Connected · Research only', 'needs'];
      return ['Paid execution ready', 'connected'];
    }
    if (!channelEnabled(channel)) return ['Off', 'off'];
    return ['Research enabled', 'enabled'];
  };

  const channelSubline = (channel) => {
    if (channel.platform === 'INSTAGRAM') {
      if (!channel.connected) return 'Connect Meta before paid execution';
      if (!channel.execution_ready) {
        return channel.execution_blocker || 'Connected · paid execution is not ready yet';
      }
      return 'Paid execution ready';
    }
    return 'Research surface · execution not available yet';
  };

  const overviewControl = (channel) => {
    if (channel.platform === 'INSTAGRAM' && !channel.connected) {
      return `<button class="channel-connect-button" type="button" data-channel-connect="INSTAGRAM">Connect</button>`;
    }
    const checked = channelEnabled(channel) ? ' checked' : '';
    const label = channelEnabled(channel) ? 'On' : 'Off';
    return `<label class="channel-toggle-control"><input class="channel-toggle" type="checkbox" data-platform="${escapeHtml(channel.platform)}" data-on-mode="${escapeHtml(enabledMode(channel))}" aria-label="${escapeHtml(channel.label)} enabled"${checked}><span class="channel-toggle-track" aria-hidden="true"></span><span class="channel-toggle-label">${label}</span></label>`;
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
      return `<tr class="${channelEnabled(channel) ? '' : 'channel-off'}" data-platform="${escapeHtml(channel.platform)}"><td class="channel-name"><strong>${escapeHtml(channel.label)}</strong><small>${escapeHtml(channelSubline(channel))}</small></td><td><span class="channel-detail-status ${statusClass}">${escapeHtml(status)}</span></td><td class="${spendClass}">${money(channel.spend_usd)}</td><td class="${channel.paid_customers ? 'channel-metric' : 'channel-zero'}">${channel.paid_customers}</td><td class="${channel.cac_usd == null ? 'channel-zero' : 'channel-metric'}">${channel.cac_usd == null ? '—' : money(channel.cac_usd)}</td><td class="${channel.revenue_usd ? 'channel-metric' : 'channel-zero'}">${money(channel.revenue_usd)}</td><td class="${channel.roas == null ? 'channel-zero' : 'channel-metric'}">${roas(channel.roas)}</td></tr>`;
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
    if (title) title.textContent = 'Channel details';
    if (copy) copy.textContent = 'Compare connection status, spend and results by channel. Turn channels on or off from Overview.';
    if (header) header.textContent = 'Status';
    if (legend) {
      legend.className = 'channels-summary';
      legend.innerHTML = '<span><b>Overview</b> controls channels</span><span><b>Here</b> you compare performance</span><span><b>History</b> stays visible when a channel is off</span>';
    }
    if (note) note.textContent = 'Channel details are read-only here. Use the toggles on Overview to include or exclude channels; historical spend and results remain visible.';
  };

  const dataObserver = new MutationObserver(() => scheduleRefresh());
  const connectDataObserver = () => {
    dataObserver.observe(snapshot, { childList: true });
    dataObserver.observe(tableBody, { childList: true });
  };

  const render = (channels) => {
    dataObserver.disconnect();
    syncing = true;
    try {
      renderOverview(channels);
      renderDetails(channels);
      polishDetailsCopy();
    } finally {
      syncing = false;
      connectDataObserver();
    }
  };

  const refresh = async () => {
    if (syncing || workspace.classList.contains('hidden')) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    try {
      const channels = await api(`/customer/workspace/${encodeURIComponent(projectId)}/channels`);
      render(channels || []);
    } catch (error) {
      showNotice(error.message, true);
    }
  };

  function scheduleRefresh() {
    window.clearTimeout(renderTimer);
    renderTimer = window.setTimeout(() => refresh(), 0);
  }

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

  snapshot.addEventListener('click', (event) => {
    const button = event.target.closest('.channel-connect-button');
    if (button) beginMetaConnect(button);
  });

  snapshot.addEventListener('change', async (event) => {
    const toggle = event.target.closest('.channel-toggle');
    if (!toggle) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    const mode = toggle.checked ? toggle.dataset.onMode : 'OFF';
    const platform = toggle.dataset.platform;
    toggle.disabled = true;
    const label = toggle.closest('.channel-toggle-control')?.querySelector('.channel-toggle-label');
    if (label) label.textContent = toggle.checked ? 'On' : 'Off';
    try {
      await api(`/customer/workspace/${encodeURIComponent(projectId)}/channels`, {
        method: 'PUT',
        body: JSON.stringify({ channels: [{ platform, mode }] }),
      });
      showNotice(`${platform === 'INSTAGRAM' ? 'Instagram & Facebook' : platform} ${toggle.checked ? 'enabled' : 'turned off'}.`);
      await refresh();
    } catch (error) {
      showNotice(error.message, true);
      await refresh();
    } finally {
      toggle.disabled = false;
    }
  });

  const workspaceObserver = new MutationObserver(() => {
    if (!workspace.classList.contains('hidden')) scheduleRefresh();
  });
  workspaceObserver.observe(workspace, { attributes: true, attributeFilter: ['class'] });
  connectDataObserver();
  polishDetailsCopy();
  scheduleRefresh();
})();
