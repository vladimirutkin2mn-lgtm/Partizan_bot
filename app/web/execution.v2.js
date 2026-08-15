(() => {
  "use strict";

  const EXECUTION_STORAGE_KEY = "partizan.execution.v1";
  const RESULTS_STYLE_ID = "partizan-results-style";
  const RESULTS_SCRIPT_ID = "partizan-results-script";
  const INTEGRATION_STYLE_ID = "partizan-integration-style";
  const INTEGRATION_SCRIPT_ID = "partizan-integration-script";
  const INTEGRATION_STATUS_STYLE_ID = "partizan-integration-status-style";
  const INTEGRATION_STATUS_SCRIPT_ID = "partizan-integration-status-script";
  const INTEGRATION_GUIDE_STYLE_ID = "partizan-integration-guide-style";
  const INTEGRATION_GUIDE_SCRIPT_ID = "partizan-integration-guide-script";
  const AUTONOMY_STYLE_ID = "partizan-autonomy-style";
  const AUTONOMY_SCRIPT_ID = "partizan-autonomy-script";
  const CREATIVE_STYLE_ID = "partizan-creative-style";
  const CREATIVE_SCRIPT_ID = "partizan-creative-script";
  const PUBLISHING_STYLE_ID = "partizan-publishing-style";
  const PUBLISHING_SCRIPT_ID = "partizan-publishing-script";
  const OUTREACH_STYLE_ID = "partizan-outreach-style";
  const OUTREACH_SCRIPT_ID = "partizan-outreach-script";
  const OUTREACH_AUTOSEND_STYLE_ID = "partizan-outreach-autosend-style";
  const OUTREACH_AUTOSEND_SCRIPT_ID = "partizan-outreach-autosend-script";

  function loadExecutionV1() {
    const script = document.createElement("script");
    script.src = "/app/assets/execution.v1.js";
    script.async = false;
    script.addEventListener("load", reopenAfterPaidActivation);
    document.head.append(script);
  }

  function loadOutreachAutosendAssets() {
    if (!document.getElementById(OUTREACH_AUTOSEND_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = OUTREACH_AUTOSEND_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/outreach-autosend.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(OUTREACH_AUTOSEND_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = OUTREACH_AUTOSEND_SCRIPT_ID;
      script.src = "/app/assets/outreach-autosend.v1.js";
      script.async = false;
      document.head.append(script);
    }
  }

  function loadOutreachAssets() {
    if (!document.getElementById(OUTREACH_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = OUTREACH_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/outreach.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(OUTREACH_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = OUTREACH_SCRIPT_ID;
      script.src = "/app/assets/outreach.v1.js";
      script.async = false;
      script.addEventListener("load", loadOutreachAutosendAssets);
      document.head.append(script);
    } else {
      loadOutreachAutosendAssets();
    }
  }

  function loadPublishingAssets() {
    if (!document.getElementById(PUBLISHING_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = PUBLISHING_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/publishing.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(PUBLISHING_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = PUBLISHING_SCRIPT_ID;
      script.src = "/app/assets/publishing.v1.js";
      script.async = false;
      script.addEventListener("load", loadOutreachAssets);
      document.head.append(script);
    } else {
      loadOutreachAssets();
    }
  }

  function loadCreativeAssets() {
    if (!document.getElementById(CREATIVE_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = CREATIVE_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/creative.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(CREATIVE_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = CREATIVE_SCRIPT_ID;
      script.src = "/app/assets/creative.v1.js";
      script.async = false;
      script.addEventListener("load", loadPublishingAssets);
      document.head.append(script);
    } else {
      loadPublishingAssets();
    }
  }

  function loadAutonomyAssets() {
    if (!document.getElementById(AUTONOMY_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = AUTONOMY_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/autonomy.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(AUTONOMY_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = AUTONOMY_SCRIPT_ID;
      script.src = "/app/assets/autonomy.v1.js";
      script.async = false;
      script.addEventListener("load", loadCreativeAssets);
      document.head.append(script);
    } else {
      loadCreativeAssets();
    }
  }

  function loadIntegrationGuideAssets() {
    if (!document.getElementById(INTEGRATION_GUIDE_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = INTEGRATION_GUIDE_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/integration-guide.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(INTEGRATION_GUIDE_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = INTEGRATION_GUIDE_SCRIPT_ID;
      script.src = "/app/assets/integration-guide.v1.js";
      script.async = false;
      script.addEventListener("load", loadAutonomyAssets);
      document.head.append(script);
    } else {
      loadAutonomyAssets();
    }
  }

  function loadIntegrationStatusAssets() {
    if (!document.getElementById(INTEGRATION_STATUS_STYLE_ID)) {
      const style = document.createElement("link");
      style.id = INTEGRATION_STATUS_STYLE_ID;
      style.rel = "stylesheet";
      style.href = "/app/assets/integration-status.v1.css";
      document.head.append(style);
    }
    if (!document.getElementById(INTEGRATION_STATUS_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = INTEGRATION_STATUS_SCRIPT_ID;
      script.src = "/app/assets/integration-status.v1.js";
      script.async = false;
      script.addEventListener("load", loadIntegrationGuideAssets);
      document.head.append(script);
    } else {
      loadIntegrationGuideAssets();
    }
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
      script.addEventListener("load", loadIntegrationStatusAssets);
      document.head.append(script);
    } else {
      loadIntegrationStatusAssets();
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
