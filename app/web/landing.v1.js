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
      const query = new URLSearchParams();
      query.set('budget', String(defaultBudget));
      window.location.assign(startDestination(query));
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