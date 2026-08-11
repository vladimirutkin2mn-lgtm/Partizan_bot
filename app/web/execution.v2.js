(() => {
  "use strict";

  const EXECUTION_STORAGE_KEY = "partizan.execution.v1";
  const RESULTS_STYLE_ID = "partizan-results-style";
  const RESULTS_SCRIPT_ID = "partizan-results-script";
  const INTEGRATION_STYLE_ID = "partizan-integration-style";
  const INTEGRATION_SCRIPT_ID = "partizan-integration-script";

  function loadExecutionV1() {
    const script = document.createElement("script");
    script.src = "/app/assets/execution.v1.js";
    script.async = false;
    script.addEventListener("load", reopenAfterPaidActivation);
    document.head.append(script);
  }

  function loadIntegrationAssets() {
    if (!document.getElementById(INTEGRATION_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = INTEGRATION_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/integration.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(INTEGRATION_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = INTEGRATION_SCRIPT_ID;
      script.src = "/app/assets/integration.v1.js";
      script.async = false;
      document.head.append(script);
    }
  }

  function loadResultsAssets() {
    if (!document.getElementById(RESULTS_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = RESULTS_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/results.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(RESULTS_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = RESULTS_SCRIPT_ID;
      script.src = "/app/assets/results.v1.js";
      script.async = false;
      script.addEventListener("load", loadIntegrationAssets);
      document.head.append(script);
    } else {
      loadIntegrationAssets();
    }
  }

  function reopenAfterPaidActivation() {
    try {
      const raw = sessionStorage.getItem(EXECUTION_STORAGE_KEY);
      if (!raw) return;
      const state = JSON.parse(raw);
      if (!state.reopenAfterPaidActivation) return;
      delete state.reopenAfterPaidActivation;
      sessionStorage.setItem(EXECUTION_STORAGE_KEY, JSON.stringify(state));
      window.setTimeout(() => {
        const button = document.querySelector("#open-current-execution");
        if (button && !button.classList.contains("hidden")) button.click();
      }, 0);
    } catch (_) {
      return;
    }
  }

  window.addEventListener("partizan:execution-updated", () => {
    try {
      const raw = sessionStorage.getItem(EXECUTION_STORAGE_KEY);
      if (!raw) return;
      const state = JSON.parse(raw);
      state.reopenAfterPaidActivation = true;
      sessionStorage.setItem(EXECUTION_STORAGE_KEY, JSON.stringify(state));
      window.location.reload();
    } catch (_) {
      window.location.reload();
    }
  });

  loadResultsAssets();
  loadExecutionV1();
})();
