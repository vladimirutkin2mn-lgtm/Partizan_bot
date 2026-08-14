(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";

  let targets = [];
  let senderReadiness = null;
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

  function actionButton(label, action, id = "") {
    const control = node("button", "button button-ghost", label);
    control.type = "button";
    control.dataset.outreachAction = action;
    control.dataset.id = id;
    control.disabled = busy;
    return control;
  }

  function ensurePanel() {
    const stage = $("#stage-results");
    if (!stage) return null;
    let panel = $("#outreach-panel");
    if (panel) return panel;
    panel = node("section", "outreach-panel");
    panel.id = "outreach-panel";
    panel.addEventListener("click", handleClick);
    panel.addEventListener("submit", handleSubmit);
    const publishing = $("#publishing-panel");
    if (publishing && publishing.nextSibling) {
      stage.insertBefore(panel, publishing.nextSibling);
    } else {
      stage.append(panel);
    }
    return panel;
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;
    panel.replaceChildren();

    const head = node("div", "outreach-head");
    const copy = node("div");
    copy.append(
      node("span", "section-kicker", "Founder Outreach"),
      node("h3", "", "Точечные партнёрства и создатели"),
      node(
        "p",
        "",
        "Здесь видно, кому и почему Partizan предлагает сотрудничество, точный текст письма, sender/policy state, доставку и конверсии. Отправка из браузера намеренно отключена.",
      ),
    );
    head.append(copy, actionButton("Обновить", "refresh"));
    panel.append(head);

    if (message) {
      panel.append(node("div", `outreach-alert ${messageType}`.trim(), message));
    }

    if (!productId()) {
      panel.append(node("p", "outreach-muted", "Сначала создай продукт."));
      return;
    }
    if (!operatorKey()) {
      panel.append(
        node(
          "p",
          "outreach-muted",
          "Введи operator key в блоке Autonomy, чтобы открыть evidence-backed outreach state.",
        ),
      );
      return;
    }

    panel.append(renderControlState());

    if (!targets.length) {
      panel.append(
        node(
          "p",
          "outreach-muted",
          "Evidence-backed outreach targets для этого продукта пока не подготовлены.",
        ),
      );
      return;
    }

    const grid = node("div", "outreach-grid");
    targets.forEach((item) => grid.append(renderTarget(item)));
    panel.append(grid);
  }

  function renderControlState() {
    const strip = node("div", "outreach-control-strip");
    const sender = node("div", "outreach-control-card");
    sender.append(
      node("span", "outreach-label", "Sender"),
      node(
        "strong",
        senderReadiness && senderReadiness.ready ? "outreach-ok" : "outreach-warn",
        senderReadiness && senderReadiness.ready ? "SMTP готов" : "SMTP не готов",
      ),
      node(
        "small",
        "",
        senderReadiness && senderReadiness.from_email
          ? `${senderReadiness.from_name || "Partizan"} · ${senderReadiness.from_email}`
          : "Sender identity не подтверждена",
      ),
    );
    if (senderReadiness && senderReadiness.blockers && senderReadiness.blockers.length) {
      sender.append(node("small", "outreach-warn", senderReadiness.blockers.join(" · ")));
    }

    const policyCard = node("div", "outreach-control-card");
    policyCard.append(
      node("span", "outreach-label", "Policy"),
      node(
        "strong",
        policy && policy.status === "ACTIVE" ? "outreach-ok" : "outreach-warn",
        policy ? policy.status : "Не настроена",
      ),
    );
    if (policy) {
      policyCard.append(
        node(
          "small",
          "",
          `prepare ≤ ${policy.max_prepared_per_day}/день · send ≤ ${policy.max_initial_sends_per_day}/день · domain ≤ ${policy.max_initial_sends_per_domain_per_day}/день`,
        ),
        node(
          "small",
          "",
          `follow-up: ${policy.max_followups} · automatic send: ${policy.automatic_send_enabled ? "включён" : "выключен"}`,
        ),
      );
    }

    const boundary = node("div", "outreach-control-card");
    boundary.append(
      node("span", "outreach-label", "Boundary"),
      node("strong", "outreach-ok", "Review only"),
      node(
        "small",
        "",
        "Workspace умеет сохранить правку, отклонить draft или suppress target. SMTP send endpoint здесь не вызывается.",
      ),
    );
    strip.append(sender, policyCard, boundary);
    return strip;
  }

  function renderTarget(item) {
    const target = item.target;
    const card = node("article", "outreach-card");

    const top = node("div", "outreach-card-head");
    const title = node("div");
    title.append(
      node("span", "outreach-target-type", target.target_type),
      node("h4", "", target.canonical_name),
      node("small", "", `${target.business_email} · confidence ${formatNumber(target.confidence)}%`),
    );
    top.append(title, statusBadge(target.status));
    card.append(top);

    card.append(renderEvidence(target));
    card.append(infoBlock("Почему выбран", target.relevance_rationale));
    card.append(infoBlock("Пересечение с ICP", target.icp_overlap_rationale));

    if (!item.brief) {
      card.append(
        node(
          "div",
          "outreach-empty",
          "Для этого target ещё нет OutreachBrief. Workspace ничего не генерирует и не отправляет сам.",
        ),
      );
    } else {
      card.append(renderBrief(item));
    }

    const controls = node("div", "outreach-actions");
    if (target.status === "ACTIVE") {
      controls.append(actionButton("Suppress target", "suppress", target.id));
    }
    card.append(controls);
    return card;
  }

  function renderEvidence(target) {
    const evidence = target.contact_evidence || {};
    const block = node("div", "outreach-evidence");
    block.append(
      node("span", "outreach-label", "Contact evidence"),
      node("strong", "", evidence.provenance_type || "—"),
    );
    if (evidence.source_label) block.append(node("p", "", evidence.source_label));
    if (evidence.source_excerpt) block.append(node("blockquote", "", evidence.source_excerpt));
    if (evidence.source_url) {
      const link = node("a", "outreach-link", "Открыть источник ↗");
      link.href = evidence.source_url;
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      block.append(link);
    }
    return block;
  }

  function infoBlock(label, text) {
    const block = node("div", "outreach-info");
    block.append(node("span", "outreach-label", label), node("p", "", text || "—"));
    return block;
  }

  function renderBrief(item) {
    const brief = item.brief;
    const wrap = node("div", "outreach-brief");
    const heading = node("div", "outreach-brief-head");
    const title = node("div");
    title.append(
      node("span", "outreach-label", "Offer & exact message"),
      node("strong", "", `${brief.offer_type} · ${brief.status}`),
    );
    heading.append(title, deliveryBadge(item.attempt));
    wrap.append(heading);

    const facts = node("div", "outreach-offer-grid");
    facts.append(
      infoBlock("Предложение", brief.collaboration),
      infoBlock("Ценность получателю", brief.value_to_recipient),
    );
    wrap.append(facts);

    if (brief.status === "DRAFT" && !item.attempt && item.target.status === "ACTIVE") {
      wrap.append(renderEditForm(brief));
      const reviewActions = node("div", "outreach-actions");
      reviewActions.append(actionButton("Отклонить draft", "reject", brief.id));
      wrap.append(reviewActions);
    } else {
      const preview = node("div", "outreach-message-preview");
      preview.append(
        node("strong", "", brief.message_subject),
        node("pre", "", brief.message_body),
      );
      wrap.append(preview);
    }

    wrap.append(renderDelivery(item.attempt));
    wrap.append(renderMetrics(item.analytics));
    wrap.append(
      node(
        "p",
        "outreach-footnote",
        `Tracking сохранён: ${brief.tracking_url} · follow-up policy: ${brief.follow_up_policy}`,
      ),
    );
    return wrap;
  }

  function renderEditForm(brief) {
    const form = node("form", "outreach-edit-form");
    form.dataset.briefId = brief.id;

    const subject = node("label", "outreach-field");
    subject.append(node("span", "", "Subject"));
    const input = document.createElement("input");
    input.name = "message_subject";
    input.maxLength = 180;
    input.required = true;
    input.value = brief.message_subject;
    subject.append(input);

    const body = node("label", "outreach-field");
    body.append(node("span", "", "Текст до tracking link"));
    const textarea = document.createElement("textarea");
    textarea.name = "message_body_without_link";
    textarea.maxLength = 6000;
    textarea.required = true;
    textarea.value = bodyWithoutTracking(brief);
    body.append(textarea);

    const submit = node("button", "button button-primary", "Сохранить правку");
    submit.type = "submit";
    submit.disabled = busy;
    form.append(
      subject,
      body,
      node(
        "p",
        "outreach-help",
        "Tracking URL добавляется сервером и не редактируется. Любой другой URL в теле будет отклонён.",
      ),
      submit,
    );
    return form;
  }

  function renderDelivery(attempt) {
    const block = node("div", "outreach-delivery");
    block.append(node("span", "outreach-label", "Delivery / reconciliation"));
    if (!attempt) {
      block.append(
        node("strong", "", "Не отправлено"),
        node("p", "", "Нет SMTP send attempt. Review workspace не запускает отправку."),
      );
      return block;
    }
    block.append(
      node("strong", "", `${attempt.provider} · ${attempt.status}`),
      node(
        "p",
        "",
        attempt.provider_reference
          ? `provider reference: ${attempt.provider_reference}`
          : attempt.error_detail || "Provider reference ещё не подтверждён.",
      ),
    );
    if (attempt.status === "RECONCILIATION_REQUIRED") {
      block.classList.add("needs-review");
      block.append(
        node(
          "small",
          "",
          "Outcome неоднозначен. Automatic retry остаётся выключен; нужна ручная сверка.",
        ),
      );
    }
    return block;
  }

  function renderMetrics(analytics) {
    const block = node("div", "outreach-metrics");
    block.append(node("span", "outreach-label", "Attributed conversions"));
    if (!analytics || !analytics.metrics) {
      block.append(node("p", "", "Метрики ещё недоступны."));
      return block;
    }
    const metrics = analytics.metrics;
    const row = node("div", "outreach-metric-row");
    [
      ["VISIT", metrics.visits],
      ["SIGNUP", metrics.signups],
      ["ACTIVATED", metrics.activated_users],
      ["PAID", metrics.paid_users],
      ["Revenue", formatMoney(metrics.revenue)],
      ["CAC", metrics.cac === null ? "—" : formatMoney(metrics.cac)],
    ].forEach(([label, value]) => {
      const metric = node("div", "outreach-metric");
      metric.append(node("strong", "", value), node("small", "", label));
      row.append(metric);
    });
    block.append(row);
    return block;
  }

  function statusBadge(status) {
    return node(
      "span",
      `outreach-badge ${String(status || "").toLowerCase()}`,
      status || "UNKNOWN",
    );
  }

  function deliveryBadge(attempt) {
    if (!attempt) return node("span", "outreach-badge draft", "НЕ ОТПРАВЛЕНО");
    const state = String(attempt.status || "").toLowerCase();
    return node("span", `outreach-badge ${state}`, attempt.status);
  }

  function bodyWithoutTracking(brief) {
    const body = String(brief.message_body || "");
    const suffix = `\n\nProduct details: ${brief.tracking_url}`;
    return body.endsWith(suffix) ? body.slice(0, -suffix.length) : body;
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
      const [targetList, readiness, outreachPolicy] = await Promise.all([
        api(`/v1/products/${id}/outreach-targets`),
        api("/v1/outreach/sender-readiness"),
        optional(`/v1/products/${id}/outreach-policy`),
      ]);
      senderReadiness = readiness;
      policy = outreachPolicy;
      const loaded = [];
      for (const target of targetList.targets || []) {
        const list = await api(`/v1/outreach-targets/${target.id}/briefs`);
        const briefs = list.briefs || [];
        const brief = briefs.length ? briefs[briefs.length - 1] : null;
        let attempt = null;
        let analytics = null;
        if (brief) {
          [attempt, analytics] = await Promise.all([
            optional(`/v1/outreach-briefs/${brief.id}/send-attempt`),
            optional(`/v1/distribution-experiments/${brief.experiment_id}/analytics`),
          ]);
        }
        loaded.push({ target, brief, attempt, analytics });
      }
      targets = loaded;
    } catch (error) {
      message = error.status === 401
        ? "Operator key не принят. Проверь ключ в блоке Autonomy."
        : error.message || "Не удалось загрузить Founder Outreach.";
      messageType = "error";
    } finally {
      busy = false;
      render();
    }
  }

  async function handleClick(event) {
    const control = event.target.closest("[data-outreach-action]");
    if (!control || busy) return;
    const action = control.dataset.outreachAction;
    const id = control.dataset.id;
    if (action === "refresh") {
      await load();
      return;
    }
    if (!id) return;

    if (action === "suppress" && !window.confirm("Suppress этот target и business contact?")) {
      return;
    }
    if (action === "reject" && !window.confirm("Отклонить этот outreach draft?")) {
      return;
    }

    busy = true;
    message = "";
    render();
    try {
      if (action === "suppress") {
        await api(`/v1/outreach-targets/${id}/suppress`, {
          method: "POST",
          body: {
            reason: "OPERATOR_SUPPRESSED",
            note: "Suppressed from Founder Outreach workspace",
          },
        });
        message = "Target и contact suppression зафиксированы.";
      } else if (action === "reject") {
        await api(`/v1/outreach-briefs/${id}/reject`, { method: "POST" });
        message = "Outreach draft отклонён. Внешняя отправка не выполнялась.";
      }
      messageType = "success";
    } catch (error) {
      message = error.message || "Операция не выполнена.";
      messageType = "error";
    } finally {
      busy = false;
      await load();
    }
  }

  async function handleSubmit(event) {
    if (!event.target.matches("form.outreach-edit-form") || busy) return;
    event.preventDefault();
    const form = event.target;
    const briefId = form.dataset.briefId;
    busy = true;
    message = "";
    render();
    try {
      await api(`/v1/outreach-briefs/${briefId}/review`, {
        method: "PATCH",
        body: {
          message_subject: form.elements.message_subject.value,
          message_body_without_link: form.elements.message_body_without_link.value,
        },
      });
      message = "Правка сохранена; tracking URL и attribution остались неизменными.";
      messageType = "success";
    } catch (error) {
      message = error.message || "Не удалось сохранить outreach draft.";
      messageType = "error";
    } finally {
      busy = false;
      await load();
    }
  }

  function formatNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(1) : "—";
  }

  function formatMoney(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? `$${number.toFixed(2)}` : "—";
  }

  function bindLifecycle() {
    window.addEventListener("partizan:execution-updated", () => {
      window.setTimeout(load, 120);
    });
    window.addEventListener("partizan:autonomy-updated", () => {
      window.setTimeout(load, 120);
    });
    const keyInput = $("#autonomy-operator-key");
    if (keyInput) {
      keyInput.addEventListener("change", () => window.setTimeout(load, 0));
    }
  }

  render();
  bindLifecycle();
  window.setTimeout(load, 220);
})();
