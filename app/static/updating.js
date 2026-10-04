/**
 * Friendly "app updating" overlay for Render restarts / gateway downtime.
 * Used by the main app and login page.
 */
(function (global) {
  const COUNTDOWN_SEC = 60;
  const PROBE_MS = 4000;

  const state = {
    visible: false,
    secondsLeft: COUNTDOWN_SEC,
    countdownTimer: null,
    probeTimer: null,
    failStreak: 0,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function ensureOverlay() {
    let root = $("app-updating-overlay");
    if (!root) {
      root = document.createElement("div");
      root.id = "app-updating-overlay";
      root.className = "app-updating-overlay hidden";
      root.setAttribute("role", "alertdialog");
      root.setAttribute("aria-modal", "true");
      root.setAttribute("aria-labelledby", "app-updating-title");
      root.setAttribute("aria-describedby", "app-updating-copy");
      root.innerHTML = `
      <div class="app-updating-card">
        <div class="app-updating-icon-wrap" aria-hidden="true">
          <img class="app-updating-icon" src="/static/favicon.svg" width="72" height="72" alt="">
        </div>
        <h2 id="app-updating-title">Bright Health is updating</h2>
        <p id="app-updating-copy">
          We’re rolling out a fresh version. This page will refresh in
          <span class="app-updating-countdown" id="app-updating-countdown">${COUNTDOWN_SEC}</span>
          seconds — or tap Refresh anytime.
        </p>
        <button type="button" class="btn primary" id="btn-app-updating-refresh">Refresh now</button>
        <p class="app-updating-hint">Thanks for waiting — you’re all set once this finishes.</p>
      </div>
    `;
      document.body.appendChild(root);
    }
    const btn = $("btn-app-updating-refresh");
    if (btn && !btn.dataset.bound) {
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => refreshNow());
    }
    return root;
  }

  function refreshNow() {
    window.location.reload();
  }

  function updateCountdownLabel() {
    const el = $("app-updating-countdown");
    if (el) el.textContent = String(Math.max(0, state.secondsLeft));
  }

  function clearTimers() {
    if (state.countdownTimer) {
      clearInterval(state.countdownTimer);
      state.countdownTimer = null;
    }
    if (state.probeTimer) {
      clearInterval(state.probeTimer);
      state.probeTimer = null;
    }
  }

  async function probeHealth() {
    try {
      const res = await fetch("/api/version", {
        credentials: "include",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        state.failStreak = 0;
        refreshNow();
      }
    } catch {
      /* still updating */
    }
  }

  function showUpdatingOverlay() {
    const root = ensureOverlay();
    if (state.visible) return;
    state.visible = true;
    state.secondsLeft = COUNTDOWN_SEC;
    root.classList.remove("hidden");
    document.body.classList.add("app-updating-active");
    updateCountdownLabel();
    clearTimers();
    state.countdownTimer = setInterval(() => {
      state.secondsLeft -= 1;
      updateCountdownLabel();
      if (state.secondsLeft <= 0) {
        clearTimers();
        refreshNow();
      }
    }, 1000);
    state.probeTimer = setInterval(probeHealth, PROBE_MS);
    setTimeout(probeHealth, 1200);
  }

  function hideUpdatingOverlay() {
    const root = $("app-updating-overlay");
    if (!root || !state.visible) return;
    state.visible = false;
    clearTimers();
    root.classList.add("hidden");
    document.body.classList.remove("app-updating-active");
  }

  function isGatewayStatus(status) {
    return status === 502 || status === 503 || status === 504;
  }

  function isProxyGatewayResponse(res, data) {
    if (!res || !isGatewayStatus(res.status)) return false;
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    // Our API returns JSON error bodies; Render's proxy often returns HTML/plain.
    if (ct.includes("application/json") && (data?.detail || data?.message)) {
      return false;
    }
    return true;
  }

  function isGatewayError(err) {
    if (!err) return false;
    if (err.isGateway || err.gateway) return true;
    if (isGatewayStatus(err.status) && err.isProxyGateway) return true;
    if (isGatewayStatus(err.status) && !err.appDetail) return true;
    const msg = String(err.message || err || "");
    if (/Request failed \(50[234]\)/i.test(msg)) return true;
    return /Failed to fetch|NetworkError|Load failed|Bad Gateway|Service Unavailable|Gateway Timeout/i.test(
      msg
    );
  }

  function noteGatewayFailure({ immediate = false } = {}) {
    state.failStreak += 1;
    if (immediate || state.failStreak >= 2) {
      showUpdatingOverlay();
    }
  }

  function noteGatewayRecovery() {
    state.failStreak = 0;
    if (state.visible) refreshNow();
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    const register = () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
    };
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });
  }

  function isSameOrigin(input) {
    try {
      const url = typeof input === "string" ? input : input?.url;
      if (!url) return true;
      if (url.startsWith("/") || url.startsWith("./")) return true;
      return new URL(url, window.location.origin).origin === window.location.origin;
    } catch {
      return true;
    }
  }

  function interceptFetch() {
    if (typeof window.fetch !== "function" || window.fetch.__brightUpdatingWrapped) return;
    const original = window.fetch.bind(window);
    async function wrapped(input, init) {
      try {
        const res = await original(input, init);
        if (isSameOrigin(input) && isGatewayStatus(res.status)) {
          let data = {};
          const ct = (res.headers.get("content-type") || "").toLowerCase();
          if (ct.includes("application/json")) {
            data = await res.clone().json().catch(() => ({}));
          }
          if (isProxyGatewayResponse(res, data)) {
            noteGatewayFailure({ immediate: true });
          }
        }
        return res;
      } catch (err) {
        if (isSameOrigin(input) && isGatewayError(err)) {
          noteGatewayFailure({ immediate: true });
        }
        throw err;
      }
    }
    wrapped.__brightUpdatingWrapped = true;
    window.fetch = wrapped;
  }

  global.BrightUpdating = {
    show: showUpdatingOverlay,
    hide: hideUpdatingOverlay,
    noteGatewayFailure,
    noteGatewayRecovery,
    isGatewayError,
    isGatewayStatus,
    isProxyGatewayResponse,
    isVisible: () => state.visible,
    registerServiceWorker,
    ensureOverlay,
  };

  interceptFetch();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      ensureOverlay();
      registerServiceWorker();
    });
  } else {
    ensureOverlay();
    registerServiceWorker();
  }
})(window);
