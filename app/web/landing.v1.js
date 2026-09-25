(() => {
  const defaultBudget = 10;
  const accountLink = document.getElementById('nav-account-link');

  const updateAccountEntry = async () => {
    if (!accountLink) return;
    try {
      const response = await fetch('/customer/account/me', {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;
      accountLink.textContent = 'Open workspace';
      accountLink.dataset.authenticated = 'true';
      accountLink.setAttribute('aria-label', 'Open your Partizan workspace');
    } catch (_error) {
      // The static Sign in link remains a valid fail-open navigation path.
    }
  };

  updateAccountEntry();

  const heroScanForm = document.getElementById('hero-scan-form');
  const heroProductLink = document.getElementById('hero-product-link');
  const versionedStartLink = document.querySelector('a[href^="/start"]');
  const startRelease = versionedStartLink
    ? new URL(versionedStartLink.href, window.location.origin).searchParams.get('release')
    : null;

  const startDestination = (query) => {
    if (startRelease) query.set('release', startRelease);
    return `/start?${query.toString()}`;
  };
  heroScanForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = new URLSearchParams();
    query.set('budget', String(defaultBudget));
    const productLink = heroProductLink?.value.trim();
    if (productLink) query.set('product', productLink);
    window.location.assign(startDestination(query));
  });

  document.querySelectorAll('a[href^="/start"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      const query = new URL(link.href, window.location.origin).searchParams;
      query.set('budget', String(defaultBudget));
      window.location.assign(startDestination(query));
    });
  });

  // Presentation-only controls from the approved landing. No campaign APIs.
  const menu = document.querySelector('.menu-button');
  const navigation = document.querySelector('.main-nav');
  menu?.addEventListener('click', () => {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    menu.setAttribute('aria-expanded', String(open));
    menu.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    navigation?.classList.toggle('open', open);
  });
  navigation?.addEventListener('click', () => {
    navigation.classList.remove('open');
    menu?.setAttribute('aria-expanded', 'false');
  });

  const bindTabs = (container, onChange) => {
    if (!container) return;
    const tabs = [...container.querySelectorAll('[role="tab"]')];
    const select = tab => {
      tabs.forEach(item => {
        const active = item === tab;
        item.setAttribute('aria-selected', String(active));
        item.dataset.state = active ? 'active' : 'inactive';
        item.tabIndex = active ? 0 : -1;
        const panel = document.getElementById(item.getAttribute('aria-controls'));
        if (panel) {
          panel.dataset.state = item.dataset.state;
          panel.hidden = !active;
        }
      });
      onChange?.(tabs.indexOf(tab));
    };
    tabs.forEach((tab,index) => {
      tab.addEventListener('click', () => select(tab));
      tab.addEventListener('keydown', event => {
        const next = event.key === 'ArrowRight' ? (index+1)%tabs.length
          : event.key === 'ArrowLeft' ? (index+tabs.length-1)%tabs.length
          : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : -1;
        if (next < 0) return;
        event.preventDefault();
        select(tabs[next]);
        tabs[next].focus();
      });
    });
    select(tabs.find(tab=>tab.getAttribute('aria-selected')==='true') || tabs[0]);
  };
  const priorities = [['Telegram','Meta'], ['Google Ads','Reddit'], ['Meta','TikTok']];
  let exampleIndex = 0;
  const updateChannelExample = () => {
    const enabled = [...document.querySelectorAll('.channel-switch[aria-checked="true"]')]
      .map(control => control.id.replace('channels-','').replaceAll('-',' '));
    const next = priorities[exampleIndex].find(channel=>enabled.includes(channel)) || enabled[0];
    const title = document.querySelector('.channel-next strong');
    const copy = document.querySelector('.channel-next p');
    if (title) title.textContent = next || 'All channels are off';
    if (copy) copy.textContent = next
      ? `Prepare a ${['telegram bot','saas product','online store'][exampleIndex]} audience test on ${next}.`
      : 'Choose a channel to continue planning.';
  };
  bindTabs(document.querySelector('.kind-tabs'), index => {
    const template = document.getElementById(`example-${['bot','saas','store'][index]}`);
    const current = document.querySelector('.showcase-tabs .workspace-demo');
    if (template && current) current.replaceWith(template.content.cloneNode(true));
    const panel = document.querySelector('.showcase-tabs .workspace-demo');
    if (panel) {
      panel.id = 'product-example-panel';
      panel.setAttribute('role', 'tabpanel');
      const tabs = [...document.querySelectorAll('.kind-tabs [role="tab"]')];
      tabs.forEach(tab => tab.setAttribute('aria-controls', panel.id));
      panel.setAttribute('aria-labelledby', tabs[index].id);
    }
    exampleIndex = index;
    updateChannelExample();
  });
  bindTabs(document.querySelector('.learning-tablist'));
  document.querySelector('.showcase-section')?.addEventListener('click', event => {
    if (!event.target.closest('.next-move-panel button')) return;
    window.location.assign(startDestination(new URLSearchParams({budget:String(defaultBudget)})));
  });
  document.querySelectorAll('.channel-switch').forEach(control => {
    control.addEventListener('click', () => {
      const checked = control.getAttribute('aria-checked') !== 'true';
      control.setAttribute('aria-checked', String(checked));
      control.dataset.state = checked ? 'checked' : 'unchecked';
      const thumb = control.querySelector('[data-slot="switch-thumb"]');
      if (thumb) thumb.dataset.state = control.dataset.state;
      const label = control.parentElement.querySelector('.allowed, .off');
      if (label) { label.textContent = checked ? 'On' : 'Off'; label.className = checked ? 'allowed' : 'off'; }
      updateChannelExample();
      const feedback = document.querySelector('.switch-feedback');
      if (feedback) feedback.textContent = 'Illustrative example updated. Your project settings have not changed.';
    });
  });
  const budgetNumber = document.getElementById('budget-number');
  const budgetSlider = document.getElementById('budget-slider');
  const formatMoney = amount => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(amount);
  const updateFeeExample = (value, normalize = false) => {
    const amount = Math.max(10,Math.min(1000,Number(value)||10));
    if (normalize) budgetNumber.value = String(amount);
    if (budgetSlider) budgetSlider.value = String(amount);
    const lines = document.querySelectorAll('.price-calculator .price-line strong');
    if (lines[0]) lines[0].textContent = formatMoney(amount);
    if (lines[1]) lines[1].textContent = formatMoney(amount*.1);
    const total = document.querySelector('.price-total strong');
    if (total) total.textContent = formatMoney(amount*1.1);
  };
  budgetNumber?.addEventListener('input',()=>updateFeeExample(budgetNumber.value));
  budgetNumber?.addEventListener('blur',()=>updateFeeExample(budgetNumber.value,true));
  budgetSlider?.addEventListener('input',()=>updateFeeExample(budgetSlider.value,true));
  document.querySelectorAll('[data-slot="accordion-trigger"]').forEach((trigger,index) => {
    const item = trigger.closest('[data-slot="accordion-item"]');
    const content = item?.querySelector('[data-slot="accordion-content"]');
    if (!content) return;
    content.id = `faq-answer-${index}`;
    content.hidden = trigger.getAttribute('aria-expanded') !== 'true';
    trigger.setAttribute('aria-controls',content.id);
    trigger.addEventListener('click',()=> {
      const open = trigger.getAttribute('aria-expanded') !== 'true';
      document.querySelectorAll('[data-slot="accordion-item"]').forEach(other=> {
        const active = other===item && open;
        other.dataset.state = active ? 'open' : 'closed';
        const button = other.querySelector('[data-slot="accordion-trigger"]');
        if (button) {
          button.setAttribute('aria-expanded',String(active));
          button.dataset.state = other.dataset.state;
        }
        const answer = other.querySelector('[data-slot="accordion-content"]');
        if(answer) { answer.dataset.state = other.dataset.state; answer.hidden = !active; }
      });
    });
  });

  const revealNodes = [...document.querySelectorAll('.reveal')];
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries, instance) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('visible');
        instance.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -35px 0px' });
    revealNodes.forEach((node) => observer.observe(node));
  } else {
    revealNodes.forEach((node) => node.classList.add('visible'));
  }


  document.querySelectorAll('.faq-item').forEach((item) => {
    item.addEventListener('toggle', () => {
      if (!item.open) return;
      document.querySelectorAll('.faq-item[open]').forEach((other) => {
        if (other !== item) other.removeAttribute('open');
      });
    });
  });
})();
