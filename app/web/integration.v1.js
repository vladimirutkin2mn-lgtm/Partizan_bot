(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";

  let statusView = null;
  let plaintextKey = "";
  let busy = false;

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

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function operatorHeaders() {
    const input = $("#integration-operator-key");
    const value = input ? input.value.trim() : "";
    return value ? { [OPERATOR_HEADER]: value } : {};
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...operatorHeaders(), ...(options.headers || {}) };
    const response = await fetch(path, { method: options.method || "GET", headers });
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

  function ensurePanel() {
    const stage = $("#stage-results");
    if (!stage) return null;
    let panel = $("#conversion-integration-panel");
    if (panel) return panel;

    panel = node("section", "conversion-integration");
    panel.id = "conversion-integration-panel";
    panel.addEventListener("click", handlePanelClick);
    stage.append(panel);
    render();
    return panel;
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;
    const id = productId();
    panel.replaceChildren();

    const head = node("div", "integration-head");
    const copy = node("div");
    copy.append(
      node("span", "section-kicker", "Conversion Integration"),
      node("h3", "", "Подключить конверсии"),
      node("p", "", "VISIT Partizan может считать сам через tracking redirect. SIGNUP, ACTIVATED и PAID продукт отправляет server-to-server по Product Event Key."),
    );
    const badge = node(
      "span",
      `integration-status${statusView && statusView.configured ? " configured" : ""}`,
      statusView && statusView.configured ? "Event Key настроен" : "Event Key не настроен",
    );
    head.append(copy, badge);
    panel.append(head);

    const alert = node("div", "integration-alert hidden");
    alert.id = "integration-alert";
    alert.setAttribute("role", "status");
    panel.append(alert);

    if (!id) {
      panel.append(node("div", "integration-muted", "Сначала создай продукт. Integration привязывается к конкретному ProductProfile."));
      return;
    }

    const grid = node("div", "integration-grid");
    grid.append(renderKeyCard(id), renderGuideCard(id));
    panel.append(grid);
  }

  function renderKeyCard(id) {
    const card = node("section", "integration-card");
    card.append(
      node("h4", "", "Product Event Key"),
      node("p", "", "Ключ нужен только backend-серверу продукта. Partizan сохраняет его digest, а plaintext показывает лишь при создании/ротации."),
    );

    const operator = node("div", "integration-field");
    const operatorLabel = node("label", "", "Operator key · не сохраняется");
    operatorLabel.setAttribute("for", "integration-operator-key");
    const operatorInput = document.createElement("input");
    operatorInput.id = "integration-operator-key";
    operatorInput.type = "password";
    operatorInput.autocomplete = "off";
    operatorInput.spellcheck = false;
    operatorInput.placeholder = "В local/dev можно оставить пустым";
    operator.append(operatorLabel, operatorInput);
    card.append(operator);

    const facts = node("div", "integration-facts");
    facts.append(
      fact("Product ID", id),
      fact("Key hint", statusView && statusView.key_hint ? statusView.key_hint : "—"),
      fact("Создан", statusView && statusView.created_at ? formatDate(statusView.created_at) : "—"),
      fact("Статус", statusView && statusView.configured ? "configured" : "not configured"),
    );
    card.append(facts);

    const actions = node("div", "integration-actions");
    actions.append(button("Обновить статус", "refresh", "button button-ghost", busy));
    const createLabel = statusView && statusView.configured ? "Ротировать Event Key" : "Создать Event Key";
    actions.append(button(createLabel, "rotate", "button button-primary", busy));
    if (statusView && statusView.configured) {
      actions.append(button("Отозвать ключ", "revoke", "button button-ghost", busy));
    }
    card.append(actions);

    if (plaintextKey) card.append(renderPlaintextKey());
    return card;
  }

  function renderPlaintextKey() {
    const box = node("div", "integration-key-once");
    box.append(
      node("strong", "", "Сохрани сейчас — повторно Partizan этот ключ не покажет"),
      node("p", "", "Помести ключ в secret manager / server environment продукта. Не вставляй его в браузерный JavaScript, query string или tracking URL."),
    );
    const value = node("code", "integration-key-value", plaintextKey);
    value.id = "integration-event-key-once";
    const actions = node("div", "integration-actions");
    actions.append(
      button("Скопировать ключ", "copy-key", "button button-primary"),
      button("Я сохранил ключ", "clear-key", "button button-ghost"),
    );
    box.append(value, actions);
    return box;
  }

  function renderGuideCard(id) {
    const card = node("section", "integration-card");
    card.append(
      node("h4", "", "Server-side endpoint"),
      node("p", "", "Сохрани attribution `ptz_experiment` / `ptz_action` при входе пользователя и отправляй downstream события с backend продукта."),
    );

    const endpoint = `${window.location.origin}/v1/products/${id}/distribution-events`;
    card.append(node("div", "integration-endpoint", `POST ${endpoint}`));

    const code = [
      "# SERVER-SIDE Python example",
      "import os, uuid, httpx",
      "",
      `url = \"${endpoint}\"`,
      "httpx.post(",
      "    url,",
      "    headers={\"X-Partizan-Event-Key\": os.environ[\"PARTIZAN_EVENT_KEY\"]},",
      "    json={",
      "        \"event_id\": str(uuid.uuid4()),  # persist for retries",
      "        \"event_type\": \"PAID\",  # SIGNUP | ACTIVATED | PAID",
      "        \"experiment_id\": saved_ptz_experiment,",
      "        \"actor_id\": internal_user_id,",
      "        \"revenue\": 6.90,",
      "    },",
      ")",
    ].join("\n");
    card.append(node("pre", "integration-code", code));

    card.append(
      node(
        "div",
        "integration-first-click",
        "First click: если deployment настроен с PARTIZAN_PUBLIC_BASE_URL, tracking_url идёт через /r/{referral_token} и VISIT записывается автоматически. SIGNUP / ACTIVATED / PAID всё равно требуют Event Key.",
      ),
    );
    return card;
  }

  function fact(label, value) {
    const box = node("div", "integration-fact");
    box.append(node("span", "", label), node("strong", "", value));
    return box;
  }

  function button(label, action, className, disabled = false) {
    const control = node("button", className, label);
    control.type = "button";
    control.dataset.integrationAction = action;
    control.disabled = disabled;
    return control;
  }

  async function handlePanelClick(event) {
    const control = event.target.closest("[data-integration-action]");
    if (!control || busy) return;
    const action = control.dataset.integrationAction;
    if (action === "refresh") await refreshStatus(false);
    if (action === "rotate") await rotateKey();
    if (action === "revoke") await revokeKey();
    if (action === "copy-key") await copyKey();
    if (action === "clear-key") clearPlaintext();
  }

  async function refreshStatus(quiet = false) {
    const id = productId();
    if (!id || busy) return;
    busy = true;
    if (!quiet) setAlert("");
    render();
    try {
      statusView = await api(`/v1/products/${id}/distribution-event-key`);
      if (!quiet) setAlert("Статус Event Key обновлён. Plaintext через status API не возвращается.", "success");
    } catch (error) {
      if (!quiet || error.status === 401 || error.status === 503) setAlert(humanizeError(error), "error");
    } finally {
      busy = false;
      render();
    }
  }

  async function rotateKey() {
    const id = productId();
    if (!id || busy) return;
    if (statusView && statusView.configured) {
      const confirmed = window.confirm("Ротация немедленно инвалидирует предыдущий Event Key. Продолжить?");
      if (!confirmed) return;
    }
    busy = true;
    setAlert("");
    render();
    try {
      const created = await api(`/v1/products/${id}/distribution-event-key`, { method: "POST" });
      plaintextKey = created.event_key || "";
      statusView = {
        product_id: created.product_id,
        configured: created.configured,
        key_hint: created.key_hint,
        created_at: created.created_at,
      };
      setAlert("Новый Event Key создан. Скопируй и сохрани его сейчас.", "success");
    } catch (error) {
      plaintextKey = "";
      setAlert(humanizeError(error), "error");
    } finally {
      busy = false;
      render();
    }
  }

  async function revokeKey() {
    const id = productId();
    if (!id || busy || !statusView || !statusView.configured) return;
    const confirmed = window.confirm("Отозвать Event Key? Клиентский backend сразу перестанет отправлять конверсии этим ключом.");
    if (!confirmed) return;
    busy = true;
    setAlert("");
    render();
    try {
      statusView = await api(`/v1/products/${id}/distribution-event-key`, { method: "DELETE" });
      plaintextKey = "";
      setAlert("Event Key отозван.", "success");
    } catch (error) {
      setAlert(humanizeError(error), "error");
    } finally {
      busy = false;
      render();
    }
  }

  async function copyKey() {
    if (!plaintextKey) return;
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        throw new Error("Clipboard API недоступен в этом браузере/контексте.");
      }
      await navigator.clipboard.writeText(plaintextKey);
      setAlert("Event Key скопирован. Сохрани его в server secret manager, затем нажми «Я сохранил ключ».", "success");
    } catch (error) {
      setAlert(String(error && error.message ? error.message : error), "error");
    }
  }

  function clearPlaintext() {
    plaintextKey = "";
    const value = $("#integration-event-key-once");
    if (value) value.textContent = "";
    setAlert("Plaintext очищен из интерфейса. Partizan хранит только digest.", "success");
    render();
  }

  function clearSecrets() {
    plaintextKey = "";
    const operator = $("#integration-operator-key");
    if (operator) operator.value = "";
    const value = $("#integration-event-key-once");
    if (value) value.textContent = "";
  }

  function setAlert(message, type = "") {
    const alert = $("#integration-alert");
    if (!alert) return;
    alert.textContent = message || "";
    alert.className = `integration-alert${message ? "" : " hidden"}${type ? ` ${type}` : ""}`;
  }

  function humanizeError(error) {
    if (error && error.status === 401) return "Нужен operator key. Введи его выше; значение не сохраняется.";
    if (error && error.status === 503) return "Production требует operator auth, но backend operator key не настроен.";
    return String(error && error.message ? error.message : error || "Integration error");
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function bindLifecycle() {
    document.addEventListener("click", (event) => {
      const progress = event.target.closest(".progress-step");
      if (progress && progress.dataset.step !== "results") clearSecrets();
      if (progress && progress.dataset.step === "results") {
        window.setTimeout(() => {
          render();
          refreshStatus(true);
        }, 0);
      }
      const reset = event.target.closest("#reset-workspace");
      if (reset) {
        clearSecrets();
        statusView = null;
      }
    });
  }

  ensurePanel();
  bindLifecycle();
})();
