const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

// These are DOM interaction checks with an entirely mocked transport, not visual/browser QA.
const root = path.resolve(__dirname, '../..');
const pages = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import json
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    pages = {path: client.get(path) for path in ('/', '/start', '/workspace')}
    assert all(page.status_code == 200 for page in pages.values())
    print(json.dumps({path: page.text for path, page in pages.items()}))
`], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
const flush = () => new Promise(resolve => setTimeout(resolve, 35));
const waitFor = async (condition) => {
  const deadline = Date.now() + 2000;
  while (!condition() && Date.now() < deadline) await flush();
  assert.ok(condition(), 'workspace state did not settle');
};
const project = { project_id: 'project-1', name: 'A real test project', market: 'United States', goal: 'Get first users', budget_usd: 50, research_state: 'NOT_STARTED', launch_unlocked: false, brief: 'Test product', project_type: 'WEBSITE_PRODUCT', status: 'ACTIVE' };
const account = { email: 'owner@example.test', projects: [project] };
const balance = { available_usd: 53.8, acquisition_spend_usd: 42, funded_usd: 100, used_usd: 46.2, remaining_acquisition_capacity_usd: 48.9, management_fee_usd: 4.2, execution_fee_usd: 0, management_fee_pct: 10, settlement_ready: false };
const fixture = {
  account, project, preview_directions: [],
  autopilot: { growth_balance: balance, paid_customers: 8, cac_usd: 5.25, revenue_usd: 80, autopilot_status: 'PAUSED', product_id: null, meta: { connected: false }, running_experiments: [], waiting_experiments: [], recent_decisions: [], blockers: [] },
};
const channels = ['TELEGRAM', 'REDDIT', 'ANOTHER_SURFACE'].map(platform => ({ platform, label: platform === 'ANOTHER_SURFACE' ? 'Another live channel' : platform, mode: 'RESEARCH_ONLY', publisher_mode: 'MANUAL', publisher_modes: [{ mode: 'MANUAL', available: true }], spend_usd: 0, paid_customers: 0, cac_usd: null, revenue_usd: 0, roas: null, autonomous_execution_available: false, capabilities: [] }));

async function open(route, options = {}) {
  const calls = [], errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(pages[route], { url: `https://partizan.example${route}${options.query || ''}`, runScripts: 'outside-only', virtualConsole });
  const w = dom.window, d = w.document;
  w.Headers = Headers;
  w.Request = Request;
  w.Response = Response;
  w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
  w.scrollTo = () => {};
  w.HTMLElement.prototype.scrollIntoView = function () { this.dataset.testScrolled = 'true'; };
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.fetch = async (url, init = {}) => {
    const call = { url: String(url), method: init.method || 'GET', body: init.body && JSON.parse(init.body), credentials: init.credentials };
    calls.push(call);
    if (options.hang?.includes(call.url)) {
      return new Promise((_, reject) => {
        init.signal?.addEventListener('abort', () => {
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        }, { once: true });
      });
    }
    const override = options.respond?.(call);
    let payload = override;
    if (payload === undefined) {
      if (call.url === '/customer/account/me' || call.url === '/customer/account/login') payload = account;
      else if (call.url === '/customer/account/projects') payload = [project];
      else if (call.url === '/customer/workspace/project-1') payload = options.fixture || fixture;
      else if (call.url.endsWith('/channels')) payload = channels;
      else if (call.url.endsWith('/starting-move/draft') || call.url.endsWith('/starting-move/setup') || call.url.endsWith('/starting-move/execution-request')) payload = null;
      else if (call.url.endsWith('/community-actions') || call.url.endsWith('/managed-delivery')) payload = [];
      else if (call.url.endsWith('/distribution-learning')) payload = { entries: [] };
      else payload = {};
    }
    const denied = options.signedOut && call.url === '/customer/account/me';
    const failedStatus = options.fail?.[call.url];
    const failed = Number.isInteger(failedStatus);
    const status = denied ? 401 : (failed ? failedStatus : 200);
    const detail = denied ? 'Sign in required' : `Test failure (${status})`;
    return {
      ok: !denied && !failed,
      status,
      json: async () => denied || failed ? { detail } : structuredClone(payload),
    };
  };
  const scripts = [...d.querySelectorAll('script')];
  // Inline scripts execute during parsing, deferred assets execute in document order.
  scripts.filter(s => !s.src).forEach(s => w.eval(s.textContent));
  scripts.filter(s => s.src).forEach(s => {
    const filename = path.basename(new URL(s.src).pathname);
    w.eval(fs.readFileSync(path.join(root, 'app/web', filename), 'utf8'));
  });
  await flush(); await flush();
  return { w, d, calls, errors, close: () => dom.window.close() };
}
const submit = (ui, id) => {
  const form = ui.d.getElementById(id);
  form.dispatchEvent(new ui.w.SubmitEvent('submit', { bubbles: true, cancelable: true, submitter: form.querySelector('[type=submit]') }));
};
const mutations = ui => ui.calls.filter(call => !['GET', 'HEAD'].includes(call.method));

test('landing tabs, FAQ and calculator work without any write request', async t => {
  const ui = await open('/'); t.after(ui.close);
  const { d, w } = ui;
  assert.equal(d.querySelector('#nav-account-link').textContent, 'Open workspace');
  const tabs = [...d.querySelectorAll('.kind-tabs [role=tab]')];
  tabs[1].click();
  assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
  assert.match(d.querySelector('.workspace-demo').textContent, /SaaS/);
  tabs[1].dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(tabs[2].getAttribute('aria-selected'), 'true');
  for (const tab of d.querySelectorAll('[role=tab]')) assert.ok(d.getElementById(tab.getAttribute('aria-controls')));
  const questions = d.querySelectorAll('[data-slot=accordion-trigger]');
  questions[0].click(); assert.equal(questions[0].getAttribute('aria-expanded'), 'true');
  questions[1].click(); assert.equal(questions[0].getAttribute('aria-expanded'), 'false');
  assert.equal(d.getElementById(questions[0].getAttribute('aria-controls')).hidden, true);
  const amount = d.getElementById('budget-number'); amount.value = '50'; amount.dispatchEvent(new w.Event('input'));
  assert.equal(d.querySelector('.price-total strong').textContent, '$55.00');
  const menu = d.querySelector('.menu-button'); menu.click(); assert.equal(menu.getAttribute('aria-expanded'), 'true');
  assert.equal(mutations(ui).length, 0); assert.deepEqual(ui.errors, []);
});

test('onboarding preserves input modes, real preview payload and goal selection', async t => {
  const ui = await open('/start', { query: '?mode=describe' }); t.after(ui.close);
  const { d, w } = ui;
  assert.equal(d.getElementById('product-brief-tab').getAttribute('aria-selected'), 'true');
  const brief = 'A useful product for busy people who practise speaking.';
  d.getElementById('brief').value = brief;
  d.getElementById('product-link-tab').click();
  assert.equal(d.getElementById('brief').value, '');
  d.getElementById('product-link').value = 'https://example.test/product';
  d.getElementById('product-brief-tab').click();
  assert.equal(d.getElementById('brief').value, brief);
  assert.equal(d.getElementById('product-link').value, '');
  submit(ui, 'preview-form'); await flush();
  const preview = ui.calls.find(c => c.url === '/v1/customer-projects/preview');
  assert.deepEqual(preview.body, { brief, product_link: null });
  d.querySelector('[data-goal="Get paying customers"]').click();
  assert.equal(d.getElementById('goal').value, 'Get paying customers');
  d.querySelectorAll('.intake-step').forEach(node => node.classList.add('hidden'));
  d.getElementById('intake-budget-step').classList.remove('hidden'); await flush();
  assert.equal(d.querySelector('[aria-current=step]').dataset.designStep, '3');
  assert.equal(mutations(ui).length, 1); assert.deepEqual(ui.errors, []);
});

test('real sign in remains available and submits to customer authentication', async t => {
  const ui = await open('/workspace', { signedOut: true }); t.after(ui.close);
  const { d } = ui;
  assert.equal(d.getElementById('login-gate').classList.contains('hidden'), false);
  assert.equal(d.querySelector('.ws-nav').classList.contains('hidden'), true);
  d.getElementById('workspace-login-email').value = 'owner@example.test';
  d.getElementById('workspace-login-password').value = 'test-password-only';
  submit(ui, 'workspace-login-form'); await flush(); await flush();
  const request = mutations(ui).find(call => call.url === '/customer/account/login');
  assert.ok(request, `${d.getElementById('notice').textContent} ${ui.errors.map(e => e.stack)} ${JSON.stringify(ui.calls)}`);
  assert.equal(request.credentials, 'same-origin');
  assert.deepEqual(request.body, { email: 'owner@example.test', password: 'test-password-only' });
  assert.equal(d.getElementById('workspace').classList.contains('hidden'), false, d.getElementById('notice').textContent);
  assert.deepEqual(ui.errors, []);
});

test('channel controls failure does not block the authenticated workspace', async t => {
  const ui = await open('/workspace', {
    fail: { '/customer/workspace/project-1/channels': 503 },
  });
  t.after(ui.close);
  const { d } = ui;
  assert.equal(d.getElementById('workspace').classList.contains('hidden'), false);
  assert.equal(d.getElementById('loading').classList.contains('hidden'), true);
  assert.ok(ui.calls.some(call => call.url === '/customer/workspace/project-1/channels'));
  assert.deepEqual(ui.errors, []);
});

test('core workspace failure exits the spinner and offers recovery actions', async t => {
  const ui = await open('/workspace', {
    fail: { '/customer/workspace/project-1': 503 },
  });
  t.after(ui.close);
  const { d } = ui;
  assert.equal(d.getElementById('workspace').classList.contains('hidden'), true);
  assert.equal(d.getElementById('loading').classList.contains('hidden'), false);
  assert.equal(d.getElementById('workspace-loading-spinner').classList.contains('hidden'), true);
  assert.match(d.getElementById('workspace-loading-title').textContent, /could not open/i);
  assert.match(d.getElementById('workspace-loading-copy').textContent, /Test failure \(503\)/);
  assert.equal(d.getElementById('workspace-loading-actions').classList.contains('hidden'), false);
  assert.equal(d.getElementById('workspace-loading-retry').disabled, false);
  assert.deepEqual(ui.errors, []);
});
test('simple workspace keeps live metrics, result sections and all server channels', async t => {
  const ui = await open('/workspace'); t.after(ui.close);
  const { d } = ui;
  assert.equal(d.getElementById('workspace').classList.contains('hidden'), false, `${d.getElementById('notice').textContent} ${ui.errors.map(e => e.stack)}`);
  assert.deepEqual([...d.querySelectorAll('.ws-nav button')].map(n => n.textContent), ['Home', 'Results', 'Channels']);
  assert.deepEqual([...d.querySelectorAll('.ws-summary strong')].map(n => n.textContent), ['8', '$42', '$53.8']);
  assert.equal(d.getElementById('design-project-name').textContent, project.name);
  assert.ok(d.getElementById('new-project-button'));
  d.querySelector('.ws-nav [data-tab=activity]').click(); await flush();
  d.querySelector('#design-results-nav [data-design-result=research]').click(); await flush();
  assert.equal(d.querySelector('.research-card').hasAttribute('data-design-hidden'), false);
  d.getElementById('autoresearch-tab').click(); await flush();
  assert.equal(d.getElementById('autoresearch-panel').classList.contains('hidden'), false);
  assert.equal(d.querySelector('.ws-nav [data-tab=activity]').getAttribute('aria-current'), 'page');
  d.querySelector('#design-results-nav [data-design-result=history]').click(); await flush();
  assert.equal(d.querySelector('.experiments-card').hasAttribute('data-design-hidden'), false);
  assert.equal(d.getElementById('autoresearch-panel').classList.contains('hidden'), true);
  d.querySelector('.ws-nav [data-tab=channels]').click(); await flush();
  assert.ok(d.querySelector('[data-design-manage=ANOTHER_SURFACE]'));
  d.querySelector('[data-design-manage=ANOTHER_SURFACE]').click();
  assert.equal(d.getElementById('design-channel-details').open, true);
  assert.equal(d.activeElement.dataset.platform, 'ANOTHER_SURFACE');
  d.getElementById('design-add-funds').click(); await flush();
  assert.equal(d.querySelector('[data-tab-panel=settings]').classList.contains('hidden'), false);
  assert.equal(d.getElementById('design-budget').open, true);
  assert.equal(mutations(ui).length, 0); assert.deepEqual(ui.errors, []);
  const ids = [...d.querySelectorAll('[id]')].map(n => n.id);
  assert.equal(ids.length, new Set(ids).size, 'rearranging the live UI must not duplicate IDs');
});

test('review opens the exact prepared action without confirming or publishing it', async t => {
  const prepared = { action_id: 'action-1', action_status: 'PREPARED', customer_publish_confirmed: false, operator_approval_required: true, draft_title: 'Useful practice', content_text: 'Exact prepared copy with a real call to action.', context_text: 'Relevant conversation context.', target_url: 'https://t.me/example_test/123', source_url: 'https://example.test/evidence', source_title: 'Source evidence' };
  const ui = await open('/workspace', { respond(call) {
    if (call.url.endsWith('/starting-move/draft')) return { platform: 'TELEGRAM', review_status: 'ACCEPTED' };
    if (call.url.endsWith('/starting-move/setup')) return { platform: 'TELEGRAM', channel_label: 'Telegram', state: 'READY_FOR_HANDOFF' };
    if (call.url.endsWith('/starting-move/execution-request')) return { status: 'ACTION_PREPARED' };
    if (call.url.endsWith('/starting-move/execution-request/prepared-action')) return prepared;
    if (call.url.endsWith('/starting-move/execution-request/confirmation')) return { ...prepared, customer_publish_confirmed: true };
  } }); t.after(ui.close);
  const { d } = ui;
  assert.equal(d.getElementById('design-review').textContent, 'Review final action →');
  d.getElementById('design-review').click();
  assert.equal(d.getElementById('design-next-dialog').open, true);
  assert.match(d.getElementById('design-next-content').textContent, /Exact prepared copy with a real call to action/);
  assert.ok(d.querySelector('#design-next-content a[href="https://t.me/example_test/123"]'));
  assert.equal(mutations(ui).length, 0, 'opening review must be read-only');
  d.getElementById('execution-confirm-submit').click(); await flush();
  assert.equal(mutations(ui).length, 1);
  assert.match(mutations(ui)[0].url, /\/execution-request\/confirmation$/);
  assert.equal(ui.calls.some(call => /\/(publish|execute|approve)$/.test(call.url)), false);
  assert.deepEqual(ui.errors, []);
});

const opportunity = { platform: 'TELEGRAM', surface: 'COMMUNITY', title: 'A relevant conversation', rationale: 'People here need the product.', url: 'https://t.me/example_test/123', recommended_action: 'Offer a useful answer.', signal_to_watch: 'Replies', provenance: [] };
const firstMoveFixture = { ...fixture, preview_opportunity: opportunity, autopilot: { ...fixture.autopilot, paid_customers: 0, growth_balance: { ...balance, acquisition_spend_usd: 0 } } };
const selectedChannels = [{ ...channels[0], label: 'Telegram', selected: true }, channels[1]];
const draft = { platform: 'TELEGRAM', review_status: 'DRAFT', title: 'Useful answer', content_text: 'Helpful text for the selected conversation.', source_url: opportunity.url, rationale: opportunity.rationale, signal_to_watch: 'Replies', execution_requirement: 'Publication requires a separate confirmation.' };
const prepared = { action_id: 'prepared-1', action_status: 'PREPARED', customer_publish_confirmed: false, operator_approval_required: true, draft_title: draft.title, content_text: draft.content_text, context_text: 'Relevant conversation', target_url: opportunity.url, source_url: opportunity.url, source_title: opportunity.title };

function firstMoveOptions(state = {}) {
  return { fixture: firstMoveFixture, respond(call) {
    if (call.url.endsWith('/channels')) return state.channels || selectedChannels;
    if (call.url.endsWith('/starting-move')) return { ...opportunity, state: 'READY' };
    if (call.url.endsWith('/starting-move/draft')) return state.draft === undefined ? draft : state.draft;
    if (call.url.endsWith('/starting-move/setup')) return state.setup || null;
    if (call.url.endsWith('/starting-move/execution-request')) return state.execution || null;
    if (call.url.endsWith('/starting-move/execution-request/prepared-action')) return state.prepared || prepared;
  } };
}

test('Home names draft review and opens only the current step without accepting anything', async t => {
  const ui = await open('/workspace', firstMoveOptions()); t.after(ui.close);
  const { d } = ui;
  assert.equal(d.getElementById('design-next-stage').textContent, 'Draft');
  assert.equal(d.getElementById('design-next-owner').textContent, 'Your turn');
  assert.equal(d.getElementById('design-review').textContent, 'Review draft →');
  d.getElementById('design-review').click();
  assert.equal(d.activeElement.id, 'starting-move-draft-content');
  assert.equal(d.getElementById('activation-card').hasAttribute('data-review-hidden'), true);
  assert.equal(d.getElementById('channel-choice-card').hasAttribute('data-review-hidden'), false);
  assert.equal(mutations(ui).length, 0);
  assert.deepEqual(ui.errors, []);
});

test('Home distinguishes channel choice and account setup from draft review', async t => {
  for (const state of [
    { channels: selectedChannels.map(item => ({ ...item, selected: false })), stage: 'Choose channel', cta: 'Choose channel →' },
    { draft: { ...draft, review_status: 'ACCEPTED' }, setup: { platform: 'TELEGRAM', state: 'NEEDS_SETUP', channel_label: 'Telegram', next_step: 'Connect Telegram to use your account.', steps: [] }, stage: 'Account setup', cta: 'Review setup →' },
  ]) {
    const ui = await open('/workspace', firstMoveOptions(state));
    try {
      assert.equal(ui.d.getElementById('design-next-stage').textContent, state.stage);
      assert.equal(ui.d.getElementById('design-review').textContent, state.cta);
      ui.d.getElementById('design-review').click();
      assert.equal(mutations(ui).length, 0);
      assert.deepEqual(ui.errors, []);
    } finally { ui.close(); }
  }
});

test('preparation and confirmation states explain whose turn it is without claiming publication', async t => {
  const ready = { platform: 'TELEGRAM', channel_label: 'Telegram', state: 'READY_FOR_HANDOFF', steps: [] };
  for (const state of [
    { execution: { status: 'REQUESTED' }, owner: 'Partizan’s turn', stage: 'Preparation' },
    { execution: { status: 'ACTION_PREPARED' }, owner: 'Your turn', stage: 'Final review' },
    { execution: { status: 'PUBLISH_CONFIRMED' }, prepared: { ...prepared, customer_publish_confirmed: true }, owner: 'Partizan’s turn', stage: 'Final review' },
    { execution: { status: 'OPERATOR_APPROVED' }, prepared: { ...prepared, action_status: 'APPROVED', customer_publish_confirmed: true }, owner: 'Current status', stage: 'Final review' },
    { execution: { status: 'OPERATOR_APPROVED' }, prepared: { ...prepared, action_status: 'EXECUTED', customer_publish_confirmed: true }, owner: 'Completed', stage: 'Results' },
  ]) {
    const ui = await open('/workspace', firstMoveOptions({ ...state, draft: { ...draft, review_status: 'ACCEPTED' }, setup: ready }));
    try {
      const { d } = ui;
      assert.equal(d.getElementById('design-next-owner').textContent, state.owner);
      assert.equal(d.getElementById('design-next-stage').textContent, state.stage);
      d.getElementById('design-review').click();
      assert.equal(d.getElementById('execution-request-card').hasAttribute('data-review-hidden'), false);
      assert.equal(d.getElementById('channel-choice-card').hasAttribute('data-review-hidden'), true);
      assert.equal(d.getElementById('design-earlier-steps').hidden, false);
      d.getElementById('design-earlier-steps').click();
      assert.equal(d.getElementById('channel-choice-card').hasAttribute('data-review-hidden'), false);
      if (state.execution.status === 'PUBLISH_CONFIRMED') assert.match(d.getElementById('design-next-copy').textContent, /nothing has been published/);
      if (state.execution.status === 'OPERATOR_APPROVED' && state.stage !== 'Results') assert.match(d.getElementById('design-next-copy').textContent, /has not happened yet/);
      assert.equal(mutations(ui).length, 0);
      assert.deepEqual(ui.errors, []);
    } finally { ui.close(); }
  }
});

const clientChannels = channels.slice(0, 2).map(item => ({ ...item, connected: true, publisher_mode: 'CLIENT_OWNED', publisher_modes: [{ mode: 'CLIENT_OWNED', available: true }], capabilities: [{ capability: 'PUBLISH', ready: true }] }));
const communityActions = clientChannels.map((item, index) => ({ action_id: `community-${index}`, platform: item.platform, action_status: 'APPROVED', experiment_status: 'RUNNING', publisher_mode: 'CLIENT_OWNED', opportunity_title: `${item.platform} opportunity`, target_url: opportunity.url, content_text: 'Exact approved copy.', replies: 0, removals: 0 }));

test('approved community actions are reachable from Home and publish only after native confirmation', async t => {
  let actions = communityActions;
  const ui = await open('/workspace', { respond(call) {
    if (call.url.endsWith('/channels')) return clientChannels;
    if (call.url.endsWith('/community-actions')) return actions;
    if (call.url.endsWith('/connection')) return { status: 'ACTIVE' };
    if (call.url.endsWith('/publish')) { actions = [communityActions[1]]; return { outcome: 'EXECUTED' }; }
  } }); t.after(ui.close);
  const { d, w } = ui;
  assert.equal(d.querySelector('[data-tab-panel=overview]').classList.contains('hidden'), false);
  assert.equal(d.getElementById('design-next-title').textContent, communityActions[0].opportunity_title);
  assert.equal(d.getElementById('design-next-owner').textContent, 'Your turn');
  assert.equal(d.getElementById('design-pending-actions').hidden, false);
  assert.match(d.getElementById('design-pending-list').textContent, /REDDIT opportunity/);
  d.getElementById('design-review').click();
  assert.equal(d.getElementById('community-action-modal').classList.contains('hidden'), false);
  assert.equal(d.activeElement.id, 'community-action-title');
  assert.match(d.getElementById('community-action-review').textContent, /Exact approved copy/);
  assert.equal(d.getElementById('community-action-publish').disabled, true);
  assert.equal(mutations(ui).length, 0, 'Home opens review only');
  const check = d.getElementById('community-action-confirm'); check.checked = true; check.dispatchEvent(new w.Event('change'));
  d.getElementById('community-action-publish').click(); await flush(); await flush();
  const publish = mutations(ui).filter(call => call.url.endsWith('/publish'));
  assert.equal(publish.length, 1);
  assert.equal(publish[0].body.confirm_publish, true);
  assert.equal(publish[0].body.expected_content_text, 'Exact approved copy.');
  assert.equal(d.getElementById('design-next-title').textContent, communityActions[1].opportunity_title);
  assert.equal(d.getElementById('design-pending-actions').hidden, true);
  assert.deepEqual(ui.errors, []);
});

test('pending actions with a manual mode open setup without granting permissions', async t => {
  const ui = await open('/workspace', { respond(call) {
    if (call.url.endsWith('/community-actions')) return [communityActions[0]];
  } }); t.after(ui.close);
  const { d } = ui;
  assert.equal(d.getElementById('design-next-stage').textContent, 'Account setup');
  assert.equal(d.getElementById('design-review').textContent, 'Review channel setup →');
  d.getElementById('design-review').click(); await flush();
  assert.equal(d.querySelector('[data-tab-panel=channels]').classList.contains('hidden'), false);
  assert.equal(d.getElementById('design-channel-details').open, true);
  assert.equal(mutations(ui).length, 0);
  assert.deepEqual(ui.errors, []);
});

test('refresh removes stale pending controls and another project cannot reuse them', async t => {
  let actions = communityActions;
  const ui = await open('/workspace', { respond(call) {
    if (call.url.endsWith('/channels')) return clientChannels;
    if (call.url.endsWith('/community-actions')) return actions;
  } }); t.after(ui.close);
  const { d, w } = ui;
  const staleButton = d.querySelector('#design-pending-list button');
  actions = [];
  w.dispatchEvent(new w.CustomEvent('partizan:community-action-updated', { detail: { projectId: 'project-1' } }));
  await waitFor(() => d.getElementById('design-pending-actions').hidden);
  assert.equal(d.getElementById('design-pending-actions').hidden, true);
  staleButton.click();
  assert.equal(d.getElementById('community-action-modal'), null);
  actions = communityActions;
  w.dispatchEvent(new w.CustomEvent('partizan:community-action-updated', { detail: { projectId: 'project-1' } }));
  await waitFor(() => !d.getElementById('design-pending-actions').hidden);
  w.history.replaceState({}, '', '/workspace?project=project-2');
  w.dispatchEvent(new w.CustomEvent('partizan:community-actions-rendered'));
  d.getElementById('design-review').click();
  assert.equal(d.getElementById('community-action-modal'), null);
  assert.equal(mutations(ui).length, 0);
  assert.deepEqual(ui.errors, []);
});
