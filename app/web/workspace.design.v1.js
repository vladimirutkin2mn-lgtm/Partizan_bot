/* Layout only: reuse live DOM, native handlers and server-provided channel capabilities. */
(() => {
  const $ = (id) => document.getElementById(id);
  const workspace = $('workspace');
  if (!workspace) return;
  const panel = (name) => document.querySelector(`[data-tab-panel="${name}"]`);
  const activity = panel('activity');
  const dialog = $('design-next-dialog');
  const content = $('design-next-content');
  const resultsNav = $('design-results-nav');
  let resultSection = 'progress';
  let navigatingResult = false;
  const setText = (node, value) => {
    if (node && node.textContent !== value) node.textContent = value;
  };
  const closeReview = () => { if (dialog.open) dialog.close(); };
  const revealControl = (node) => {
    for (let parent = node?.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
  };
  window.addEventListener('partizan:reveal-control', (event) => {
    const node = $(event.detail.id) || document.querySelector(event.detail.selector || ':not(*)');
    revealControl(node);
    if (node && activity.contains(node)) {
      resultSection = node.closest('[data-design-group]')?.dataset.designGroup || 'progress';
      filterResults();
    }
  });

  // Move the existing, already bound buttons; never create a second tab controller.
  const settings = document.querySelector('.tab-button[data-tab="settings"]');
  settings.classList.add('ws-icon-button');
  document.querySelector('.ws-account').append(settings);
  const tests = $('autoresearch-tab');
  if (tests) {
    tests.dataset.designResult = 'tests';
    resultsNav.querySelector('[data-design-result="tests"]').replaceWith(tests);
  }
  const fold = (node, label, id) => {
    if (!node || node.closest('.design-disclosure')) return;
    const details = document.createElement('details'); details.className = 'design-disclosure';
    if (id) details.id = id;
    const summary = document.createElement('summary'); summary.textContent = label;
    node.before(details); details.append(summary, node);
  };
  fold(document.querySelector('.balance-card'), 'Budget', 'design-budget');
  fold(document.querySelector('.guardrail-card'), 'Spending limits');
  fold(document.querySelector('.integrations-card'), 'Connected accounts', 'design-accounts');

  const organize = () => {
    ['activation-card', 'channel-choice-card', 'execution-request-card'].forEach((id) => {
      const node = $(id); if (node && node.parentElement !== content) content.append(node);
    });
    ['performance-metrics', 'performance-finance', 'performance-overview', 'autoresearch-overview'].forEach((id) => {
      const node = $(id);
      if (node && node.parentElement !== activity) {
        node.dataset.designGroup = 'progress'; activity.prepend(node);
      }
    });
    const snapshotCard = document.querySelector('.channel-snapshot-card');
    if (snapshotCard && snapshotCard.parentElement !== panel('channels')) {
      panel('channels').prepend(snapshotCard);
      snapshotCard.querySelector('.section-head')?.remove();
    }
    fold(document.querySelector('.channels-card'), 'Channel details & connections', 'design-channel-details');
    fold(document.querySelector('.project-details-card'), 'Project');
    Array.from(activity.children).forEach((node) => {
      if (!node.dataset.designGroup) node.dataset.designGroup = node.id === 'community-action-inbox' ? 'history' : 'progress';
    });
    filterResults();
  };
  const filterResults = () => {
    Array.from(activity.children).forEach((node) => {
      node.toggleAttribute('data-design-hidden', node.dataset.designGroup !== resultSection);
    });
    resultsNav.querySelectorAll('button').forEach((button) => {
      const active = button.dataset.designResult === resultSection;
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
  };
  const activeTab = () => document.querySelector('[data-tab-panel]:not(.hidden)')?.dataset.tabPanel || 'overview';
  const syncTabs = () => {
    const tab = activeTab();
    const signedIn = !workspace.classList.contains('hidden');
    const onResults = tab === 'activity' || tab === 'experiments';
    document.querySelector('.ws-nav').classList.toggle('hidden', !signedIn);
    settings.classList.toggle('hidden', !signedIn);
    resultsNav.classList.toggle('hidden', !onResults);
    document.querySelectorAll('.tab-button').forEach((button) => {
      const selected = button.dataset.tab === tab || (button.dataset.tab === 'activity' && onResults);
      if (selected) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    setText($('design-page-title'), ({ overview: 'Your growth', activity: 'Results', experiments: 'Results', channels: 'Channels', settings: 'Settings' })[tab] || 'Your growth');
    $('workspace-status').hidden = tab !== 'overview';
    document.querySelector('.hero-actions').hidden = tab !== 'overview';
    $('design-page-subtitle').hidden = tab === 'overview';
    setText($('design-page-subtitle'), ({ activity: 'What’s working, and what we’re learning.', experiments: 'What’s working, and what we’re learning.', channels: 'Choose where to find your customers.', settings: 'Your budget, accounts and project.' })[tab] || '');
    if (tab === 'experiments') resultSection = 'tests';
    else if (tab === 'activity' && resultSection === 'tests') resultSection = 'progress';
    filterResults();
    if (tab !== 'overview' || !signedIn) closeReview();
  };
  const showResult = (name) => {
    resultSection = name;
    const button = name === 'tests' ? tests : document.querySelector('.tab-button[data-tab="activity"]');
    navigatingResult = true;
    button?.click();
    navigatingResult = false;
    filterResults(); syncTabs();
  };
  document.querySelectorAll('[data-design-result]').forEach((button) => {
    if (button === tests) return;
    button.addEventListener('click', () => showResult(button.dataset.designResult));
  });
  document.querySelector('.ws-nav [data-tab="activity"]').addEventListener('click', () => {
    // Explicitly choosing Results from the header returns to its Progress screen.
    if (!navigatingResult) resultSection = 'progress';
    syncTabs();
  });
  $('design-review').addEventListener('click', () => {
    if (!dialog.open) dialog.showModal();
  });
  $('design-close-review').addEventListener('click', closeReview);
  $('design-add-funds').addEventListener('click', () => {
    $('design-budget').open = true;
    $('overview-fund-button').click();
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#project-menu')) $('project-menu').open = false;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && $('project-menu').open) {
      $('project-menu').open = false; $('project-menu').querySelector('summary').focus();
    }
  });

  const syncSummary = () => {
    document.querySelectorAll('[data-mirror]').forEach((target) => {
      const source = $(target.dataset.mirror);
      if (!source) return;
      const value = source.textContent.trim();
      setText(target, target.hasAttribute('data-currency') && !value.startsWith('$') && value !== '—' ? `$${value}` : value);
    });
    const choice = [$('execution-request-card'), $('channel-choice-card'), $('activation-card')]
      .find((node) => node && !node.classList.contains('hidden'));
    const current = $('current-work')?.querySelector('div');
    const title = choice?.querySelector('h2')?.textContent || current?.querySelector('strong')?.textContent || 'Your next move will appear here.';
    const copy = choice?.querySelector(':scope > .section-copy')?.textContent || current?.querySelector('span')?.textContent || 'Partizan is checking your project for a useful next step.';
    setText($('design-next-title'), title); setText($('design-next-copy'), copy);
    $('design-review').disabled = !choice;
    setText($('design-review'), choice?.id === 'execution-request-card' ? 'Review exact action →' : 'Review next move →');
    const decision = $('decisions')?.querySelector('div');
    setText($('design-recent-title'), decision?.querySelector('strong')?.textContent || 'Your workspace is ready.');
    setText($('design-recent-copy'), decision?.querySelector('span')?.textContent || 'Research, actions and results stay together.');
    const select = $('project-switcher');
    setText($('design-project-name'), select.options[select.selectedIndex]?.textContent || 'Your projects');
  };
  const syncChannels = () => {
    $('channel-snapshot').querySelectorAll('[data-platform]').forEach((row) => {
      if (!row.classList.contains('channel-snapshot-row') || row.querySelector('[data-design-manage]')) return;
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'ws-text-link design-manage-channel';
      button.dataset.designManage = row.dataset.platform; button.textContent = 'Manage ›';
      button.setAttribute('aria-label', `Manage ${row.querySelector('strong')?.textContent || row.dataset.platform}`);
      button.addEventListener('click', () => {
        $('design-channel-details').open = true;
        const target = Array.from($('channels-table-body').querySelectorAll('[data-execution-platform]'))
          .find((node) => node.dataset.executionPlatform === row.dataset.platform);
        target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target?.querySelector('select, input, button')?.focus({ preventScroll: true });
      });
      row.querySelector('.channel-snapshot-name')?.after(button);
    });
  };
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-focus-channel]')) $('design-channel-details').open = true;
  }, true);
  const structureObserver = new MutationObserver(organize);
  structureObserver.observe(panel('overview'), { childList: true });
  structureObserver.observe(activity, { childList: true });
  structureObserver.observe(panel('settings'), { childList: true });
  const tabObserver = new MutationObserver(syncTabs);
  [workspace, ...document.querySelectorAll('[data-tab-panel]')].forEach((node) => {
    tabObserver.observe(node, { attributes: true, attributeFilter: ['class'] });
  });
  const summaryObserver = new MutationObserver(syncSummary);
  [content, $('current-work'), $('decisions'), $('project-switcher'), ...document.querySelectorAll('[id^="metric-"]')].forEach((node) => {
    if (node) summaryObserver.observe(node, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class'] });
  });
  new MutationObserver(syncChannels).observe($('channel-snapshot'), { childList: true });
  window.addEventListener('partizan:workspace-ready', () => { organize(); syncSummary(); syncTabs(); syncChannels(); });
  organize(); syncSummary(); syncTabs(); syncChannels();
})();
