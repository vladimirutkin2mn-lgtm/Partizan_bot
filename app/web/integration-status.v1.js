(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";
  let currentProductId = null;
  let statusView = null;
  let busy = false;
  let message = "";

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

  function liveOperatorKey() {
    const input = document.querySelector("#integration-operator-key");
    return input ? input.value.trim() : "";
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function badge(label, ok) {
    return node(
      "span",
      `integration-readiness-badge ${ok ? "ok" : "missing"}`,
      `${ok ? "✓" : "○"} ${label}`,
    );
  }

  function ensureCard() {
    const panel = document.querySelector("#conversion-integration-panel");
    if (!panel) return null;
    let card = document.querySelector("#integration-readiness-card");
    if (!card) {
      card = node("section", "integration-card integration-readiness-card");
      card.id = "integration-readiness-card";
      panel.append(card);
    }
    return card;
  }

  function render() {
    const card = ensureCard();
    if (!card) return;
    const id = productId();
    if (id !== currentProductId) {
      currentProductId = id;
      statusView = null;
      message = "";
    }
    card.replaceChildren();
    card.append(
      node("h4", "", "Проверка интеграции"),
      node(
        "p",
        "",
        "Partizan показывает только фактически настроенные зависимости и реально увиденные события. Проверка ничего не отправляет в продукт и не создаёт конверсии.",
      ),
    );

    if (!id) {
      card.append(node("p", "integration-muted", "Сначала создай продукт."));
      return;
    }

    const actions = node("div", "integration-actions");
    const refresh = node(
      "button",
      "button button-ghost",
      busy ? "Проверяю…" : "Проверить интеграцию",
    );
    refresh.type = "button";
    refresh.disabled = busy;
    refresh.dataset.integrationReadinessRefresh = "true";
    actions.append(refresh);
    card.append(actions);

    if (message) card.append(node("p", "integration-readiness-message", message));
    if (!statusView) {
      card.append(node("p", "integration-muted", "Проверка ещё не запускалась."));
      return;
    }

    const config = node("div", "integration-readiness-badges");
    config.append(
      badge("Event Key", statusView.event_key_configured),
      badge("Public tracking", statusView.public_tracking_configured),
      badge("Experiment", statusView.experiment_count > 0),
    );
    card.append(config);

    const funnel = node("div", "integration-readiness-funnel");
    const observed = new Set(statusView.observed_event_types || []);
    for (const eventType of ["VISIT", "SIGNUP", "ACTIVATED", "PAID"]) {
      funnel.append(badge(eventType, observed.has(eventType)));
    }
    card.append(funnel);

    if (statusView.ready_for_attributed_conversions) {
      card.append(
        node(
          "p",
          "integration-readiness-ready",
          "Базовая инфраструктура готова принимать и связывать реальные конверсии.",
        ),
      );
    }

    if (statusView.blockers && statusView.blockers.length) {
      const list = node("ul", "integration-readiness-blockers");
      for (const blocker of statusView.blockers) list.append(node("li", "", blocker));
      card.append(node("strong", "", "Что осталось настроить"), list);
    }

    const counts = statusView.funnel || {};
    card.append(
      node(
        "p",
        "integration-muted",
        `Уже увидено: VISIT ${counts.visits || 0} · SIGNUP ${counts.signups || 0} · ACTIVATED ${counts.activated_users || 0} · PAID ${counts.paid_users || 0}`,
      ),
    );
  }

  async function refresh() {
    const id = productId();
    if (!id || busy) return;
    busy = true;
    message = "";
    render();
    const headers = { Accept: "application/json" };
    const operatorKey = liveOperatorKey();
    if (operatorKey) headers[OPERATOR_HEADER] = operatorKey;
    try {
      const response = await fetch(`/v1/products/${id}/integration-status`, { headers });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          response.status === 401 ? "Нужен operator key." : payload.detail || `HTTP ${response.status}`,
        );
      }
      statusView = payload;
      message = "Статус обновлён по текущему состоянию Partizan.";
    } catch (error) {
      statusView = null;
      message = String(error && error.message ? error.message : error);
    } finally {
      busy = false;
      render();
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-integration-readiness-refresh]");
    if (target) refresh();
  });

  const observer = new MutationObserver(() => {
    if (
      document.querySelector("#conversion-integration-panel") &&
      !document.querySelector("#integration-readiness-card")
    ) {
      render();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  render();
})();
