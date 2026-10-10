// Render the actual workspace DOM with synthetic GET responses, then export an offline visual review.
// Product scripts are removed from the export. Its controls navigate snapshots; they cannot call APIs.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = execFileSync(process.env.PYTHON || 'python', ['-c', `
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    response = client.get('/workspace')
    assert response.status_code == 200
    print(response.text)
`], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });

const states = [
  ['channel', 'Выбор канала'], ['draft', 'Проверка черновика'], ['waiting', 'Подготовка Partizan'],
  ['confirm', 'Финальная проверка'], ['pending', 'Ожидающие действия'], ['done', 'Выполненное действие'],
];
const project = { project_id: 'demo', name: 'Daily English', market: 'United States', goal: 'Get first users', budget_usd: 10, research_state: 'NOT_STARTED', launch_unlocked: false, brief: 'Short daily English practice.', project_type: 'WEBSITE_PRODUCT', status: 'ACTIVE' };
const account = { email: 'demo@example.test', projects: [project] };
const opportunity = { platform: 'TELEGRAM', surface: 'COMMUNITY', state: 'READY', title: 'A conversation about daily English practice', rationale: 'People in this conversation are looking for short ways to practise.', url: 'https://example.test/conversation', recommended_action: 'Offer a useful five-minute exercise.', signal_to_watch: 'Replies and new customers', provenance: [] };
const draft = { platform: 'TELEGRAM', review_status: 'DRAFT', title: 'Make five minutes a daily habit.', content_text: 'Try a small daily habit: choose one everyday question and answer it out loud for five minutes. Daily English gives you a fresh prompt and a short practice session each day.', source_url: opportunity.url, rationale: opportunity.rationale, signal_to_watch: 'Replies and new customers', execution_requirement: 'Review the final text and destination before separately confirming publication.' };
const setup = { platform: 'TELEGRAM', channel_label: 'Telegram', state: 'READY_FOR_HANDOFF', steps: [] };
const prepared = { action_id: 'demo-prepared', action_status: 'PREPARED', customer_publish_confirmed: false, operator_approval_required: true, draft_title: draft.title, content_text: draft.content_text, context_text: 'A relevant conversation about building a daily speaking habit.', target_url: opportunity.url, source_url: opportunity.url, source_title: opportunity.title };

const controller = `
const activeCard = document.querySelector('#design-next-content > :not([data-review-hidden]):not(.hidden)');
const notice = text => { const n = document.getElementById('notice'); n.textContent = text; n.classList.remove('hidden'); };
function route(name, section = 'progress') {
  document.querySelectorAll('[data-tab-panel]').forEach(n => n.classList.toggle('hidden', n.dataset.tabPanel !== name));
  document.querySelectorAll('.tab-button').forEach(n => n.setAttribute('aria-current', n.dataset.tab === name ? 'page' : 'false'));
  document.getElementById('design-page-title').textContent = ({overview:'Your growth',activity:'Results',experiments:'Results',channels:'Channels',settings:'Settings'})[name];
  document.getElementById('workspace-status').hidden = name !== 'overview';
  document.querySelector('.hero-actions').hidden = name !== 'overview';
  document.getElementById('design-results-nav').classList.toggle('hidden', !['activity','experiments'].includes(name));
  document.querySelectorAll('[data-design-group]').forEach(n => n.toggleAttribute('data-design-hidden', n.dataset.designGroup !== section));
}
document.addEventListener('submit', e => { e.preventDefault(); notice('Это визуальный пример. Действие не отправлено.'); });
document.addEventListener('change', e => {
  if (e.target.id === 'community-action-confirm') document.getElementById('community-action-publish').disabled = !e.target.checked;
});
document.addEventListener('click', e => {
  const a = e.target.closest('a'); if (a) { e.preventDefault(); notice('Ссылки в примере используют демонстрационные адреса.'); return; }
  const b = e.target.closest('button'); if (!b) return;
  e.preventDefault();
  if (b.dataset.previewAction) {
    document.getElementById('community-action-modal')?.remove();
    document.body.insertAdjacentHTML('beforeend', previewModals[b.dataset.previewAction]);
    document.getElementById('community-action-modal').classList.remove('hidden'); return;
  }
  if (b.id === 'design-review') {
    const d = document.getElementById('design-next-dialog'); if (activeCard) d.showModal(); else route('activity'); return;
  }
  if (b.id === 'design-close-review') { document.getElementById('design-next-dialog').close(); return; }
  if (b.hasAttribute('data-close-community-action')) { document.getElementById('community-action-modal').classList.add('hidden'); return; }
  if (b.id === 'design-earlier-steps') {
    const expanded = b.getAttribute('aria-expanded') !== 'true'; b.setAttribute('aria-expanded', String(expanded));
    b.textContent = expanded ? 'Hide earlier steps' : 'Review earlier steps';
    document.querySelectorAll('#design-next-content > *').forEach(n => n.toggleAttribute('data-review-hidden', !expanded && n !== activeCard)); return;
  }
  if (b.dataset.designResult) { route(b.dataset.designResult === 'tests' ? 'experiments' : 'activity', b.dataset.designResult); return; }
  if (b.dataset.tab || b.dataset.openTab) { route(b.dataset.tab || b.dataset.openTab); return; }
  if (b.dataset.designManage) { document.getElementById('design-channel-details').open = true; return; }
  if (b.id === 'design-add-funds') { route('settings'); document.getElementById('design-budget').open = true; return; }
  notice('Это визуальный пример. Публикации, оплаты и настройки здесь не выполняются.');
});`;

async function render(state) {
  const errors = [];
  const console = new VirtualConsole(); console.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, { url: 'https://preview.example.test/workspace?project=demo', runScripts: 'outside-only', virtualConsole: console });
  const w = dom.window, d = w.document;
  Object.assign(w, { Headers, Request, Response, CSS: { escape: value => String(value) } });
  w.scrollTo = () => {}; w.HTMLElement.prototype.scrollIntoView = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  const accepted = ['waiting', 'confirm', 'pending', 'done'].includes(state);
  const channels = ['TELEGRAM', 'REDDIT'].map((platform, i) => ({ platform, label: i ? 'Reddit' : 'Telegram', selected: state !== 'channel' && !i, mode: 'RESEARCH_ONLY', publisher_mode: 'CLIENT_OWNED', connected: true, publisher_modes: [{ mode: 'CLIENT_OWNED', available: true }], capabilities: [{ capability: 'PUBLISH', ready: true }], spend_usd: 0, paid_customers: 0, revenue_usd: 0, cac_usd: null, roas: null, autonomous_execution_available: false }));
  const actions = state === 'pending' ? channels.map((channel, i) => ({ action_id: `demo-${i}`, platform: channel.platform, action_status: 'APPROVED', experiment_status: 'RUNNING', publisher_mode: 'CLIENT_OWNED', opportunity_title: i ? 'Help someone start a daily speaking habit' : 'Share a five-minute practice exercise', target_url: opportunity.url, content_text: draft.content_text, replies: 0, removals: 0 })) : [];
  const balance = { available_usd: 10, acquisition_spend_usd: 0, funded_usd: 10, used_usd: 0, remaining_acquisition_capacity_usd: 10, management_fee_usd: 0, execution_fee_usd: 0, management_fee_pct: 10, settlement_ready: false };
  const fixture = { account, project, preview_opportunity: opportunity, preview_directions: [], autopilot: { growth_balance: balance, paid_customers: 0, cac_usd: null, revenue_usd: 0, autopilot_status: 'ACTIVE', product_id: null, meta: { connected: false }, running_experiments: [], waiting_experiments: [], recent_decisions: [], blockers: [] } };
  const requests = [];
  w.fetch = async (input, init = {}) => {
    const url = String(input), method = init.method || 'GET'; requests.push({ url, method });
    if (method !== 'GET') throw new Error('Preview cannot mutate product state');
    let payload = {};
    if (url === '/customer/account/me') payload = account;
    else if (url === '/customer/account/projects') payload = [project];
    else if (url === '/customer/workspace/demo') payload = fixture;
    else if (url.endsWith('/channels')) payload = channels;
    else if (url.endsWith('/starting-move')) payload = opportunity;
    else if (url.endsWith('/starting-move/draft')) payload = state === 'channel' ? null : { ...draft, review_status: accepted ? 'ACCEPTED' : 'DRAFT' };
    else if (url.endsWith('/starting-move/setup')) payload = accepted ? setup : null;
    else if (url.endsWith('/starting-move/execution-request')) payload = accepted ? { status: state === 'waiting' ? 'REQUESTED' : state === 'done' ? 'OPERATOR_APPROVED' : 'ACTION_PREPARED' } : null;
    else if (url.endsWith('/starting-move/execution-request/prepared-action')) payload = { ...prepared, action_status: state === 'done' ? 'EXECUTED' : 'PREPARED', customer_publish_confirmed: state === 'done' };
    else if (url.endsWith('/community-actions')) payload = actions;
    else if (url.endsWith('/connection')) payload = { status: 'ACTIVE' };
    else if (url.endsWith('/managed-delivery') || url.endsWith('/managed-distribution/assignments')) payload = [];
    else if (url.endsWith('/distribution-learning')) payload = { entries: [] };
    return { ok: true, status: 200, json: async () => structuredClone(payload) };
  };
  const scripts = [...d.querySelectorAll('script')];
  scripts.filter(s => !s.src).forEach(s => w.eval(s.textContent));
  scripts.filter(s => s.src).forEach(s => w.eval(fs.readFileSync(path.join(root, 'app/web', path.basename(new URL(s.src).pathname)), 'utf8')));
  await new Promise(resolve => setTimeout(resolve, 150));
  if (errors.length || d.getElementById('workspace').classList.contains('hidden')) throw new Error(`Preview failed: ${errors.join(', ')}`);
  d.getElementById('design-review').click();
  d.getElementById('design-close-review').click();
  const modals = {};
  for (const action of actions) {
    d.querySelector(`[data-review-community-action="${action.action_id}"]`).click();
    modals[action.action_id] = d.getElementById('community-action-modal').outerHTML;
    d.querySelector('[data-close-community-action]').click();
  }
  if (actions.length) {
    d.getElementById('design-review').dataset.previewAction = actions[0].action_id;
    d.querySelectorAll('#design-pending-list button').forEach((button, i) => { button.dataset.previewAction = actions[i + 1].action_id; });
  }
  d.querySelectorAll('script').forEach(s => s.remove());
  d.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
    const style = d.createElement('style'); style.textContent = fs.readFileSync(path.join(root, 'app/web', path.basename(new URL(link.href).pathname)), 'utf8'); link.replaceWith(style);
  });
  const script = d.createElement('script');
  script.textContent = `const previewModals = ${JSON.stringify(modals).replace(/</g, '\\u003c')};\n${controller}`;
  d.body.append(script);
  const result = '<!DOCTYPE html>' + d.documentElement.outerHTML;
  dom.window.close();
  if (requests.some(request => request.method !== 'GET')) throw new Error('Unexpected preview write');
  return result;
}

(async () => {
  const snapshots = [];
  for (const [state] of states) snapshots.push(await render(state));
  const output = path.resolve(process.argv[2] || 'Partizan_workspace_UX_preview.html');
  const options = states.map(([state, label], i) => `<option value="${i}">${label}</option>`).join('');
  fs.writeFileSync(output, `<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Partizan — просмотр UX-правок</title><style>body{margin:0;font:14px Arial;background:#f7faf8;color:#173b2a}header{padding:16px 20px;border-bottom:1px solid #dfe8e2;display:flex;align-items:center;gap:14px;flex-wrap:wrap}p{margin:0;color:#667b70;font-size:13px}select{padding:9px;border:1px solid #ccdcd1;border-radius:6px;background:white;color:#173b2a}iframe{display:block;width:100%;height:calc(100vh - 100px);border:0}</style></head><body><header><strong>Partizan: новая главная</strong><select id="state" aria-label="Состояние проекта">${options}</select><p>Демонстрационные данные. Можно открыть проверку и разделы. Публикации и оплаты отключены.</p></header><iframe id="preview" title="Кабинет Partizan" sandbox="allow-scripts"></iframe><script>const snapshots=${JSON.stringify(snapshots).replace(/</g, '\\u003c')};const select=document.getElementById('state');const frame=document.getElementById('preview');const show=()=>frame.srcdoc=snapshots[Number(select.value)];select.addEventListener('change',show);show();</script></body></html>`);
  process.stdout.write(JSON.stringify({ output, states: states.length, bytes: fs.statSync(output).size }) + '\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
