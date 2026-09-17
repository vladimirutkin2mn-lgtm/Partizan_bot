(() => {
  const params = new URLSearchParams(window.location.search);
  if (params.get('meta') !== 'error') return;

  const error = params.get('meta_error') || '';
  const reason = params.get('meta_reason') || '';
  const code = params.get('meta_code') || '';
  const messages = {
    access_denied: 'Meta did not grant the requested access. Open Connect Meta again and approve the requested permissions.',
    missing_oauth_response: 'Meta returned without completing authorization. Start Connect Meta again.',
    no_manageable_ad_accounts: 'Meta authorized, but no manageable ad accounts were returned for this Facebook user.',
    no_promotable_pages: 'Meta authorized the ad account, but no Facebook Page available for promotion was returned.',
    oauth_state_invalid: 'This Meta authorization attempt expired or became invalid. Start Connect Meta again.',
    meta_api_rejected: 'Meta rejected an API request after authorization. Partizan did not save the connection.',
    partizan_meta_oauth_failed: 'Partizan could not complete the Meta authorization flow.',
  };

  const detail = [
    reason ? `reason ${reason}` : '',
    code ? `code ${code}` : '',
  ].filter(Boolean).join(', ');
  const base = messages[error] || 'Meta connection was not completed.';
  const message = detail ? `${base} (${detail})` : base;
  let shown = false;

  const render = () => {
    if (shown) return;
    const node = document.getElementById('notice');
    if (!node) return;
    shown = true;
    node.textContent = message;
    node.classList.add('error');
    node.classList.remove('hidden');
    window.setTimeout(() => node.classList.add('hidden'), 9000);
  };

  window.addEventListener('partizan:workspace-ready', () => {
    window.setTimeout(render, 0);
  }, { once: true });
  window.setTimeout(render, 1200);
})();
