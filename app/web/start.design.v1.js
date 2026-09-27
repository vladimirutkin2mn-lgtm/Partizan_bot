/* Presentation adapter for the approved onboarding. Requests stay in start.v2.js. */
(() => {
  const $ = (id) => document.getElementById(id);
  const linkTab = $('product-link-tab');
  const briefTab = $('product-brief-tab');
  if (!linkTab || !briefTab) return;
  let mode = 'link';
  let savedLink = $('product-link').value;
  let savedBrief = $('brief').value;
  const chooseMode = (next) => {
    if (mode !== next) {
      if (mode === 'link') savedLink = $('product-link').value;
      else savedBrief = $('brief').value;
      $('product-link').value = next === 'link' ? savedLink : '';
      $('brief').value = next === 'describe' ? savedBrief : '';
    }
    mode = next;
    const describe = next === 'describe';
    $('product-link-panel').hidden = describe;
    $('brief-fallback').classList.toggle('hidden', !describe);
    [linkTab, briefTab].forEach((tab, index) => {
      const selected = describe === (index === 1);
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
  };
  linkTab.addEventListener('click', () => chooseMode('link'));
  briefTab.addEventListener('click', () => chooseMode('describe'));
  [linkTab, briefTab].forEach((tab) => tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const describe = event.key === 'End' || (!['Home'].includes(event.key) && mode === 'link');
    chooseMode(describe ? 'describe' : 'link');
    (describe ? briefTab : linkTab).focus();
  }));
  if (new URLSearchParams(window.location.search).get('mode') === 'describe') chooseMode('describe');
  // The native empty-input validation also reveals this field.
  new MutationObserver(() => {
    if (mode === 'link' && !$('brief-fallback').classList.contains('hidden')) chooseMode('describe');
  }).observe($('brief-fallback'), { attributes: true, attributeFilter: ['class'] });

  const select = $('goal');
  const choices = document.createElement('div');
  choices.className = 'design-goal-choices';
  choices.setAttribute('role', 'group');
  choices.setAttribute('aria-label', 'Your goal');
  const explanations = [
    'Help the first people try your product.', 'Find people ready to pay.',
    'Start conversations with potential customers.', 'Turn interest into account registrations.',
    'Learn which acquisition efforts create revenue.',
  ];
  Array.from(select.options).forEach((option, index) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'rd-choice'; button.dataset.goal = option.value;
    button.append(document.createTextNode(option.value));
    const small = document.createElement('small'); small.textContent = explanations[index] || '';
    button.append(small);
    button.addEventListener('click', () => {
      select.value = option.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    choices.append(button);
  });
  const goalRoot = select.closest('.goal-select') || select;
  goalRoot.hidden = true;
  goalRoot.before(choices);
  const syncGoal = () => choices.querySelectorAll('button').forEach((button) => {
    const active = button.dataset.goal === select.value;
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
  });
  select.addEventListener('change', syncGoal); syncGoal();

  const visible = (id) => !$(id).classList.contains('hidden');
  const syncProgress = () => {
    let step = 0;
    if (visible('stage-unlocked')) step = 5;
    else if (visible('stage-preview')) step = visible('account-gate') ? 5 : 4;
    else if (visible('intake-budget-step')) step = 3;
    else if (visible('intake-goal-step')) step = 2;
    else if (visible('intake-understanding-step') || visible('intake-clarification-step')) step = 1;
    document.querySelectorAll('[data-design-step]').forEach((node, index) => {
      node.classList.toggle('active', index === step);
      node.classList.toggle('done', index < step);
      if (index === step) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
      node.querySelector('span').textContent = index < step ? '✓' : String(index + 1);
    });
  };
  const observer = new MutationObserver(syncProgress);
  document.querySelectorAll('.stage, .intake-step, #account-gate').forEach((node) => {
    observer.observe(node, { attributes: true, attributeFilter: ['class'] });
  });
  syncProgress();
})();
