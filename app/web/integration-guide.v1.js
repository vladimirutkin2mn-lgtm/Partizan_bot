(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";
  let currentProductId = null;
  let guide = null;
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

  function ensureCard() {
    const panel = document.querySelector("#conversion-integration-panel");
    if (!panel) return null;
    let card = document.querySelector("#integration-guide-card");
    if (!card) {
      card = node("section", "integration-card integration-guide-card");
      card.id = "integration-guide-card";
      panel.append(card);
    }
    return card;
  }

  function codeBlock(label, content) {
    const wrapper = node("div", "integration-guide-code");
    const header = node("div", "integration-guide-code-header");
    header.append(node("strong", "", label));
    const copy = node("button", "button button-ghost", "Копировать");
    copy.type = "button";
    copy.dataset.integrationGuideCopy = label.toLowerCase();
    copy.dataset.integrationGuideText = content;
    header.append(copy);
    const pre = node("pre", "");
    const code = node("code", "", content);
    pre.append(code);
    wrapper.append(header, pre);
    return wrapper;
  }

  function renderList(title, items) {
    const wrapper = node("div", "integration-guide-list");
    wrapper.append(node("strong", "", title));
    const list = node("ul", "");
    for (const item of items || []) list.append(node("li", "", item));
    wrapper.append(list);
    return wrapper;
  }

  function render() {
    const card = ensureCard();
    if (!card) return;
    const id = productId();
    if (id !== currentProductId) {
      currentProductId = id;
      guide = null;
      message = "";
    }
    card.replaceChildren();
    card.append(
      node("h4", "", "Код для подключения продукта"),
      node(
        "p",
        "",
        "Partizan генерирует шаблоны под текущий product_id. Секрет Event Key в них не вставляется: он остаётся переменной окружения продукта.",
      ),
    );

    if (!id) {
      card.append(node("p", "integration-muted", "Сначала создай продукт."));
      return;
    }

    const actions = node("div", "integration-actions");
    const load = node(
      "button",
      "button button-ghost",
      busy ? "Готовлю…" : "Показать готовый код",
    );
    load.type = "button";
    load.disabled = busy;
    load.dataset.integrationGuideLoad = "true";
    actions.append(load);
    card.append(actions);

    if (message) card.append(node("p", "integration-guide-message", message));
    if (!guide) return;

    const meta = node("p", "integration-muted");
    meta.textContent = guide.public_base_configured
      ? `Partizan URL: ${guide.base_url}`
      : "Публичный Partizan URL ещё не настроен — в примерах оставлен безопасный placeholder.";
    card.append(meta);

    card.append(
      renderList("Чек-лист подключения", guide.checklist),
      renderList("Надёжная доставка через outbox", guide.outbox_guidance),
      codeBlock("cURL", guide.snippets.curl),
      codeBlock("Python", guide.snippets.python),
      codeBlock("Node.js", guide.snippets.node),
    );
  }

  async function loadGuide() {
    const id = productId();
    if (!id || busy) return;
    busy = true;
    message = "";
    render();
    const headers = { Accept: "application/json" };
    const operatorKey = liveOperatorKey();
    if (operatorKey) headers[OPERATOR_HEADER] = operatorKey;
    try {
      const response = await fetch(`/v1/products/${id}/integration-guide`, { headers });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          response.status === 401 ? "Нужен operator key." : payload.detail || `HTTP ${response.status}`,
        );
      }
      guide = payload;
      message = "Шаблоны созданы для текущего продукта.";
    } catch (error) {
      guide = null;
      message = String(error && error.message ? error.message : error);
    } finally {
      busy = false;
      render();
    }
  }

  async function copySnippet(button) {
    const text = button.dataset.integrationGuideText || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const previous = button.textContent;
      button.textContent = "Скопировано";
      window.setTimeout(() => {
        button.textContent = previous;
      }, 1200);
    } catch (_) {
      message = "Не удалось скопировать автоматически — выдели код вручную.";
      render();
    }
  }

  document.addEventListener("click", (event) => {
    const load = event.target.closest("[data-integration-guide-load]");
    if (load) {
      loadGuide();
      return;
    }
    const copy = event.target.closest("[data-integration-guide-copy]");
    if (copy) copySnippet(copy);
  });

  const observer = new MutationObserver(() => {
    if (
      document.querySelector("#conversion-integration-panel") &&
      !document.querySelector("#integration-guide-card")
    ) {
      render();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  render();
})();
