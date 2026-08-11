(() => {
  "use strict";

  const STORAGE_KEY = "partizan.workspace.v1";
  const PLATFORM_ORDER = ["TELEGRAM", "INSTAGRAM", "REDDIT", "TIKTOK"];
  const PLATFORM_LABELS = {
    TELEGRAM: "Telegram",
    INSTAGRAM: "Instagram",
    REDDIT: "Reddit",
    TIKTOK: "TikTok",
  };
  const KIND_LABELS = {
    CHANNEL: "канал",
    GROUP: "группа",
    CREATOR_ACCOUNT: "автор",
    SUBREDDIT: "сабреддит",
    CONTENT_CLUSTER: "контент-кластер",
  };
  const TACTIC_LABELS = {
    COMMUNITY: "Сообщества",
    PAID_PLATFORM: "Платная реклама",
    OWNED_ORGANIC: "Свой контент",
  };
  const ACTION_LABELS = {
    COMMENT: "Комментарий",
    REPLY: "Ответ",
    STANDALONE_POST: "Публикация",
    ORGANIC_VIDEO: "Видео",
    PAID_CAMPAIGN: "Рекламная кампания",
  };
  const STAGES = ["product", "audience", "distribution", "experiments"];

  let state = loadState();
  let activeStage = state.activeStage || furthestStage();
  let platformFilter = "ALL";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  function loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return freshState();
      return { ...freshState(), ...JSON.parse(raw) };
    } catch (_) {
      return freshState();
    }
  }

  function freshState() {
    return {
      productId: null,
      intake: null,
      icps: null,
      distribution: null,
      plays: null,
      activeStage: "product",
    };
  }

  function saveState() {
    state.activeStage = activeStage;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  async function api(path, options = {}) {
    const init = {
      method: options.method || "GET",
      headers: { Accept: "application/json", ...(options.headers || {}) },
    };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    const contentType = response.headers.get("content-type") || "";
    let payload = null;
    if (contentType.includes("application/json")) {
      payload = await response.json();
    } else {
      payload = await response.text();
    }
    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? payload.detail : payload;
      const message = typeof detail === "string" ? detail : JSON.stringify(detail || {});
      throw new Error(message || `HTTP ${response.status}`);
    }
    return payload;
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function tag(text, className = "tag") {
    return node("span", className, text);
  }

  function showNotice(message, type = "error") {
    const notice = $("#notice");
    notice.textContent = message;
    notice.className = `notice ${type}`;
    notice.classList.remove("hidden");
    window.clearTimeout(showNotice.timer);
    showNotice.timer = window.setTimeout(() => notice.classList.add("hidden"), 7000);
  }

  function hideNotice() {
    $("#notice").classList.add("hidden");
  }

  function setLoading(button, loading) {
    if (!button) return;
    button.classList.toggle("loading", loading);
    button.disabled = loading || button.dataset.locked === "true";
  }

  async function withLoading(button, task) {
    hideNotice();
    setLoading(button, true);
    try {
      return await task();
    } catch (error) {
      showNotice(humanizeError(error));
      throw error;
    } finally {
      setLoading(button, false);
      renderControls();
    }
  }

  function humanizeError(error) {
    const text = String(error && error.message ? error.message : error || "Неизвестная ошибка");
    if (text.includes("Failed to fetch")) return "Не удалось связаться с Partizan API. Проверь, что сервер запущен.";
    if (text.includes("Generate ICPs")) return "Сначала сгенерируй аудитории, затем запускай поиск каналов.";
    if (text.includes("Audience Intelligence")) return "Сначала построй карту каналов, затем собирай эксперименты.";
    return text;
  }

  function isConfirmed() {
    return Boolean(state.intake && state.intake.product && state.intake.product.status === "CONFIRMED");
  }

  function furthestStage() {
    if (state.plays) return "experiments";
    if (state.distribution) return "distribution";
    if (state.icps) return "audience";
    return "product";
  }

  function stageUnlocked(stage) {
    if (stage === "product") return true;
    if (stage === "audience") return isConfirmed() || Boolean(state.icps);
    if (stage === "distribution") return Boolean(state.icps);
    if (stage === "experiments") return Boolean(state.distribution);
    return false;
  }

  function stageComplete(stage) {
    if (stage === "product") return isConfirmed();
    if (stage === "audience") return Boolean(state.icps);
    if (stage === "distribution") return Boolean(state.distribution);
    if (stage === "experiments") return Boolean(state.plays);
    return false;
  }

  function setStage(stage, { scroll = true } = {}) {
    if (!stageUnlocked(stage) && stage !== "product") {
      showNotice("Этот этап ещё не открыт. Заверши предыдущий шаг.", "error");
      return;
    }
    activeStage = stage;
    state.activeStage = stage;
    saveState();
    $$(".stage").forEach((element) => element.classList.toggle("active", element.dataset.stage === stage));
    $$(".progress-step").forEach((element) => {
      const name = element.dataset.step;
      element.classList.toggle("active", name === stage);
      element.classList.toggle("complete", stageComplete(name));
      element.disabled = !stageUnlocked(name);
    });
    if (scroll) window.scrollTo({ top: 150, behavior: "smooth" });
  }

  function renderAll() {
    renderProjectHeader();
    renderProduct();
    renderAudience();
    renderDistribution();
    renderPlays();
    renderControls();
    setStage(activeStage, { scroll: false });
  }

  function renderProjectHeader() {
    const profile = state.intake && state.intake.product;
    $("#project-name").textContent = profile ? profile.name || "Без названия" : "Не создан";
    $("#copy-product-id").classList.toggle("hidden", !state.productId);
  }

  function renderProduct() {
    const profileContainer = $("#product-profile");
    const profile = state.intake && state.intake.product;
    const productState = $("#product-state");

    if (!profile) {
      productState.textContent = "Не начато";
      profileContainer.className = "panel profile-panel empty-state";
      profileContainer.replaceChildren(
        node("div", "empty-icon", "⌁"),
        node("h3", "", "Здесь появится профиль продукта"),
        node("p", "", "Partizan выделит ценность, аудиторию, рынок, бюджет, максимальный CAC и предположения, которые нужно подтвердить."),
      );
      renderClarifications();
      return;
    }

    productState.textContent = profile.status === "CONFIRMED" ? "Подтверждено" : "Черновик";
    profileContainer.className = "panel profile-panel";
    const head = node("div", "profile-head");
    const title = node("div");
    title.append(node("span", "mini-label", "Product Profile"), node("h3", "", profile.name || "Без названия"));
    title.append(node("p", "", profile.market ? `Рынок: ${profile.market}` : "Рынок ещё не определён"));
    head.append(title, tag(profile.status === "CONFIRMED" ? "CONFIRMED" : "DRAFT", "status-pill"));

    const metrics = node("div", "profile-metrics");
    metrics.append(
      profileMetric("Цена", formatNullable(profile.price)),
      profileMetric("Бюджет теста", formatNullable(profile.budget)),
      profileMetric("Макс. CAC", formatNullable(profile.max_cac)),
    );

    profileContainer.replaceChildren(head, metrics);
    addProfileBlock(profileContainer, "Ценность", profile.value_proposition || profile.description || "—");
    addProfileBlock(profileContainer, "Цель", profile.goal || "—");
    addTagBlock(profileContainer, "Известная аудитория", profile.known_audience || []);
    addTagBlock(profileContainer, "Допущения", profile.assumptions || []);
    addTagBlock(profileContainer, "Ограничения", profile.constraints || []);
    renderClarifications();
  }

  function profileMetric(label, value) {
    const box = node("div", "profile-metric");
    box.append(node("span", "", label), node("strong", "", value));
    return box;
  }

  function addProfileBlock(parent, label, value) {
    const block = node("div", "profile-block");
    block.append(node("label", "", label), node("p", "", value));
    parent.append(block);
  }

  function addTagBlock(parent, label, values) {
    if (!values || !values.length) return;
    const block = node("div", "profile-block");
    block.append(node("label", "", label));
    const row = node("div", "tag-row");
    values.slice(0, 8).forEach((value) => row.append(tag(value)));
    block.append(row);
    parent.append(block);
  }

  function renderClarifications() {
    const panel = $("#clarification-panel");
    const confirm = $("#confirm-panel");
    const intake = state.intake;
    const questions = intake ? intake.clarifications || [] : [];
    const needsAnswers = intake && intake.next_action === "answer_clarifications" && questions.length > 0;
    panel.classList.toggle("hidden", !needsAnswers);
    confirm.classList.toggle("hidden", !(intake && intake.next_action === "confirm"));
    if (!needsAnswers) return;
    const question = questions[0];
    $("#clarification-question").textContent = question.question;
    $("#clarification-rationale").textContent = question.rationale || "Ответ поможет точнее выбрать аудиторию и каналы.";
    $("#clarification-counter").textContent = `ещё ${questions.length}`;
    $("#clarification-answer").value = "";
  }

  function renderAudience() {
    const list = $("#icp-list");
    const summary = $("#audience-summary");
    const data = state.icps;
    if (!data || !data.icps || !data.icps.length) {
      summary.classList.add("hidden");
      list.replaceChildren(emptyPanel("02", "Сначала подтвердите профиль продукта. Здесь появятся наиболее перспективные ICP."));
      return;
    }

    const ranked = [...data.icps].sort((a, b) => a.rank - b.rank);
    const average = ranked.reduce((sum, item) => sum + Number(item.score || 0), 0) / ranked.length;
    summary.classList.remove("hidden");
    summary.replaceChildren(
      stat("Сегментов", data.ranked_count || ranked.length),
      stat("Лучший score", formatScore(ranked[0].score)),
      stat("Средний score", formatScore(average)),
      stat("Топ для карты", Math.min(data.ranked_count || ranked.length, 3)),
    );

    list.replaceChildren();
    ranked.slice(0, 8).forEach((icp) => {
      const card = node("article", "panel icp-card");
      card.dataset.rank = String(icp.rank).padStart(2, "0");
      const top = node("div", "card-topline");
      top.append(tag(`ICP ${icp.rank}`), node("span", "score", formatScore(icp.score)));
      card.append(top, node("h3", "", icp.title), node("p", "", icp.description));
      card.append(node("p", "hook", `Хук: ${icp.message_hook}`));
      list.append(card);
    });
  }

  function renderDistribution() {
    renderPlatformSummary();
    renderPlatformFilter();
    renderOpportunities();
  }

  function renderPlatformSummary() {
    const summary = $("#platform-summary");
    const opportunities = state.distribution && state.distribution.opportunities
      ? state.distribution.opportunities
      : [];
    if (!opportunities.length) {
      summary.replaceChildren();
      return;
    }
    const counts = countBy(opportunities, (item) => item.platform);
    summary.replaceChildren();
    PLATFORM_ORDER.forEach((platform) => {
      const card = node("div", "platform-card");
      card.dataset.platform = platform;
      const label = node("span");
      label.append(node("i", "platform-dot"), document.createTextNode(PLATFORM_LABELS[platform]));
      card.append(label, node("strong", "", counts[platform] || 0), node("span", "", "возможностей"));
      summary.append(card);
    });
  }

  function renderPlatformFilter() {
    const toolbar = $("#opportunity-toolbar");
    const container = $("#platform-filter");
    const opportunities = state.distribution && state.distribution.opportunities
      ? state.distribution.opportunities
      : [];
    toolbar.classList.toggle("hidden", !opportunities.length);
    if (!opportunities.length) return;
    const counts = countBy(opportunities, (item) => item.platform);
    container.replaceChildren();
    const options = ["ALL", ...PLATFORM_ORDER.filter((platform) => counts[platform])];
    options.forEach((platform) => {
      const button = node("button", platformFilter === platform ? "active" : "", platform === "ALL" ? "Все" : PLATFORM_LABELS[platform]);
      button.type = "button";
      button.addEventListener("click", () => {
        platformFilter = platform;
        renderPlatformFilter();
        renderOpportunities();
      });
      container.append(button);
    });
  }

  function renderOpportunities() {
    const list = $("#opportunity-list");
    const data = state.distribution;
    if (!data || !data.opportunities || !data.opportunities.length) {
      list.replaceChildren(emptyPanel("03", "После генерации ICP Partizan построит карту конкретных мест, где можно добраться до выбранной аудитории."));
      return;
    }
    let opportunities = [...data.opportunities];
    if (platformFilter !== "ALL") opportunities = opportunities.filter((item) => item.platform === platformFilter);
    opportunities.sort((a, b) => Number(b.relevance_score || 0) - Number(a.relevance_score || 0));
    $("#opportunity-count").textContent = `Показано ${Math.min(opportunities.length, 18)} из ${opportunities.length}`;
    list.replaceChildren();
    opportunities.slice(0, 18).forEach((opportunity) => {
      const card = node("article", "panel opportunity-card");
      const top = node("div", "card-topline");
      top.append(
        tag(PLATFORM_LABELS[opportunity.platform] || opportunity.platform, "platform-pill"),
        tag(KIND_LABELS[opportunity.kind] || opportunity.kind),
      );
      if (opportunity.relevance_score !== null && opportunity.relevance_score !== undefined) {
        top.append(node("span", "relevance", `${formatScore(opportunity.relevance_score)} fit`));
      }
      card.append(top, node("h3", "", opportunity.title), node("p", "", opportunity.rationale || "Релевантная точка присутствия аудитории."));
      if (opportunity.url) {
        const link = node("a", "opportunity-link", "Открыть источник ↗");
        link.href = opportunity.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        card.append(link);
      }
      list.append(card);
    });
  }

  function renderPlays() {
    const list = $("#play-list");
    const summary = $("#play-summary");
    const data = state.plays;
    if (!data || !data.plays || !data.plays.length) {
      summary.classList.add("hidden");
      list.replaceChildren(emptyPanel("04", "Сначала постройте карту каналов. Здесь появятся приоритетные paid, community и organic эксперименты."));
      return;
    }

    const plays = [...data.plays].sort((a, b) => Number(b.priority_score || 0) - Number(a.priority_score || 0));
    const ready = plays.filter((item) => item.status === "READY");
    const cheapest = ready.length ? Math.min(...ready.map((item) => Number(item.estimated_cost_min || 0))) : 0;
    summary.classList.remove("hidden");
    summary.replaceChildren(
      stat("Всего", data.play_count || plays.length),
      stat("Готово", data.ready_count ?? ready.length),
      stat("С блокерами", data.blocked_count ?? plays.length - ready.length),
      stat("Мин. тест", formatNumber(cheapest)),
    );

    list.replaceChildren();
    plays.slice(0, 20).forEach((play, index) => list.append(playCard(play, index + 1)));
  }

  function playCard(play, rank) {
    const card = node("article", "panel play-card");
    card.append(node("div", "play-rank", String(rank).padStart(2, "0")));
    const main = node("div", "play-main");
    const meta = node("div", "play-meta");
    meta.append(
      tag(PLATFORM_LABELS[play.platform] || play.platform, "platform-pill"),
      tag(TACTIC_LABELS[play.tactic_class] || play.tactic_class),
      tag(ACTION_LABELS[play.action_type] || play.action_type),
      tag(play.status, play.status === "READY" ? "tag ready" : "tag blocked"),
    );
    main.append(meta, node("h3", "", play.opportunity_title), node("p", "", play.hypothesis));
    if (play.execution_steps && play.execution_steps.length) {
      const steps = node("ol", "play-steps");
      play.execution_steps.slice(0, 4).forEach((step) => steps.append(node("li", "", step)));
      main.append(steps);
    }
    if (play.blockers && play.blockers.length) {
      main.append(node("div", "blockers", `Блокеры: ${play.blockers.join(" · ")}`));
    }
    card.append(main);

    const economics = node("aside", "play-economics");
    economics.append(
      econ("Приоритет", formatScore(play.priority_score)),
      econ("Стоимость", `${formatNumber(play.estimated_cost_min)}–${formatNumber(play.estimated_cost_max)}`),
      econ("Сигнал", `${play.time_to_signal_days} дн.`),
      econ("Работа", `${formatNumber(play.effort_hours)} ч.`),
    );
    card.append(economics);
    return card;
  }

  function stat(label, value) {
    const card = node("div", "stat-card");
    card.append(node("span", "", label), node("strong", "", value));
    return card;
  }

  function econ(label, value) {
    const row = node("div", "econ-row");
    row.append(node("span", "", label), node("strong", "", value));
    return row;
  }

  function emptyPanel(number, message) {
    const panel = node("div", "panel empty-wide");
    panel.append(node("span", "empty-number", number), node("p", "", message));
    return panel;
  }

  function renderControls() {
    const confirmed = isConfirmed();
    lockButton($("#generate-icps"), !confirmed);
    lockButton($("#discover-distribution"), !state.icps);
    lockButton($("#generate-plays"), !state.distribution);
    $$(".progress-step").forEach((element) => {
      element.disabled = !stageUnlocked(element.dataset.step);
      element.classList.toggle("complete", stageComplete(element.dataset.step));
    });
  }

  function lockButton(button, locked) {
    if (!button) return;
    button.dataset.locked = locked ? "true" : "false";
    if (!button.classList.contains("loading")) button.disabled = locked;
  }

  function formatNullable(value) {
    return value === null || value === undefined ? "—" : formatNumber(value);
  }

  function formatNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(number);
  }

  function formatScore(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return number.toFixed(number % 1 === 0 ? 0 : 1);
  }

  function countBy(items, getter) {
    return items.reduce((acc, item) => {
      const key = getter(item);
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
  }

  function parseLinks(raw) {
    const values = raw.split(/\s+/).map((value) => value.trim()).filter(Boolean);
    return values.map((value) => {
      try {
        return new URL(value).toString();
      } catch (_) {
        throw new Error(`Некорректная ссылка: ${value}`);
      }
    });
  }

  async function checkHealth() {
    const badge = $("#health-badge");
    try {
      await api("/health");
      badge.className = "health-badge healthy";
      badge.querySelector("span").textContent = "API работает";
    } catch (_) {
      badge.className = "health-badge unhealthy";
      badge.querySelector("span").textContent = "API недоступен";
    }
  }

  async function createProduct(event) {
    event.preventDefault();
    const button = $("#create-product");
    const brief = $("#product-brief").value.trim();
    if (brief.length < 20) {
      showNotice("Опиши продукт чуть подробнее — минимум 20 символов.");
      return;
    }
    await withLoading(button, async () => {
      const intake = await api("/v1/products", {
        method: "POST",
        body: { brief, reference_links: parseLinks($("#reference-links").value) },
      });
      state = { ...freshState(), productId: intake.product.id, intake, activeStage: "product" };
      activeStage = "product";
      saveState();
      renderAll();
      showNotice("Продукт разобран. Проверь профиль и ответь на уточнения.", "success");
    });
  }

  async function answerClarification(event) {
    event.preventDefault();
    const question = state.intake && state.intake.clarifications && state.intake.clarifications[0];
    if (!question || !state.productId) return;
    const answer = $("#clarification-answer").value.trim();
    if (!answer) return;
    const button = event.currentTarget.querySelector("button[type=submit]");
    await withLoading(button, async () => {
      state.intake = await api(`/v1/products/${state.productId}/clarifications`, {
        method: "POST",
        body: { question_id: question.id, answer },
      });
      saveState();
      renderProduct();
      renderControls();
      if (state.intake.next_action === "confirm") showNotice("Уточнений достаточно. Проверь профиль и подтверди его.", "success");
    });
  }

  async function confirmProduct() {
    if (!state.productId) return;
    const button = $("#confirm-product");
    await withLoading(button, async () => {
      state.intake = await api(`/v1/products/${state.productId}/confirm`, { method: "POST" });
      saveState();
      renderAll();
      showNotice("Профиль подтверждён. Теперь найдём лучшие аудитории.", "success");
      setStage("audience");
    });
  }

  async function generateIcps() {
    if (!state.productId) return;
    const button = $("#generate-icps");
    await withLoading(button, async () => {
      state.icps = await api(`/v1/products/${state.productId}/icps/generate`, { method: "POST" });
      state.distribution = null;
      state.plays = null;
      saveState();
      renderAll();
      showNotice(`Готово: ${state.icps.ranked_count || state.icps.icps.length} аудиторий ранжированы.`, "success");
      setStage("audience", { scroll: false });
    });
  }

  async function discoverDistribution() {
    if (!state.productId) return;
    const button = $("#discover-distribution");
    await withLoading(button, async () => {
      state.distribution = await api(`/v1/products/${state.productId}/distribution/discover`, { method: "POST" });
      state.plays = null;
      platformFilter = "ALL";
      saveState();
      renderAll();
      showNotice(`Карта готова: найдено ${state.distribution.opportunity_count} конкретных возможностей.`, "success");
      setStage("distribution", { scroll: false });
    });
  }

  async function generatePlays() {
    if (!state.productId) return;
    const button = $("#generate-plays");
    await withLoading(button, async () => {
      state.plays = await api(`/v1/products/${state.productId}/distribution-plays/generate`, { method: "POST" });
      saveState();
      renderAll();
      showNotice(`Собрано ${state.plays.play_count} экспериментов. Готовы к запуску: ${state.plays.ready_count}.`, "success");
      setStage("experiments", { scroll: false });
    });
  }

  function resetWorkspace() {
    if (state.productId && !window.confirm("Начать новый проект? Текущий Product ID останется в backend, но будет убран из этой вкладки.")) return;
    sessionStorage.removeItem(STORAGE_KEY);
    state = freshState();
    activeStage = "product";
    platformFilter = "ALL";
    $("#product-brief").value = "";
    $("#reference-links").value = "";
    $("#brief-count").textContent = "0";
    renderAll();
    setStage("product");
  }

  async function copyProductId() {
    if (!state.productId) return;
    try {
      await navigator.clipboard.writeText(state.productId);
      showNotice("Product ID скопирован.", "success");
    } catch (_) {
      showNotice(`Product ID: ${state.productId}`, "success");
    }
  }

  function bindEvents() {
    $("#product-form").addEventListener("submit", createProduct);
    $("#product-brief").addEventListener("input", (event) => {
      $("#brief-count").textContent = String(event.target.value.length);
    });
    $("#clarification-form").addEventListener("submit", answerClarification);
    $("#confirm-product").addEventListener("click", confirmProduct);
    $("#generate-icps").addEventListener("click", generateIcps);
    $("#discover-distribution").addEventListener("click", discoverDistribution);
    $("#generate-plays").addEventListener("click", generatePlays);
    $("#reset-workspace").addEventListener("click", resetWorkspace);
    $("#copy-product-id").addEventListener("click", copyProductId);
    $$(".progress-step").forEach((button) => button.addEventListener("click", () => setStage(button.dataset.step)));
  }

  bindEvents();
  renderAll();
  checkHealth();
})();
