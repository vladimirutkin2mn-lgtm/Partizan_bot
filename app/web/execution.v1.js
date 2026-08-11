(() => {
  "use strict";

  const WORKSPACE_STORAGE_KEY = "partizan.workspace.v1";
  const EXECUTION_STORAGE_KEY = "partizan.execution.v1";
  const OPERATOR_HEADER = "X-Partizan-Operator-Key";
  const RETRYABLE_OUTCOMES = new Set(["FAILED", "UNAVAILABLE", "IN_PROGRESS"]);

  let operatorKey = "";
  let selectedPlay = null;
  let execution = loadExecution();

  const $ = (selector) => document.querySelector(selector);

  function loadExecution() {
    try {
      const raw = sessionStorage.getItem(EXECUTION_STORAGE_KEY);
      if (!raw) return freshExecution();
      return { ...freshExecution(), ...JSON.parse(raw) };
    } catch (_) {
      return freshExecution();
    }
  }

  function freshExecution() {
    return {
      productId: null,
      selectedPlayId: null,
      plan: null,
      receipt: null,
    };
  }

  function saveExecution() {
    sessionStorage.setItem(
      EXECUTION_STORAGE_KEY,
      JSON.stringify({
        productId: execution.productId,
        selectedPlayId: execution.selectedPlayId,
        plan: execution.plan,
        receipt: execution.receipt,
      }),
    );
  }

  function clearExecution() {
    execution = freshExecution();
    selectedPlay = null;
    operatorKey = "";
    sessionStorage.removeItem(EXECUTION_STORAGE_KEY);
    const keyInput = $("#operator-key");
    if (keyInput) keyInput.value = "";
    renderCurrentExecutionButton();
    enhancePlayCards();
  }

  function workspaceState() {
    try {
      const raw = sessionStorage.getItem(WORKSPACE_STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function syncProjectBoundary() {
    const workspace = workspaceState();
    if (!workspace || !workspace.productId) {
      if (execution.productId) clearExecution();
      return workspace;
    }
    if (execution.productId && execution.productId !== workspace.productId) {
      clearExecution();
    }
    return workspace;
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.operator && operatorKey.trim()) {
      headers[OPERATOR_HEADER] = operatorKey.trim();
    }
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
      const text = typeof detail === "string" ? detail : JSON.stringify(detail || {});
      const error = new Error(text || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function humanizeError(error) {
    if (error && error.status === 401) {
      return "Нужен operator key. В production защищённые действия без него не выполняются.";
    }
    if (error && error.status === 503) {
      return "Operator authentication включена, но сервер не настроен. Проверь OPERATOR_API_KEY в окружении backend.";
    }
    const text = String(error && error.message ? error.message : error || "Неизвестная ошибка");
    if (text.includes("CampaignSlot")) return "Для этого identity сначала нужен активный CampaignSlot.";
    if (text.includes("CommunityPolicy")) return "Перед запуском Reddit-действия нужно применить актуальную CommunityPolicy.";
    if (text.includes("ActionTarget")) return "Для этого действия пока нет конкретного public target. Сначала нужно обогатить opportunity.";
    if (text.includes("destination_url")) return "Укажи destination URL продукта перед подготовкой действия.";
    return text;
  }

  function setAlert(message, success = false) {
    const alert = $("#execution-alert");
    if (!message) {
      alert.className = "execution-alert hidden";
      alert.textContent = "";
      return;
    }
    alert.textContent = message;
    alert.className = `execution-alert${success ? " success" : ""}`;
  }

  function setLoading(button, loading) {
    if (!button) return;
    button.classList.toggle("loading", loading);
    button.disabled = loading;
  }

  async function withLoading(button, task) {
    setAlert("");
    setLoading(button, true);
    try {
      return await task();
    } catch (error) {
      setAlert(humanizeError(error));
      throw error;
    } finally {
      setLoading(button, false);
      renderExecution();
    }
  }

  function openDrawer(play = null) {
    const workspace = syncProjectBoundary();
    if (!workspace || !workspace.productId) return;

    if (play) {
      selectedPlay = play;
      if (execution.selectedPlayId !== play.id) {
        execution = {
          productId: workspace.productId,
          selectedPlayId: play.id,
          plan: null,
          receipt: null,
        };
        saveExecution();
      }
      const destination = $("#execution-destination");
      if (!execution.plan) {
        const references = workspace.intake && workspace.intake.product
          ? workspace.intake.product.reference_links || []
          : [];
        destination.value = references.length ? references[0] : "";
      }
    } else {
      selectedPlay = findSelectedPlay(workspace);
    }

    $("#execution-drawer").classList.remove("hidden");
    $("#execution-backdrop").classList.remove("hidden");
    $("#execution-drawer").setAttribute("aria-hidden", "false");
    $("#execution-backdrop").setAttribute("aria-hidden", "false");
    document.body.classList.add("execution-open");
    renderExecution();
  }

  function closeDrawer() {
    $("#execution-drawer").classList.add("hidden");
    $("#execution-backdrop").classList.add("hidden");
    $("#execution-drawer").setAttribute("aria-hidden", "true");
    $("#execution-backdrop").setAttribute("aria-hidden", "true");
    document.body.classList.remove("execution-open");
    operatorKey = "";
    $("#operator-key").value = "";
  }

  function findSelectedPlay(workspace = workspaceState()) {
    if (!workspace || !workspace.plays || !execution.selectedPlayId) return null;
    return (workspace.plays.plays || []).find((play) => play.id === execution.selectedPlayId) || null;
  }

  function sortedRenderedPlays(workspace) {
    if (!workspace || !workspace.plays || !workspace.plays.plays) return [];
    return [...workspace.plays.plays]
      .sort((a, b) => Number(b.priority_score || 0) - Number(a.priority_score || 0))
      .slice(0, 20);
  }

  function enhancePlayCards() {
    const workspace = syncProjectBoundary();
    if (!workspace || !workspace.plays) return;
    const plays = sortedRenderedPlays(workspace);
    const cards = Array.from(document.querySelectorAll("#play-list .play-card"));
    cards.forEach((card, index) => {
      const play = plays[index];
      if (!play || play.status !== "READY") return;
      card.dataset.playId = play.id;
      const economics = card.querySelector(".play-economics");
      if (!economics || economics.querySelector(".execution-launch")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-ghost execution-launch";
      button.textContent = execution.selectedPlayId === play.id && execution.plan
        ? "Открыть текущий запуск"
        : "Подготовить к запуску →";
      button.addEventListener("click", () => openDrawer(play));
      economics.append(button);
    });
    renderCurrentExecutionButton();
  }

  function renderCurrentExecutionButton() {
    const button = $("#open-current-execution");
    if (!button) return;
    button.classList.toggle("hidden", !execution.plan);
  }

  function renderExecution() {
    const workspace = syncProjectBoundary();
    if (!workspace) return;
    selectedPlay = selectedPlay || findSelectedPlay(workspace);
    const playLabel = $("#execution-play-label");
    playLabel.textContent = selectedPlay
      ? `${selectedPlay.opportunity_title} · ${selectedPlay.platform} · ${selectedPlay.action_type}`
      : "Выберите READY-эксперимент";

    $("#prepare-execution").disabled = !selectedPlay;
    const planSection = $("#execution-plan");
    const operatorSection = $("#operator-access");
    const runSection = $("#execution-run");
    const receiptSection = $("#execution-receipt");

    planSection.classList.toggle("hidden", !execution.plan);
    operatorSection.classList.toggle("hidden", !execution.plan);
    runSection.classList.toggle("hidden", !execution.plan || execution.plan.action.status === "PREPARED");
    receiptSection.classList.toggle("hidden", !execution.receipt);

    if (!execution.plan) {
      renderCurrentExecutionButton();
      return;
    }

    const action = execution.plan.action;
    const experiment = execution.plan.experiment;
    $("#execution-action-meta").textContent = `${action.status} · ${action.platform} · ${action.action_type}`;
    $("#execution-target").value = action.target_url || "";
    $("#execution-content").value = action.content_text || "";
    $("#execution-context").value = action.content_payload && action.content_payload.context_text
      ? action.content_payload.context_text
      : "";

    const facts = $("#execution-facts");
    facts.replaceChildren(
      fact("Action", action.id),
      fact("Experiment", experiment.id),
      fact("Attribution", action.attribution_level),
      factLink("Tracking", action.tracking_url || experiment.tracking_url),
    );

    const prepared = action.status === "PREPARED";
    $("#save-execution").disabled = !prepared;
    $("#approve-execution").disabled = !prepared;
    $("#execution-target").disabled = !prepared;
    $("#execution-content").disabled = !prepared;
    $("#execution-context").disabled = !prepared;

    const paid = action.action_type === "PAID_CAMPAIGN";
    const note = $("#execution-safety-note");
    note.className = `execution-safety-note${paid ? " paid" : ""}`;
    note.textContent = paid
      ? "Paid safety: эта кнопка может только создать provider objects в PAUSED/DISABLE. Расход не запускается — activation требует отдельной exact-budget authorization и отсутствует в этом интерфейсе."
      : "Выполнение идёт через существующий adapter boundary. ASSISTED/UNAVAILABLE не будут выданы за успешный EXECUTED.";

    const runButton = $("#run-execution");
    runButton.classList.toggle("hidden", action.status !== "APPROVED" || Boolean(execution.receipt));

    renderReceipt();
    renderCurrentExecutionButton();
  }

  function fact(label, value) {
    const box = document.createElement("div");
    box.className = "execution-fact";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("strong");
    valueNode.textContent = value || "—";
    box.append(labelNode, valueNode);
    return box;
  }

  function factLink(label, value) {
    const box = document.createElement("div");
    box.className = "execution-fact";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("strong");
    if (value) {
      const link = document.createElement("a");
      link.href = value;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Открыть tracking URL ↗";
      valueNode.append(link);
    } else {
      valueNode.textContent = "—";
    }
    box.append(labelNode, valueNode);
    return box;
  }

  function renderReceipt() {
    const receipt = execution.receipt;
    const container = $("#receipt-card");
    const retryButton = $("#retry-execution");
    if (!receipt) {
      container.replaceChildren();
      retryButton.classList.add("hidden");
      return;
    }

    const top = document.createElement("div");
    top.className = "receipt-topline";
    const title = document.createElement("h3");
    title.textContent = receipt.provider || receipt.adapter_name || "Execution adapter";
    const badge = document.createElement("span");
    badge.className = `receipt-outcome ${receipt.outcome}`;
    badge.textContent = receipt.outcome;
    top.append(title, badge);

    const message = document.createElement("p");
    message.className = "receipt-message";
    message.textContent = receipt.message;

    const guidance = document.createElement("div");
    guidance.className = "receipt-guidance";
    guidance.textContent = receiptGuidance(receipt);

    container.replaceChildren(top, message, guidance);
    const links = document.createElement("div");
    links.className = "receipt-links";
    if (receipt.executed_url) {
      links.append(externalLink("Открыть выполненное действие ↗", receipt.executed_url));
    }
    if (receipt.external_reference) {
      const reference = document.createElement("span");
      reference.className = "muted";
      reference.textContent = `ref: ${receipt.external_reference}`;
      links.append(reference);
    }
    if (links.childNodes.length) container.append(links);

    const partial = receipt.metadata && receipt.metadata.partial_provider_ids
      && Object.keys(receipt.metadata.partial_provider_ids).length > 0;
    const reconciliation = Boolean(receipt.metadata && receipt.metadata.requires_reconciliation);
    const retryable = RETRYABLE_OUTCOMES.has(receipt.outcome) && !partial && !reconciliation;
    retryButton.classList.toggle("hidden", !retryable);
  }

  function receiptGuidance(receipt) {
    switch (receipt.outcome) {
      case "EXECUTED":
        return "Adapter подтвердил внешнее выполнение. Эксперимент переведён в RUNNING и теперь должен измеряться по tracking/referral attribution.";
      case "STAGED":
        return "Платная кампания создана у провайдера в PAUSED/DISABLE. Расход не начался. Для старта нужен отдельный operator-only activation flow с точным budget cap.";
      case "ASSISTED":
        return "Partizan подготовил действие, но универсального compliant execution API для этой поверхности нет. Выполни его через разрешённый операторский процесс и затем зафиксируй внешний результат отдельным flow.";
      case "UNAVAILABLE":
        return "Нужная интеграция/identity/permission пока не настроена. Это не считается выполнением. После исправления конфигурации можно сделать явный retry, если backend допускает его.";
      case "IN_PROGRESS":
        return "Предыдущая внешняя попытка могла прерваться до подтверждённого результата. Повтор допустим только явно и может требовать ручной проверки провайдера.";
      case "FAILED":
        return "Провайдер не подтвердил выполнение. Если нет частично созданных provider objects/reconciliation requirement, backend допускает явный retry.";
      default:
        return "Проверь receipt и не трактуй неподтверждённый provider outcome как выполненное действие.";
    }
  }

  function externalLink(text, href) {
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = text;
    return link;
  }

  async function prepareAction() {
    const workspace = syncProjectBoundary();
    if (!workspace || !workspace.productId || !selectedPlay) return;
    const button = $("#prepare-execution");
    await withLoading(button, async () => {
      const destination = $("#execution-destination").value.trim();
      const plan = await api(
        `/v1/products/${workspace.productId}/distribution-plays/${selectedPlay.id}/actions/auto-prepare`,
        { method: "POST", body: { destination_url: destination || null } },
      );
      execution = {
        productId: workspace.productId,
        selectedPlayId: selectedPlay.id,
        plan,
        receipt: null,
      };
      saveExecution();
      renderExecution();
      enhancePlayCards();
      setAlert("Черновик подготовлен. Проверь target, текст и tracking перед подтверждением.", true);
    });
  }

  async function saveEdits({ silent = false } = {}) {
    if (!execution.plan || execution.plan.action.status !== "PREPARED") return execution.plan;
    const button = $("#save-execution");
    const actionId = execution.plan.action.id;
    const task = async () => {
      execution.plan = await api(`/v1/distribution-actions/${actionId}`, {
        method: "PATCH",
        operator: true,
        body: {
          target_url: $("#execution-target").value.trim() || null,
          context_text: $("#execution-context").value,
          content_text: $("#execution-content").value,
        },
      });
      saveExecution();
      renderExecution();
      if (!silent) setAlert("Правки сохранены в PREPARED action.", true);
      return execution.plan;
    };
    return silent ? task() : withLoading(button, task);
  }

  async function approveAction() {
    if (!execution.plan || execution.plan.action.status !== "PREPARED") return;
    const button = $("#approve-execution");
    await withLoading(button, async () => {
      await saveEdits({ silent: true });
      const actionId = execution.plan.action.id;
      execution.plan = await api(`/v1/distribution-actions/${actionId}/approve`, {
        method: "POST",
        operator: true,
      });
      saveExecution();
      renderExecution();
      setAlert("Action подтверждён. Backend policy gates пройдены; теперь можно вызвать execution adapter.", true);
    });
  }

  async function runAction(retry = false) {
    if (!execution.plan) return;
    const button = retry ? $("#retry-execution") : $("#run-execution");
    if (retry) {
      const confirmed = window.confirm(
        "Повторить внешнюю попытку? Делайте это только после проверки, что предыдущая попытка не выполнила действие без receipt.",
      );
      if (!confirmed) return;
    }
    await withLoading(button, async () => {
      const actionId = execution.plan.action.id;
      const result = await api(`/v1/distribution-actions/${actionId}/execute`, {
        method: "POST",
        operator: true,
        body: { retry },
      });
      execution.plan = result.plan;
      execution.receipt = result.receipt;
      saveExecution();
      renderExecution();
      setAlert(receiptGuidance(result.receipt), result.receipt.outcome === "EXECUTED" || result.receipt.outcome === "STAGED");
    });
  }

  function bind() {
    $("#close-execution").addEventListener("click", closeDrawer);
    $("#execution-backdrop").addEventListener("click", closeDrawer);
    $("#open-current-execution").addEventListener("click", () => openDrawer());
    $("#operator-key").addEventListener("input", (event) => {
      operatorKey = event.target.value;
    });
    $("#prepare-execution").addEventListener("click", prepareAction);
    $("#save-execution").addEventListener("click", () => saveEdits());
    $("#approve-execution").addEventListener("click", approveAction);
    $("#run-execution").addEventListener("click", () => runAction(false));
    $("#retry-execution").addEventListener("click", () => runAction(true));
    $("#reset-workspace").addEventListener("click", () => {
      window.setTimeout(() => {
        const workspace = workspaceState();
        if (!workspace || !workspace.productId || workspace.productId !== execution.productId) {
          clearExecution();
          closeDrawer();
        }
      }, 0);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("#execution-drawer").classList.contains("hidden")) {
        closeDrawer();
      }
    });

    const playList = $("#play-list");
    if (playList) {
      new MutationObserver(enhancePlayCards).observe(playList, { childList: true, subtree: true });
    }
  }

  syncProjectBoundary();
  bind();
  enhancePlayCards();
  renderCurrentExecutionButton();
})();
