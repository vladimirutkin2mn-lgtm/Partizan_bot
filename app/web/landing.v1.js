(() => {
  const defaultBudget = 1000;
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

  document.querySelectorAll('a[href="/start"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      window.location.assign(`/start?budget=${encodeURIComponent(defaultBudget)}`);
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


  const demo = document.getElementById('product-demo');
  const demoSteps = [...document.querySelectorAll('[data-demo-step]')];
  const demoProgress = document.getElementById('demo-progress');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let demoTimer = null;
  let demoStarted = false;

  const showDemoStep = (index) => {
    demoSteps.forEach((step, stepIndex) => {
      step.classList.toggle('is-active', stepIndex === index);
      step.classList.toggle('is-complete', stepIndex < index);
    });
    if (demoProgress) demoProgress.textContent = `${index + 1} / ${demoSteps.length}`;
  };

  const runDemo = () => {
    if (demoStarted || reducedMotion || demoSteps.length === 0) return;
    demoStarted = true;
    let index = 0;
    showDemoStep(index);
    demoTimer = window.setInterval(() => {
      index += 1;
      if (index >= demoSteps.length) {
        window.clearInterval(demoTimer);
        demoTimer = null;
        return;
      }
      showDemoStep(index);
    }, 1450);
  };

  if (demo && !reducedMotion && 'IntersectionObserver' in window) {
    const demoObserver = new IntersectionObserver((entries, instance) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      runDemo();
      instance.disconnect();
    }, { threshold: 0.35 });
    demoObserver.observe(demo);
  } else if (demo && !reducedMotion) {
    runDemo();
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