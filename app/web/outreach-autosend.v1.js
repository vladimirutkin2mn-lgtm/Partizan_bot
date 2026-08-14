(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";

  let state = null;
  let policy = null;
  let busy = false;
  let message = "";
  let messageType = "";

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

  function operatorKey() {
    const input = $("#autonomy-operator-key");
    return input ? input.value.trim() : "";
  }

  async function api(path, options = {}) {
    const headers = {
      Accept: "application/json",
      ...(operatorKey() ? { [OPERATOR_HEADER]: operatorKey() } : {}),
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

  async function optional(path) {
    try {
      return await api(path);
    } catch (error) {
      if (error.status === 404) return null;
      throw error;
    }
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function ensurePanel() {
    const stage = $("#stage-results");
    if (!stage) return null;
    let panel = $("#outreach-autosend-panel");
    if (panel) return panel;
    panel = node("section", "outreach-autosend-panel");
    panel.id = "outreach-autosend-panel";
    panel.addEventListener("click", handleClick);
    panel.addEventListener("submit", handleSubmit);
    const outreach = $("#outreach-panel");
    if (outreach && outreach.nextSibling) {
      stage.insertBefore(panel, outreach.nextSibling);
    } else {
      stage.append(panel);
    }
    return panel;
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;
    panel.replaceChildren();

    const head = node("div", "outreach-autosend-head");
    const copy = node("div");
    copy.append(
      node("span", "section-kicker", "Autonomous Outreach"),
      node("h3", "", "Разрешение на ограниченную автоотправку"),
      node(
        "p",
        "",
        "Delegation разрешает autonomous worker отправлять только первые outreach-письма внутри текущих Growth Mandate, Outreach Policy и sender identity. Follow-up всегда 0.",
      ),
    );
    const refresh = button("Обновить", "refresh");
    head.append(copy, refresh);
    panel.append(head);

    if (message) {
      panel.append(node("div", `outreach-autosend-alert ${messageType}`.trim(), message));
    }

    if (!productId()) {
      panel.append(node("p", "outreach-autosend-muted", "Сначала создай продукт."));
      return;
    }
    if (!operatorKey()) {
      panel.append(
        node(
          "p",
          "outreach-autosend-muted",
          "Введи operator key в блоке Autonomy, чтобы управлять delegation.",
        ),
      );
      return;
    }
    if (!state) {
      panel.append(node("p", "outreach-autosend-muted", "Состояние ещё не загружено."));
      return;
    }

    panel.append(renderState());
    if (shouldOfferDelegation()) panel.append(renderDelegationForm());
  }

  function renderState() {
    const delegation = state.delegation;
    const wrap = node("div", "outreach-autosend-grid");

    const status = node("div", "outreach-autosend-card");
    status.append(node("span", "outreach-autosend-label", "Delegation"));
    if (!delegation) {
      status.append(
        node("strong", "outreach-autosend-warn", "Не выдана"),
        node("small", "", "Автономная отправка выключена. Подготовка draft может продолжаться."),
      );
    } else {
      status.append(
        node(
          "strong",
          state.valid ? "outreach-autosend-ok" : "outreach-autosend-warn",
          `${delegation.status}${state.valid ? " · CURRENT" : " · BLOCKED"}`,
        ),
        node(
          "small",
          "",
          `delegation v${delegation.version} · policy v${delegation.outreach_policy_version} · mandate v${delegation.growth_mandate_version}`,
        ),
      );
    }

    const limits = node("div", "outreach-autosend-card");
    limits.append(node("span", "outreach-autosend-label", "Жёсткие лимиты"));
    const daily = delegation
      ? delegation.max_initial_sends_per_day
      : policy && policy.max_initial_sends_per_day;
    const perDomain = delegation
      ? delegation.max_initial_sends_per_domain_per_day
      : policy && policy.max_initial_sends_per_domain_per_day;
    limits.append(
      node("strong", "", daily ? `≤ ${daily} initial / день` : "Лимиты не настроены"),
      node("small", "", perDomain ? `≤ ${perDomain} / contact-domain / день` : "—"),
      node("small", "", "Follow-up: 0 · blind retry: запрещён"),
    );

    const sender = node("div", "outreach-autosend-card");
    sender.append(node("span", "outreach-autosend-label", "Pinned sender"));
    if (delegation) {
      sender.append(
        node("strong", "", delegation.sender_name),
        node("small", "", delegation.sender_email),
        node("small", "", `Reply-To: ${delegation.reply_to}`),
      );
    } else {
      sender.append(node("strong", "", "Будет зафиксирован при delegation"));
    }
    wrap.append(status, limits, sender);

    if (state.blockers && state.blockers.length) {
      const blockers = node("div", "outreach-autosend-blockers");
      blockers.append(node("strong", "", "Почему auto-send сейчас не выполняется"));
      const list = node("ul");
      state.blockers.forEach((item) => list.append(node("li", "", item)));
      blockers.append(list);
      wrap.append(blockers);
    }

    if (delegation && delegation.status !== "REVOKED") {
      const controls = node("div", "outreach-autosend-actions");
      if (delegation.status === "ACTIVE") {
        controls.append(button("Поставить на паузу", "pause"));
      } else if (delegation.status === "PAUSED") {
        controls.append(button("Возобновить", "resume"));
      }
      controls.append(button("Отозвать delegation", "revoke"));
      wrap.append(controls);
    }
    return wrap;
  }

  function shouldOfferDelegation() {
    if (!state.delegation) return true;
    if (state.delegation.status === "REVOKED") return true;
    return state.delegation.status === "ACTIVE" && !state.valid;
  }

  function renderDelegationForm() {
    const form = node("form", "outreach-autosend-form");
    const title = state.delegation
      ? "Обновить delegation под текущие policy / mandate / sender"
      : "Выдать bounded delegation";
    form.append(node("strong", "", title));

    const check = node("label", "outreach-autosend-check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "confirm_autonomous_initial_send";
    input.required = true;
    check.append(
      input,
      node(
        "span",
        "",
        "Я явно разрешаю Partizan автоматически отправлять только initial outreach-письма в текущих лимитах. Это не разрешает follow-up, массовую рассылку или отправку вне Growth Mandate.",
      ),
    );
    const submit = node("button", "button button-primary", "Разрешить bounded auto-send");
    submit.type = "submit";
    submit.disabled = busy;
    form.append(
      check,
      node(
        "p",
        "outreach-autosend-help",
        "Изменение Outreach Policy, Growth Mandate или sender identity автоматически инвалидирует delegation до нового явного подтверждения.",
      ),
      submit,
    );
    return form;
  }

  function button(label, action) {
    const control = node("button", "button button-ghost", label);
    control.type = "button";
    control.dataset.outreachAutosendAction = action;
    control.disabled = busy;
    return control;
  }

  async function load() {
    const id = productId();
    if (!id || !operatorKey() || busy) {
      render();
      return;
    }
    busy = true;
    message = "";
    messageType = "";
    render();
    try {
      [state, policy] = await Promise.all([
        api(`/v1/products/${id}/outreach-autosend/state`),
        optional(`/v1/products/${id}/outreach-policy`),
      ]);
    } catch (error) {
      message = error.status === 401
        ? "Operator key не принят."
        : error.message || "Не удалось загрузить auto-send state.";
      messageType = "error";
    } finally {
      busy = false;
      render();
    }
  }

  async function handleClick(event) {
    const control = event.target.closest("[data-outreach-autosend-action]");
    if (!control || busy) return;
    const action = control.dataset.outreachAutosendAction;
    if (action === "refresh") {
      await load();
      return;
    }
    if (action === "revoke" && !window.confirm("Полностью отозвать auto-send delegation?")) {
      return;
    }
    const status = action === "pause" ? "PAUSED" : action === "resume" ? "ACTIVE" : "REVOKED";
    busy = true;
    message = "";
    render();
    try {
      await api(`/v1/products/${productId()}/outreach-autosend/status`, {
        method: "POST",
        body: { status },
      });
      message = status === "ACTIVE"
        ? "Delegation возобновлена."
        : status === "PAUSED"
          ? "Delegation поставлена на паузу."
          : "Delegation отозвана.";
      messageType = "success";
    } catch (error) {
      message = error.message || "Не удалось изменить delegation.";
      messageType = "error";
    } finally {
      busy = false;
      await load();
    }
  }

  async function handleSubmit(event) {
    if (!event.target.matches("form.outreach-autosend-form") || busy) return;
    event.preventDefault();
    const form = event.target;
    if (!form.elements.confirm_autonomous_initial_send.checked) {
      message = "Нужно явное подтверждение bounded auto-send.";
      messageType = "error";
      render();
      return;
    }
    busy = true;
    message = "";
    render();
    try {
      await api(`/v1/products/${productId()}/outreach-autosend/delegate`, {
        method: "POST",
        body: { confirm_autonomous_initial_send: true },
      });
      message = "Bounded auto-send delegation зафиксирована.";
      messageType = "success";
    } catch (error) {
      message = error.message || "Delegation не создана.";
      messageType = "error";
    } finally {
      busy = false;
      await load();
    }
  }

  function bindLifecycle() {
    window.addEventListener("partizan:autonomy-updated", () => window.setTimeout(load, 120));
    window.addEventListener("partizan:execution-updated", () => window.setTimeout(load, 120));
    const keyInput = $("#autonomy-operator-key");
    if (keyInput) keyInput.addEventListener("change", () => window.setTimeout(load, 0));
  }

  render();
  bindLifecycle();
  window.setTimeout(load, 260);
})();
