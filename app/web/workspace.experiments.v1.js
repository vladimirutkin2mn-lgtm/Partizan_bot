(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);
  const money = (value) => `$${Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;
  let projectId = new URLSearchParams(window.location.search).get('project');
  let overview = null;

  const syncProjectId = (candidate = null) => {
    projectId = candidate || new URLSearchParams(window.location.search).get('project');
    return projectId;
  };

  const api = async (path, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (options.body != null) headers.set('Content-Type', 'application/json');
    const response = await fetch(path, {
      ...options,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload.detail || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  };

  const statusLabel = (status) => ({
    NOT_CONFIGURED: 'Not configured',
    PAUSED: 'Paused',
    NO_BASELINE: 'Needs baseline',
    BUDGET_EXHAUSTED: 'Research budget used',
    WAITING_EVIDENCE: 'Waiting for results',
    GENERATED: 'New test ready',
    IDLE: 'Ready for next test',
  })[status] || status || 'Not started';

  const objectiveLabel = (objective) => ({
    PAID_CAC: 'paid CAC',
    PAID_CONVERSION: 'paid conversion',
    ACTIVATION_CONVERSION: 'activation conversion',
    SIGNUP_CONVERSION: 'signup conversion',
    NONE: 'insufficient downstream evidence',
  })[objective] || objective || 'evidence';

  const outcomeLabel = (outcome) => ({
    KEEP: 'Adopted',
    DISCARD: 'Stopped',
    INCONCLUSIVE: 'Inconclusive',
    BLOCKED: 'Blocked',
    FAILED: 'Failed',
  })[outcome] || outcome || 'Pending';

  const surfaceLabel = (surface) => ({
    EXECUTION_PLATFORM: 'Execution platform',
    CREATOR: 'Creator / influencer',
    MEDIA: 'Media / newsletter',
    PARTNERSHIP: 'Partnership / affiliate',
    SEARCH: 'Search / SEO',
    DIRECTORY: 'Directory / reviews',
    COMMUNITY: 'Public community',
  })[surface] || surface || 'Research';

  const compact = (value, limit = 88) => {
    const text = String(value || '').trim();
    if (!text) return '';
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  };

  const metricSummary = (evidence) => {
    if (!evidence) return 'No measured baseline evidence yet.';
    const values = [];
    if (Number(evidence.paid_users || 0) > 0) values.push(`${evidence.paid_users} customers`);
    if (Number(evidence.spend || 0) > 0) values.push(`${money(evidence.spend)} spend`);
    if (Number(evidence.paid_users || 0) > 0 && Number(evidence.spend || 0) > 0) {
      values.push(`CAC ${money(Number(evidence.spend) / Number(evidence.paid_users))}`);
    }
    if (Number(evidence.revenue || 0) > 0) values.push(`${money(evidence.revenue)} revenue`);
    if (Number(evidence.spend || 0) > 0 && Number(evidence.revenue || 0) > 0) {
      values.push(`ROAS ${(Number(evidence.revenue) / Number(evidence.spend)).toFixed(2)}×`);
    }
    return values.length ? values.join(' · ') : 'No measured downstream outcome yet.';
  };

  const variantRows = (variant) => {
    if (!variant) return '<div class="ar-empty">No variant established yet.</div>';
    const rows = [
      ['Channel', variant.platform],
      ['Tactic', variant.tactic_id],
      ['Audience', variant.audience],
      ['Message', variant.message_angle],
      ['Offer', variant.offer],
    ].filter((item) => item[1]);
    return rows.map((item) => (
      `<div class="ar-variant-row"><span>${escapeHtml(item[0])}</span>`
      + `<strong>${escapeHtml(item[1])}</strong></div>`
    )).join('');
  };

  const renderProvenance = (items) => {
    const node = $('autoresearch-provenance');
    if (!items || !items.length) {
      node.innerHTML = '<div class="ar-empty">No persisted public research source is attached yet.</div>';
      return;
    }
    node.innerHTML = items.map((item) => {
      const links = (item.source_urls || []).slice(0, 3).map((url) => (
        `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">source ↗</a>`
      )).join(' ');
      const tags = (item.signal_tags || []).slice(0, 5).join(' · ');
      return `<article class="ar-source">
        <div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.platform || surfaceLabel(item.surface))}</span></div>
        <p>${escapeHtml(item.rationale || 'Public research evidence for this hypothesis.')}</p>
        <small>${escapeHtml(tags || 'research provenance')}${links ? ` · ${links}` : ''}</small>
      </article>`;
    }).join('');
  };

  const renderOverview = (data) => {
    const status = $('autoresearch-overview-status');
    if (!status) return;
    status.textContent = statusLabel(data.status);
    status.classList.toggle('good', data.status === 'IDLE');
    status.classList.toggle('warn', [
      'PAUSED', 'NO_BASELINE', 'BUDGET_EXHAUSTED', 'WAITING_EVIDENCE',
    ].includes(data.status));

    const champion = data.champion;
    const trial = data.active_trial;
    const latestEvaluation = (data.recent_evaluations || [])[0];

    $('autoresearch-overview-winner').textContent = champion
      ? compact(
        champion.variant.message_angle
          || champion.variant.audience
          || champion.variant.tactic_id
          || champion.variant.platform,
      )
      : 'Waiting for a measured baseline';
    $('autoresearch-overview-winner-detail').textContent = champion
      ? `${champion.variant.platform} · ${metricSummary(champion.evidence)}`
      : 'Public research can suggest tests, but it cannot create a winner.';

    $('autoresearch-overview-test').textContent = trial
      ? compact(trial.hypothesis || `Testing ${trial.changed_dimensions.join(', ')}`)
      : (data.status === 'PAUSED' ? 'Continuous testing is paused' : 'Ready for the next bounded test');
    $('autoresearch-overview-test-detail').textContent = trial
      ? `${trial.challenger.platform} · changed ${trial.changed_dimensions.join(', ')}`
      : (data.status === 'NO_BASELINE'
        ? 'Partizan needs a baseline before it can compare challengers.'
        : 'Partizan proposes one challenger at a time when policy and evidence allow it.');

    $('autoresearch-overview-learning').textContent = latestEvaluation
      ? outcomeLabel(latestEvaluation.outcome)
      : statusLabel(data.status);
    $('autoresearch-overview-learning-detail').textContent = latestEvaluation
      ? compact(
        (latestEvaluation.rationale || [])[0]
          || `Decision based on ${objectiveLabel(latestEvaluation.objective)}.`,
        112,
      )
      : 'Each decision is stored and feeds the next hypothesis instead of resetting the plan.';

    $('autoresearch-overview-boundary').textContent = (
      'Continuous AutoResearch is included in the funded workspace. Research can suggest what to test; '
      + 'measured/replay evidence decides winners. Paid execution remains behind settlement and channel-permission gates.'
    );
  };

  const renderOverviewUnavailable = (error) => {
    if (!$('autoresearch-overview-status')) return;
    const needsResearch = error && error.status === 409;
    $('autoresearch-overview-status').textContent = needsResearch ? 'Starts after deep research' : 'Not available';
    $('autoresearch-overview-status').classList.remove('good');
    $('autoresearch-overview-status').classList.add('warn');
    $('autoresearch-overview-winner').textContent = 'No measured winner yet';
    $('autoresearch-overview-winner-detail').textContent = needsResearch
      ? 'Deep research establishes the evidence context before continuous testing starts.'
      : 'AutoResearch state could not be loaded.';
    $('autoresearch-overview-test').textContent = needsResearch
      ? 'First challenger comes after research'
      : 'No active challenger visible';
    $('autoresearch-overview-test-detail').textContent = needsResearch
      ? 'The free pre-scan is a snapshot; continuous learning begins in the funded workspace after research.'
      : 'Open Experiments later to retry.';
    $('autoresearch-overview-learning').textContent = needsResearch ? 'Continuous learning included' : 'Waiting';
    $('autoresearch-overview-learning-detail').textContent = needsResearch
      ? 'Partizan will retain decisions and use them to choose what to test next.'
      : 'No learning state is available right now.';
    $('autoresearch-overview-boundary').textContent = (
      'Funding unlocks continuous research and learning, not unrestricted ad spend. '
      + 'Every execution path still requires its normal integration, permission and spend gates.'
    );
  };

  const renderHistory = (data) => {
    const evaluations = new Map(
      (data.recent_evaluations || []).map((item) => [String(item.trial_id), item]),
    );
    const trials = data.recent_trials || [];
    $('autoresearch-history').innerHTML = trials.length ? trials.map((trial) => {
      const evaluation = evaluations.get(String(trial.id));
      const state = evaluation ? outcomeLabel(evaluation.outcome) : 'Waiting for results';
      const rationale = evaluation && evaluation.rationale && evaluation.rationale.length
        ? evaluation.rationale[0]
        : (trial.hypothesis || 'Bounded challenger awaiting measured evidence.');
      const objective = evaluation ? ` · ${objectiveLabel(evaluation.objective)}` : '';
      return `<div class="ar-history-row">
        <div><strong>${escapeHtml(state)}</strong><span>${escapeHtml(trial.challenger.platform)} · ${escapeHtml(trial.changed_dimensions.join(', '))}${escapeHtml(objective)}</span></div>
        <p>${escapeHtml(rationale)}</p>
      </div>`;
    }).join('') : '<div class="ar-empty">No AutoResearch tests yet.</div>';
  };

  const render = (data) => {
    overview = data;
    renderOverview(data);
    $('autoresearch-status').textContent = statusLabel(data.status);
    $('autoresearch-status').classList.toggle('good', data.status === 'IDLE');
    $('autoresearch-status').classList.toggle('warn', [
      'PAUSED', 'NO_BASELINE', 'BUDGET_EXHAUSTED', 'WAITING_EVIDENCE',
    ].includes(data.status));

    const champion = data.champion;
    $('autoresearch-winner-variant').innerHTML = champion
      ? variantRows(champion.variant)
      : '<div class="ar-empty">A measured/replay baseline has not been established yet.</div>';
    $('autoresearch-winner-metrics').textContent = champion
      ? metricSummary(champion.evidence)
      : 'AutoResearch will not invent a winner from public research.';

    const trial = data.active_trial;
    if (trial) {
      $('autoresearch-testing-title').textContent = trial.hypothesis || 'Bounded challenger';
      $('autoresearch-testing-meta').textContent = `${trial.challenger.platform} · changed ${trial.changed_dimensions.join(', ')} · shadow budget ${money(trial.challenger.test_budget)}`;
      $('autoresearch-testing-variant').innerHTML = variantRows(trial.challenger);
      $('autoresearch-testing-state').textContent = 'Waiting for measured/replay evidence before any decision.';
    } else {
      $('autoresearch-testing-title').textContent = data.status === 'PAUSED'
        ? 'AutoResearch is paused.'
        : 'No challenger is waiting right now.';
      $('autoresearch-testing-meta').textContent = data.remaining_research_budget == null
        ? 'Research budget is governed by the configured per-trial limit.'
        : `${money(data.remaining_research_budget)} shadow research budget remains.`;
      $('autoresearch-testing-variant').innerHTML = '';
      $('autoresearch-testing-state').textContent = data.status === 'NO_BASELINE'
        ? 'Establish a baseline before Partizan can compare challengers scientifically.'
        : 'The next worker sweep may generate one bounded hypothesis when policy allows it.';
    }

    renderProvenance(data.provenance || []);
    renderHistory(data);

    const control = $('autoresearch-control');
    control.classList.toggle('hidden', !data.configured);
    control.textContent = data.paused ? 'Resume AutoResearch' : 'Pause AutoResearch';
    control.dataset.nextStatus = data.paused ? 'ACTIVE' : 'PAUSED';
    $('autoresearch-boundary').textContent = (
      'Research sources can justify what to test, but never count as visits, conversions, customers, '
      + 'CAC, revenue or permission to spend. Champion changes require Phase 2 measured/replay evidence.'
    );
  };

  const load = async () => {
    if (!syncProjectId(projectId)) return;
    const node = $('autoresearch-loading');
    node.classList.remove('hidden');
    $('autoresearch-error').classList.add('hidden');
    try {
      const data = await api(`/customer/workspace/${encodeURIComponent(projectId)}/autoresearch`);
      render(data);
      $('autoresearch-content').classList.remove('hidden');
    } catch (error) {
      renderOverviewUnavailable(error);
      $('autoresearch-content').classList.add('hidden');
      $('autoresearch-error').textContent = error.status === 409
        ? 'Complete deep research to start continuous AutoResearch.'
        : error.message;
      $('autoresearch-error').classList.remove('hidden');
    } finally {
      node.classList.add('hidden');
    }
  };

  const activate = () => {
    document.querySelectorAll('.tab-button').forEach((button) => {
      button.classList.toggle('active', button.dataset.tab === 'experiments');
    });
    document.querySelectorAll('[data-tab-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.tabPanel !== 'experiments');
    });
    load().catch(() => {});
  };

  const install = () => {
    const nav = document.querySelector('.workspace-tabs');
    const workspaceNode = $('workspace');
    if (!nav || !workspaceNode || $('autoresearch-tab')) return;

    const button = document.createElement('button');
    button.id = 'autoresearch-tab';
    button.className = 'tab-button';
    button.type = 'button';
    button.dataset.tab = 'experiments';
    button.textContent = 'Experiments';
    const settings = nav.querySelector('[data-tab="settings"]');
    nav.insertBefore(button, settings || null);
    button.addEventListener('click', activate);

    nav.querySelectorAll('.tab-button:not(#autoresearch-tab)').forEach((item) => {
      item.addEventListener('click', () => {
        const panel = $('autoresearch-panel');
        if (panel) panel.classList.add('hidden');
      });
    });

    const overviewPanel = workspaceNode.querySelector('[data-tab-panel="overview"]');
    if (overviewPanel && !$('autoresearch-overview')) {
      const overviewCard = document.createElement('section');
      overviewCard.id = 'autoresearch-overview';
      overviewCard.className = 'panel ar-overview';
      overviewCard.innerHTML = `
        <div class="ar-overview-head">
          <div>
            <span class="eyebrow">Continuous learning · included</span>
            <h2>Partizan keeps improving how it gets you customers.</h2>
            <p>Research → test → measure → learn → test again. The plan does not reset after the first recommendation.</p>
          </div>
          <div class="ar-overview-actions"><span id="autoresearch-overview-status" class="status-pill">Loading</span><button id="autoresearch-overview-open" class="text-button" type="button">Open experiments →</button></div>
        </div>
        <div class="ar-overview-grid">
          <div><span>Current winner</span><strong id="autoresearch-overview-winner">Loading…</strong><small id="autoresearch-overview-winner-detail">Checking measured evidence.</small></div>
          <div><span>Testing now</span><strong id="autoresearch-overview-test">Loading…</strong><small id="autoresearch-overview-test-detail">Checking the active challenger.</small></div>
          <div><span>Learning</span><strong id="autoresearch-overview-learning">Loading…</strong><small id="autoresearch-overview-learning-detail">Checking recent decisions.</small></div>
        </div>
        <p id="autoresearch-overview-boundary" class="note ar-overview-boundary">Loading AutoResearch safety state…</p>`;
      const finance = overviewPanel.querySelector('.overview-finance');
      overviewPanel.insertBefore(overviewCard, finance || null);
      $('autoresearch-overview-open').addEventListener('click', activate);
    }

    const panel = document.createElement('section');
    panel.id = 'autoresearch-panel';
    panel.className = 'tab-panel hidden';
    panel.dataset.tabPanel = 'experiments';
    panel.innerHTML = `
      <section class="panel ar-hero">
        <div><span class="eyebrow">Growth AutoResearch</span><h2>Continuous acquisition experiments</h2><p>Partizan proposes one bounded challenger at a time, waits for downstream evidence, learns, then decides what to test next.</p></div>
        <div class="ar-actions"><span id="autoresearch-status" class="status-pill">Loading</span><button id="autoresearch-control" class="button button-secondary hidden" type="button">Pause AutoResearch</button></div>
      </section>
      <div id="autoresearch-loading" class="panel ar-loading"><span class="spinner"></span><span>Loading AutoResearch state…</span></div>
      <div id="autoresearch-error" class="panel ar-error hidden"></div>
      <div id="autoresearch-content" class="hidden">
        <section class="ar-grid">
          <article class="panel ar-card"><span class="eyebrow">Current winner</span><h2>Strategy to beat</h2><div id="autoresearch-winner-variant" class="ar-variant"></div><p id="autoresearch-winner-metrics" class="ar-metrics"></p></article>
          <article class="panel ar-card"><span class="eyebrow">Testing now</span><h2 id="autoresearch-testing-title">—</h2><p id="autoresearch-testing-meta" class="ar-metrics"></p><div id="autoresearch-testing-variant" class="ar-variant"></div><p id="autoresearch-testing-state" class="note"></p></article>
        </section>
        <section class="panel ar-card"><div class="section-head"><div><span class="eyebrow">Research provenance</span><h2>Why this test is worth running</h2></div></div><div id="autoresearch-provenance" class="ar-sources"></div><p id="autoresearch-boundary" class="note ar-boundary"></p></section>
        <section class="panel ar-card"><div class="section-head"><div><span class="eyebrow">Recent tests</span><h2>What Partizan learned</h2></div></div><div id="autoresearch-history" class="ar-history"></div></section>
      </div>`;
    const settingsPanel = workspaceNode.querySelector('[data-tab-panel="settings"]');
    workspaceNode.insertBefore(panel, settingsPanel || null);

    window.addEventListener('partizan:workspace-ready', (event) => {
      const nextProjectId = event.detail && event.detail.projectId;
      if (!nextProjectId) return;
      syncProjectId(nextProjectId);
      load().catch(() => {});
    });

    if (syncProjectId()) load().catch(() => {});

    $('autoresearch-control').addEventListener('click', async () => {
      const control = $('autoresearch-control');
      if (!overview || !control.dataset.nextStatus) return;
      control.disabled = true;
      try {
        const data = await api(
          `/customer/workspace/${encodeURIComponent(projectId)}/autoresearch/status`,
          {
            method: 'POST',
            body: JSON.stringify({ status: control.dataset.nextStatus }),
          },
        );
        render(data);
      } catch (error) {
        $('autoresearch-error').textContent = error.message;
        $('autoresearch-error').classList.remove('hidden');
      } finally {
        control.disabled = false;
      }
    });
  };

  install();
})();
