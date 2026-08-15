(() => {
  "use strict";

  const OPERATOR_HEADER = "X-Partizan-Operator-Key";
  const INPUT_ID = "global-operator-key";
  const nativeFetch = window.fetch.bind(window);

  function operatorKey() {
    const input = document.getElementById(INPUT_ID);
    return input ? input.value.trim() : "";
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
    if (key && !headers.has(OPERATOR_HEADER)) headers.set(OPERATOR_HEADER, key);
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

  function mountOperatorAccess() {
    if (document.getElementById(INPUT_ID)) return;
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
    input.id = INPUT_ID;
    input.type = "password";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "key";
    input.setAttribute("aria-label", "Partizan operator key");

    wrap.append(label, input);
    actions.prepend(wrap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountOperatorAccess, { once: true });
  } else {
    mountOperatorAccess();
  }
})();
