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

  const refreshProjectLabels = async () => {
    const switcher = document.getElementById('project-switcher');
    if (!switcher || !switcher.options.length) return;
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
    } catch (_) {
      // The core workspace handles authentication; labels can safely keep their fallback.
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
