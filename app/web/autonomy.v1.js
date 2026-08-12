(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";
  const PLATFORM_OPTIONS = ["TELEGRAM", "INSTAGRAM", "REDDIT", "TIKTOK"];
  const ACTION_OPTIONS = [
    ["COMMENT", "Комментарии"],
    ["REPLY", "Ответы"],
    ["STANDALONE_POST", "Посты"],
    ["ORGANIC_VIDEO", "Органические видео"],
    ["PAID_CAMPAIGN", "Платная реклама"],
  ];

  let overview = null;
  let operatorKey = "";
  let busy = false;
  let alertMessage = "";
  let alertType = "";

  const $ = (selector) => document.querySelector(selector);

  function workspaceState() {
    try {
      const raw = sessionStorage.getItem(WORKSPACE_STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  }

  function productId() {
    return workspaceState().productId || null;
  }

  function currentProduct() {
    const state = workspaceState();
    return state.intake && state.intake.product ? state.intake.product : null;
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function operatorHeaders() {
    return operatorKey ? { [OPERATOR_HEADER]: operatorKey } : {};
  }

  async function api(path, options = {}) {
    const headers = {
      Accept: "application/json",
      ...operatorHeaders(),
      ...(options.headers || {}),
    };
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
      const error = new Error(
        typeof detail === "string" ? detail : JSON.stringify(detail || {}),
      );
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function ensurePanel() {
    const stage = $("#stage-results");
    if (!stage) return null;
    let panel = $("#autonomy-panel");
    if (panel) return panel;
    panel = node("section", "autonomy-panel");
    panel.id = "autonomy-panel";
    panel.addEventListener("click", handleClick);
    stage.append(panel);
    return panel;
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;
    panel.replaceChildren();

    const head = node("div", "autonomy-head");
    const copy = node("div");
    copy.append(
      node("span", "section-kicker", "Autonomous Growth"),
      node("h3", "", "Дай Partizan рамки — и он работает сам"),
      node(
        "p",
        "",
        "Мандат задаёт бюджет, разрешённые каналы и уровень самостоятельности. Всё за пределами рамок блокируется или ждёт подтверждения.",
      ),
    );
    const status = overview && overview.mandate ? overview.mandate.status : "НЕ НАСТРОЕНО";
    head.append(copy, node("span", `autonomy-status status-${status.toLowerCase()}`, status));
    panel.append(head);

    const alert = node(
      "div",
      `autonomy-alert${alertMessage ? "" : " hidden"}${alertType ? ` ${alertType}` : ""}`,
      alertMessage,
    );
    alert.id = "autonomy-alert";
    alert.setAttribute("role", "status");
    panel.append(alert);

    const id = productId();
    if (!id) {
      panel.append(node("p", "autonomy-muted", "Сначала создай продукт."));
      return;
    }

    panel.append(renderAccessBar());
    if (overview && overview.mandate) panel.append(renderBudgetStrip());

    const grid = node("div", "autonomy-main-grid");
    grid.append(renderMandateCard(), renderActivityCard());
    panel.append(grid);
    if (overview) panel.append(renderTimeline());
  }

  function renderAccessBar() {
    const bar = node("div", "autonomy-access");
    const field = node("label", "autonomy-access-field");
    field.append(node("span", "", "Operator key · не сохраняется"));
    const input = document.createElement("input");
    input.id = "autonomy-operator-key";
    input.type = "password";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "В local/dev можно оставить пустым";
    input.value = operatorKey;
    input.addEventListener("input", () => {
      operatorKey = input.value.trim();
    });
    field.append(input);

    const actions = node("div", "autonomy-access-actions");
    actions.append(
      button("Обновить", "refresh", "button button-ghost", busy),
      button("Запустить цикл сейчас", "sweep", "button button-primary", busy || !isActive()),
    );
    if (overview && overview.mandate && overview.mandate.status === "ACTIVE") {
      actions.append(button("ПАУЗА", "pause", "button autonomy-stop", busy));
    } else if (overview && overview.mandate && overview.mandate.status === "PAUSED") {
      actions.append(button("Возобновить", "resume", "button button-ghost", busy));
    }
    bar.append(field, actions);
    return bar;
  }

  function renderBudgetStrip() {
    const exposure = overview.budget_exposure || {};
    const strip = node("div", "autonomy-budget-strip");
    strip.append(
      budgetMetric(
        "Осталось всего",
        money(overview.remaining_total_budget),
        `лимит ${money(overview.mandate.total_budget_cap)}`,
      ),
      budgetMetric(
        "Осталось сегодня",
        money(overview.remaining_daily_budget),
        `дневной лимит ${money(overview.mandate.max_autonomous_spend_per_day)}`,
      ),
      budgetMetric(
        "Уже потрачено",
        money(exposure.observed_total_spend),
        `сегодня ${money(exposure.observed_daily_spend)}`,
      ),
      budgetMetric(
        "Зарезервировано",
        money(exposure.reserved_running_paid_budget),
        "остаток cap у RUNNING paid",
      ),
    );
    return strip;
  }

  function budgetMetric(label, value, detail) {
    const metric = node("div", "autonomy-budget-metric");
    metric.append(node("span", "", label), node("strong", "", value), node("small", "", detail));
    return metric;
  }

  function renderMandateCard() {
    const mandate = overview && overview.mandate ? overview.mandate : null;
    const product = currentProduct() || {};
    const totalDefault = mandate ? mandate.total_budget_cap : numberOr(product.budget, 1000);
    const cacDefault = mandate ? mandate.target_max_cac : numberOr(product.max_cac, 12);
    const perExperiment = mandate
      ? mandate.max_autonomous_spend_per_experiment
      : Math.min(50, totalDefault);
    const daily = mandate ? mandate.max_autonomous_spend_per_day : Math.min(100, totalDefault);
    const approval = mandate && mandate.approval_threshold !== null
      ? mandate.approval_threshold
      : perExperiment;

    const card = node("section", "autonomy-card mandate-card");
    card.append(
      node("h4", "", mandate ? "Рамки автономности" : "Настроить автономность"),
      node(
        "p",
        "",
        "Это не рекомендация, а жёсткий контракт: worker проверяет его перед prepare, approve и каждым стартом paid spend.",
      ),
    );

    const numbers = node("div", "autonomy-number-grid");
    numbers.append(
      numberField("autonomy-total", "Бюджет всего", totalDefault, 0.01),
      numberField("autonomy-cac", "Макс. CAC", cacDefault, 0.01),
      numberField("autonomy-per-experiment", "Сам на один тест", perExperiment, 0),
      numberField("autonomy-daily", "Сам в день", daily, 0),
      numberField(
        "autonomy-concurrent",
        "Одновременно RUNNING",
        mandate && mandate.max_concurrent_running_experiments
          ? mandate.max_concurrent_running_experiments
          : 2,
        1,
        "1",
      ),
      numberField("autonomy-approval-threshold", "Выше — спросить меня", approval, 0),
    );
    card.append(numbers);

    card.append(optionGroup("Разрешённые площадки", PLATFORM_OPTIONS.map((value) => [value, value]), "platform", mandate));
    card.append(optionGroup("Что можно делать", ACTION_OPTIONS, "action", mandate));

    const delegation = node("div", "autonomy-delegation");
    delegation.append(
      checkField(
        "autonomy-prepare",
        "Сам готовит действия",
        mandate ? mandate.autonomous_prepare : true,
      ),
      checkField(
        "autonomy-approve",
        "Сам подтверждает действия в пределах лимита",
        mandate ? mandate.autonomous_approve : false,
      ),
      checkField(
        "autonomy-paid",
        "Сам запускает paid spend в пределах exact budget",
        mandate ? mandate.autonomous_paid_activation : false,
      ),
    );
    card.append(delegation);

    const save = button(
      mandate && mandate.status === "REVOKED" ? "Создать новый мандат" : "Сохранить мандат",
      "save",
      "button button-primary autonomy-save",
      busy,
    );
    card.append(save);
    return card;
  }

  function renderActivityCard() {
    const card = node("section", "autonomy-card activity-card");
    card.append(
      node("h4", "", "Что происходит сейчас"),
      node("p", "", "RUNNING — реально запущено. Ожидает — подготовлено или STAGED, но ещё не имеет права идти дальше без подтверждения."),
    );
    if (!overview) {
      card.append(node("div", "autonomy-muted", "Обнови статус, чтобы увидеть активность."));
      return card;
    }
    card.append(activitySection("Сейчас работает", overview.running_experiments || [], "running"));
    card.append(activitySection("Ждёт подтверждения", overview.waiting_approval || [], "waiting"));
    return card;
  }

  function activitySection(title, items, mode) {
    const section = node("div", "autonomy-activity-section");
    const head = node("div", "autonomy-activity-head");
    head.append(node("strong", "", title), node("span", "", items.length));
    section.append(head);
    if (!items.length) {
      section.append(node("p", "autonomy-muted", mode === "running" ? "Нет RUNNING экспериментов." : "Очередь пуста."));
      return section;
    }
    const list = node("div", "autonomy-experiment-list");
    list.append(...items.map(experimentCard));
    section.append(list);
    return section;
  }

  function experimentCard(item) {
    const card = node("article", "autonomy-experiment");
    const head = node("div", "autonomy-experiment-head");
    head.append(
      node("strong", "", `${item.platform} · ${actionLabel(item.action_type)}`),
      node("span", "", item.adapter_outcome || item.experiment_status),
    );
    const detail = node("p", "", `${item.action_status} / ${item.experiment_status}`);
    card.append(head, detail);
    if (item.budget_cap !== null && item.budget_cap !== undefined) {
      card.append(node("small", "", `budget cap ${money(item.budget_cap)}`));
    }
    return card;
  }

  function renderTimeline() {
    const section = node("section", "autonomy-timeline");
    section.append(
      node("h4", "", "Последние решения Partizan"),
      node("p", "", "Единый журнал launch, paid activation и Growth Manager control с версией мандата, под которой принято решение."),
    );
    const items = overview.recent_decisions || [];
    if (!items.length) {
      section.append(node("div", "autonomy-muted", "Автономных решений пока нет."));
      return section;
    }
    const list = node("div", "autonomy-timeline-list");
    list.append(...items.slice(0, 12).map(timelineItem));
    section.append(list);
    return section;
  }

  function timelineItem(item) {
    const row = node("article", "autonomy-timeline-item");
    const marker = node("span", `autonomy-kind kind-${item.kind.toLowerCase()}`, kindLabel(item.kind));
    const copy = node("div", "autonomy-timeline-copy");
    const title = [item.platform, item.decision, item.outcome].filter(Boolean).join(" · ");
    copy.append(
      node("strong", "", title || item.outcome),
      node("p", "", (item.reasons || []).join(" ") || "Без дополнительного комментария."),
      node("small", "", `мандат v${item.mandate_version} · ${formatDate(item.recorded_at)}`),
    );
    if (item.budget !== null && item.budget !== undefined && Number(item.budget) > 0) {
      copy.append(node("small", "", `budget ${money(item.budget)}`));
    }
    row.append(marker, copy);
    return row;
  }

  function optionGroup(title, options, kind, mandate) {
    const group = node("fieldset", "autonomy-options");
    group.append(node("legend", "", title));
    const selected = mandate
      ? new Set(kind === "platform" ? mandate.allowed_platforms : mandate.allowed_actions)
      : new Set(options.map(([value]) => value));
    const grid = node("div", "autonomy-option-grid");
    options.forEach(([value, label]) => {
      const control = checkField(`autonomy-${kind}-${value}`, label, selected.has(value));
      control.dataset.optionKind = kind;
      control.dataset.optionValue = value;
      grid.append(control);
    });
    group.append(grid);
    return group;
  }

  function checkField(id, label, checked) {
    const wrapper = node("label", "autonomy-check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = id;
    input.checked = Boolean(checked);
    wrapper.append(input, node("span", "", label));
    return wrapper;
  }

  function numberField(id, label, value, min, step = "0.01") {
    const wrapper = node("label", "autonomy-number-field");
    wrapper.append(node("span", "", label));
    const input = document.createElement("input");
    input.id = id;
    input.type = "number";
    input.min = String(min);
    input.step = step;
    input.value = value === null || value === undefined ? "" : String(value);
    wrapper.append(input);
    return wrapper;
  }

  function button(label, action, className, disabled = false) {
    const control = node("button", className, label);
    control.type = "button";
    control.dataset.autonomyAction = action;
    control.disabled = disabled;
    return control;
  }

  async function handleClick(event) {
    const control = event.target.closest("[data-autonomy-action]");
    if (!control || busy) return;
    const action = control.dataset.autonomyAction;
    if (action === "refresh") await refreshOverview(false);
    if (action === "save") await saveMandate();
    if (action === "pause") await setStatus("PAUSED");
    if (action === "resume") await setStatus("ACTIVE");
    if (action === "sweep") await runSweep();
  }

  async function refreshOverview(quiet = false) {
    const id = productId();
    if (!id || busy) return;
    busy = true;
    if (!quiet) setAlert("");
    render();
    try {
      overview = await api(`/v1/products/${id}/autonomy-overview?timeline_limit=30`);
      if (!quiet) setAlert("Автономный контур обновлён.", "success");
    } catch (error) {
      overview = null;
      if (!quiet || error.status === 401 || error.status === 503) {
        setAlert(humanizeError(error), "error");
      }
    } finally {
      busy = false;
      render();
    }
  }

  async function saveMandate() {
    const id = productId();
    if (!id || busy) return;
    let payload;
    try {
      payload = collectMandate();
    } catch (error) {
      setAlert(String(error.message || error), "error");
      return;
    }
    busy = true;
    setAlert("");
    render();
    try {
      await api(`/v1/products/${id}/growth-mandate`, { method: "PUT", body: payload });
      overview = await api(`/v1/products/${id}/autonomy-overview?timeline_limit=30`);
      setAlert("Мандат сохранён. Worker может действовать только внутри этих рамок.", "success");
    } catch (error) {
      setAlert(humanizeError(error), "error");
    } finally {
      busy = false;
      render();
    }
  }

  async function setStatus(status) {
    const id = productId();
    if (!id || busy || !overview || !overview.mandate) return;
    if (status === "PAUSED") {
      const confirmed = window.confirm(
        "Остановить автономные новые prepare/approve/activation? Safety paid-control продолжит синхронизацию и hard-stop.",
      );
      if (!confirmed) return;
    }
    busy = true;
    setAlert("");
    render();
    try {
      await api(`/v1/products/${id}/growth-mandate/status`, {
        method: "PATCH",
        body: { status },
      });
      overview = await api(`/v1/products/${id}/autonomy-overview?timeline_limit=30`);
      setAlert(status === "PAUSED" ? "Автономность поставлена на паузу." : "Автономность возобновлена.", "success");
    } catch (error) {
      setAlert(humanizeError(error), "error");
    } finally {
      busy = false;
      render();
    }
  }

  async function runSweep() {
    const id = productId();
    if (!id || busy || !isActive()) return;
    busy = true;
    setAlert("Partizan выполняет один bounded growth cycle…");
    render();
    try {
      await api(`/v1/ops/autonomous-growth/sweep?product_id=${encodeURIComponent(id)}`, {
        method: "POST",
      });
      overview = await api(`/v1/products/${id}/autonomy-overview?timeline_limit=30`);
      setAlert("Цикл завершён. Ни одно действие не могло выйти за текущий мандат.", "success");
    } catch (error) {
      setAlert(humanizeError(error), "error");
    } finally {
      busy = false;
      render();
    }
  }

  function collectMandate() {
    const total = numberValue("#autonomy-total");
    const maxCac = nullableNumberValue("#autonomy-cac");
    const perExperiment = numberValue("#autonomy-per-experiment");
    const daily = numberValue("#autonomy-daily");
    const concurrent = Math.trunc(numberValue("#autonomy-concurrent"));
    const approval = nullableNumberValue("#autonomy-approval-threshold");
    const platforms = selectedOptions("platform");
    const actions = selectedOptions("action");
    const prepare = checked("#autonomy-prepare");
    const approve = checked("#autonomy-approve");
    const paid = checked("#autonomy-paid");
    if (!platforms.length) throw new Error("Выбери хотя бы одну площадку.");
    if (!actions.length) throw new Error("Выбери хотя бы один тип действия.");
    if (paid && !approve) {
      throw new Error("Автономный paid activation требует разрешить автономное подтверждение действий.");
    }
    return {
      total_budget_cap: total,
      target_max_cac: maxCac,
      max_autonomous_spend_per_experiment: perExperiment,
      max_autonomous_spend_per_day: daily,
      max_concurrent_running_experiments: concurrent,
      allowed_platforms: platforms,
      allowed_actions: actions,
      autonomous_prepare: prepare,
      autonomous_approve: approve,
      autonomous_paid_activation: paid,
      approval_threshold: approval,
    };
  }

  function selectedOptions(kind) {
    return Array.from(document.querySelectorAll(`[data-option-kind="${kind}"]`))
      .filter((wrapper) => {
        const input = wrapper.querySelector("input[type='checkbox']");
        return input && input.checked;
      })
      .map((wrapper) => wrapper.dataset.optionValue);
  }

  function numberValue(selector) {
    const input = $(selector);
    const value = input ? Number(input.value) : Number.NaN;
    if (!Number.isFinite(value)) throw new Error("Заполни все числовые лимиты.");
    return value;
  }

  function nullableNumberValue(selector) {
    const input = $(selector);
    if (!input || input.value.trim() === "") return null;
    const value = Number(input.value);
    if (!Number.isFinite(value)) throw new Error("Проверь числовые лимиты.");
    return value;
  }

  function checked(selector) {
    const input = $(selector);
    return Boolean(input && input.checked);
  }

  function isActive() {
    return Boolean(overview && overview.mandate && overview.mandate.status === "ACTIVE");
  }

  function setAlert(message, type = "") {
    alertMessage = message || "";
    alertType = type || "";
    const alert = $("#autonomy-alert");
    if (!alert) return;
    alert.textContent = alertMessage;
    alert.className = `autonomy-alert${alertMessage ? "" : " hidden"}${alertType ? ` ${alertType}` : ""}`;
  }

  function clearSecret() {
    operatorKey = "";
    const input = $("#autonomy-operator-key");
    if (input) input.value = "";
  }

  function humanizeError(error) {
    if (error && error.status === 401) return "Нужен operator key. Введи его выше; значение не сохраняется.";
    if (error && error.status === 503) return "Production требует operator auth, но backend operator key не настроен.";
    if (error && error.status === 422) return "Проверь лимиты и выбранные разрешения мандата.";
    return String(error && error.message ? error.message : error || "Autonomy error");
  }

  function money(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(Number(value));
  }

  function numberOr(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : fallback;
  }

  function actionLabel(value) {
    const found = ACTION_OPTIONS.find(([key]) => key === value);
    return found ? found[1] : value;
  }

  function kindLabel(kind) {
    if (kind === "LAUNCH") return "Launch";
    if (kind === "PAID_ACTIVATION") return "Paid";
    if (kind === "GROWTH_CONTROL") return "Growth";
    return kind;
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

  function bindLifecycle() {
    document.addEventListener("click", (event) => {
      const progress = event.target.closest(".progress-step");
      if (progress && progress.dataset.step !== "results") clearSecret();
      if (progress && progress.dataset.step === "results") {
        window.setTimeout(() => {
          render();
          refreshOverview(true);
        }, 0);
      }
      const reset = event.target.closest("#reset-workspace");
      if (reset) {
        clearSecret();
        overview = null;
        setAlert("");
      }
    });
  }

  render();
  bindLifecycle();
})();
