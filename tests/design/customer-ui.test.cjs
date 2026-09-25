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
    return { ok: !denied, status: denied ? 401 : 200, json: async () => denied ? { detail: 'Sign in required' } : structuredClone(payload) };
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
  assert.equal(d.getElementById('design-review').textContent, 'Review exact action →');
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
