(() => {
  "use strict";

  const EXECUTION_STORAGE_KEY = "partizan.execution.v1";

  function loadExecutionV1() {
    const script = document.createElement("script");
    script.src = "/app/assets/execution.v1.js";
    script.async = false;
    script.addEventListener("load", reopenAfterPaidActivation);
    document.head.append(script);
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

  loadExecutionV1();
})();
