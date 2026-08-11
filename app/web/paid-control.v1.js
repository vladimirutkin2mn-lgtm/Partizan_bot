(() => {
  "use strict";

  const EXECUTION_STORAGE_KEY = "partizan.execution.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";
  const SUPPORTED_PROVIDERS = new Set(["meta-marketing-api", "tiktok-marketing-api"]);

  let lifecycle = null;
  let auditEvents = [];
  let loading = false;
  let lastActionId = null;

  const $ = (selector) => document.querySelector(selector);

  function readExecution() {
    try {
      const raw = sessionStorage.getItem(EXECUTION_STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function writeExecution(plan, receipt) {
    const current = readExecution() || {};
    sessionStorage.setItem(
      EXECUTION_STORAGE_KEY,
      JSON.stringify({
        productId: current.productId || null,
        selectedPlayId: current.selectedPlayId || null,
        plan,
        receipt,
      }),
    );
    window.dispatchEvent(
      new CustomEvent("partizan:execution-updated", { detail: { plan, receipt } }),
    );
  }

  function operatorHeaders() {
    const input = $("#operator-key");
    const value = input ? input.value.trim() : "";
    return value ? { [OPERATOR_HEADER]: value } : {};
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...operatorHeaders(), ...(options.headers || {}) };
    const init = { method: options.method || "GET", headers };
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? payload.detail : payload;
      const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail || {}));
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function currentPaidAction() {
    const execution = readExecution();
    if (!execution || !execution.plan || !execution.plan.action || !execution.receipt) return null;
    const action = execution.plan.action;
    const receipt = execution.receipt;
    if (action.action_type !== "PAID_CAMPAIGN") return null;
    if (!SUPPORTED_PROVIDERS.has(receipt.provider)) return null;
    if (!["STAGED", "EXECUTED"].includes(receipt.outcome)) return null;
    return { execution, action, receipt };
  }

  function ensurePanel() {
    let panel = $("#paid-control-panel");
    if (panel) return panel;
    const receiptSection = $("#execution-receipt");
    if (!receiptSection) return null;
    panel = document.createElement("section");
    panel.id = "paid-control-panel";
    panel.className = "paid-control-panel hidden";
    receiptSection.insertAdjacentElement("afterend", panel);
    panel.addEventListener("click", handlePanelClick);
    panel.addEventListener("change", handlePanelChange);
    return panel;
  }

  function syncVisibility() {
    const panel = ensurePanel();
    if (!panel) return;
    const paid = currentPaidAction();
    panel.classList.toggle("hidden", !paid);
    if (!paid) {
      lifecycle = null;
      auditEvents = [];
      lastActionId = null;
      panel.replaceChildren();
      return;
    }
    if (lastActionId !== paid.action.id) {
      lifecycle = null;
      auditEvents = [];
      lastActionId = paid.action.id;
      renderPanel();
      loadState({ quiet: true });
      return;
    }
    renderPanel();
  }

  async function loadState({ quiet = false } = {}) {
    const paid = currentPaidAction();
    if (!paid || loading) return;
    loading = true;
    if (!quiet) setPanelAlert("");
    renderPanel();
    try {
      lifecycle = await api(`/v1/ops/paid-control/lifecycle/${paid.action.id}`);
      auditEvents = await api(`/v1/ops/paid-control/audit?action_id=${paid.action.id}&limit=12`);
      if (!quiet) setPanelAlert("Paid state обновлён из локального control plane.", true);
    } catch (error) {
      if (!quiet || error.status === 401 || error.status === 503) {
        setPanelAlert(humanizeError(error));
      }
    } finally {
      loading = false;
      renderPanel();
    }
  }

  function renderPanel() {
    const panel = ensurePanel();
    const paid = currentPaidAction();
    if (!panel || !paid) return;

    const rememberedAlert = panel.querySelector(".paid-control-alert");
    const alertText = rememberedAlert ? rememberedAlert.textContent : "";
    const alertSuccess = rememberedAlert ? rememberedAlert.classList.contains("success") : false;

    panel.replaceChildren();
    const head = document.createElement("div");
    head.className = "paid-control-head";
    const copy = document.createElement("div");
    const kicker = document.createElement("span");
    kicker.className = "section-kicker";
    kicker.textContent = "Paid Control";
    const title = document.createElement("h3");
    title.textContent = "Управление реальным расходом";
    const subtitle = document.createElement("p");
    subtitle.textContent = "Только exact-budget authorization → activation → sync/pause. Бюджет здесь не редактируется.";
    copy.append(kicker, title, subtitle);
    head.append(copy);
    if (lifecycle) {
      const badge = document.createElement("span");
      badge.className = `paid-state-badge ${lifecycle.state}`;
      badge.textContent = lifecycle.state;
      head.append(badge);
    }
    panel.append(head);

    const alert = document.createElement("div");
    alert.className = `paid-control-alert hidden${alertSuccess ? " success" : ""}`;
    alert.textContent = alertText;
    panel.append(alert);
    if (alertText) alert.classList.remove("hidden");

    if (!lifecycle) {
      const box = actionBox(
        "Загрузить paid lifecycle",
        "В local/dev ключ может быть пустым. В production введи operator key выше и обнови state.",
      );
      const row = document.createElement("div");
      row.className = "paid-action-row";
      row.append(button("Обновить paid state", "paid-refresh-state", "button button-primary"));
      box.append(row);
      panel.append(box);
      return;
    }

    panel.append(renderFacts(lifecycle));
    const actions = document.createElement("div");
    actions.className = "paid-control-actions";
    renderLifecycleAction(actions, lifecycle, paid);
    panel.append(actions);
    panel.append(renderAudit());
  }

  function renderFacts(view) {
    const facts = document.createElement("div");
    facts.className = "paid-facts";
    facts.append(
      paidFact("Provider", providerLabel(view.provider)),
      paidFact("State", view.state, "acid"),
      paidFact("Next", view.safe_next_action),
      paidFact("Budget cap", formatNumber(view.budget_cap)),
      paidFact("Provider spend", formatNumber(view.provider_spend)),
      paidFact("Synced spend", formatNumber(view.synced_spend)),
      paidFact("Provider status", view.provider_status || "—"),
      paidFact("Pause", view.pause_state || "—"),
      paidFact("Reconcile", view.requires_reconciliation ? "YES" : "NO"),
    );
    return facts;
  }

  function renderLifecycleAction(container, view, paid) {
    const refreshBox = actionBox(
      "Текущий control state",
      "Refresh перечитывает локальный lifecycle/audit. Контакт с рекламным провайдером происходит только по кнопке Sync.",
    );
    const refreshRow = document.createElement("div");
    refreshRow.className = "paid-action-row";
    refreshRow.append(button(loading ? "Обновляем…" : "Обновить lifecycle", "paid-refresh-state", "button button-ghost", loading));
    refreshBox.append(refreshRow);
    container.append(refreshBox);

    switch (view.safe_next_action) {
      case "AUTHORIZE_ACTIVATION":
        container.append(renderAuthorizationBox(view));
        break;
      case "ACTIVATE":
        container.append(renderActivationBox(view));
        break;
      case "SYNC_OR_PAUSE":
        container.append(renderActiveControlBox(view));
        break;
      case "RECONCILE":
        container.append(renderReconciliationBox(view));
        break;
      case "NONE":
        container.append(renderTerminalBox(view));
        break;
      case "STAGE_PROVIDER":
        container.append(actionBox(
          "Сначала staged execution",
          "Вернись к ExecutionAdapter. Paid Control не создаёт provider objects сам и не обходит STAGED boundary.",
        ));
        break;
      default:
        container.append(actionBox("Состояние требует проверки", "Paid Control не предлагает write-action без безопасного backend next step."));
    }

    if (paid.receipt.outcome === "STAGED") {
      const note = document.createElement("div");
      note.className = "paid-terminal";
      note.textContent = "STAGED receipt подтверждает только создание PAUSED/DISABLE provider objects. До успешного activation provider spend должен оставаться 0.";
      container.append(note);
    }
  }

  function renderAuthorizationBox(view) {
    const box = actionBox(
      "1. Разрешить расход",
      "Budget cap заблокирован значением backend. Это одноразовое разрешение не активирует кампанию само по себе.",
    );
    const budget = document.createElement("div");
    budget.className = "paid-budget-lock";
    const label = document.createElement("span");
    label.textContent = "Exact approved budget cap";
    const value = document.createElement("strong");
    value.textContent = formatNumber(view.budget_cap);
    budget.append(label, value);
    box.append(budget);

    const confirm = document.createElement("label");
    confirm.className = "paid-confirm-spend";
    const checkbox = document.createElement("input");
    checkbox.id = "paid-confirm-spend";
    checkbox.type = "checkbox";
    const copy = document.createElement("span");
    copy.textContent = `Я подтверждаю максимальный расход ${formatNumber(view.budget_cap)}. Изменить лимит в этом интерфейсе нельзя.`;
    confirm.append(checkbox, copy);
    box.append(confirm);

    const row = document.createElement("div");
    row.className = "paid-action-row";
    row.append(button("Создать authorization", "paid-authorize", "button button-primary", true));
    box.append(row);
    return box;
  }

  function renderActivationBox(view) {
    const box = actionBox(
      "2. Активировать кампанию",
      "Authorization уже существует. Activation — отдельное write-действие, после которого провайдер может начать расход в пределах exact budget cap.",
    );
    const budget = document.createElement("div");
    budget.className = "paid-budget-lock";
    const label = document.createElement("span");
    label.textContent = view.authorization_expires_at
      ? `Authorization expires ${formatDate(view.authorization_expires_at)}`
      : "One-shot authorization";
    const value = document.createElement("strong");
    value.textContent = formatNumber(view.budget_cap);
    budget.append(label, value);
    box.append(budget);
    const row = document.createElement("div");
    row.className = "paid-action-row";
    row.append(button("Активировать расход →", "paid-activate", "button button-primary"));
    box.append(row);
    return box;
  }

  function renderActiveControlBox(view) {
    const box = actionBox(
      "Кампания активна",
      `Provider spend: ${formatNumber(view.provider_spend)} / cap ${formatNumber(view.budget_cap)}. Sync может автоматически сработать как hard-stop при достижении cap/anomaly.`,
    );
    const row = document.createElement("div");
    row.className = "paid-action-row";
    row.append(
      button("Sync provider spend/status", "paid-sync", "button button-ghost"),
      button("Emergency pause", "paid-pause", "button button-ghost"),
    );
    box.append(row);
    return box;
  }

  function renderReconciliationBox(view) {
    const box = actionBox(
      "Требуется reconciliation",
      view.last_error || "Provider state неоднозначен. Доступен только безопасный control re-sync — activation/restart здесь не предлагается.",
    );
    const row = document.createElement("div");
    row.className = "paid-action-row";
    row.append(button("Reconcile provider state", "paid-reconcile", "button button-ghost"));
    box.append(row);
    return box;
  }

  function renderTerminalBox(view) {
    const wrapper = document.createElement("div");
    wrapper.className = "paid-terminal";
    wrapper.textContent = view.state === "PAUSED"
      ? `Кампания PAUSED. Safe next action = NONE. Этот UI не предлагает restart/re-enable. Причина: ${view.pause_reason || "provider/control stop"}.`
      : `Safe next action = NONE для состояния ${view.state}.`;
    return wrapper;
  }

  function renderAudit() {
    const section = document.createElement("div");
    section.className = "paid-audit";
    const head = document.createElement("div");
    head.className = "paid-audit-head";
    const title = document.createElement("strong");
    title.textContent = "Последние audit events";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "paid-refresh";
    refresh.dataset.action = "paid-refresh-state";
    refresh.textContent = "Обновить";
    head.append(title, refresh);
    section.append(head);

    const list = document.createElement("div");
    list.className = "paid-audit-list";
    if (!auditEvents.length) {
      const empty = document.createElement("div");
      empty.className = "paid-audit-row";
      empty.textContent = "Audit events пока нет.";
      list.append(empty);
    } else {
      auditEvents.slice(0, 12).forEach((event) => list.append(auditRow(event)));
    }
    section.append(list);
    return section;
  }

  function auditRow(event) {
    const row = document.createElement("div");
    row.className = "paid-audit-row";
    const eventNode = document.createElement("strong");
    eventNode.textContent = event.event_type;
    const actor = document.createElement("span");
    actor.textContent = `${event.actor} · ${event.result}`;
    const time = document.createElement("time");
    time.textContent = formatDate(event.occurred_at);
    row.append(eventNode, actor, time);
    if (event.reason) {
      const reason = document.createElement("div");
      reason.className = "paid-audit-reason";
      reason.textContent = event.reason;
      row.append(reason);
    }
    return row;
  }

  function actionBox(titleText, description) {
    const box = document.createElement("div");
    box.className = "paid-action-box";
    const title = document.createElement("strong");
    title.textContent = titleText;
    const text = document.createElement("p");
    text.textContent = description;
    box.append(title, text);
    return box;
  }

  function button(label, action, className, disabled = false) {
    const control = document.createElement("button");
    control.type = "button";
    control.className = className;
    control.dataset.action = action;
    control.textContent = label;
    control.disabled = disabled || loading;
    return control;
  }

  function paidFact(label, value, extraClass = "") {
    const box = document.createElement("div");
    box.className = "paid-fact";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("strong");
    if (extraClass) valueNode.className = extraClass;
    valueNode.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
    box.append(labelNode, valueNode);
    return box;
  }

  function handlePanelChange(event) {
    if (event.target && event.target.id === "paid-confirm-spend") {
      const buttonNode = $("#paid-control-panel [data-action='paid-authorize']");
      if (buttonNode) buttonNode.disabled = !event.target.checked || loading;
    }
  }

  async function handlePanelClick(event) {
    const control = event.target.closest("[data-action]");
    if (!control || loading) return;
    const action = control.dataset.action;
    switch (action) {
      case "paid-refresh-state":
        await runMutation(control, () => loadState({ quiet: false }), { renderAfter: false });
        break;
      case "paid-authorize":
        await authorizeSpend(control);
        break;
      case "paid-activate":
        await activateSpend(control);
        break;
      case "paid-sync":
        await syncProvider(control);
        break;
      case "paid-pause":
        await pauseProvider(control);
        break;
      case "paid-reconcile":
        await reconcileProvider(control);
        break;
      default:
        break;
    }
  }

  async function authorizeSpend(control) {
    const paid = currentPaidAction();
    if (!paid || !lifecycle || lifecycle.safe_next_action !== "AUTHORIZE_ACTIVATION") return;
    const checkbox = $("#paid-confirm-spend");
    if (!checkbox || !checkbox.checked) return;
    const routes = providerRoutes(lifecycle.provider, paid.action.id);
    await runMutation(control, async () => {
      await api(routes.authorize, {
        method: "POST",
        body: {
          approved_budget_cap: lifecycle.budget_cap,
          confirm_spend: true,
        },
      });
      await loadState({ quiet: true });
      setPanelAlert("Exact-budget authorization создан. Расход всё ещё не запущен — activation отдельным шагом.", true);
    });
  }

  async function activateSpend(control) {
    const paid = currentPaidAction();
    if (!paid || !lifecycle || lifecycle.safe_next_action !== "ACTIVATE" || !lifecycle.authorization_id) return;
    const confirmed = window.confirm(
      `Активировать ${providerLabel(lifecycle.provider)} campaign с максимальным budget cap ${formatNumber(lifecycle.budget_cap)}? После подтверждения провайдер может начать реальный расход.`,
    );
    if (!confirmed) return;
    const routes = providerRoutes(lifecycle.provider, paid.action.id);
    await runMutation(control, async () => {
      const result = await api(routes.activate, {
        method: "POST",
        body: { authorization_id: lifecycle.authorization_id },
      });
      writeExecution(result.plan, result.receipt);
      await loadState({ quiet: true });
      setPanelAlert("Activation подтверждён backend. Теперь контролируй provider spend/status и hard cap.", true);
    });
  }

  async function syncProvider(control) {
    const paid = currentPaidAction();
    if (!paid || !lifecycle) return;
    const routes = providerRoutes(lifecycle.provider, paid.action.id);
    await runMutation(control, async () => {
      await api(routes.sync, { method: "POST" });
      await loadState({ quiet: true });
      setPanelAlert("Provider status/spend синхронизирован. Если cap/anomaly сработал, lifecycle покажет PAUSED.", true);
    });
  }

  async function pauseProvider(control) {
    const paid = currentPaidAction();
    if (!paid || !lifecycle || lifecycle.state !== "ACTIVE") return;
    const confirmed = window.confirm("Emergency pause остановит provider campaign. Автоматического restart/re-enable в Partizan нет. Продолжить?");
    if (!confirmed) return;
    const routes = providerRoutes(lifecycle.provider, paid.action.id);
    await runMutation(control, async () => {
      await api(routes.pause, { method: "POST" });
      await loadState({ quiet: true });
      setPanelAlert("Emergency pause отправлен провайдеру. Проверь lifecycle/pause_state перед любыми дальнейшими действиями.", true);
    });
  }

  async function reconcileProvider(control) {
    const paid = currentPaidAction();
    if (!paid || !lifecycle || lifecycle.safe_next_action !== "RECONCILE") return;
    await runMutation(control, async () => {
      await api(`/v1/ops/paid-control/reconciliation/${paid.action.id}/sync`, { method: "POST" });
      await loadState({ quiet: true });
      setPanelAlert("Reconciliation sync выполнен. Write-path activation/restart при этом не вызывался.", true);
    });
  }

  async function runMutation(control, task, { renderAfter = true } = {}) {
    if (loading) return;
    loading = true;
    setPanelAlert("");
    renderPanel();
    try {
      await task();
    } catch (error) {
      setPanelAlert(humanizeError(error));
    } finally {
      loading = false;
      if (renderAfter) renderPanel();
    }
  }

  function providerRoutes(provider, actionId) {
    if (provider === "meta-marketing-api") {
      return {
        authorize: `/v1/distribution-actions/${actionId}/paid-campaign/activation-authorizations`,
        activate: `/v1/distribution-actions/${actionId}/paid-campaign/activate`,
        sync: `/v1/distribution-actions/${actionId}/paid-campaign/meta/sync`,
        pause: `/v1/distribution-actions/${actionId}/paid-campaign/meta/pause`,
      };
    }
    if (provider === "tiktok-marketing-api") {
      return {
        authorize: `/v1/distribution-actions/${actionId}/paid-campaign/tiktok/activation-authorizations`,
        activate: `/v1/distribution-actions/${actionId}/paid-campaign/tiktok/activate`,
        sync: `/v1/distribution-actions/${actionId}/paid-campaign/tiktok/sync`,
        pause: `/v1/distribution-actions/${actionId}/paid-campaign/tiktok/pause`,
      };
    }
    throw new Error("Paid Control не поддерживает этот provider.");
  }

  function setPanelAlert(message, success = false) {
    const panel = ensurePanel();
    if (!panel) return;
    let alert = panel.querySelector(".paid-control-alert");
    if (!alert) {
      alert = document.createElement("div");
      alert.className = "paid-control-alert hidden";
      const head = panel.querySelector(".paid-control-head");
      if (head) head.insertAdjacentElement("afterend", alert);
      else panel.prepend(alert);
    }
    alert.textContent = message || "";
    alert.className = `paid-control-alert${success ? " success" : ""}${message ? "" : " hidden"}`;
  }

  function humanizeError(error) {
    if (error && error.status === 401) return "Нужен operator key. Введи его в Operator access выше; он не сохраняется.";
    if (error && error.status === 503) return "Backend требует operator auth, но серверная конфигурация не готова.";
    return String(error && error.message ? error.message : error || "Paid Control error");
  }

  function providerLabel(provider) {
    if (provider === "meta-marketing-api") return "Meta Ads";
    if (provider === "tiktok-marketing-api") return "TikTok Ads";
    return provider || "—";
  }

  function formatNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(number);
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function bindObservers() {
    const receipt = $("#execution-receipt");
    if (receipt) {
      new MutationObserver(syncVisibility).observe(receipt, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class"],
      });
    }
    window.addEventListener("partizan:execution-updated", () => {
      window.setTimeout(syncVisibility, 0);
    });
    const operatorInput = $("#operator-key");
    if (operatorInput) {
      operatorInput.addEventListener("input", () => {
        if (currentPaidAction() && !lifecycle) setPanelAlert("");
      });
    }
  }

  ensurePanel();
  bindObservers();
  syncVisibility();
})();
