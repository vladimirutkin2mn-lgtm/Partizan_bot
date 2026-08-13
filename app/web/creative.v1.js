(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";

  let items = [];
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

  function operatorHeaders() {
    const key = operatorKey();
    return key ? { [OPERATOR_HEADER]: key } : {};
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

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function button(label, action, actionId, assetId = "", disabled = false) {
    const control = node("button", "button button-ghost", label);
    control.type = "button";
    control.dataset.creativeAction = action;
    control.dataset.actionId = actionId || "";
    control.dataset.assetId = assetId || "";
    control.disabled = disabled;
    return control;
  }

  function ensurePanel() {
    const autonomy = $("#autonomy-panel");
    const stage = $("#stage-results");
    if (!stage) return null;
    let panel = $("#creative-panel");
    if (panel) return panel;
    panel = node("section", "creative-panel");
    panel.id = "creative-panel";
    panel.addEventListener("click", handleClick);
    if (autonomy && autonomy.nextSibling) {
      stage.insertBefore(panel, autonomy.nextSibling);
    } else {
      stage.append(panel);
    }
    return panel;
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;
    panel.replaceChildren();

    const head = node("div", "creative-head");
    const copy = node("div");
    copy.append(
      node("span", "section-kicker", "Creative Lab"),
      node("h3", "", "Что Partizan собирается показать людям"),
      node(
        "p",
        "",
        "Здесь виден креатив конкретного эксперимента, его brief и готовность к площадке. Генерация не запускает рекламный spend.",
      ),
    );
    const refresh = button("Обновить креативы", "refresh", "", "", busy);
    refresh.className = "button button-ghost";
    head.append(copy, refresh);
    panel.append(head);

    if (message) {
      panel.append(node("div", `creative-alert ${messageType}`.trim(), message));
    }

    if (!productId()) {
      panel.append(node("p", "creative-muted", "Сначала создай продукт."));
      return;
    }
    if (!items.length) {
      panel.append(
        node(
          "p",
          "creative-muted",
          "Креативов пока нет. Они появятся после подготовки paid/organic video действия.",
        ),
      );
      return;
    }

    const grid = node("div", "creative-grid");
    items.forEach((item) => grid.append(renderCard(item)));
    panel.append(grid);
  }

  function renderCard(item) {
    const readiness = item.readiness;
    const brief = readiness.brief;
    const selected = readiness.selected_asset;
    const card = node("article", "creative-card");

    const head = node("div", "creative-card-head");
    const title = node("div");
    title.append(
      node("strong", "", `${brief.platform} · ${brief.media_type}`),
      node("small", "", `action ${shortId(brief.action_id)} · brief ${shortId(brief.id)}`),
    );
    head.append(
      title,
      node(
        "span",
        `creative-status status-${readiness.status.toLowerCase()}`,
        readiness.status === "READY" ? "ГОТОВО" : "НЕ ГОТОВО",
      ),
    );
    card.append(head);

    if (selected && selected.public_url && selected.media_type === "IMAGE") {
      const preview = document.createElement("img");
      preview.className = "creative-preview";
      preview.src = selected.public_url;
      preview.alt = `Creative ${brief.platform}`;
      preview.loading = "lazy";
      card.append(preview);
    } else if (selected && selected.public_url && selected.media_type === "VIDEO") {
      const preview = document.createElement("video");
      preview.className = "creative-preview";
      preview.src = selected.public_url;
      preview.controls = true;
      preview.preload = "metadata";
      card.append(preview);
    } else {
      const placeholder = node("div", "creative-preview creative-placeholder");
      placeholder.append(
        node("strong", "", brief.media_type === "VIDEO" ? "Видео ещё не готово" : "Изображение ещё не готово"),
        node("span", "", (readiness.reasons || [])[0] || "Нет provider-ready asset."),
      );
      card.append(placeholder);
    }

    card.append(renderBrief(brief));

    if (selected) {
      const meta = node("div", "creative-meta");
      meta.append(
        metaRow("Источник", selected.source),
        metaRow("Размер", dimensions(selected)),
        metaRow("Модель", selected.provenance && selected.provenance.model ? selected.provenance.model : "—"),
        metaRow("Обновлён", formatDate(selected.updated_at)),
      );
      card.append(meta);
    }

    if (readiness.reasons && readiness.reasons.length) {
      const reasons = node("div", "creative-reasons");
      reasons.append(node("strong", "", "Что мешает запуску"));
      readiness.reasons.slice(0, 4).forEach((reason) => reasons.append(node("p", "", reason)));
      card.append(reasons);
    }

    const actions = node("div", "creative-actions");
    if (selected) {
      actions.append(
        button("Перегенерировать", "regenerate", brief.action_id, selected.id, busy),
        button("Убрать креатив", "retire", brief.action_id, selected.id, busy),
      );
    } else {
      actions.append(button("Сгенерировать", "generate", brief.action_id, "", busy));
    }
    card.append(actions);
    return card;
  }

  function renderBrief(brief) {
    const box = node("div", "creative-brief");
    box.append(node("strong", "", "Задача для креатива"));
    const content = brief.content || {};
    const hook = content.message_hook || content.script_or_brief || content.content_text;
    const value = content.value_proposition;
    const audience = audienceText(content.audience);
    if (hook) box.append(labeledText("Хук", hook));
    if (value) box.append(labeledText("Ценность", value));
    if (audience) box.append(labeledText("Аудитория", audience));
    if (brief.constraints && brief.constraints.length) {
      box.append(labeledText("Ограничения", brief.constraints.slice(0, 3).join(" · ")));
    }
    return box;
  }

  function labeledText(label, value) {
    const row = node("p");
    row.append(node("span", "", `${label}: `), document.createTextNode(String(value)));
    return row;
  }

  function metaRow(label, value) {
    const row = node("div");
    row.append(node("span", "", label), node("strong", "", value || "—"));
    return row;
  }

  function dimensions(asset) {
    const size = asset.width && asset.height ? `${asset.width}×${asset.height}` : "—";
    if (asset.duration_seconds) return `${size} · ${asset.duration_seconds}s`;
    return size;
  }

  function audienceText(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.join(", ");
    const pieces = Object.values(value)
      .filter((item) => typeof item === "string" || typeof item === "number")
      .slice(0, 5);
    return pieces.join(" · ");
  }

  async function load() {
    const id = productId();
    if (!id || busy) return;
    busy = true;
    message = "";
    render();
    try {
      const [assets, overview] = await Promise.all([
        api(`/v1/products/${id}/creative-assets`),
        api(`/v1/products/${id}/autonomy-overview?timeline_limit=5`),
      ]);
      const actionIds = new Set();
      (assets || []).forEach((asset) => actionIds.add(asset.action_id));
      [...(overview.running_experiments || []), ...(overview.waiting_approval || [])]
        .forEach((entry) => {
          if (entry.action_id) actionIds.add(entry.action_id);
        });

      const readinessRows = await Promise.all(
        Array.from(actionIds).map(async (actionId) => {
          try {
            return await api(`/v1/distribution-actions/${actionId}/creative-readiness`);
          } catch (error) {
            if (error.status === 409 || error.status === 404) return null;
            throw error;
          }
        }),
      );
      items = readinessRows
        .filter(Boolean)
        .sort((a, b) => String(a.brief.created_at).localeCompare(String(b.brief.created_at)) * -1)
        .map((readiness) => ({ readiness }));
    } catch (error) {
      items = [];
      message = humanizeError(error);
      messageType = "error";
    } finally {
      busy = false;
      render();
    }
  }

  async function handleClick(event) {
    const control = event.target.closest("[data-creative-action]");
    if (!control || busy) return;
    const action = control.dataset.creativeAction;
    if (action === "refresh") return load();
    const actionId = control.dataset.actionId;
    const assetId = control.dataset.assetId;
    if (!actionId) return;

    busy = true;
    message = "";
    render();
    try {
      if (action === "retire" && assetId) {
        await api(`/v1/creative-assets/${assetId}/retire`, { method: "POST" });
        message = "Креатив убран. Autonomous paid staging остановится, пока не появится READY asset.";
      }
      if (action === "generate") {
        const generated = await api(`/v1/distribution-actions/${actionId}/creative-generate`, {
          method: "POST",
        });
        message = generationMessage(generated);
      }
      if (action === "regenerate" && assetId) {
        await api(`/v1/creative-assets/${assetId}/retire`, { method: "POST" });
        const generated = await api(`/v1/distribution-actions/${actionId}/creative-generate`, {
          method: "POST",
        });
        message = generationMessage(generated);
      }
      messageType = "success";
    } catch (error) {
      message = humanizeError(error);
      messageType = "error";
    } finally {
      busy = false;
      await load();
    }
  }

  function generationMessage(result) {
    if (!result) return "Генерация завершена.";
    if (result.outcome === "READY") return "Новый provider-ready креатив готов.";
    if (result.outcome === "UNAVAILABLE") return result.message || "Creative provider не настроен.";
    return result.message || "Креатив не удалось подготовить.";
  }

  function humanizeError(error) {
    if (error && error.status === 401) {
      return "Нужен operator key. Введи его в блоке Autonomous Growth выше и нажми «Обновить креативы».";
    }
    if (error && error.status === 503) return "Backend operator auth не настроен.";
    return String(error && error.message ? error.message : error || "Creative workspace error");
  }

  function shortId(value) {
    return value ? String(value).slice(0, 8) : "—";
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
      if (progress && progress.dataset.step === "results") {
        window.setTimeout(load, 50);
      }
      const autonomyAction = event.target.closest("[data-autonomy-action]");
      if (autonomyAction && ["refresh", "sweep"].includes(autonomyAction.dataset.autonomyAction)) {
        window.setTimeout(load, 500);
      }
      const reset = event.target.closest("#reset-workspace");
      if (reset) {
        items = [];
        message = "";
        render();
      }
    });
    window.addEventListener("partizan:execution-updated", () => window.setTimeout(load, 100));
  }

  render();
  bindLifecycle();
  window.setTimeout(load, 100);
})();
