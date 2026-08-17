(() => {
  const budget = document.getElementById('budget-range');
  const budgetValue = document.getElementById('budget-value');
  const channelCount = document.getElementById('channel-count');
  const experimentCount = document.getElementById('experiment-count');
  const customerCount = document.getElementById('customer-count');

  const formatMoney = (value) => new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);

  const updatePlanner = () => {
    if (!budget) return;
    const value = Number(budget.value);
    const min = Number(budget.min);
    const max = Number(budget.max);
    const pct = ((value - min) / (max - min)) * 100;
    budget.style.setProperty('--range-pct', `${pct}%`);

    const channels = Math.max(2, Math.min(7, Math.round(2 + value / 900)));
    const experiments = Math.max(3, Math.min(15, Math.round(3 + value / 270)));
    const customers = Math.max(8, Math.round(value / 24));

    budgetValue.textContent = formatMoney(value);
    channelCount.textContent = String(channels);
    experimentCount.textContent = String(experiments);
    customerCount.textContent = `~${customers}`;
  };

  budget?.addEventListener('input', updatePlanner);
  updatePlanner();

  document.querySelectorAll('a.button-primary[href="/app"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      const value = budget ? Number(budget.value) : 1000;
      window.location.assign(`/start?budget=${encodeURIComponent(value)}`);
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
