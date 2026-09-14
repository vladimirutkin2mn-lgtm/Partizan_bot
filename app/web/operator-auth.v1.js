(() => {
  "use strict";

  const OPERATOR_HEADER = "X-Partizan-Operator-Key";
  const GLOBAL_INPUT_ID = "global-operator-key";
  const EXECUTION_INPUT_ID = "operator-key";
  const APPROVAL_QUEUE_ID = "customer-approval-queue";
  const nativeFetch = window.fetch.bind(window);

  function operatorKey() {
    const globalInput = document.getElementById(GLOBAL_INPUT_ID);
    const executionInput = document.getElementById(EXECUTION_INPUT_ID);
    return (globalInput && globalInput.value.trim()) ||
      (executionInput && executionInput.value.trim()) || "";
  }

  function isInternalApi(input) {
    const raw = input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.href);
    return url.origin === window.location.origin && url.pathname.startsWith("/v1/");
  }

  function mergedHeaders(input, init) {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    if (init && init.headers) {
      new Headers(init.headers).forEach((value, name) => headers.set(name, value));
    }
    const key = operatorKey();
    if (key) headers.set(OPERATOR_HEADER, key);
    return headers;
  }

  window.fetch = function partizanAuthenticatedFetch(input, init = {}) {
    if (!isInternalApi(input)) return nativeFetch(input, init);
    const headers = mergedHeaders(input, init);
    if (input instanceof Request) {
      return nativeFetch(new Request(input, { ...init, headers }));
    }
    return nativeFetch(input, { ...init, headers });
  };

  function syncInputs(source, target) {
    if (!source || !target) return;
    source.addEventListener("input", () => {
      if (target.value === source.value) return;
      target.value = source.value;
      target.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  function textNode(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null || value === "" ? "—" : String(value);
    return node;
  }

  function formatDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }

  function shortId(value) {
    const raw = String(value || "");
    return raw.length > 14 ? `${raw.slice(0, 8)}…${raw.slice(-4)}` : raw || "—";
  }

  async function responseDetail(response) {
    try {
      const payload = await response.json();
      if (payload && typeof payload.detail === "string") return payload.detail;
    } catch (_error) {
      // Fall through to the status label.
    }
    return `${response.status} ${response.statusText}`.trim();
  }

  function createFact(label, value, options = {}) {
    const fact = document.createElement("div");
    fact.className = "operator-approval-fact";
    fact.append(textNode("span", "operator-approval-fact-label", label));

    if (options.url && value) {
      const link = document.createElement("a");
      link.className = "operator-approval-fact-value operator-approval-link";
      link.href = String(value);
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      link.textContent = String(value);
      fact.append(link);
    } else {
      fact.append(textNode("strong", "operator-approval-fact-value", value));
    }
    return fact;
  }

  function createExactBlock(label, value) {
    const wrap = document.createElement("div");
    wrap.className = "operator-approval-exact-block";
    wrap.append(textNode("span", "operator-approval-fact-label", label));
    const pre = document.createElement("pre");
    pre.textContent = value || "—";
    wrap.append(pre);
    return wrap;
  }

  function createExecutionReceipt(execution) {
    const wrap = document.createElement("div");
    wrap.className = "operator-execution-receipt";
    if (execution.error) {
      wrap.dataset.tone = "error";
      wrap.append(
        textNode("span", "operator-approval-fact-label", "Execution blocked"),
        textNode("p", "", execution.error)
      );
      return wrap;
    }
    const receipt = execution.receipt;
    if (!receipt) {
      wrap.append(
        textNode("span", "operator-approval-fact-label", "Execution state"),
        textNode("p", "", "No provider attempt recorded. Execution remains a separate explicit operator action.")
      );
      return wrap;
    }
    wrap.dataset.outcome = receipt.outcome;
    wrap.append(
      textNode("span", "operator-approval-fact-label", "Execution receipt"),
      textNode("strong", "", `${receipt.outcome} · ${receipt.provider}`),
      textNode("p", "", receipt.message),
      textNode("code", "", receipt.external_reference || "No external reference")
    );
    return wrap;
  }

  function mountCustomerApprovalQueue(actions) {
    if (!actions || document.getElementById(APPROVAL_QUEUE_ID)) return;

    const trigger = document.createElement("button");
    trigger.id = "open-customer-approval-queue";
    trigger.className = "button button-ghost button-small operator-approval-trigger";
    trigger.type = "button";
    trigger.textContent = "Customer approvals";
    actions.prepend(trigger);

    const backdrop = document.createElement("div");
    backdrop.id = "customer-approval-backdrop";
    backdrop.className = "operator-approval-backdrop hidden";
    backdrop.setAttribute("aria-hidden", "true");

    const drawer = document.createElement("aside");
    drawer.id = APPROVAL_QUEUE_ID;
    drawer.className = "operator-approval-drawer hidden";
    drawer.setAttribute("aria-hidden", "true");
    drawer.setAttribute("aria-label", "Customer execution approvals");

    const header = document.createElement("div");
    header.className = "operator-approval-header";
    const headerCopy = document.createElement("div");
    headerCopy.append(
      textNode("span", "section-kicker", "Operator queue"),
      textNode("h2", "", "Customer execution approvals"),
      textNode(
        "p",
        "muted",
        "Approval and execution are separate. Both operate only on the exact customer-confirmed action."
      )
    );
    const close = document.createElement("button");
    close.id = "close-customer-approval-queue";
    close.className = "drawer-close";
    close.type = "button";
    close.setAttribute("aria-label", "Закрыть очередь approvals");
    close.textContent = "×";
    header.append(headerCopy, close);

    const toolbar = document.createElement("div");
    toolbar.className = "operator-approval-toolbar";
    const summary = textNode("span", "operator-approval-summary", "Не загружено");
    summary.id = "customer-approval-summary";
    const refresh = document.createElement("button");
    refresh.id = "refresh-customer-approval-queue";
    refresh.className = "button button-ghost button-small";
    refresh.type = "button";
    refresh.textContent = "Обновить";
    toolbar.append(summary, refresh);

    const alert = document.createElement("div");
    alert.id = "customer-approval-alert";
    alert.className = "operator-approval-alert hidden";
    alert.setAttribute("role", "status");

    const list = document.createElement("div");
    list.id = "customer-approval-list";
    list.className = "operator-approval-list";

    drawer.append(header, toolbar, alert, list);
    document.body.append(backdrop, drawer);

    let requests = [];
    let executionStates = new Map();
    let loading = false;

    function setAlert(message, tone = "error") {
      alert.textContent = message || "";
      alert.dataset.tone = tone;
      alert.classList.toggle("hidden", !message);
    }

    function setOpen(open) {
      drawer.classList.toggle("hidden", !open);
      backdrop.classList.toggle("hidden", !open);
      drawer.setAttribute("aria-hidden", open ? "false" : "true");
      backdrop.setAttribute("aria-hidden", open ? "false" : "true");
      document.body.classList.toggle("operator-approval-open", open);
    }

    function sortedVisibleRequests() {
      return requests
        .filter((item) => ["PUBLISH_CONFIRMED", "OPERATOR_APPROVED"].includes(item.status))
        .sort((left, right) => {
          const priority = (item) => item.status === "PUBLISH_CONFIRMED" ? 0 : 1;
          const statusOrder = priority(left) - priority(right);
          if (statusOrder !== 0) return statusOrder;
          return String(right.requested_at || "").localeCompare(String(left.requested_at || ""));
        });
    }

    async function loadExecutionState(requestId) {
      const response = await fetch(`/v1/customer-execution-requests/${requestId}/execution`);
      if (!response.ok) {
        return { error: await responseDetail(response) };
      }
      return response.json();
    }

    async function hydrateExecutionStates() {
      const approved = requests.filter((item) => item.status === "OPERATOR_APPROVED");
      const pairs = await Promise.all(approved.map(async (item) => [
        String(item.id),
        await loadExecutionState(item.id),
      ]));
      executionStates = new Map(pairs);
    }

    function render() {
      const visible = sortedVisibleRequests();
      const pending = visible.filter((item) => item.status === "PUBLISH_CONFIRMED").length;
      const approved = visible.filter((item) => item.status === "OPERATOR_APPROVED").length;
      summary.textContent = `${pending} ждут approval · ${approved} approved`;
      list.replaceChildren();

      if (!visible.length) {
        const empty = document.createElement("div");
        empty.className = "operator-approval-empty";
        empty.append(
          textNode("strong", "", "Нет customer-confirmed действий"),
          textNode("p", "muted", "Здесь появятся запросы со статусом PUBLISH_CONFIRMED.")
        );
        list.append(empty);
        return;
      }

      visible.forEach((request) => {
        const pendingApproval = request.status === "PUBLISH_CONFIRMED";
        const execution = executionStates.get(String(request.id));
        const card = document.createElement("article");
        card.className = `operator-approval-card ${pendingApproval ? "is-pending" : "is-approved"}`;
        card.dataset.requestId = String(request.id);

        const cardHeader = document.createElement("div");
        cardHeader.className = "operator-approval-card-header";
        const titleWrap = document.createElement("div");
        titleWrap.append(
          textNode("span", "operator-approval-platform", request.platform),
          textNode("h3", "", request.source_title)
        );
        const badge = textNode("span", "operator-approval-status", request.status);
        cardHeader.append(titleWrap, badge);

        const facts = document.createElement("div");
        facts.className = "operator-approval-facts";
        facts.append(
          createFact("Request", shortId(request.id)),
          createFact("Action", shortId(request.distribution_action_id)),
          createFact("Customer confirmed", formatDate(request.customer_publish_confirmed_at)),
          createFact("Operator approved", formatDate(request.operator_approved_at)),
          createFact("Locked target", request.source_url, { url: true }),
          createFact("Draft title", request.draft_title || "—")
        );

        const exact = document.createElement("div");
        exact.className = "operator-approval-exact";
        exact.append(
          createExactBlock("Exact context", request.context_text),
          createExactBlock("Exact content", request.content_text)
        );

        const fingerprint = document.createElement("div");
        fingerprint.className = "operator-approval-fingerprint";
        fingerprint.append(
          textNode("span", "operator-approval-fact-label", "Customer confirmation SHA-256"),
          textNode("code", "", request.customer_publish_confirmation_fingerprint)
        );

        const boundary = textNode(
          "p",
          "operator-approval-boundary",
          "Backend повторно сверит request/action binding и SHA-256 exact snapshot перед approval и перед execution."
        );

        const actionsRow = document.createElement("div");
        actionsRow.className = "operator-approval-actions";
        if (pendingApproval) {
          const approve = document.createElement("button");
          approve.className = "button button-primary";
          approve.type = "button";
          approve.textContent = "Approve exact action";
          approve.addEventListener("click", async () => {
            if (!window.confirm("Approve exactly this customer-confirmed action? Execution will remain separate.")) {
              return;
            }
            approve.disabled = true;
            approve.textContent = "Approving…";
            setAlert("");
            try {
              const response = await fetch(`/v1/customer-execution-requests/${request.id}/approve-action`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ confirm_approval: true }),
              });
              if (!response.ok) throw new Error(await responseDetail(response));
              const updated = await response.json();
              requests = requests.map((item) => item.id === updated.id ? updated : item);
              executionStates.set(String(updated.id), await loadExecutionState(updated.id));
              setAlert("Exact customer-confirmed action approved. Execution is still separate.", "success");
              render();
            } catch (error) {
              setAlert(`Approval blocked: ${error.message || error}`);
              approve.disabled = false;
              approve.textContent = "Approve exact action";
            }
          });
          actionsRow.append(approve);
        } else if (!execution) {
          actionsRow.append(textNode("strong", "operator-approval-complete", "Loading execution state…"));
        } else if (execution.error) {
          actionsRow.append(textNode("strong", "operator-approval-complete", "Execution blocked by binding/state validation"));
        } else if (execution.receipt) {
          actionsRow.append(textNode("strong", "operator-approval-complete", "Execution attempt recorded · retry disabled"));
        } else if (execution.action_status === "APPROVED" && execution.experiment_status === "APPROVED") {
          const execute = document.createElement("button");
          execute.className = "button button-primary operator-execution-button";
          execute.type = "button";
          execute.textContent = "Execute exact action";
          execute.addEventListener("click", async () => {
            if (!window.confirm("Execute exactly this approved customer action now? This may cause an external provider action. It will not auto-retry.")) {
              return;
            }
            execute.disabled = true;
            execute.textContent = "Executing…";
            setAlert("");
            try {
              const response = await fetch(`/v1/customer-execution-requests/${request.id}/execute-action`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ confirm_execution: true }),
              });
              if (!response.ok) throw new Error(await responseDetail(response));
              executionStates.set(String(request.id), await response.json());
              setAlert("Execution attempt recorded. This customer-bound endpoint will not auto-retry.", "success");
              render();
            } catch (error) {
              setAlert(`Execution blocked: ${error.message || error}`);
              execute.disabled = false;
              execute.textContent = "Execute exact action";
            }
          });
          actionsRow.append(execute);
        } else {
          actionsRow.append(textNode("strong", "operator-approval-complete", "Execution state is read-only"));
        }

        card.append(cardHeader, facts, exact, fingerprint, boundary);
        if (!pendingApproval && execution) card.append(createExecutionReceipt(execution));
        card.append(actionsRow);
        list.append(card);
      });
    }

    async function loadRequests() {
      if (loading) return;
      loading = true;
      refresh.disabled = true;
      refresh.textContent = "Загрузка…";
      setAlert("");
      try {
        const response = await fetch("/v1/customer-execution-requests");
        if (!response.ok) {
          const detail = await responseDetail(response);
          if (response.status === 401) {
            throw new Error(`Operator authentication required. ${detail}`);
          }
          throw new Error(detail);
        }
        const payload = await response.json();
        requests = Array.isArray(payload) ? payload : [];
        executionStates = new Map();
        await hydrateExecutionStates();
        render();
      } catch (error) {
        requests = [];
        executionStates = new Map();
        render();
        setAlert(`Queue unavailable: ${error.message || error}`);
      } finally {
        loading = false;
        refresh.disabled = false;
        refresh.textContent = "Обновить";
      }
    }

    trigger.addEventListener("click", () => {
      setOpen(true);
      loadRequests();
    });
    close.addEventListener("click", () => setOpen(false));
    backdrop.addEventListener("click", () => setOpen(false));
    refresh.addEventListener("click", loadRequests);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !drawer.classList.contains("hidden")) setOpen(false);
    });
  }

  function mountOperatorAccess() {
    if (document.getElementById(GLOBAL_INPUT_ID)) return;
    const actions = document.querySelector(".topbar-actions");
    if (!actions) return;

    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = "/app/assets/operator-auth.v1.css";
    document.head.append(style);

    const wrap = document.createElement("label");
    wrap.className = "global-operator-access";
    wrap.title = "Ключ живёт только в памяти страницы и не сохраняется браузером";

    const label = document.createElement("span");
    label.textContent = "Operator";
    const input = document.createElement("input");
    input.id = GLOBAL_INPUT_ID;
    input.type = "password";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "key";
    input.setAttribute("aria-label", "Partizan operator key");

    wrap.append(label, input);
    actions.prepend(wrap);

    const executionInput = document.getElementById(EXECUTION_INPUT_ID);
    if (executionInput) {
      syncInputs(input, executionInput);
      syncInputs(executionInput, input);
    }

    mountCustomerApprovalQueue(actions);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountOperatorAccess, { once: true });
  } else {
    mountOperatorAccess();
  }
})();
