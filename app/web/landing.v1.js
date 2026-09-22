(() => {
  const defaultBudget = 10;
  const accountLink = document.getElementById('nav-account-link');
  const updateAccountEntry = async () => {
    if (!accountLink) return;
    try {
      const response = await fetch('/customer/account/me', { method:'GET', credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'} });
      if (!response.ok) return;
      accountLink.textContent = 'Open workspace';
      accountLink.dataset.authenticated = 'true';
    } catch (_) {}
  };
  updateAccountEntry();

  const versionedStartLink = document.querySelector('a[href^="/start"]');
  const startRelease = versionedStartLink ? new URL(versionedStartLink.href, window.location.origin).searchParams.get('release') : null;
  const startDestination = (query) => { if (startRelease) query.set('release', startRelease); return `/start?${query.toString()}`; };
  const heroScanForm = document.getElementById('hero-scan-form');
  const heroProductLink = document.getElementById('hero-product-link');
  heroScanForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = new URLSearchParams();
    query.set('budget', String(defaultBudget));
    const product = heroProductLink?.value.trim();
    if (product) query.set('product', product);
    window.location.assign(startDestination(query));
  });
  document.querySelectorAll('a[href^="/start"]').forEach((link) => link.addEventListener('click', (event) => {
    event.preventDefault();
    const query = new URLSearchParams();
    query.set('budget', String(defaultBudget));
    const product = heroProductLink?.value.trim();
    if (product) query.set('product', product);
    window.location.assign(startDestination(query));
  }));

  const scenarios = {
    telegram:{product:'Daily English',meta:'Telegram bot · Example project',audience:'Busy adults who want a daily English habit.',audienceCopy:'They want to practise speaking, but a full course rarely fits their day.',audienceTag:'Language-learning audiences',message:'“Five minutes of speaking. Right inside Telegram.”',channel:'Telegram + Meta',action:'Test a five-minute speaking message with language-learning audiences.'},
    saas:{product:'Invoice Pilot',meta:'SaaS · Example project',audience:'Solo consultants who lose time chasing invoices.',audienceCopy:'They already manage client work themselves and feel the pain of admin immediately.',audienceTag:'Freelancer & consultant audiences',message:'“Know what is overdue before it becomes awkward.”',channel:'Reddit + Search',action:'Test a clear overdue-invoice pain point with self-employed professionals.'},
    store:{product:'Trail Ritual',meta:'Online store · Example project',audience:'Weekend hikers who want compact recovery gear.',audienceCopy:'They buy around specific trips and respond to concrete use cases more than generic wellness claims.',audienceTag:'Outdoor micro-communities',message:'“Recovery gear that fits beside your water bottle.”',channel:'Creators + TikTok',action:'Test a compact-packability message with small outdoor creators.'}
  };
  document.querySelectorAll('.scenario-tab').forEach((button)=>button.addEventListener('click',()=>{
    document.querySelectorAll('.scenario-tab').forEach((x)=>x.classList.toggle('active',x===button));
    const s=scenarios[button.dataset.scenario]; if(!s) return;
    document.getElementById('demo-product').textContent=s.product;
    document.getElementById('demo-meta').textContent=s.meta;
    document.getElementById('demo-audience').textContent=s.audience;
    document.getElementById('demo-audience-copy').textContent=s.audienceCopy;
    document.getElementById('demo-audience-tag').textContent=s.audienceTag;
    document.getElementById('demo-message').textContent=s.message;
    document.getElementById('demo-channel').textContent=s.channel;
    document.getElementById('demo-action').textContent=s.action;
  }));

  const nextName=document.getElementById('next-channel-name');
  const nextCopy=document.getElementById('next-channel-copy');
  const channelOrder=['Telegram','Meta','Google Ads','Reddit','TikTok'];
  document.querySelectorAll('.channel-toggle input').forEach((input)=>input.addEventListener('change',()=>{
    const label=input.closest('.channel-toggle');
    label.querySelector('.toggle-copy').textContent=input.checked?'On':'Off';
    const selected=channelOrder.find((name)=>document.querySelector(`.channel-toggle input[data-channel="${name}"]`)?.checked)||'Research only';
    nextName.textContent=selected;
    nextCopy.textContent=selected==='Research only'?'Keep researching without an execution channel.':`Prepare the next audience test on ${selected}.`;
  }));

  const range=document.getElementById('fee-range');
  const out=document.getElementById('fee-range-output');
  const spend=document.getElementById('fee-spend');
  const fee=document.getElementById('fee-fee');
  const total=document.getElementById('fee-total');
  const renderFee=()=>{const value=Number(range?.value||100);const partizan=value*.1;if(out)out.textContent=String(value);if(spend)spend.textContent=`$${value.toLocaleString()}`;if(fee)fee.textContent=`$${partizan.toLocaleString(undefined,{maximumFractionDigits:2})}`;if(total)total.textContent=`$${(value+partizan).toLocaleString(undefined,{maximumFractionDigits:2})}`;};
  range?.addEventListener('input',renderFee);renderFee();

  const reveal=[...document.querySelectorAll('.reveal')];
  if('IntersectionObserver' in window){const observer=new IntersectionObserver((entries,instance)=>entries.forEach((entry)=>{if(!entry.isIntersecting)return;entry.target.classList.add('visible');instance.unobserve(entry.target);}),{threshold:.1,rootMargin:'0px 0px -30px 0px'});reveal.forEach((n)=>observer.observe(n));}else reveal.forEach((n)=>n.classList.add('visible'));
  document.querySelectorAll('.faq-item').forEach((item)=>item.addEventListener('toggle',()=>{if(!item.open)return;document.querySelectorAll('.faq-item[open]').forEach((other)=>{if(other!==item)other.removeAttribute('open');});}));
})();