(() => {
  const select = document.getElementById('goal');
  if (!select) return;

  select.classList.add('goal-native');

  const root = document.createElement('div');
  root.className = 'goal-select';

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'goal-trigger';
  trigger.id = 'goal-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', 'goal-menu');

  const label = document.createElement('span');
  label.textContent = select.options[select.selectedIndex]?.textContent || select.value;
  const chevron = document.createElement('span');
  chevron.className = 'goal-chevron';
  chevron.setAttribute('aria-hidden', 'true');
  trigger.append(label, chevron);

  const menu = document.createElement('div');
  menu.id = 'goal-menu';
  menu.className = 'goal-menu hidden';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-labelledby', 'goal-trigger');

  const optionButtons = Array.from(select.options).map((option, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'goal-option';
    button.setAttribute('role', 'option');
    button.setAttribute('aria-selected', String(option.selected));
    button.dataset.value = option.value;
    button.dataset.index = String(index);
    button.textContent = option.textContent;
    menu.appendChild(button);
    return button;
  });

  select.parentNode.insertBefore(root, select);
  root.append(select, trigger, menu);

  const close = ({ focusTrigger = false } = {}) => {
    menu.classList.add('hidden');
    trigger.setAttribute('aria-expanded', 'false');
    if (focusTrigger) trigger.focus();
  };

  const open = (focusIndex = select.selectedIndex) => {
    menu.classList.remove('hidden');
    trigger.setAttribute('aria-expanded', 'true');
    const target = optionButtons[Math.max(0, focusIndex)] || optionButtons[0];
    target?.focus();
  };

  const choose = (button) => {
    const index = Number(button.dataset.index);
    select.selectedIndex = index;
    label.textContent = select.options[index].textContent;
    optionButtons.forEach((item) => item.setAttribute('aria-selected', String(item === button)));
    select.dispatchEvent(new Event('change', { bubbles: true }));
    close({ focusTrigger: true });
  };

  trigger.addEventListener('click', () => {
    if (menu.classList.contains('hidden')) open();
    else close();
  });

  trigger.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      open(event.key === 'ArrowUp' ? optionButtons.length - 1 : select.selectedIndex);
    }
  });

  optionButtons.forEach((button) => {
    button.addEventListener('click', () => choose(button));
    button.addEventListener('keydown', (event) => {
      const index = optionButtons.indexOf(button);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        optionButtons[(index + 1) % optionButtons.length].focus();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        optionButtons[(index - 1 + optionButtons.length) % optionButtons.length].focus();
      } else if (event.key === 'Home') {
        event.preventDefault();
        optionButtons[0].focus();
      } else if (event.key === 'End') {
        event.preventDefault();
        optionButtons[optionButtons.length - 1].focus();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        close({ focusTrigger: true });
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        choose(button);
      }
    });
  });

  document.addEventListener('pointerdown', (event) => {
    if (!root.contains(event.target)) close();
  });
})();
