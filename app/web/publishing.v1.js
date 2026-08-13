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
    let panel = $("#publishing-panel");
    if (panel) return panel;
    panel = node("section", "publishing-panel");
    panel.id = "publishing-panel";
    panel.addEventListener("click", handleClick);
    panel.addEventListener("submit", handleSubmit);
    const creative = $("#creative-panel");
    if (creative && creative.nextSibling) {
      stage.insertBefore(panel, creative.nextSibling);
    } else {
      stage.append(panel);
    }
    return panel;
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;
    panel.replaceChildren();

    const head = node("div", "publishing-head");
    const copy = node("div");
    copy.append(
      node("span", "section-kicker", "Publishing"),
      node("h3", "", "Разрешение на публикацию"),
      node(
        "p",
        "",
        "Partizan сам готовит ролик и продолжает после согласия. Здесь ты контролируешь точный аккаунт, текст, приватность и обязательные настройки TikTok.",
      ),
    );
    const refresh = actionButton("Обновить", "refresh", "");
    refresh.disabled = busy;
    head.append(copy, refresh);
    panel.append(head);

    if (message) {
      panel.append(node("div", `publishing-alert ${messageType}`.trim(), message));
    }

    if (!productId()) {
      panel.append(node("p", "publishing-muted", "Сначала создай продукт."));
      return;
    }
    if (!items.length) {
      panel.append(
        node(
          "p",
          "publishing-muted",
          "TikTok organic video, готовых к permissioned publishing, пока нет.",
        ),
      );
      return;
    }

    const grid = node("div", "publishing-grid");
    items.forEach((item) => grid.append(renderCard(item)));
    panel.append(grid);
  }

  function renderCard(item) {
    const readiness = item.readiness;
    const asset = readiness.selected_asset;
    const card = node("article", "publishing-card");
    const media = node("div");
    if (asset && asset.public_url) {
      const video = document.createElement("video");
      video.className = "publishing-preview";
      video.src = asset.public_url;
      video.controls = true;
      video.preload = "metadata";
      media.append(video);
    } else {
      media.append(node("div", "publishing-preview", "Видео недоступно"));
    }
    media.append(
      node(
        "p",
        "publishing-ids",
        `action ${shortId(readiness.action_id)} · asset ${asset ? shortId(asset.id) : "—"}`,
      ),
    );

    const body = node("div", "publishing-body");
    const head = node("div", "publishing-card-head");
    const title = node("div");
    title.append(
      node("h4", "", "TikTok · organic video"),
      node("small", "", asset ? `Источник: ${asset.source}` : "Нет READY asset"),
    );
    head.append(title, statusBadge(item));
    body.append(head);

    if (item.loadError) {
      body.append(node("div", "publishing-warning", item.loadError));
      body.append(actionButton("Получить данные TikTok", "refresh-preflight", readiness.action_id));
      card.append(media, body);
      return card;
    }

    if (item.preflight) body.append(renderAccount(item.preflight));
    body.append(renderProviderState(item));

    if (needsAuthorization(item)) {
      if (item.preflight) {
        body.append(renderAuthorizationForm(item));
      } else {
        const block = node("div", "publishing-warning");
        block.append(
          node("strong", "", "Нужны актуальные настройки creator account"),
          node("p", "", "Partizan запросит текущие privacy и interaction options у TikTok до согласия."),
          actionButton("Получить данные TikTok", "refresh-preflight", readiness.action_id),
        );
        body.append(block);
      }
    } else if (item.authorization && item.authorization.status === "AUTHORIZED") {
      body.append(renderAuthorizationSummary(item));
    }

    body.append(renderControls(item));
    card.append(media, body);
    return card;
  }

  function renderAccount(preflight) {
    const block = node("div", "publishing-account");
    block.append(
      node("strong", "", preflight.creator_nickname),
      node("p", "", `@${preflight.creator_username}`),
      node(
        "small",
        "",
        `Creator info действует до ${formatDate(preflight.expires_at)}. Privacy выбирается только из актуального списка TikTok.`,
      ),
    );
    return block;
  }

  function renderProviderState(item) {
    const block = node("div", "publishing-summary");
    const attempt = item.attempt;
    const reconciliation = item.reconciliation;
    if (reconciliation && reconciliation.status === "PUBLISHED") {
      block.className = "publishing-success";
      block.append(node("strong", "", "TikTok подтвердил публикацию"));
      block.append(
        node("p", "", `publish_id: ${reconciliation.provider_publish_id}`),
      );
      if (reconciliation.public_post_ids && reconciliation.public_post_ids.length) {
        block.append(
          node("p", "", `public post id: ${reconciliation.public_post_ids.join(", ")}`),
        );
      }
      return block;
    }
    if (reconciliation && reconciliation.status === "FAILED") {
      block.className = "publishing-warning";
      block.append(
        node("strong", "", "Публикация не завершилась"),
        node("p", "", reconciliation.fail_reason || "TikTok вернул FAILED."),
      );
      return block;
    }
    if (attempt && attempt.status === "RECONCILIATION_REQUIRED") {
      block.className = "publishing-warning";
      block.append(
        node("strong", "", "Нужна сверка с TikTok"),
        node(
          "p",
          "",
          attempt.provider_publish_id
            ? "Есть реальный publish_id — можно безопасно проверить статус."
            : "Результат внешнего вызова неоднозначен и publish_id не подтверждён. Partizan не будет публиковать повторно.",
        ),
      );
      return block;
    }
    if (attempt && attempt.provider_publish_id) {
      block.append(
        node("strong", "", "TikTok обрабатывает публикацию"),
        node("p", "", `publish_id: ${attempt.provider_publish_id}`),
      );
      return block;
    }
    if (item.authorization && item.authorization.status === "AUTHORIZED") {
      block.append(
        node("strong", "", "Согласие зафиксировано"),
        node("p", "", "Автономный worker продолжит публикацию точного разрешённого ролика."),
      );
      return block;
    }
    block.append(
      node("strong", "", "Публикация ещё не разрешена"),
      node("p", "", "До твоего согласия Partizan не отправит ролик в TikTok."),
    );
    return block;
  }

  function renderAuthorizationForm(item) {
    const preflight = item.preflight;
    const asset = item.readiness.selected_asset;
    const form = node("form", "publishing-form");
    form.dataset.actionId = item.readiness.action_id;
    form.dataset.preflightId = preflight.id;
    form.dataset.generatedAsset = asset && asset.source === "GENERATED" ? "1" : "0";

    const caption = node("label", "publishing-field");
    caption.append(node("span", "", "Текст публикации"));
    const textarea = document.createElement("textarea");
    textarea.name = "title";
    textarea.maxLength = 2200;
    textarea.placeholder = "Текст, который увидит пользователь в TikTok";
    textarea.value = suggestedTitle(item.readiness.brief);
    caption.append(textarea);

    const privacy = node("label", "publishing-field");
    privacy.append(node("span", "", "Приватность · выбери сам"));
    const select = document.createElement("select");
    select.name = "privacy_level";
    select.required = true;
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "Выбери уровень приватности";
    empty.selected = true;
    select.append(empty);
    (preflight.privacy_level_options || []).forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = privacyLabel(value);
      select.append(option);
    });
    privacy.append(select);

    const interactions = fieldset("Взаимодействия · по умолчанию выключены");
    interactions.append(
      checkbox("allow_comment", "Разрешить комментарии", false, preflight.comment_disabled),
      checkbox("allow_duet", "Разрешить Duet", false, preflight.duet_disabled),
      checkbox("allow_stitch", "Разрешить Stitch", false, preflight.stitch_disabled),
    );

    const commercial = fieldset("Коммерческий контент");
    commercial.append(
      checkbox("commercial_content_enabled", "Включить disclosure коммерческого контента"),
      checkbox("brand_organic_toggle", "Продвигаю свой бренд / продукт"),
      checkbox("brand_content_toggle", "Branded Content / сторонний бренд"),
      checkbox("branded_content_policy_accepted", "Я принимаю Branded Content Policy для этого поста"),
    );

    const disclosures = fieldset("Обязательные подтверждения");
    const generated = asset && asset.source === "GENERATED";
    disclosures.append(
      checkbox("is_aigc", "Пометить как AI-generated content", generated, generated),
      checkbox("music_usage_confirmation_accepted", "Я подтверждаю Music Usage Confirmation"),
      checkbox("explicit_publish_consent", "Я разрешаю опубликовать именно этот ролик с этими настройками"),
    );

    const submit = node("button", "button button-primary", "Разрешить публикацию");
    submit.type = "submit";
    submit.disabled = busy;
    form.append(
      caption,
      privacy,
      interactions,
      commercial,
      disclosures,
      node("p", "publishing-help", "После разрешения браузер не публикует сам: autonomous worker использует одноразовую authorization и продолжает безопасный provider flow."),
      submit,
    );
    return form;
  }

  function renderAuthorizationSummary(item) {
    const auth = item.authorization;
    const block = node("div", "publishing-success");
    block.append(
      node("strong", "", "Разрешение активно"),
      node("p", "", `${privacyLabel(auth.privacy_level)} · AIGC ${auth.is_aigc ? "да" : "нет"}`),
      node("small", "", `Действует до ${formatDate(auth.expires_at)} · authorization ${shortId(auth.id)}`),
    );
    return block;
  }

  function renderControls(item) {
    const actions = node("div", "publishing-actions");
    const actionId = item.readiness.action_id;
    if (item.authorization && item.authorization.status === "AUTHORIZED" && !item.attempt) {
      actions.append(actionButton("Отозвать разрешение", "revoke", actionId));
    }
    if (item.attempt && item.attempt.provider_publish_id) {
      actions.append(actionButton("Проверить статус TikTok", "reconcile", actionId));
    }
    actions.append(actionButton("Обновить creator info", "refresh-preflight", actionId));
    Array.from(actions.querySelectorAll("button")).forEach((control) => {
      control.disabled = busy;
    });
    return actions;
  }

  function fieldset(legendText) {
    const group = node("fieldset", "publishing-group");
    group.append(node("legend", "", legendText));
    return group;
  }

  function checkbox(name, label, checked = false, disabled = false) {
    const wrap = node("label", "publishing-check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = name;
    input.checked = Boolean(checked);
    input.disabled = Boolean(disabled);
    wrap.append(input, node("span", "", label));
    return wrap;
  }

  function actionButton(label, action, actionId) {
    const control = node("button", "button button-ghost", label);
    control.type = "button";
    control.dataset.publishingAction = action;
    control.dataset.actionId = actionId || "";
    return control;
  }

  function statusBadge(item) {
    const reconciliation = item.reconciliation;
    const attempt = item.attempt;
    let state = "waiting";
    let label = "ЖДЁТ СОГЛАСИЯ";
    if (reconciliation && reconciliation.status === "PUBLISHED") {
      state = "published";
      label = "ОПУБЛИКОВАНО";
    } else if (reconciliation && reconciliation.status === "FAILED") {
      state = "failed";
      label = "ОШИБКА";
    } else if (attempt && attempt.status === "RECONCILIATION_REQUIRED") {
      state = "failed";
      label = "НУЖНА СВЕРКА";
    } else if (attempt && attempt.provider_publish_id) {
      state = "processing";
      label = "ОБРАБАТЫВАЕТСЯ";
    } else if (item.authorization && item.authorization.status === "AUTHORIZED") {
      state = "processing";
      label = "РАЗРЕШЕНО";
    }
    return node("span", `publishing-badge ${state}`, label);
  }

  function needsAuthorization(item) {
    if (item.reconciliation && item.reconciliation.status === "PUBLISHED") return false;
    if (item.attempt) return false;
    return !item.authorization || item.authorization.status !== "AUTHORIZED";
  }

  async function load() {
    const id = productId();
    if (!id || busy) {
      render();
      return;
    }
    busy = true;
    message = "";
    messageType = "";
    render();
    try {
      const assets = await api(`/v1/products/${id}/creative-assets`);
      const actionIds = Array.from(
        new Set(
          (assets || [])
            .filter(
              (asset) =>
                asset.platform === "TIKTOK" &&
                asset.media_type === "VIDEO" &&
                asset.purpose === "ORGANIC_VIDEO" &&
                asset.action_id,
            )
            .map((asset) => asset.action_id),
        ),
      );
      const loaded = [];
      for (const actionId of actionIds) {
        try {
          const readiness = await api(
            `/v1/distribution-actions/${actionId}/creative-readiness`,
          );
          if (
            readiness.status !== "READY" ||
            !readiness.selected_asset ||
            readiness.brief.platform !== "TIKTOK" ||
            readiness.brief.purpose !== "ORGANIC_VIDEO"
          ) {
            continue;
          }
          loaded.push(await loadPublishingState(readiness));
        } catch (error) {
          loaded.push({
            readiness: {
              action_id: actionId,
              brief: { platform: "TIKTOK", purpose: "ORGANIC_VIDEO" },
              selected_asset: null,
            },
            loadError: error.message,
          });
        }
      }
      items = loaded;
    } catch (error) {
      message = error.message || "Не удалось загрузить Publishing.";
      messageType = "error";
    } finally {
      busy = false;
      render();
    }
  }

  async function loadPublishingState(readiness) {
    const actionId = readiness.action_id;
    const base = `/v1/distribution-actions/${actionId}/owned-publishing/tiktok`;
    const [preflight, authorization, attempt, reconciliation] = await Promise.all([
      optional(`${base}/preflight`),
      optional(`${base}/authorization`),
      optional(`${base}/direct-post`),
      optional(`${base}/direct-post/reconciliation`),
    ]);
    return { readiness, preflight, authorization, attempt, reconciliation };
  }

  async function handleClick(event) {
    const control = event.target.closest("[data-publishing-action]");
    if (!control || busy) return;
    const action = control.dataset.publishingAction;
    const actionId = control.dataset.actionId;
    if (action === "refresh") {
      await load();
      return;
    }
    if (!actionId) return;
    busy = true;
    message = "";
    render();
    try {
      const base = `/v1/distribution-actions/${actionId}/owned-publishing/tiktok`;
      if (action === "refresh-preflight") {
        await api(`${base}/preflight`, { method: "POST" });
        message = "Актуальные настройки creator account получены из TikTok.";
        messageType = "success";
      } else if (action === "revoke") {
        await api(`${base}/authorization/revoke`, { method: "POST" });
        message = "Разрешение на публикацию отозвано.";
        messageType = "success";
      } else if (action === "reconcile") {
        await api(`${base}/direct-post/reconcile`, { method: "POST" });
        message = "Статус TikTok обновлён.";
        messageType = "success";
      }
    } catch (error) {
      message = error.message || "Операция не выполнена.";
      messageType = "error";
    } finally {
      busy = false;
      await load();
    }
  }

  async function handleSubmit(event) {
    if (!event.target.matches("form.publishing-form") || busy) return;
    event.preventDefault();
    const form = event.target;
    const actionId = form.dataset.actionId;
    const preflightId = form.dataset.preflightId;
    const privacy = form.elements.privacy_level.value;
    if (!privacy) {
      message = "Выбери приватность из актуального списка TikTok.";
      messageType = "error";
      render();
      return;
    }
    if (!form.elements.music_usage_confirmation_accepted.checked) {
      message = "Нужно подтвердить Music Usage Confirmation.";
      messageType = "error";
      render();
      return;
    }
    if (!form.elements.explicit_publish_consent.checked) {
      message = "Нужно явное согласие на публикацию именно этого ролика.";
      messageType = "error";
      render();
      return;
    }

    const commercial = form.elements.commercial_content_enabled.checked;
    const branded = form.elements.brand_content_toggle.checked;
    if (commercial && !form.elements.brand_organic_toggle.checked && !branded) {
      message = "Для commercial content выбери свой бренд, Branded Content или оба варианта.";
      messageType = "error";
      render();
      return;
    }
    if (branded && !form.elements.branded_content_policy_accepted.checked) {
      message = "Для Branded Content нужно подтвердить Branded Content Policy.";
      messageType = "error";
      render();
      return;
    }

    busy = true;
    render();
    try {
      const base = `/v1/distribution-actions/${actionId}/owned-publishing/tiktok`;
      await api(`${base}/authorization`, {
        method: "POST",
        body: {
          preflight_id: preflightId,
          title: form.elements.title.value,
          privacy_level: privacy,
          allow_comment: form.elements.allow_comment.checked,
          allow_duet: form.elements.allow_duet.checked,
          allow_stitch: form.elements.allow_stitch.checked,
          commercial_content_enabled: commercial,
          brand_organic_toggle: form.elements.brand_organic_toggle.checked,
          brand_content_toggle: branded,
          is_aigc:
            form.dataset.generatedAsset === "1" || form.elements.is_aigc.checked,
          music_usage_confirmation_accepted:
            form.elements.music_usage_confirmation_accepted.checked,
          branded_content_policy_accepted:
            form.elements.branded_content_policy_accepted.checked,
          explicit_publish_consent: form.elements.explicit_publish_consent.checked,
        },
      });
      message = "Разрешение зафиксировано. Autonomous worker продолжит публикацию.";
      messageType = "success";
    } catch (error) {
      message = error.message || "Не удалось зафиксировать разрешение.";
      messageType = "error";
    } finally {
      busy = false;
      await load();
    }
  }

  function suggestedTitle(brief) {
    const content = (brief && brief.content) || {};
    return content.message_hook || content.value_proposition || "";
  }

  function privacyLabel(value) {
    const labels = {
      PUBLIC_TO_EVERYONE: "Публично для всех",
      MUTUAL_FOLLOW_FRIENDS: "Взаимные подписки / друзья",
      FOLLOWER_OF_CREATOR: "Подписчики creator",
      SELF_ONLY: "Только я",
    };
    return labels[value] || value;
  }

  function shortId(value) {
    const text = String(value || "");
    return text ? text.slice(0, 8) : "—";
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function bindLifecycle() {
    window.addEventListener("partizan:execution-updated", () => {
      window.setTimeout(load, 120);
    });
    window.addEventListener("partizan:autonomy-updated", () => {
      window.setTimeout(load, 120);
    });
  }

  render();
  bindLifecycle();
  window.setTimeout(load, 180);
})();