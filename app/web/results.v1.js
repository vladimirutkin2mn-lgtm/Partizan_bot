(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const PLATFORM_LABELS = {
    TELEGRAM: "Telegram",
    INSTAGRAM: "Instagram",
    REDDIT: "Reddit",
    TIKTOK: "TikTok",
  };
  const DECISION_LABELS = {
    SCALE: "SCALE · масштабировать",
    CONTINUE: "CONTINUE · продолжить",
    MODIFY: "MODIFY · изменить",
    STOP: "STOP · остановить",
  };

  let analytics = null;
  let learning = null;
  let portfolio = null;
  let loading = false;
  const decisions = new Map();

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  function workspaceState() {
    try {
      const raw = sessionStorage.getItem(WORKSPACE_STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  }

  function currentProductId() {
    return workspaceState().productId || null;
  }

  function currentProduct() {
    const state = workspaceState();
    return state.intake && state.intake.product ? state.intake.product : null;
  }

  function resultsUnlocked() {
    const state = workspaceState();
    return Boolean(state.productId && state.plays);
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function ensureSurface() {
    const progress = $(".progress");
    const main = $("main.shell");
    if (!progress || !main) return;

    if (!$("#results-step")) {
      const button = node("button", "progress-step");
      button.id = "results-step";
      button.type = "button";
      button.dataset.step = "results";
      button.append(
        node("span", "", "05"),
        node("strong", "", "Результаты"),
        node("small", "", "что сработало"),
      );
      button.addEventListener("click", activateResults);
      progress.append(button);
    }

    if (!$("#stage-results")) {
      const section = node("section", "stage results-stage");
      section.id = "stage-results";
      section.dataset.stage = "results";

      const heading = node("div", "section-heading");
      const copy = node("div");
      copy.append(
        node("span", "section-kicker", "05 · Results & Learning"),
        node("h2", "", "Что реально принесло клиентов и деньги?"),
        node(
          "p",
          "",
          "Partizan сводит реальные события и расход по DistributionExperiment, считает CAC/ROAS и предлагает следующее решение: масштабировать, продолжить, изменить или остановить.",
        ),
      );
      const toolbar = node("div", "results-toolbar");
      const refresh = node("button", "button button-primary", "Обновить результаты");
      refresh.id = "refresh-results";
      refresh.type = "button";
      refresh.addEventListener("click", refreshResults);
      toolbar.append(refresh);
      heading.append(copy, toolbar);

      const alert = node("div", "results-alert hidden");
      alert.id = "results-alert";
      alert.setAttribute("role", "status");

      const summary = node("div", "results-summary-grid");
      summary.id = "results-summary";

      const breakdownBlock = blockShell(
        "Экономика по площадкам",
        "Сравнение Telegram, Instagram, Reddit и TikTok по фактическому расходу и платящим пользователям.",
      );
      const breakdowns = node("div", "results-breakdowns");
      breakdowns.id = "results-breakdowns";
      breakdownBlock.append(breakdowns);

      const experimentsBlock = blockShell(
        "Эксперименты",
        "Каждая карточка — один реально подготовленный DistributionExperiment и его наблюдаемая экономика.",
      );
      const experimentList = node("div", "results-experiment-list");
      experimentList.id = "results-experiment-list";
      experimentsBlock.append(experimentList);

      const bottom = node("div", "results-bottom-grid");
      const portfolioPanel = node("section", "results-panel");
      portfolioPanel.append(
        node("h3", "", "Следующий портфель"),
        node("p", "", "Growth Manager переоценивает READY plays с учётом уже накопленной экономики."),
      );
      const portfolioList = node("div", "portfolio-list");
      portfolioList.id = "results-portfolio";
      portfolioPanel.append(portfolioList);

      const learningPanel = node("section", "results-panel");
      learningPanel.append(
        node("h3", "", "Learning memory"),
        node("p", "", "История решений по платформам, тактикам и конкретным opportunity."),
      );
      const learningList = node("div", "learning-list");
      learningList.id = "results-learning";
      learningPanel.append(learningList);
      bottom.append(portfolioPanel, learningPanel);

      const note = node(
        "p",
        "results-note",
        "SCALE здесь означает рекомендацию. Этот экран никогда не повышает бюджет и не запускает provider spend. Новый paid-запуск всё равно проходит prepare → approve → STAGED → exact-budget authorization → activation.",
      );

      section.append(heading, alert, summary, breakdownBlock, experimentsBlock, bottom, note);
      const experimentStage = $("#stage-experiments");
      if (experimentStage) experimentStage.insertAdjacentElement("afterend", section);
      else main.append(section);
    }

    syncResultsNav();
  }

  function blockShell(titleText, description) {
    const block = node("section", "results-section-block");
    const head = node("div", "results-block-head");
    const copy = node("div");
    copy.append(node("h3", "", titleText), node("p", "", description));
    head.append(copy);
    block.append(head);
    return block;
  }

  function syncResultsNav() {
    const button = $("#results-step");
    if (!button) return;
    const unlocked = resultsUnlocked();
    button.disabled = !unlocked;
    button.classList.toggle("complete", Boolean(analytics && analytics.experiment_count > 0));
  }

  function activateResults() {
    if (!resultsUnlocked()) {
      showAlert("Сначала собери Distribution Plays. Результаты появляются после этапа «Эксперименты».", "error");
      return;
    }
    $$(".stage").forEach((stage) => stage.classList.toggle("active", stage.id === "stage-results"));
    $$(".progress-step").forEach((button) => button.classList.toggle("active", button.id === "results-step"));
    syncResultsNav();
    window.scrollTo({ top: 150, behavior: "smooth" });
    refreshResults();
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
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
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || {}));
    }
    return payload;
  }

  async function refreshResults() {
    const productId = currentProductId();
    if (!productId || loading) return;
    loading = true;
    const button = $("#refresh-results");
    if (button) {
      button.disabled = true;
      button.textContent = "Обновляем…";
    }
    showAlert("");
    try {
      analytics = await api(`/v1/products/${productId}/distribution-analytics`);
      const [learningResult, portfolioResult] = await Promise.allSettled([
        api(`/v1/products/${productId}/distribution-learning`),
        api(`/v1/products/${productId}/distribution-portfolio?max_items=4`),
      ]);
      learning = learningResult.status === "fulfilled" ? learningResult.value : { entries: [] };
      portfolio = portfolioResult.status === "fulfilled"
        ? portfolioResult.value
        : { items: [], budget_remaining: null };
      renderAll();
      showAlert("Результаты обновлены из persisted distribution analytics.", "success");
    } catch (error) {
      showAlert(humanizeError(error), "error");
    } finally {
      loading = false;
      if (button) {
        button.disabled = false;
        button.textContent = "Обновить результаты";
      }
      syncResultsNav();
    }
  }

  function renderAll() {
    renderSummary();
    renderBreakdowns();
    renderExperiments();
    renderPortfolio();
    renderLearning();
  }

  function renderSummary() {
    const container = $("#results-summary");
    if (!container) return;
    if (!analytics) {
      container.replaceChildren(empty("Здесь появится общая экономика продукта."));
      return;
    }
    const experiments = analytics.experiments || [];
    const visits = sumMetric(experiments, "visits");
    const signups = sumMetric(experiments, "signups");
    const activated = sumMetric(experiments, "activated_users");
    const paid = analytics.total_paid_users || 0;
    const target = currentProduct() && currentProduct().max_cac;
    const cac = analytics.blended_cac;
    const cacClass = target && cac !== null && cac !== undefined
      ? Number(cac) <= Number(target) ? "good" : "bad"
      : "";
    const cacDetail = target
      ? `цель ≤ ${formatMoney(target)}`
      : "target CAC не задан";

    container.replaceChildren(
      kpi("Экспериментов", analytics.experiment_count || 0, `${visits} переходов`),
      kpi("Воронка", `${signups} → ${activated} → ${paid}`, "signup → activation → paid"),
      kpi("Расход / выручка", `${formatMoney(analytics.total_spend)} / ${formatMoney(analytics.total_revenue)}`, `ROAS ${formatRatio(analytics.blended_roas)}`),
      kpi("Blended CAC", formatMoney(cac), cacDetail, cacClass),
    );
  }

  function kpi(label, value, detail, extraClass = "") {
    const box = node("div", `result-kpi ${extraClass}`.trim());
    box.append(node("span", "", label), node("strong", "", value), node("small", "", detail));
    return box;
  }

  function renderBreakdowns() {
    const container = $("#results-breakdowns");
    if (!container) return;
    const rows = analytics
      ? (analytics.breakdowns || []).filter((row) => row.dimension === "PLATFORM")
      : [];
    if (!rows.length) {
      container.replaceChildren(empty("Пока нет платформенной экономики."));
      return;
    }
    const order = ["TELEGRAM", "INSTAGRAM", "REDDIT", "TIKTOK"];
    rows.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
    container.replaceChildren(...rows.map(breakdownCard));
  }

  function breakdownCard(row) {
    const card = node("article", "breakdown-card");
    const head = node("header");
    head.append(
      node("strong", "", PLATFORM_LABELS[row.key] || row.label || row.key),
      node("span", "", `${row.experiment_count} exp.`),
    );
    const metrics = node("div", "breakdown-metrics");
    metrics.append(
      compactMetric("Расход", formatMoney(row.spend)),
      compactMetric("Paid", row.paid_users),
      compactMetric("CAC", formatMoney(row.cac)),
      compactMetric("ROAS", formatRatio(row.roas)),
    );
    card.append(head, metrics);
    return card;
  }

  function compactMetric(label, value) {
    const box = node("div");
    box.append(node("span", "", label), node("strong", "", value));
    return box;
  }

  function renderExperiments() {
    const container = $("#results-experiment-list");
    if (!container) return;
    const experiments = analytics ? analytics.experiments || [] : [];
    if (!experiments.length) {
      container.replaceChildren(empty("Ещё нет DistributionExperiment. Подготовь и запусти READY play на этапе «Эксперименты»."));
      return;
    }
    container.replaceChildren(...experiments.map(experimentCard));
  }

  function experimentCard(item) {
    const card = node("article", "result-experiment");
    const head = node("div", "result-experiment-head");
    const title = node("div", "result-experiment-title");
    title.append(
      node("strong", "", item.play.opportunity_title || item.play.tactic_id),
      node("small", "", `${PLATFORM_LABELS[item.play.platform] || item.play.platform} · ${item.play.tactic_id}`),
    );
    const chips = node("div", "result-chip-row");
    chips.append(
      chip(item.experiment.status),
      chip(item.play.tactic_class),
      chip(item.action.status),
    );
    head.append(title, chips);

    const metrics = item.metrics || {};
    const metricGrid = node("div", "experiment-metrics");
    metricGrid.append(
      compactMetric("Расход", formatMoney(metrics.spend)),
      compactMetric("Переходы", number(metrics.visits)),
      compactMetric("Signup", number(metrics.signups)),
      compactMetric("Activated", number(metrics.activated_users)),
      compactMetric("Paid", number(metrics.paid_users)),
      compactMetric("CAC", formatMoney(metrics.cac)),
      compactMetric("Выручка", formatMoney(metrics.revenue)),
      compactMetric("ROAS", formatRatio(metrics.roas)),
      compactMetric("Visit→Signup", formatPercent(metrics.visit_to_signup_rate)),
      compactMetric("Signup→Paid", formatPercent(metrics.signup_to_paid_rate)),
      compactMetric("Transactions", number(metrics.transactions)),
      compactMetric("Rev/Paid", formatMoney(metrics.revenue_per_paid_user)),
    );

    const funnel = node("div", "experiment-funnel");
    funnel.append(
      node("span", "", `${item.event_count || 0} событий`),
      node("span", "", `Attribution: ${item.experiment.attribution_level}`),
      node("span", "", `Action: ${item.action.action_type}`),
    );

    const tracking = node("div", "experiment-tracking", item.experiment.tracking_url || "Tracking URL отсутствует");
    card.append(head, metricGrid, funnel, tracking);

    const decision = decisions.get(item.experiment.id);
    const memory = latestLearningFor(item.experiment.id);
    if (decision) {
      card.append(decisionBox(decision));
    } else if (memory) {
      card.append(memoryDecisionBox(memory));
    } else if (["RUNNING", "FINISHED"].includes(item.experiment.status)) {
      const box = node("div", "decision-box");
      const row = node("div", "decision-head");
      row.append(
        node("strong", "", "Growth Manager ещё не оценивал этот сигнал"),
        decisionButton(item.experiment.id),
      );
      box.append(row, node("p", "muted", "Оценка использует только текущие фактические метрики и бюджетные guardrails."));
      card.append(box);
    } else {
      const box = node("div", "decision-box");
      box.append(node("strong", "", "Решение появится после запуска"), node("p", "muted", `Сейчас experiment=${item.experiment.status}. Analytics и Growth Manager требуют RUNNING или FINISHED.`));
      card.append(box);
    }
    return card;
  }

  function chip(text) {
    return node("span", "result-chip", text || "—");
  }

  function decisionButton(experimentId) {
    const button = node("button", "button button-primary button-small", "Получить решение");
    button.type = "button";
    button.dataset.evaluateExperiment = experimentId;
    button.addEventListener("click", evaluateExperiment);
    return button;
  }

  async function evaluateExperiment(event) {
    const button = event.currentTarget;
    const experimentId = button.dataset.evaluateExperiment;
    if (!experimentId || button.disabled) return;
    button.disabled = true;
    button.textContent = "Оцениваем…";
    showAlert("");
    try {
      const decision = await api(`/v1/distribution-experiments/${experimentId}/growth-decision`, { method: "POST" });
      decisions.set(experimentId, decision);
      const productId = currentProductId();
      if (productId) {
        const [learningResult, portfolioResult] = await Promise.allSettled([
          api(`/v1/products/${productId}/distribution-learning`),
          api(`/v1/products/${productId}/distribution-portfolio?max_items=4`),
        ]);
        if (learningResult.status === "fulfilled") learning = learningResult.value;
        if (portfolioResult.status === "fulfilled") portfolio = portfolioResult.value;
      }
      renderExperiments();
      renderPortfolio();
      renderLearning();
      showAlert(`Growth Manager: ${decision.action}. Provider campaigns не изменялись.`, "success");
    } catch (error) {
      showAlert(humanizeError(error), "error");
    } finally {
      button.disabled = false;
      button.textContent = "Получить решение";
    }
  }

  function decisionBox(decision) {
    const box = node("div", "decision-box");
    const head = node("div", "decision-head");
    head.append(
      node("strong", "", "Growth Manager"),
      node("span", `decision-action ${decision.action}`, DECISION_LABELS[decision.action] || decision.action),
    );
    const list = node("ul");
    (decision.rationale || []).forEach((reason) => list.append(node("li", "", reason)));
    const economics = node("div", "decision-economics");
    economics.append(
      node("span", "", `Budget remaining: ${formatMoney(decision.budget_remaining)}`),
      node("span", "", `Recommended increment: ${formatMoney(decision.recommended_budget_increment)}`),
      node("span", "", decision.duplicate ? "same signal · existing decision" : "new decision"),
    );
    box.append(head, list, economics);
    return box;
  }

  function memoryDecisionBox(entry) {
    const box = node("div", "decision-box");
    const head = node("div", "decision-head");
    head.append(
      node("strong", "", "Последнее сохранённое решение"),
      node("span", `decision-action ${entry.action}`, DECISION_LABELS[entry.action] || entry.action),
    );
    box.append(head, node("p", "muted", entry.summary || "Decision stored in learning memory."));
    return box;
  }

  function latestLearningFor(experimentId) {
    const entries = learning && learning.entries ? learning.entries : [];
    return entries
      .filter((entry) => entry.experiment_id === experimentId)
      .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))[0] || null;
  }

  function renderPortfolio() {
    const container = $("#results-portfolio");
    if (!container) return;
    const items = portfolio && portfolio.items ? portfolio.items : [];
    if (!items.length) {
      container.replaceChildren(empty("Нет следующего READY portfolio: либо plays закончились, либо текущие эксперименты ещё идут."));
      return;
    }
    container.replaceChildren(...items.map((item) => {
      const card = node("article", "portfolio-item");
      const head = node("header");
      const title = node("strong", "", item.play.opportunity_title || item.play.tactic_id);
      const score = node("span", "portfolio-score", `${formatScore(item.portfolio_score)}/100`);
      head.append(title, score);
      const meta = node("p", "", `${PLATFORM_LABELS[item.play.platform] || item.play.platform} · ${item.play.tactic_id} · рекомендуемый cap ${formatMoney(item.recommended_budget_cap)}`);
      const rationale = node("p", "", (item.rationale || []).join(" "));
      card.append(head, meta, rationale);
      return card;
    }));
  }

  function renderLearning() {
    const container = $("#results-learning");
    if (!container) return;
    const entries = learning && learning.entries ? [...learning.entries] : [];
    entries.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
    if (!entries.length) {
      container.replaceChildren(empty("Learning memory пуст. Получи первое решение Growth Manager по RUNNING/FINISHED эксперименту."));
      return;
    }
    container.replaceChildren(...entries.slice(0, 8).map((entry) => {
      const card = node("article", "learning-item");
      const head = node("header");
      head.append(
        node("strong", "", `${PLATFORM_LABELS[entry.platform] || entry.platform} · ${entry.tactic_id}`),
        node("span", `decision-action ${entry.action}`, entry.action),
      );
      card.append(head, node("p", "", entry.summary || ""));
      return card;
    }));
  }

  function sumMetric(experiments, key) {
    return experiments.reduce((sum, item) => sum + Number((item.metrics && item.metrics[key]) || 0), 0);
  }

  function empty(message) {
    return node("div", "results-empty", message);
  }

  function showAlert(message, type = "") {
    const alert = $("#results-alert");
    if (!alert) return;
    alert.textContent = message || "";
    alert.className = `results-alert${message ? "" : " hidden"}${type ? ` ${type}` : ""}`;
  }

  function number(value) {
    return value === null || value === undefined ? "—" : String(value);
  }

  function formatMoney(value) {
    if (value === null || value === undefined) return "—";
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "—";
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(amount);
  }

  function formatRatio(value) {
    if (value === null || value === undefined) return "—";
    const ratio = Number(value);
    if (!Number.isFinite(ratio)) return "—";
    return `${ratio.toFixed(2)}×`;
  }

  function formatPercent(value) {
    if (value === null || value === undefined) return "—";
    const ratio = Number(value);
    if (!Number.isFinite(ratio)) return "—";
    return `${(ratio * 100).toFixed(1)}%`;
  }

  function formatScore(value) {
    const score = Number(value);
    return Number.isFinite(score) ? score.toFixed(1) : "0.0";
  }

  function humanizeError(error) {
    const text = String(error && error.message ? error.message : error || "Неизвестная ошибка");
    if (text.includes("RUNNING or FINISHED")) return "Growth Manager оценивает эксперимент только после реального запуска (RUNNING) или завершения (FINISHED).";
    if (text.includes("Distribution Plays")) return "Сначала собери Distribution Plays.";
    if (text.includes("Failed to fetch")) return "Не удалось получить distribution analytics. Проверь API.";
    return text;
  }

  function bindNavigationGuard() {
    const progress = $(".progress");
    if (progress) {
      const observer = new MutationObserver(() => syncResultsNav());
      observer.observe(progress, {
        subtree: true,
        attributes: true,
        attributeFilter: ["disabled", "class"],
      });
    }
    $$(".progress-step").forEach((button) => {
      if (button.id === "results-step") return;
      button.addEventListener("click", () => window.setTimeout(syncResultsNav, 0));
    });
    const reset = $("#reset-workspace");
    if (reset) {
      reset.addEventListener("click", () => window.setTimeout(() => {
        analytics = null;
        learning = null;
        portfolio = null;
        decisions.clear();
        syncResultsNav();
      }, 0));
    }
  }

  ensureSurface();
  bindNavigationGuard();
  syncResultsNav();
})();
