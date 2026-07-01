const state = {
  documents: [],
  documentIndex: [],
  libraryPage: 1,
  libraryFilter: "",
  libraryTotal: 0,
  libraryCounts: {},
  selectedIds: new Set(),
  analyses: [],
  latestAnalysis: null,
  models: [],
  settings: {},
  selectedOpenItemId: null,
  selectedOpenItem: null,
  analysisRunning: false,
  analysisJobId: null,
  auditEvents: [],
  auditOffset: 0,
  auditTotal: 0,
  sourceLegend: [],
  referenceRegistry: {},
  activeDocumentId: null,
  customTasks: { jobs: [], drafts: [], activeJob: null },
  selectedCustomTaskId: null,
  activeJobType: null,
  refiningCustomTaskId: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const THEME_KEY = "beatit-theme";

function getTheme() {
  return document.documentElement.getAttribute("data-theme") === "day" ? "day" : "night";
}

function applyTheme(theme) {
  const next = theme === "day" ? "day" : "night";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem(THEME_KEY, next);

  const toggle = $("#theme-toggle");
  if (toggle) {
    toggle.setAttribute(
      "aria-label",
      next === "night" ? "Switch to day theme" : "Switch to night theme"
    );
    toggle.title = next === "night" ? "Day theme" : "Night theme";
  }
}

function toggleTheme() {
  applyTheme(getTheme() === "night" ? "day" : "night");
}

function initTheme() {
  applyTheme(getTheme());
  $("#theme-toggle")?.addEventListener("click", toggleTheme);
}

function toast(message, type = "success") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast ${type}`;
  setTimeout(() => el.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? 120000;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const { timeoutMs: _timeoutMs, ...fetchOptions } = options;

  try {
    const res = await fetch(path, {
      credentials: "include",
      headers: fetchOptions.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      signal: controller.signal,
      ...fetchOptions,
    });
    if (res.status === 401 && !path.includes("/login")) {
      window.location.href = "/login";
      throw new Error("Please sign in");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      let message = data.detail || data.message || `Request failed (${res.status})`;
      if (res.status === 502 && path.includes("/analyze")) {
        message =
          typeof data.detail === "string" && data.detail.startsWith("Analysis failed:")
            ? data.detail
            : "Analysis failed — the model may have timed out or returned an error. Wait a moment and try again.";
      }
      const error = new Error(message);
      error.status = res.status;
      throw error;
    }
    return data;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("Request timed out — check your connection and try again.");
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatVersionUpdated(isoDate) {
  if (!isoDate) return "";
  try {
    const iso = isoDate.includes("T") ? isoDate : `${isoDate}T12:00:00`;
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return isoDate;
    const date = dt.toLocaleDateString("en-US", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "long",
      day: "numeric",
    });
    const time = dt.toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
    return `${date} · ${time} ET`;
  } catch {
    return isoDate;
  }
}

function renderAppVersion(data) {
  const el = $("#app-version");
  if (!el || !data?.version) return;
  const updated = data.updated_display || formatVersionUpdated(data.updated);
  const name = data.name || "BeatIt";
  el.textContent = `${name} v${data.version}${updated ? ` · updated ${updated}` : ""}`;
}

async function loadAppVersion() {
  try {
    const res = await fetch("/api/version");
    if (!res.ok) return;
    renderAppVersion(await res.json());
  } catch {
    /* keep placeholder */
  }
}

async function checkHealth() {
  const pill = $("#llm-status");
  const settingsConn = $("#settings-llm-connection");

  try {
    const data = await api("/api/health");
    updateLlmStatusDisplay(data, pill, settingsConn);
  } catch {
    if (pill) {
      pill.textContent = "Offline";
      pill.className = "status-pill bad";
      pill.classList.remove("hidden");
    }
    if (settingsConn) {
      settingsConn.textContent = "Could not reach the API.";
      settingsConn.className = "settings-llm-connection bad";
    }
  }
}

function updateLlmStatusDisplay(data, pill, settingsConn) {
  const llm = data?.llm || {};
  const active = llm.active || {};
  const provider = llm.configured_provider || "openrouter";
  const model =
    state.settings?.openrouter_model ||
    llm.openrouter?.model ||
    active.model ||
    "Unknown model";

  if (active.ready) {
    if (pill) pill.classList.add("hidden");
    if (settingsConn) {
      const via =
        active.provider === "ollama"
          ? `Ollama VM · ${active.model || llm.ollama?.configured_model || "model"}`
          : `OpenRouter · ${active.model || model}`;
      const mode =
        provider === "auto" && active.provider === "ollama"
          ? " (auto — VM active)"
          : provider === "auto"
            ? " (auto — cloud fallback)"
            : "";
      settingsConn.textContent = `Connected · ${via}${mode}`;
      settingsConn.className = "settings-llm-connection ok";
    }
    return;
  }

  let headerText = "LLM unavailable";
  let settingsText = "LLM is not available.";
  if (provider === "ollama") {
    const err =
      llm.ollama?.error ||
      active.error ||
      "Ollama not reachable — check OLLAMA_BASE_URL and Tailscale";
    headerText = "Ollama offline";
    settingsText = err;
  } else if (provider === "auto") {
    const ollama = llm.ollama || {};
    const or = llm.openrouter || {};
    if (!or.connected && or.error) {
      headerText = "LLM error";
      settingsText = `Auto mode: VM unreachable and OpenRouter failed — ${or.error}`;
    } else if (!ollama.connected) {
      headerText = "VM offline";
      settingsText = `Auto mode: Ollama unreachable (${ollamaReachabilityHint(ollama)}). Using OpenRouter when configured.`;
    } else if (!ollama.model_available) {
      settingsText = `Ollama connected but model missing — run ollama pull ${ollama.configured_model || "your model"} on the VM`;
    } else if (active.error) {
      settingsText = active.error;
    }
  } else if (provider === "openrouter") {
    const err = llm.openrouter?.error || active.error || "Set OPENROUTER_API_KEY";
    headerText = "LLM error";
    settingsText = `OpenRouter error: ${err}`;
  } else if (active.error) {
    settingsText = active.error;
  }

  if (pill) {
    pill.textContent = headerText;
    pill.className = "status-pill bad";
    pill.classList.remove("hidden");
  }
  if (settingsConn) {
    settingsConn.textContent = settingsText;
    settingsConn.className = "settings-llm-connection bad";
  }
}

function renderSettingsOllamaInfo(settings, llmHealth) {
  const el = $("#settings-ollama-info");
  if (!el) return;
  const provider = settings?.llm_provider || "openrouter";
  if (provider === "openrouter") {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const base = settings?.ollama_base_url || "—";
  const model = settings?.ollama_model || "—";
  const ollama = llmHealth?.ollama || {};
  const models = (ollama.available_models || []).slice(0, 8);
  const modelList = models.length ? models.join(", ") : "none reported";
  el.classList.remove("hidden");
  el.innerHTML = `
    <p><strong>Provider:</strong> ${escapeHtml(provider)} · <strong>Ollama URL:</strong> ${escapeHtml(base)} · <strong>Model:</strong> ${escapeHtml(model)}</p>
    <p>VM models: ${escapeHtml(modelList)}${(ollama.available_models || []).length > 8 ? " …" : ""}</p>
    <p>Setup guide: <code>docs/TAILSCALE_OLLAMA_SETUP.md</code> · test: <code>./scripts/check_ollama.sh</code></p>`;
}

function ollamaReachabilityHint(ollama) {
  return ollama?.base_url || "check Tailscale and firewall";
}

function docPathLines(doc) {
  const lines = [];
  const meta = doc.metadata || {};
  if (meta.original_filename) lines.push({ label: "Original file", value: meta.original_filename });
  if (meta.modality) lines.push({ label: "Modality", value: meta.modality });
  if (meta.file_size_label) lines.push({ label: "File size", value: meta.file_size_label });
  if (meta.relative_path && meta.relative_path !== meta.original_filename) {
    lines.push({ label: "Folder path", value: meta.relative_path });
  }
  if (doc.file_path) lines.push({ label: "Stored file", value: doc.file_path });
  if (doc.extracted_path) lines.push({ label: "Extracted text", value: doc.extracted_path });
  if (doc.source_uri) lines.push({ label: "Source URL", value: doc.source_uri });
  return lines;
}

function renderPathLines(lines) {
  if (!lines.length) return "";
  return lines
    .map(({ label, value }) => `<code>${escapeHtml(label)}: ${escapeHtml(value)}</code>`)
    .join("");
}

const UPLOAD_BANNER_KEY = "beatit-upload-banner";

function dismissUploadResult() {
  const panel = $("#upload-result");
  if (!panel) return;
  panel.classList.add("hidden");
  try {
    sessionStorage.setItem(UPLOAD_BANNER_KEY, "dismissed");
  } catch {
    /* ignore */
  }
}

function showUploadResult(doc) {
  const panel = $("#upload-result");
  const title = $("#upload-result-title");
  const paths = $("#upload-result-paths");
  if (!panel || !doc) return;

  try {
    sessionStorage.removeItem(UPLOAD_BANNER_KEY);
  } catch {
    /* ignore */
  }

  title.textContent = doc.title;
  paths.innerHTML = renderPathLines(docPathLines(doc));
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function initUploadResultBanner() {
  try {
    if (sessionStorage.getItem(UPLOAD_BANNER_KEY) === "dismissed") {
      $("#upload-result")?.classList.add("hidden");
    }
  } catch {
    /* ignore */
  }
}

function bindFileInput(inputId, labelSelector) {
  const input = $(inputId);
  if (!input) return;
  input.addEventListener("change", () => {
    const label = input.closest(".file-label");
    if (!label) return;
    let nameEl = label.querySelector(".file-name");
    const file = input.files[0];
    if (!file) {
      nameEl?.remove();
      return;
    }
    if (!nameEl) {
      nameEl = document.createElement("span");
      nameEl.className = "file-name";
      label.appendChild(nameEl);
    }
    nameEl.textContent = `Selected: ${file.name}`;
  });
}

function defaultPdfTitle(file) {
  return file?.name || "";
}

function bindPdfFileInput() {
  const input = $("#pdf-file");
  const titleInput = $("#pdf-title");
  if (!input) return;

  titleInput?.addEventListener("input", () => {
    titleInput.dataset.userEdited = "1";
  });

  input.addEventListener("change", () => {
    const label = input.closest(".file-label");
    const file = input.files[0];
    if (label) {
      let nameEl = label.querySelector(".file-name");
      if (!file) {
        nameEl?.remove();
        if (titleInput && titleInput.dataset.userEdited !== "1") titleInput.value = "";
        return;
      }
      if (!nameEl) {
        nameEl = document.createElement("span");
        nameEl.className = "file-name";
        label.appendChild(nameEl);
      }
      nameEl.textContent = `Selected: ${file.name}`;
    }
    if (file && titleInput && titleInput.dataset.userEdited !== "1") {
      titleInput.value = defaultPdfTitle(file);
    }
  });
}

function setAnalysisRunning(running, jobId = null, jobType = null) {
  state.analysisRunning = running;
  state.analysisJobId = running ? jobId : null;
  state.activeJobType = running ? jobType : null;
  const isCustom = jobType === "query";
  const banner = $("#analyze-running-banner");
  const loading = $("#analyze-loading");
  banner?.classList.toggle("hidden", !running || isCustom);
  loading?.classList.toggle("hidden", !running || isCustom);
  if (running && isCustom) {
    updateCustomTasksRunningBanner(true);
  } else {
    updateCustomTasksRunningBanner(false);
  }
  if (running) {
    $("#analyze-actions-card")?.setAttribute("open", "");
  }
  ["#btn-baseline", "#btn-summarize", "#btn-analyze", "#btn-reassess-baseline"].forEach((sel) => {
    const btn = $(sel);
    if (!btn) return;
    btn.disabled = running;
    btn.setAttribute("aria-disabled", running ? "true" : "false");
  });
  ["#btn-scope-select-all", "#btn-scope-clear", "#btn-scope-match-last", "#btn-scope-library"].forEach((sel) => {
    const btn = $(sel);
    if (!btn) return;
    btn.disabled = running;
  });
}

async function pollAnalysisJob(jobId, { isCustomQuery = false } = {}) {
  const deadline = Date.now() + 30 * 60 * 1000;
  while (Date.now() < deadline) {
    const data = await api(`/api/analyze/jobs/${jobId}`);
    const job = data.job;
    if (job.status === "completed") {
      if (job.analysis) return job.analysis;
      if (job.analysis_id) {
        const byId = await api(`/api/analyses/${job.analysis_id}`);
        if (byId.analysis) return byId.analysis;
      }
      if (!isCustomQuery) {
        const latest = await api("/api/analyses/latest");
        if (latest.analysis) return latest.analysis;
      }
      throw new Error("Analysis finished but results could not be loaded. Check Custom Tasks.");
    }
    if (job.status === "failed") {
      throw new Error(job.error || "Analysis failed");
    }
    if (isCustomQuery) {
      updateCustomTasksRunningBanner(true, job.query);
    }
    await sleep(2000);
  }
  if (isCustomQuery) {
    throw new Error("Custom task is still running. Check Custom Tasks for status.");
  }
  const latest = await api("/api/analyses/latest");
  if (latest.analysis) return latest.analysis;
  throw new Error("Analysis is taking longer than expected. Refresh the page to check status.");
}

async function startAnalysisJob({ query = "", baseline = false, summarize = false, assessmentGuidance = "" } = {}) {
  const docIds = state.selectedIds.size ? [...state.selectedIds] : null;

  if (summarize) {
    const qs = docIds ? "?" + docIds.map((id) => `document_ids=${encodeURIComponent(id)}`).join("&") : "";
    const data = await api(`/api/analyze/summarize${qs}`, { method: "POST" });
    return { jobId: data.job.id, jobType: "summarize" };
  }

  const guidance = (assessmentGuidance || "").trim();
  const data = await api("/api/analyze", {
    method: "POST",
    body: JSON.stringify({
      query,
      document_ids: docIds,
      include_baseline_assessment: baseline,
      assessment_guidance: guidance || null,
    }),
  });
  const jobType = baseline && !query.trim() ? "baseline" : "query";
  return { jobId: data.job.id, jobType: data.job?.job_type || jobType };
}

function finishAnalysisRun(analysis) {
  switchTab("analyze");
  renderLatestAssessment(analysis);
  state.analyses = [analysis, ...state.analyses.filter((a) => a.id !== analysis.id)];
  toast(`Assessment saved · ${formatTimestamp(analysis.created_at)}`);
  scrollToAssessmentResults();
}

function finishCustomTaskRun(analysis, queryText = "") {
  state.selectedCustomTaskId = analysis?.id || null;
  switchTab("custom-tasks");
  loadCustomTasks().then(() => {
    if (analysis?.id) selectCustomTask(analysis.id);
  });
  if (queryText) {
    if ($("#analyze-query")) $("#analyze-query").value = "";
    if ($("#custom-task-query")) $("#custom-task-query").value = "";
  }
  toast("Custom task complete — review the draft below");
  updateCustomTasksRunningBanner(false);
}

function scrollToAssessmentResults() {
  const target = $("#executive-summary-card") || $("#analyze-results-section");
  scrollToElement(target);
}

function scrollToOpenItems() {
  if (!$("#panel-analyze")?.classList.contains("active")) {
    switchTab("analyze");
  }
  requestAnimationFrame(() => {
    scrollToElement($("#open-items-card"));
  });
}

function getStickyHeaderOffset(extra = 16) {
  const header = document.querySelector(".header");
  if (!header) return 96 + extra;
  return header.getBoundingClientRect().height + extra;
}

function scrollToElement(element, { behavior = "smooth", offset = null } = {}) {
  if (!element) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const scrollBehavior = reduceMotion ? "auto" : behavior;
  const topOffset = offset ?? getStickyHeaderOffset();
  const targetTop = element.getBoundingClientRect().top + window.scrollY - topOffset;
  window.scrollTo({ top: Math.max(0, targetTop), behavior: scrollBehavior });
}

function scrollToCustomTaskDetail() {
  const anchor = $("#custom-task-detail-top") || $("#custom-task-detail");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => scrollToElement(anchor));
  });
}

function updateHomeToolbar() {
  const hasAssessment = Boolean(state.latestAnalysis);
  const onHome = $("#panel-analyze")?.classList.contains("active");
  $("#btn-export-pdf")?.classList.toggle("hidden", !(hasAssessment && onHome));
}

function updateHomeWorkflow() {
  updateHomeToolbar();
}

function renderHomeState(hasAssessment) {
  $("#analyze-results-section")?.classList.toggle("hidden", !hasAssessment);
  $("#analyze-actions-card")?.classList.toggle("analyze-actions-secondary", hasAssessment);
  if (hasAssessment && !state.analysisRunning) {
    setAnalyzeActionsExpanded(false);
  } else if (!hasAssessment) {
    setAnalyzeActionsExpanded(true);
  }
  updateHomeToolbar();
}

function initHowToNavigation() {
  $("#panel-howto")?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-howto-nav]");
    if (!btn) return;
    event.preventDefault();
    const target = btn.dataset.howtoNav;
    if (target === "open-items") {
      scrollToOpenItems();
      return;
    }
    switchTab(target);
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}

function initScrollTop() {
  const btn = $("#btn-scroll-top");
  if (!btn) return;

  const threshold = 320;
  const update = () => {
    const show = window.scrollY > threshold;
    btn.classList.toggle("is-visible", show);
    btn.hidden = !show;
  };

  btn.addEventListener("click", () => {
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    window.scrollTo({ top: 0, behavior });
  });

  window.addEventListener("scroll", update, { passive: true });
  update();
}

function setAnalyzeActionsExpanded(expanded) {
  const card = $("#analyze-actions-card");
  if (!card) return;
  if (expanded) card.setAttribute("open", "");
  else card.removeAttribute("open");
}

async function resumeActiveAnalysisJob() {
  try {
    const data = await api("/api/analyze/jobs/active");
    if (!data.job) return;

    const jobId = data.job.id;
    const payload = await api(`/api/analyze/jobs/${jobId}`);
    const job = payload.job;
    const isCustomQuery = job.job_type === "query";

    if (job.status === "completed" && job.analysis) {
      if (isCustomQuery) finishCustomTaskRun(job.analysis);
      else finishAnalysisRun(job.analysis);
      return;
    }
    if (job.status === "failed") {
      toast(job.error || "Analysis failed", "error");
      if (isCustomQuery) loadCustomTasks();
      return;
    }

    setAnalysisRunning(true, jobId, job.job_type);
    if (isCustomQuery) {
      switchTab("custom-tasks");
      updateCustomTasksRunningBanner(true, job.query);
    }
    const analysis = await pollAnalysisJob(jobId, { isCustomQuery });
    if (isCustomQuery) finishCustomTaskRun(analysis, job.query);
    else finishAnalysisRun(analysis);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setAnalysisRunning(false);
  }
}

function resumeActiveAnalysisJobInBackground() {
  resumeActiveAnalysisJob().catch((err) => {
    setAnalysisRunning(false);
    if (err?.message) toast(err.message, "error");
  });
}

function switchTab(name, options = {}) {
  $$(".tab").forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
  if (name === "library" && !options.skipLibraryLoad) {
    loadDocuments().catch((e) => toast(e.message, "error"));
  }
  if (name === "history") loadHistory();
  if (name === "analyze") loadLatestAssessment();
  if (name === "custom-tasks") loadCustomTasks();
  if (name === "settings") loadSettings();
  updateHomeToolbar();
}

function jobStatusLabel(status) {
  if (status === "pending") return "Queued";
  if (status === "running") return "Running";
  if (status === "completed") return "Complete";
  if (status === "failed") return "Failed";
  return status || "Unknown";
}

function updateCustomTasksRunningBanner(running, query = "", options = {}) {
  const section = $("#custom-tasks-running-section");
  const el = $("#custom-tasks-running");
  if (!section || !el) return;
  if (!running) {
    section.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const label = options.refining ? "Refining" : "Running";
  const hint = options.refining
    ? "Updating this draft in place. The revised answer replaces the current draft when finished."
    : "This may take several minutes. You can leave this tab — the draft will appear below when finished.";
  section.classList.remove("hidden");
  el.innerHTML = `
    <div class="custom-task-running-card" role="status" aria-live="polite">
      <span class="badge status-running">${label}</span>
      <p class="custom-task-running-query">${escapeHtml(truncate(query || "Custom analysis", 200))}</p>
      <p class="muted small">${escapeHtml(hint)}</p>
    </div>`;
}

function updateCustomTasksBadge() {
  const badge = $("#custom-tasks-badge");
  if (!badge) return;
  const drafts = state.customTasks?.drafts?.length || 0;
  const running =
    state.analysisRunning && state.activeJobType === "query" ? 1 : state.customTasks?.activeJob ? 1 : 0;
  const total = drafts + running;
  if (total === 0) {
    badge.classList.add("hidden");
    badge.textContent = "";
    return;
  }
  badge.classList.remove("hidden");
  badge.textContent = running ? `${running} running · ${drafts} draft${drafts === 1 ? "" : "s"}` : `${drafts} draft${drafts === 1 ? "" : "s"}`;
}

function formatActor(actor) {
  return actor || "Unknown user";
}

function renderCustomTasksList() {
  const list = $("#custom-tasks-drafts-list");
  if (!list) return;
  const drafts = state.customTasks?.drafts || [];
  if (!drafts.length) {
    list.innerHTML = `<p class="muted">No custom task drafts yet. Run a custom analysis from Home.</p>`;
    return;
  }
  const refiningId =
    state.customTasks?.activeJob?.refine_analysis_id ||
    (state.analysisRunning ? state.refiningCustomTaskId : null);
  list.innerHTML = drafts
    .map(
      (draft) => `
      <article class="custom-task-item${draft.id === state.selectedCustomTaskId ? " selected" : ""}${draft.id === refiningId ? " refining" : ""}" data-id="${escapeHtml(draft.id)}">
        <div class="custom-task-item-main">
          <span class="badge">Draft</span>
          ${draft.id === refiningId ? '<span class="badge status-running">Refining</span>' : ""}
          ${draft.refinement_count ? `<span class="badge">${draft.refinement_count} refinement${draft.refinement_count === 1 ? "" : "s"}</span>` : ""}
          <time class="muted small">${escapeHtml(formatTimestamp(draft.updated_at || draft.created_at))}</time>
          <span class="muted small custom-task-item-by">By ${escapeHtml(formatActor(draft.created_by))}</span>
        </div>
        ${draft.annotation_title
          ? `<p class="custom-task-annotation-preview">${escapeHtml(truncate(draft.annotation_title, 120))}</p>
             <p class="custom-task-item-query muted small">${escapeHtml(truncate(draft.query, 160))}</p>`
          : `<p class="custom-task-item-query">${escapeHtml(truncate(draft.query, 180))}</p>`}
        <button type="button" class="btn ghost btn-view-custom-task" data-id="${escapeHtml(draft.id)}">Review</button>
      </article>`
    )
    .join("");

  list.querySelectorAll(".btn-view-custom-task").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      selectCustomTask(btn.dataset.id);
    });
  });
  list.querySelectorAll(".custom-task-item").forEach((item) => {
    item.addEventListener("click", () => selectCustomTask(item.dataset.id));
  });
}

function getSelectedCustomTaskDraft() {
  const id = state.selectedCustomTaskId;
  if (!id) return null;
  return (state.customTasks?.drafts || []).find((d) => d.id === id) || null;
}

function buildCustomTaskShareContent(analysis) {
  const lines = [];
  if (analysis.annotation_title) lines.push(`Title: ${analysis.annotation_title}`);
  if (analysis.created_by) lines.push(`Generated by: ${analysis.created_by}`);
  if (analysis.query) lines.push(`Question: ${analysis.query}`);
  if (analysis.annotation_notes) {
    lines.push("");
    lines.push(analysis.annotation_notes);
  }
  lines.push("", "BeatIt custom task export (PDF attached).");
  const defaultSubject =
    (analysis.annotation_title || "").trim() ||
    truncate(analysis.query || "", 120) ||
    "BeatIt custom task";
  return {
    subject: `BeatIt: ${defaultSubject}`,
    body: lines.join("\n"),
  };
}

function updateNativeShareButton() {
  const btn = $("#btn-native-share-custom-task");
  if (!btn) return;
  const canShare = typeof navigator.share === "function";
  btn.classList.toggle("hidden", !canShare);
}

function renderCustomTaskDetail(analysis) {
  const panel = $("#custom-task-detail");
  if (!panel || !analysis) return;

  panel.classList.remove("hidden");

  const queryPill = $("#custom-task-query-pill");
  if (queryPill) {
    if (analysis.query?.trim()) {
      queryPill.textContent = analysis.query.trim();
      queryPill.classList.remove("hidden");
    } else {
      queryPill.textContent = "";
      queryPill.classList.add("hidden");
    }
  }

  const metaParts = [
    `Generated by ${formatActor(analysis.created_by)}`,
    formatTimestamp(analysis.created_at),
    analysis.model || "Unknown model",
  ];
  if (analysis.refinement_count > 0) {
    metaParts.push(
      `${analysis.refinement_count} refinement${analysis.refinement_count === 1 ? "" : "s"}`
    );
  }
  $("#custom-task-detail-meta").textContent = metaParts.join(" · ");

  const titleInput = $("#custom-task-annotation-title");
  const notesInput = $("#custom-task-annotation-notes");
  if (titleInput) titleInput.value = analysis.annotation_title || "";
  if (notesInput) notesInput.value = analysis.annotation_notes || "";
  const status = $("#custom-task-annotation-status");
  if (status) status.textContent = "";

  updateNativeShareButton();

  const refPrefix = analysis.id;
  const summaryDisplay = analysis.executive_summary_display || analysis.executive_summary || "";
  const responseDisplay = analysis.response_display || analysis.response || "";
  const updatedAt = analysis.updated_at || analysis.created_at;

  state.referenceRegistry = analysis.reference_registry || {};

  setSectionLastUpdated($("#custom-task-answer-time"), updatedAt);

  const answerEl = $("#custom-task-answer");
  const answerParts = [];
  if (summaryDisplay) {
    answerParts.push(
      `<div class="answer-section">${formatNumberedReferences(summaryDisplay, state.referenceRegistry, refPrefix)}</div>`
    );
  }
  if (responseDisplay) {
    answerParts.push(
      `<div class="answer-section">${formatNumberedReferences(responseDisplay, state.referenceRegistry, refPrefix)}</div>`
    );
  }
  if (answerEl) {
    answerEl.innerHTML = answerParts.length
      ? answerParts.join('<hr class="answer-divider">')
      : '<p class="muted">No response returned.</p>';
  }

  renderSourcesSidebar({
    wrap: $("#custom-task-sources-sidebar"),
    inner: $("#custom-task-sources-sidebar-inner"),
    appendix: analysis.references || [],
    idPrefix: refPrefix,
  });

  updateCustomTaskRefineControls(analysis);
  scrollToCustomTaskDetail();
}

function updateCustomTaskRefineControls(analysis) {
  const queryInput = $("#custom-task-refine-query");
  const btn = $("#btn-refine-custom-task");
  const status = $("#custom-task-refine-status");
  if (queryInput) queryInput.value = analysis?.query || "";

  const activeJob = state.customTasks?.activeJob;
  const refiningThis =
    state.analysisRunning &&
    state.activeJobType === "query" &&
    (activeJob?.refine_analysis_id === analysis?.id ||
      state.refiningCustomTaskId === analysis?.id);

  if (btn) btn.disabled = Boolean(state.analysisRunning);
  if (status) {
    if (refiningThis) status.textContent = "Refinement running…";
    else if (analysis?.refinement_count > 0) {
      status.textContent = `${analysis.refinement_count} prior refinement${analysis.refinement_count === 1 ? "" : "s"}`;
    } else {
      status.textContent = "";
    }
  }
}

async function refineCustomTask() {
  const id = state.selectedCustomTaskId;
  const draft = getSelectedCustomTaskDraft();
  if (!id || !draft) return toast("Select a custom task first", "error");
  if (state.analysisRunning) {
    return toast("An analysis is already running. Please wait for it to finish.", "error");
  }

  const query = $("#custom-task-refine-query")?.value.trim();
  const refinement = $("#custom-task-refine-notes")?.value.trim();
  if (!query) return toast("Question is required", "error");
  if (!refinement && query === (draft.query || "").trim()) {
    return toast("Change the question or describe what to change", "error");
  }

  const docIds = state.selectedIds.size ? [...state.selectedIds] : null;
  const btn = $("#btn-refine-custom-task");

  try {
    const data = await api(`/api/analyses/${id}/refine`, {
      method: "POST",
      body: JSON.stringify({
        query,
        refinement,
        document_ids: docIds,
      }),
    });
    const jobId = data.job.id;
    state.refiningCustomTaskId = id;
    setAnalysisRunning(true, jobId, "query");
    updateCustomTasksRunningBanner(true, query, { refining: true });
    updateCustomTaskRefineControls(draft);
    toast("Refinement started");

    const analysis = await pollAnalysisJob(jobId, { isCustomQuery: true });
    finishCustomTaskRun(analysis, query);
    if ($("#custom-task-refine-notes")) $("#custom-task-refine-notes").value = "";
  } catch (err) {
    if (err.status === 409) {
      toast("An analysis is already running — resuming progress.", "error");
      await resumeActiveAnalysisJob();
      return;
    }
    toast(err.message, "error");
  } finally {
    state.refiningCustomTaskId = null;
    setAnalysisRunning(false);
    updateCustomTasksRunningBanner(false);
    updateCustomTaskRefineControls(getSelectedCustomTaskDraft());
  }
}

function selectCustomTask(id) {
  const draft = (state.customTasks?.drafts || []).find((d) => d.id === id);
  state.selectedCustomTaskId = id;
  renderCustomTasksList();
  if (draft) renderCustomTaskDetail(draft);
}

function closeCustomTaskDetail() {
  state.selectedCustomTaskId = null;
  $("#custom-task-detail")?.classList.add("hidden");
  renderCustomTasksList();
}

async function loadCustomTasks() {
  try {
    const data = await api("/api/custom-tasks");
    state.customTasks = {
      jobs: data.jobs || [],
      drafts: data.drafts || [],
      activeJob: data.active_job || null,
    };
    if (state.customTasks.activeJob && ["pending", "running"].includes(state.customTasks.activeJob.status)) {
      const job = state.customTasks.activeJob;
      updateCustomTasksRunningBanner(true, job.query, {
        refining: Boolean(job.refine_analysis_id),
      });
      if (job.refine_analysis_id) {
        state.refiningCustomTaskId = job.refine_analysis_id;
        state.selectedCustomTaskId = job.refine_analysis_id;
      }
    } else if (!state.analysisRunning) {
      updateCustomTasksRunningBanner(false);
    }
    renderCustomTasksList();
    updateCustomTasksBadge();
    if (state.selectedCustomTaskId) {
      const draft = state.customTasks.drafts.find((d) => d.id === state.selectedCustomTaskId);
      if (draft) renderCustomTaskDetail(draft);
      else closeCustomTaskDetail();
    }
  } catch (err) {
    $("#custom-tasks-drafts-list").innerHTML = `<p class="muted">Could not load custom tasks: ${escapeHtml(err.message)}</p>`;
  }
}

async function promoteCustomTask() {
  const id = state.selectedCustomTaskId;
  if (!id) return;
  const data = await api(`/api/analyses/${id}/promote`, { method: "POST" });
  toast("Added to medical record — shown on Home");
  closeCustomTaskDetail();
  await loadCustomTasks();
  await loadLatestAssessment();
  await loadHistory();
  switchTab("analyze");
  if (data.analysis) {
    renderLatestAssessment(data.analysis);
    scrollToAssessmentResults();
  }
}

async function saveCustomTaskAnnotations() {
  const id = state.selectedCustomTaskId;
  if (!id) return;
  const status = $("#custom-task-annotation-status");
  const btn = $("#btn-save-custom-task-annotations");
  if (btn) btn.disabled = true;
  try {
    const data = await api(`/api/analyses/${id}/annotations`, {
      method: "PATCH",
      body: JSON.stringify({
        annotation_title: $("#custom-task-annotation-title")?.value.trim() ?? "",
        annotation_notes: $("#custom-task-annotation-notes")?.value.trim() ?? "",
      }),
    });
    const idx = (state.customTasks?.drafts || []).findIndex((d) => d.id === id);
    if (idx >= 0 && data.analysis) {
      state.customTasks.drafts[idx] = data.analysis;
    }
    renderCustomTaskDetail(data.analysis);
    renderCustomTasksList();
    if (status) status.textContent = "Saved";
    toast("Annotations saved");
  } catch (err) {
    if (status) status.textContent = "";
    toast(err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function downloadAnalysisPdf(analysisId, { silent = false, analysis = null } = {}) {
  const res = await fetch(`/api/analyses/${analysisId}/export.pdf`, {
    credentials: "include",
  });
  if (res.status === 401) {
    window.location.href = "/login";
    return null;
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const fallback = buildPdfDownloadFilename(analysis || { id: analysisId });
  const filename = filenameFromContentDisposition(res, fallback);
  return { blob, filename, silentHandled: silent };
}

function filenameFromContentDisposition(res, fallback) {
  const disposition =
    res.headers.get("Content-Disposition") || res.headers.get("content-disposition") || "";
  if (!disposition) return fallback;

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;\n]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1].trim());
    } catch {
      /* fall through */
    }
  }

  const quotedMatch = disposition.match(/filename="([^"]+)"/i);
  if (quotedMatch) return quotedMatch[1];

  const plainMatch = disposition.match(/filename=([^;\n]+)/i);
  if (plainMatch) return plainMatch[1].trim().replace(/^["']|["']$/g, "");

  return fallback;
}

function formatPdfFilenameStamp(iso) {
  try {
    const dt = new Date(iso || Date.now());
    if (Number.isNaN(dt.getTime())) return "unknown";
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      })
        .formatToParts(dt)
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value])
    );
    return `${parts.year}-${parts.month}-${parts.day}_${parts.hour}${parts.minute}${parts.second}`;
  } catch {
    return "unknown";
  }
}

function buildPdfDownloadFilename(analysis) {
  const stamp = formatPdfFilenameStamp(new Date().toISOString());
  const title = String(analysis?.annotation_title || "").trim();
  if (title) {
    const slug = title
      .toLowerCase()
      .replace(/[^\w\s-]/g, "")
      .replace(/[\s_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40);
    if (slug) {
      const prefix = analysis?.analysis_type === "query" ? "custom-task" : "assessment";
      return `beatit-${prefix}-${slug}-${stamp}.pdf`;
    }
  }
  if (analysis?.analysis_type === "query") {
    return `beatit-custom-task-${stamp}.pdf`;
  }
  return `beatit-assessment-${stamp}.pdf`;
}

function triggerPdfDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function exportCustomTaskPdf(options = {}) {
  const id = state.selectedCustomTaskId;
  if (!id) return toast("Select a custom task first", "error");
  const btn = $("#btn-export-custom-task-pdf");
  if (btn) btn.disabled = true;
  try {
    const draft = getSelectedCustomTaskDraft();
    if (!draft) return toast("Select a custom task first", "error");
    const result = await downloadAnalysisPdf(id, { ...options, analysis: draft });
    if (!result) return;
    triggerPdfDownload(result.blob, result.filename);
    if (!options.silent) toast("PDF downloaded");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function nativeShareCustomTask() {
  const draft = getSelectedCustomTaskDraft();
  if (!draft) return toast("Select a custom task first", "error");
  if (typeof navigator.share !== "function") {
    return toast("Sharing is not supported on this device — use PDF", "error");
  }

  const btn = $("#btn-native-share-custom-task");
  if (btn) btn.disabled = true;
  try {
    const { subject, body } = buildCustomTaskShareContent(draft);
    const result = await downloadAnalysisPdf(draft.id, { silent: true, analysis: draft });
    if (!result) return;

    const file = new File([result.blob], result.filename, { type: "application/pdf" });
    const shareData = { title: subject, text: body };
    if (typeof navigator.canShare === "function" && navigator.canShare({ files: [file] })) {
      shareData.files = [file];
    }

    await navigator.share(shareData);
    toast("Shared");
  } catch (err) {
    if (err?.name !== "AbortError") toast(err.message || "Share failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function discardCustomTask() {
  const id = state.selectedCustomTaskId;
  if (!id) return;
  if (!confirm("Discard this draft? It will not appear on Home.")) return;
  await api(`/api/analyses/${id}/discard`, { method: "POST" });
  toast("Draft discarded");
  closeCustomTaskDetail();
  await loadCustomTasks();
}

function tierLabel(tier) {
  if (tier === "budget") return "Budget";
  if (tier === "premium") return "Premium";
  return "Standard";
}

function updateModelDescription() {
  const select = $("#settings-model");
  const desc = $("#settings-model-desc");
  if (!select || !desc) return;
  const model = state.models.find((m) => m.id === select.value);
  desc.textContent = model?.description || "";
}

function renderModelSelect() {
  const select = $("#settings-model");
  if (!select) return;
  const current = state.settings.openrouter_model || "";
  select.innerHTML = state.models
    .map(
      (m) =>
        `<option value="${escapeHtml(m.id)}"${m.id === current ? " selected" : ""}>${escapeHtml(m.label)} (${tierLabel(m.tier)})</option>`
    )
    .join("");
  updateModelDescription();
}

async function loadSettings() {
  const data = await api("/api/settings");
  state.models = data.models || [];
  state.settings = data.settings || {};
  state.sourceLegend = data.source_legend || [];
  renderModelSelect();
  renderSourceLabelsForm();
  renderSourceLegend(state.sourceLegend);

  const patientEl = $("#settings-patient-context");
  if (patientEl) {
    patientEl.value =
      state.settings.patient_context || data.default_patient_context || "";
  }

  const current = $("#settings-current");
  if (current) {
    const modelId = state.settings.openrouter_model || data.default_model;
    current.textContent = `Selected model: ${modelId}`;
  }

  const llmHealth = data.llm;
  renderSettingsOllamaInfo(state.settings, llmHealth);
  if (llmHealth) {
    updateLlmStatusDisplay({ llm: llmHealth }, $("#llm-status"), $("#settings-llm-connection"));
  } else {
    const health = await api("/api/health");
    updateLlmStatusDisplay(health, $("#llm-status"), $("#settings-llm-connection"));
  }

  await loadAuditTrail(true);
}

function formatAuditTimestamp(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

function renderAuditEvent(event) {
  const actor = event.actor_display || event.actor;
  const details = (event.details || [])
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join("");
  return `
    <article class="audit-event">
      <div class="audit-event-header">
        <div class="audit-event-heading">
          <strong class="audit-event-label">${escapeHtml(event.label || event.event_type || "Event")}</strong>
          ${actor ? `<span class="audit-event-actor muted small">by ${escapeHtml(actor)}</span>` : ""}
        </div>
        <time class="audit-event-time muted small">${escapeHtml(formatAuditTimestamp(event.created_at))}</time>
      </div>
      <p class="audit-event-summary">${escapeHtml(event.summary || "")}</p>
      ${details ? `<ul class="audit-event-details">${details}</ul>` : ""}
    </article>`;
}

async function loadAuditTrail(reset = false) {
  const list = $("#audit-trail-list");
  const countEl = $("#audit-trail-count");
  const loadMoreBtn = $("#btn-audit-load-more");
  const filterEl = $("#audit-filter");
  if (!list) return;

  if (reset) {
    state.auditOffset = 0;
    state.auditEvents = [];
  }

  const category = filterEl?.value || "all";
  const limit = 50;
  const offset = state.auditOffset || 0;

  try {
    const data = await api(
      `/api/audit-events?limit=${limit}&offset=${offset}&category=${encodeURIComponent(category)}`
    );
    const events = data.events || [];
    state.auditEvents = reset ? events : [...(state.auditEvents || []), ...events];
    state.auditOffset = state.auditEvents.length;
    state.auditTotal = data.total || 0;

    if (countEl) {
      countEl.textContent =
        state.auditTotal === 0
          ? "No audit events yet."
          : `Showing ${state.auditEvents.length} of ${state.auditTotal} events`;
    }

    if (!state.auditEvents.length) {
      list.innerHTML = `<p class="muted">No audit events recorded yet. Actions like adding documents, running analyses, and adding comments will appear here.</p>`;
    } else {
      list.innerHTML = state.auditEvents.map(renderAuditEvent).join("");
    }

    if (loadMoreBtn) {
      loadMoreBtn.classList.toggle("hidden", state.auditEvents.length >= state.auditTotal);
    }
  } catch (err) {
    list.innerHTML = `<p class="muted">Could not load audit trail: ${escapeHtml(err.message)}</p>`;
    if (countEl) countEl.textContent = "";
    loadMoreBtn?.classList.add("hidden");
  }
}

async function saveModelSettings() {
  const select = $("#settings-model");
  if (!select) return;
  const data = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ openrouter_model: select.value }),
  });
  state.settings = { ...state.settings, ...data.settings };
  toast("Model saved");
  const current = $("#settings-current");
  if (current) current.textContent = `Selected model: ${data.settings.openrouter_model}`;
  checkHealth();
  if ($("#panel-settings")?.classList.contains("active")) loadAuditTrail(true);
}

async function savePatientContext() {
  const el = $("#settings-patient-context");
  if (!el) return;
  const patient_context = el.value.trim();
  if (!patient_context) return toast("Patient context cannot be empty", "error");
  const data = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ patient_context }),
  });
  state.settings = { ...state.settings, ...data.settings };
  toast("Patient context saved");
  if ($("#panel-settings")?.classList.contains("active")) loadAuditTrail(true);
}

const LIBRARY_PAGE_SIZE = 10;
const SELECTION_STORAGE_KEY = "beatit-assessment-selection";
const ASSESSMENT_GUIDANCE_STORAGE_KEY = "beatit-assessment-guidance";

const LIBRARY_TYPE_LABELS = {
  text: "Clinical notes",
  url: "Web page",
  youtube: "YouTube",
  facebook: "Facebook",
  pdf: "PDF",
  video: "Video",
  imaging: "DICOM / imaging",
};

function libraryTypeLabel(type) {
  return LIBRARY_TYPE_LABELS[type] || type || "Unknown";
}

function findDocumentById(id) {
  return state.documents.find((doc) => doc.id === id) || state.documentIndex.find((doc) => doc.id === id);
}

function findDocumentByTitle(title) {
  const key = String(title || "").trim();
  if (!key) return null;
  const fromPage = state.documents.find((doc) => doc.title === key || doc.citation_display_name === key);
  if (fromPage) return fromPage;
  return (
    state.documentIndex.find((doc) => doc.title === key || doc.citation_display_name === key) || null
  );
}

function libraryTotalPages() {
  return Math.max(1, Math.ceil((state.libraryTotal || 0) / LIBRARY_PAGE_SIZE));
}

function updateSelectedLabel() {
  const el = $("#selected-count");
  const customScope = $("#custom-task-doc-scope");
  const label = selectionScopeLabel();
  if (el) {
    el.textContent = label;
  }
  if (customScope) {
    customScope.textContent = label;
  }
  renderLibrarySelectionControls();
  renderAssessmentScopeCard();
}

function selectionScopeLabel() {
  const n = state.selectedIds.size;
  if (n === 0) return "Using all documents";
  return `${n} selected for assessment`;
}

function saveSelectionToSession() {
  try {
    sessionStorage.setItem(SELECTION_STORAGE_KEY, JSON.stringify([...state.selectedIds]));
  } catch {
    /* ignore */
  }
}

function loadSelectionFromSession() {
  try {
    const raw = sessionStorage.getItem(SELECTION_STORAGE_KEY);
    if (!raw) return;
    const ids = JSON.parse(raw);
    if (!Array.isArray(ids)) return;
    ids.forEach((id) => {
      if (typeof id === "string") state.selectedIds.add(id);
    });
  } catch {
    /* ignore */
  }
}

function reconcileSelectionWithIndex() {
  const valid = new Set(state.documentIndex.map((doc) => doc.id));
  for (const id of [...state.selectedIds]) {
    if (!valid.has(id)) state.selectedIds.delete(id);
  }
}

function plannedAssessmentIds() {
  if (state.selectedIds.size) return [...state.selectedIds];
  return state.documentIndex.map((doc) => doc.id);
}

function scopeSummaryFromIds(ids) {
  const idSet = new Set(ids);
  const docs = state.documentIndex.filter((doc) => idSet.has(doc.id));
  const byType = {};
  docs.forEach((doc) => {
    byType[doc.source_type] = (byType[doc.source_type] || 0) + 1;
  });
  return { count: ids.length, byType, docs };
}

function formatScopeBreakdown(byType) {
  return Object.entries(byType)
    .sort((a, b) => libraryTypeLabel(a[0]).localeCompare(libraryTypeLabel(b[0])))
    .map(([type, count]) => `${libraryTypeLabel(type)}: ${count}`)
    .join(" · ");
}

function scopeSetsEqual(a, b) {
  if (a.length !== b.length) return false;
  const setA = new Set(a);
  return b.every((id) => setA.has(id));
}

function renderAssessmentScopeCard() {
  const total = state.documentIndex.length;
  const nextIds = plannedAssessmentIds();
  const next = scopeSummaryFromIds(nextIds);
  const lastIds = state.latestAnalysis?.document_ids || [];
  const last = lastIds.length ? scopeSummaryFromIds(lastIds) : null;
  const hasAssessment = Boolean(state.latestAnalysis);
  const usingAll = state.selectedIds.size === 0;

  const nextEl = $("#assessment-scope-next");
  const lastEl = $("#assessment-scope-last");
  const warnEl = $("#assessment-scope-warning");
  const matchBtn = $("#btn-scope-match-last");
  const reassessBtn = $("#btn-reassess-baseline");
  const baselineBtn = $("#btn-baseline");

  if (nextEl) {
    const mode = usingAll ? "All stored documents" : `${next.count} selected documents`;
    const breakdown = formatScopeBreakdown(next.byType);
    nextEl.innerHTML = `
      <p class="assessment-scope-heading">Next assessment</p>
      <p class="assessment-scope-value"><strong>${escapeHtml(mode)}</strong>${total ? ` <span class="muted">(${total} in library)</span>` : ""}</p>
      ${breakdown ? `<p class="muted small">${escapeHtml(breakdown)}</p>` : ""}
      ${!usingAll && next.count < total ? `<p class="muted small">Unselected documents will not be sent to the LLM.</p>` : ""}`;
  }

  if (lastEl) {
    if (last && hasAssessment) {
      lastEl.classList.remove("hidden");
      const breakdown = formatScopeBreakdown(last.byType);
      const sampleTitles = last.docs.slice(0, 5).map((doc) => escapeHtml(truncate(doc.title, 60)));
      const more =
        last.docs.length > 5
          ? `<li class="muted small">…and ${last.docs.length - 5} more</li>`
          : "";
      lastEl.innerHTML = `
        <p class="assessment-scope-heading">Last assessment used</p>
        <p class="assessment-scope-value"><strong>${last.count} document${last.count === 1 ? "" : "s"}</strong> · ${escapeHtml(formatTimestamp(state.latestAnalysis.created_at))}</p>
        ${breakdown ? `<p class="muted small">${escapeHtml(breakdown)}</p>` : ""}
        ${state.latestAnalysis.assessment_guidance ? `<p class="muted small assessment-scope-guidance-note"><strong>Guidance:</strong> ${escapeHtml(truncate(state.latestAnalysis.assessment_guidance, 240))}</p>` : ""}
        ${sampleTitles.length ? `<ul class="assessment-scope-doc-list">${sampleTitles.map((t) => `<li>${t}</li>`).join("")}${more}</ul>` : ""}`;
    } else {
      lastEl.classList.add("hidden");
      lastEl.innerHTML = "";
    }
  }

  const warnings = [];
  if (hasAssessment && lastIds.length && !scopeSetsEqual(nextIds, lastIds)) {
    if (nextIds.length > lastIds.length) {
      warnings.push(
        "Next run includes more documents than the current assessment — useful for catching overlooked reports."
      );
    } else if (nextIds.length < lastIds.length) {
      warnings.push(
        "Next run uses fewer documents than the last assessment. Missing reports can produce false gaps."
      );
    } else {
      warnings.push("Document selection differs from the last assessment.");
    }
  }
  if (hasAssessment && usingAll && total > 20) {
    warnings.push(
      `All ${total} library items will be sent. Large imaging-only files may add little text — prefer selecting PDFs and clinical reports when possible.`
    );
  }

  if (warnEl) {
    if (warnings.length) {
      warnEl.classList.remove("hidden");
      warnEl.innerHTML = warnings
        .map((text) => `<p class="assessment-scope-alert">${escapeHtml(text)}</p>`)
        .join("");
    } else {
      warnEl.classList.add("hidden");
      warnEl.innerHTML = "";
    }
  }

  matchBtn?.classList.toggle("hidden", !lastIds.length);

  const reassessLabel = hasAssessment ? "Re-run baseline assessment" : "Run baseline assessment";
  if (reassessBtn) reassessBtn.textContent = reassessLabel;
  if (baselineBtn) baselineBtn.textContent = reassessLabel;
  if (reassessBtn) reassessBtn.disabled = state.analysisRunning || !total;
}

function applyLastAssessmentScope() {
  const ids = state.latestAnalysis?.document_ids;
  if (!ids?.length) return toast("No prior assessment scope to restore", "error");
  state.selectedIds.clear();
  ids.forEach((id) => state.selectedIds.add(id));
  saveSelectionToSession();
  renderDocuments();
  updateSelectedLabel();
  toast(`Restored last assessment scope (${ids.length} documents)`);
}

function scrollToAssessmentScope() {
  switchTab("analyze");
  const card = $("#assessment-scope-card");
  requestAnimationFrame(() => scrollToElement(card));
}

function goToLibraryForScope() {
  switchTab("library");
  window.scrollTo({ top: 0, behavior: "smooth" });
  toast("Select documents with checkboxes, then return to Home to reassess");
}

const ASSESSMENT_GUIDANCE_PRESETS = [
  "Pay close attention to pathology and imaging reports — cite them explicitly",
  "Include findings from video and Facebook transcripts when they are in scope",
  "Do not flag as missing anything already covered in stored clinical reports",
  "Compare dates across reports and use the most recent staging data",
  "Focus on the report from ABC and related follow-up documents",
];

function getAssessmentGuidanceInput() {
  return $("#assessment-guidance")?.value.trim() || "";
}

function setAssessmentGuidanceInput(text) {
  const el = $("#assessment-guidance");
  if (el) el.value = text || "";
  saveAssessmentGuidanceToSession();
}

function appendAssessmentGuidance(text) {
  const el = $("#assessment-guidance");
  if (!el || !text) return;
  const current = el.value.trim();
  el.value = current ? `${current}\n\n${text}` : text;
  saveAssessmentGuidanceToSession();
}

function saveAssessmentGuidanceToSession() {
  try {
    sessionStorage.setItem(ASSESSMENT_GUIDANCE_STORAGE_KEY, $("#assessment-guidance")?.value || "");
  } catch {
    /* ignore */
  }
}

function loadAssessmentGuidanceFromSession() {
  try {
    const raw = sessionStorage.getItem(ASSESSMENT_GUIDANCE_STORAGE_KEY);
    if (raw != null) setAssessmentGuidanceInput(raw);
  } catch {
    /* ignore */
  }
}

function initAssessmentGuidancePresets() {
  const container = $("#assessment-guidance-presets");
  if (!container) return;
  container.innerHTML = ASSESSMENT_GUIDANCE_PRESETS.map(
    (text, index) =>
      `<button type="button" class="btn ghost guidance-preset" data-index="${index}">${escapeHtml(text)}</button>`
  ).join("");
  container.querySelectorAll(".guidance-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      appendAssessmentGuidance(ASSESSMENT_GUIDANCE_PRESETS[Number(btn.dataset.index)]);
      $("#assessment-guidance")?.focus();
    });
  });
  $("#assessment-guidance")?.addEventListener("input", saveAssessmentGuidanceToSession);
}

async function confirmAndRunBaseline() {
  const nextIds = plannedAssessmentIds();
  if (!nextIds.length) return toast("Add documents to the library first", "error");
  const next = scopeSummaryFromIds(nextIds);
  const usingAll = state.selectedIds.size === 0;
  const mode = usingAll ? `all ${next.count} stored documents` : `${next.count} selected documents`;
  const breakdown = formatScopeBreakdown(next.byType);
  const guidance = getAssessmentGuidanceInput();
  const hasAssessment = Boolean(state.latestAnalysis);
  let msg = hasAssessment
    ? `Re-run baseline assessment using ${mode}?`
    : `Run baseline assessment using ${mode}?`;
  if (breakdown) msg += `\n\nIncludes: ${breakdown}`;
  if (guidance) msg += `\n\nGuidance:\n${truncate(guidance, 500)}`;
  if (hasAssessment) msg += "\n\nThis replaces the current Home assessment and open items.";
  if (!confirm(msg)) return;
  await runAnalysis({ baseline: true, assessmentGuidance: guidance });
}

function reassessFromOpenItem() {
  scrollToAssessmentScope();
  const item = state.selectedOpenItem;
  if (item && !getAssessmentGuidanceInput()) {
    setAssessmentGuidanceInput(
      `Check whether this is truly an open gap or already documented in stored reports: ${item.item}`
    );
  }
  $("#assessment-guidance")?.focus();
}

function documentIdsOfType(sourceType) {
  if (!sourceType) return [];
  return state.documentIndex
    .filter((doc) => doc.source_type === sourceType)
    .map((doc) => doc.id);
}

function selectDocumentIds(ids) {
  ids.forEach((id) => state.selectedIds.add(id));
  saveSelectionToSession();
  renderDocuments();
  updateSelectedLabel();
}

function selectDocumentsOnPage() {
  selectDocumentIds(state.documents.map((doc) => doc.id));
  toast(`Selected ${state.documents.length} on this page`);
}

function selectDocumentsByType(sourceType) {
  const ids = documentIdsOfType(sourceType);
  if (!ids.length) {
    toast(`No ${libraryTypeLabel(sourceType).toLowerCase()} documents to select`, "error");
    return;
  }
  selectDocumentIds(ids);
  toast(`Selected ${ids.length} ${libraryTypeLabel(sourceType).toLowerCase()} document${ids.length === 1 ? "" : "s"}`);
}

function selectAllDocuments() {
  const ids = state.documentIndex.map((doc) => doc.id);
  if (!ids.length) return toast("No documents to select", "error");
  selectDocumentIds(ids);
  toast(`Selected all ${ids.length} documents`);
}

function clearDocumentSelection() {
  if (!state.selectedIds.size) return;
  state.selectedIds.clear();
  saveSelectionToSession();
  renderDocuments();
  updateSelectedLabel();
  toast("Selection cleared — assessments will use all documents");
}

function renderLibrarySelectionControls() {
  const summary = $("#library-selection-summary");
  const filteredBtn = $("#btn-select-filtered-type");
  const typeSelect = $("#library-select-type");
  const counts = state.libraryCounts || {};
  const selected = state.selectedIds.size;
  const total = state.documentIndex.length || Object.values(counts).reduce((a, b) => a + b, 0);

  if (summary) {
    if (!total) {
      summary.textContent = "No documents stored yet.";
    } else if (selected === 0) {
      summary.textContent = `No selection — assessments use all ${total} document${total === 1 ? "" : "s"}.`;
    } else if (selected === total) {
      summary.textContent = `All ${selected} documents selected for assessment.`;
    } else {
      summary.textContent = `${selected} of ${total} selected for assessment.`;
    }
  }

  if (filteredBtn) {
    const filterType = state.libraryFilter || "";
    const filterCount = filterType ? counts[filterType] || 0 : 0;
    if (filterType && filterCount > 0) {
      filteredBtn.classList.remove("hidden");
      filteredBtn.textContent = `Select all ${libraryTypeLabel(filterType)} (${filterCount})`;
    } else {
      filteredBtn.classList.add("hidden");
    }
  }

  if (typeSelect) {
    const current = typeSelect.value || "";
    const typeKeys = [
      ...Object.keys(LIBRARY_TYPE_LABELS).filter((type) => counts[type]),
      ...Object.keys(counts).filter((type) => !LIBRARY_TYPE_LABELS[type]),
    ].sort((a, b) => libraryTypeLabel(a).localeCompare(libraryTypeLabel(b)));
    typeSelect.innerHTML = [
      `<option value="">Choose type…</option>`,
      ...typeKeys.map(
        (type) =>
          `<option value="${escapeHtml(type)}"${type === current ? " selected" : ""}>${escapeHtml(libraryTypeLabel(type))} (${counts[type]})</option>`
      ),
    ].join("");
  }
}

function renderDocuments() {
  const list = $("#documents-list");
  const summary = $("#library-summary");
  const pagination = $("#library-pagination");
  if (!list) return;

  const total = state.libraryTotal || 0;
  const filterLabel = state.libraryFilter ? libraryTypeLabel(state.libraryFilter) : "All types";
  if (summary) {
    if (!total) {
      summary.textContent = state.libraryFilter
        ? `No ${filterLabel.toLowerCase()} documents`
        : "No documents stored";
    } else {
      const start = (state.libraryPage - 1) * LIBRARY_PAGE_SIZE + 1;
      const end = Math.min(state.libraryPage * LIBRARY_PAGE_SIZE, total);
      summary.textContent = `Showing ${start}–${end} of ${total} · ${filterLabel}`;
    }
  }

  if (!state.documents.length) {
    list.innerHTML = state.libraryFilter
      ? `<p class="muted">No documents match this filter.</p>`
      : `<p class="muted">No documents yet. Add clinical notes, URLs, PDFs, imaging, or YouTube transcripts.</p>`;
    if (pagination) pagination.classList.add("hidden");
    renderLibrarySelectionControls();
    return;
  }

  list.innerHTML = state.documents
    .map((doc) => {
      const selected = state.selectedIds.has(doc.id);
      const meta = doc.metadata || {};
      const excerpt = meta.page_count
        ? `${meta.page_count} pages`
        : meta.modality
          ? meta.modality
          : meta.imaging_format === "DICOM" || meta.is_dicom || [".dcm", ".dicom"].includes(meta.file_extension)
            ? "DICOM"
            : meta.file_size_label
              ? meta.file_size_label
              : "";
      const paths = renderPathLines(docPathLines(doc));
      const info = doc.source_info || {};
      const sourceBadge = info.shorthand
        ? `<span class="source-tag ${escapeHtml(info.css_class || "source-document")}" title="${escapeHtml(info.type_display || "")}">${escapeHtml(info.shorthand)}</span>`
        : "";
      const displayName = info.display_name || doc.title;
      return `
        <article class="doc-item ${selected ? "selected" : ""}" data-id="${doc.id}">
          <div class="doc-item-heading">
            <label class="doc-select-check" title="Include in assessment">
              <input type="checkbox" class="doc-select-input" data-id="${doc.id}"${selected ? " checked" : ""}>
            </label>
            ${sourceBadge}
            <strong>${escapeHtml(displayName)}</strong>
          </div>
          ${displayName !== doc.title ? `<p class="muted small doc-stored-title">Stored title: ${escapeHtml(doc.title)}</p>` : ""}
          <div class="doc-meta">
            <span class="badge">${escapeHtml(doc.source_type)}</span>
            <span>${formatDate(doc.created_at)}</span>
            ${excerpt ? `<span>${excerpt}</span>` : ""}
          </div>
          ${paths ? `<div class="doc-paths">${paths}</div>` : ""}
          <div class="doc-actions">
            <button class="btn ghost btn-view" data-id="${doc.id}">View</button>
            <button class="btn secondary btn-select" data-id="${doc.id}">
              ${selected ? "Deselect" : "Select for analysis"}
            </button>
            <button class="btn danger btn-delete" data-id="${doc.id}">Delete</button>
          </div>
        </article>`;
    })
    .join("");

  renderLibraryPagination();
}

function renderLibraryTypeFilter() {
  const select = $("#library-type-filter");
  if (!select) return;
  const current = state.libraryFilter || "";
  const counts = state.libraryCounts || {};
  const allCount = Object.values(counts).reduce((a, b) => a + b, 0);
  const typeKeys = [
    ...Object.keys(LIBRARY_TYPE_LABELS).filter((type) => counts[type]),
    ...Object.keys(counts).filter((type) => !LIBRARY_TYPE_LABELS[type]),
  ].sort((a, b) => libraryTypeLabel(a).localeCompare(libraryTypeLabel(b)));
  const options = [
    `<option value="">All types (${allCount})</option>`,
    ...typeKeys.map(
      (type) =>
        `<option value="${escapeHtml(type)}"${type === current ? " selected" : ""}>${escapeHtml(libraryTypeLabel(type))} (${counts[type]})</option>`
    ),
  ];
  select.innerHTML = options.join("");
}

function renderLibraryPagination() {
  const wrap = $("#library-pagination");
  const info = $("#library-page-info");
  const prev = $("#btn-library-prev");
  const next = $("#btn-library-next");
  if (!wrap || !info || !prev || !next) return;

  const totalPages = libraryTotalPages();
  if ((state.libraryTotal || 0) <= LIBRARY_PAGE_SIZE) {
    wrap.classList.add("hidden");
    return;
  }

  wrap.classList.remove("hidden");
  info.textContent = `Page ${state.libraryPage} of ${totalPages}`;
  prev.disabled = state.libraryPage <= 1;
  next.disabled = state.libraryPage >= totalPages;
}

async function loadDocumentIndex() {
  const data = await api("/api/documents/index");
  state.documentIndex = data.documents || [];
  state.libraryCounts = data.counts_by_type || {};
  if (!state.libraryFilter) {
    state.libraryTotal = data.total || 0;
  }
  reconcileSelectionWithIndex();
  renderLibraryTypeFilter();
  renderLibrarySelectionControls();
  renderAssessmentScopeCard();
}

async function refreshLibrary(options = {}) {
  await loadDocumentIndex();
  await loadDocuments(options);
}

async function openLibraryAfterIngest() {
  await refreshLibrary({ page: 1 });
  switchTab("library", { skipLibraryLoad: true });
}

async function loadDocuments(options = {}) {
  const page = options.page ?? state.libraryPage ?? 1;
  const sourceType =
    options.sourceType !== undefined ? options.sourceType : state.libraryFilter ?? "";
  const params = new URLSearchParams({
    limit: String(LIBRARY_PAGE_SIZE),
    offset: String((page - 1) * LIBRARY_PAGE_SIZE),
  });
  if (sourceType) params.set("source_type", sourceType);

  const data = await api(`/api/documents?${params}`);
  state.documents = data.documents || [];
  state.libraryPage = page;
  state.libraryFilter = sourceType;
  state.libraryTotal = data.total ?? 0;
  state.libraryCounts = data.counts_by_type || state.libraryCounts;
  if (data.source_legend) {
    state.sourceLegend = data.source_legend;
    renderSourceLegend(state.sourceLegend);
  }
  renderLibraryTypeFilter();
  renderLibrarySelectionControls();
  renderDocuments();
  updateSelectedLabel();
  updateHomeWorkflow();
}

function analysisTypeLabel(type) {
  if (type === "baseline") return "Baseline assessment";
  if (type === "summarize") return "Document summary";
  return "Custom query";
}

function formatTimestamp(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

function formatEasternTimestamp(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("en-US", {
      timeZone: "America/New_York",
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

function setSectionLastUpdated(timeEl, iso) {
  if (!timeEl) return;
  const wrap = timeEl.closest(".section-updated");
  if (!iso) {
    timeEl.textContent = "";
    timeEl.removeAttribute("datetime");
    wrap?.classList.add("hidden");
    return;
  }
  timeEl.textContent = formatEasternTimestamp(iso);
  timeEl.dateTime = iso;
  wrap?.classList.remove("hidden");
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sourceTagClass(tagText) {
  const lower = String(tagText || "").toLowerCase();
  if (lower.includes("document")) return "source-document";
  if (lower.includes("patient context")) return "source-context";
  if (lower.includes("inference") || lower.includes("not verified")) return "source-inference";
  if (lower.includes("unknown")) return "source-unknown";
  return "source-inference";
}

function refMetaFromRegistry(num, registry = state.referenceRegistry) {
  return registry?.[num] || registry?.[String(num)] || null;
}

function renderSourceBadge(ref, { compact = true } = {}) {
  if (!ref) return "";
  const cls = ref.css_class || sourceTagClass(ref.raw_label || ref.label || "");
  const shorthand = ref.shorthand || ref.type_display || "Ref";
  const title = ref.display_label || ref.label || ref.type_display || "Source";
  return `<span class="source-tag ${cls}" title="${escapeHtml(title)}">${escapeHtml(compact ? shorthand : ref.type_display || shorthand)}</span>`;
}

function renderSourceLegend(items) {
  const list = $("#source-legend-list");
  if (!list) return;
  const legend = items || state.sourceLegend || [];
  if (!legend.length) return;
  list.innerHTML = legend
    .map(
      (item) =>
        `<li>${renderSourceBadge(item, { compact: true })} ${escapeHtml(item.display || item.type_display || "")} — ${escapeHtml(item.description || "")}</li>`
    )
    .join("");
}

function formatMarkdownEmphasis(escaped) {
  return escaped.replace(/\*\*([^*\n]+)\*\*/g, '<span class="text-emphasis">$1</span>');
}

function describeSourceTagInner(inner) {
  const lower = String(inner || "").toLowerCase();
  const labels = state.settings.source_labels || {};
  if (lower.startsWith("patient context")) {
    return {
      css_class: "source-context",
      shorthand: labels.patient_context?.shorthand || "Ctx",
      display_label: labels.patient_context?.display || inner,
    };
  }
  if (lower.includes("inference") && lower.includes("not verified")) {
    return {
      css_class: "source-inference",
      shorthand: labels.inference?.shorthand || "AI",
      display_label: labels.inference?.display || inner,
    };
  }
  if (lower.startsWith("unknown")) {
    return {
      css_class: "source-unknown",
      shorthand: labels.unknown?.shorthand || "?",
      display_label: labels.unknown?.display || "Not documented",
      type_display: labels.unknown?.display || "Not documented",
    };
  }
  if (lower.startsWith("document")) {
    const titleMatch = inner.match(/^document\s+"([^"]+)"/i);
    const title = titleMatch?.[1] || inner;
    const doc = findDocumentByTitle(title);
    if (doc?.source_info) return doc.source_info;
    return {
      css_class: "source-document",
      shorthand: labels.document?.shorthand || "Doc",
      display_label: title,
    };
  }
  return {
    css_class: "source-inference",
    shorthand: labels.inference?.shorthand || "AI",
    display_label: inner,
  };
}

function sourceCitationTitle(meta, inner) {
  if (meta.css_class === "source-unknown") {
    return "Not supported by stored documents — do not treat as verified fact";
  }
  if (meta.type_display) return meta.type_display;
  return meta.display_label || inner || "Source";
}

function renderInlineSourceCitation(meta, inner) {
  const title = sourceCitationTitle(meta, inner);
  return `<span class="source-cite-inline ${meta.css_class || "source-inference"}" title="${escapeHtml(title)}">${renderSourceBadge(meta)}</span>`;
}

function formatWithSources(text) {
  if (!text) return "";
  const escaped = escapeHtml(text);
  const withTags = escaped.replace(/\[SOURCE:\s*([^\]]+)\]/gi, (_, inner) =>
    renderInlineSourceCitation(describeSourceTagInner(inner), inner)
  );
  return formatMarkdownEmphasis(withTags).replace(/\n/g, "<br>");
}

function refEntryId(idPrefix, num) {
  return `ref-entry-${idPrefix}-${num}`;
}

function refEntryHash(idPrefix, num) {
  return `#${refEntryId(idPrefix, num)}`;
}

function findRefNumByRawLabel(label, registry = state.referenceRegistry) {
  const normalized = String(label || "").trim().toLowerCase();
  if (!normalized || !registry) return null;
  for (const [num, ref] of Object.entries(registry)) {
    const raw = String(ref.raw_label || ref.label || "").trim().toLowerCase();
    if (raw === normalized) return num;
  }
  return null;
}

const CITE_PILL_GENERIC_LABELS = new Set([
  "ai inference",
  "not documented",
  "not verified",
  "patient context",
  "clinical record",
  "diagnostic test",
  "web source",
]);

function citePillLabel(ref) {
  if (!ref) return "Source";
  const type = ref.type || "";
  const css = ref.css_class || "";
  if (type === "unknown" || css.includes("unknown")) return "Not in library";
  if (type === "inference" || css.includes("inference")) return "Not verified";
  if (type === "patient_context" || css.includes("context")) return "Patient context";

  const label = String(ref.display_label || ref.label || "").trim();
  if (label && !CITE_PILL_GENERIC_LABELS.has(label.toLowerCase())) {
    return truncate(label, 36);
  }
  if (ref.source_uri) {
    try {
      const host = new URL(ref.source_uri).hostname.replace(/^www\./, "");
      if (host) return truncate(host, 36);
    } catch (_) {
      /* ignore */
    }
  }
  if (ref.type === "web" || ref.css_class?.includes("web")) {
    return truncate(label || "Web source", 36);
  }
  return truncate(label || "Source", 36);
}

const SOURCES_SIDEBAR_PREVIEW = 6;

function sourceFaviconUrl(ref) {
  if (!ref?.source_uri) return null;
  try {
    const host = new URL(ref.source_uri).hostname;
    return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=32`;
  } catch (_) {
    return null;
  }
}

function sourceSidebarFaviconStack(appendix) {
  const seen = new Set();
  const icons = [];
  for (const ref of appendix) {
    const favicon = sourceFaviconUrl(ref);
    if (!favicon) continue;
    try {
      const host = new URL(ref.source_uri).hostname;
      if (seen.has(host)) continue;
      seen.add(host);
      icons.push(favicon);
      if (icons.length >= 5) break;
    } catch (_) {
      /* ignore */
    }
  }
  if (!icons.length) return "";
  return `<span class="sources-favicon-stack" aria-hidden="true">${icons
    .map((url) => `<img src="${escapeHtml(url)}" alt="" width="16" height="16" loading="lazy">`)
    .join("")}</span>`;
}

const SOURCE_URL_PATTERN = /https?:\/\/[^\s\]\)"'<>]+/i;
const SOURCE_NCT_PATTERN = /\b(NCT\d{8})\b/i;

function extractSourceUriFromLabel(label) {
  const text = String(label || "").trim();
  if (!text) return null;
  const urlMatch = text.match(SOURCE_URL_PATTERN);
  if (urlMatch) return urlMatch[0].replace(/[.,;)]+$/, "");
  const nctMatch = text.match(SOURCE_NCT_PATTERN);
  if (nctMatch) return `https://clinicaltrials.gov/study/${nctMatch[1].toUpperCase()}`;
  return null;
}

function enrichReference(ref) {
  if (!ref) return ref;
  let enriched = { ...ref };
  if (!enriched.source_uri && enriched.document_id) {
    const doc = findDocumentById(enriched.document_id);
    if (doc?.source_uri) {
      enriched = { ...enriched, source_uri: doc.source_uri };
      if (doc.source_type === "url" || doc.source_type === "youtube" || doc.source_type === "facebook") {
        enriched.type = enriched.type || "web";
        enriched.css_class = enriched.css_class || "source-web";
      }
    }
  }
  if (!enriched.source_uri) {
    const uri = extractSourceUriFromLabel(enriched.raw_label || enriched.label || "");
    if (uri) {
      enriched = {
        ...enriched,
        source_uri: uri,
        type: enriched.type === "inference" ? "web" : enriched.type || "web",
        css_class: enriched.css_class === "source-inference" ? "source-web" : enriched.css_class || "source-web",
      };
    }
  }
  return enriched;
}

function enrichReferenceList(appendix) {
  return (appendix || []).map(enrichReference);
}

function sortSourcesForSidebar(appendix) {
  return [...appendix].sort((a, b) => {
    const aLink = Boolean(a.source_uri);
    const bLink = Boolean(b.source_uri);
    if (aLink !== bLink) return aLink ? -1 : 1;
    return (a.num || 0) - (b.num || 0);
  });
}

function sourcePublisherLabel(ref) {
  if (ref?.source_uri) {
    try {
      const host = new URL(ref.source_uri).hostname.replace(/^www\./, "");
      if (host.endsWith(".gov")) {
        const base = host.slice(0, -4);
        return base ? `${base} (.gov)` : host;
      }
      return host;
    } catch (_) {
      return truncate(ref.source_uri, 36);
    }
  }
  return citePillLabel(ref);
}

function sourceCardUrlLine(ref) {
  if (!ref?.source_uri) return "";
  return `<p class="source-card-url muted small">${escapeHtml(truncate(ref.source_uri, 96))}</p>`;
}

function sourceCardTitleHtml(ref) {
  const title = ref.display_label || ref.label || `Source ${ref.num}`;
  const safeTitle = escapeHtml(truncate(title, 120));
  if (ref.source_uri) {
    return `<a href="${escapeHtml(ref.source_uri)}" class="source-card-title-link" target="_blank" rel="noopener noreferrer">${safeTitle}</a>`;
  }
  return safeTitle;
}

function sourceCardSnippet(ref) {
  const type = ref.type || "";
  if (ref.source_uri) {
    if (type === "web" || ref.css_class?.includes("web")) {
      return "Web source cited in this answer — open the link to verify.";
    }
    if (ref.document_title) {
      return `From your library: ${ref.document_title}`;
    }
    return "Linked source — open to view the original.";
  }
  if (type === "inference") {
    return "General medical knowledge — not from your library. Verify independently.";
  }
  if (type === "unknown") {
    return "Not in stored records. Verify on ClinicalTrials.gov or with your care team.";
  }
  if (type === "patient_context") {
    return "From Settings → Patient context (not verified clinical record).";
  }
  if (ref.document_title) {
    return `Stored document: ${ref.document_title}`;
  }
  return ref.type_display || ref.display_label || "";
}

function renderSourceSidebarCard(ref, idPrefix, { collapsed = false } = {}) {
  const snippet = sourceCardSnippet(ref);
  const publisher = sourcePublisherLabel(ref);
  const favicon = sourceFaviconUrl(ref);
  let action = "";
  if (ref.source_uri) {
    action = `<a href="${escapeHtml(ref.source_uri)}" class="source-card-link" target="_blank" rel="noopener noreferrer">Visit site ↗</a>`;
  } else if (ref.document_id) {
    action = `<button type="button" class="source-card-link ref-doc-link" data-doc-id="${escapeHtml(ref.document_id)}">Library</button>`;
  }
  const collapsedClass = collapsed ? " is-collapsed" : "";
  const cardClass = ref.source_uri ? " source-card-linkable" : "";
  const faviconHtml = favicon
    ? `<img src="${escapeHtml(favicon)}" alt="" class="source-card-favicon" width="14" height="14" loading="lazy">`
    : "";
  return `<article id="${escapeHtml(refEntryId(idPrefix, ref.num))}" class="source-card${collapsedClass}${cardClass}" data-ref-num="${escapeHtml(String(ref.num))}">
    <h5 class="source-card-title">${sourceCardTitleHtml(ref)}</h5>
    <p class="source-card-snippet muted small">${escapeHtml(snippet)}</p>
    ${sourceCardUrlLine(ref)}
    <div class="source-card-footer">
      <span class="source-card-publisher">${faviconHtml}${escapeHtml(publisher)}</span>
      ${action}
    </div>
  </article>`;
}

function renderSourcesSidebar({ wrap, inner, appendix, idPrefix = "ref" }) {
  if (!wrap || !inner) return;
  wrap.classList.remove("is-expanded");
  const enriched = sortSourcesForSidebar(enrichReferenceList(appendix));
  if (!enriched.length) {
    wrap.classList.add("hidden");
    inner.innerHTML = "";
    return;
  }
  wrap.classList.remove("hidden");
  const count = enriched.length;
  const linkCount = enriched.filter((ref) => ref.source_uri).length;
  const countLabel =
    linkCount >= Math.max(1, Math.ceil(count * 0.4))
      ? `${linkCount || count} site${(linkCount || count) === 1 ? "" : "s"}`
      : `${count} source${count === 1 ? "" : "s"}`;
  const favicons = sourceSidebarFaviconStack(enriched);
  const hasMore = count > SOURCES_SIDEBAR_PREVIEW;
  inner.innerHTML = `
    <div class="sources-sidebar-header">
      <div class="sources-sidebar-header-row">
        ${favicons}
        <h4>${countLabel}</h4>
      </div>
      <p class="sources-sidebar-sub muted small">Links and records cited in this answer</p>
    </div>
    <div class="sources-sidebar-list">
      ${enriched
        .map((ref, index) =>
          renderSourceSidebarCard(ref, idPrefix, {
            collapsed: hasMore && index >= SOURCES_SIDEBAR_PREVIEW,
          })
        )
        .join("")}
    </div>
    ${
      hasMore
        ? `<button type="button" class="btn ghost sources-show-all" data-action="expand-sources">Show all</button>`
        : ""
    }`;
}

function formatNumberedReferences(text, registry = state.referenceRegistry, idPrefix = "ref") {
  if (!text) return "";
  let escaped = escapeHtml(text);
  escaped = escaped.replace(/\[SOURCE:\s*([^\]]+)\]/gi, (_, inner) => {
    const num = findRefNumByRawLabel(inner, registry);
    if (num != null) return `[${num}]`;
    return renderInlineSourceCitation(describeSourceTagInner(inner), inner);
  });
  return formatMarkdownEmphasis(escaped)
    .replace(/\[(\d+)\]/g, (_, num) => {
      const ref = enrichReference(refMetaFromRegistry(num, registry));
      const hash = refEntryHash(idPrefix, num);
      const fullTitle = ref?.display_label || ref?.label || `Reference ${num}`;
      if (!ref) {
        return `<a href="${hash}" class="cite-pill ref-cite-link" title="${escapeHtml(fullTitle)}">Source</a>`;
      }
      const cls = ref.css_class || sourceTagClass(ref.raw_label || ref.label || "");
      const docAttr = ref.document_id ? ` data-doc-id="${escapeHtml(ref.document_id)}"` : "";
      const href = ref.source_uri || hash;
      const externalAttr = ref.source_uri
        ? ` target="_blank" rel="noopener noreferrer" data-external="1"`
        : "";
      return `<a href="${escapeHtml(href)}" class="cite-pill ref-cite-link ${cls}" title="${escapeHtml(fullTitle)}"${docAttr}${externalAttr}>${escapeHtml(citePillLabel(ref))}</a>`;
    })
    .replace(/\n/g, "<br>");
}

function renderReferenceActions(ref) {
  if (ref.source_uri) {
    return `<a href="${escapeHtml(ref.source_uri)}" class="ref-external-link" target="_blank" rel="noopener noreferrer">Open source ↗</a>`;
  }
  if (ref.document_id) {
    return `<button type="button" class="btn ghost ref-doc-link" data-doc-id="${escapeHtml(ref.document_id)}">Open in Library</button>`;
  }
  const type = ref.type || ref.css_class || "";
  if (type === "inference" || String(type).includes("inference")) {
    return `<span class="ref-no-source">No stored source — verify independently</span>`;
  }
  if (type === "unknown" || String(type).includes("unknown")) {
    return `<span class="ref-no-source">Not in library — verify externally</span>`;
  }
  if (type === "patient_context" || String(type).includes("context")) {
    return `<span class="ref-no-source">From patient context (Settings)</span>`;
  }
  return "";
}

function renderReferenceEntry(ref, idPrefix = "ref", { anchor = true } = {}) {
  const label = ref.display_label || ref.label || "";
  const actions = renderReferenceActions(ref);
  const idAttr = anchor ? ` id="${escapeHtml(refEntryId(idPrefix, ref.num))}"` : "";
  return `<li${idAttr} class="reference-entry">
    ${renderSourceBadge(ref)}
    <span class="ref-num">[${escapeHtml(String(ref.num))}]</span>
    <span class="ref-label">${escapeHtml(label)}</span>
    ${actions}
  </li>`;
}

function renderReferenceList(refs, heading = "References", idPrefix = "ref") {
  if (!refs || !refs.length) return "";
  const items = refs.map((ref) => renderReferenceEntry(ref, idPrefix, { anchor: false })).join("");
  return `
    <div class="section-references-inner">
      <h5>${escapeHtml(heading)}</h5>
      <ol class="reference-list">${items}</ol>
    </div>`;
}

function renderReferencesBlock(element, refs, heading = "References", idPrefix = "ref") {
  if (!element) return;
  if (!refs || !refs.length) {
    element.classList.add("hidden");
    element.innerHTML = "";
    return;
  }
  element.classList.remove("hidden");
  element.innerHTML = renderReferenceList(refs, heading, idPrefix);
}

function renderReferencesAppendix(
  analysis,
  { wrap, list, idPrefix } = {}
) {
  wrap = wrap || $("#references-appendix");
  list = list || $("#references-appendix-list");
  const appendix = analysis?.references || [];
  if (!wrap || !list) return;
  const prefix = idPrefix || analysis?.id || "home";
  if (!appendix.length) {
    wrap.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  wrap.classList.remove("hidden");
  list.innerHTML = appendix.map((ref) => renderReferenceEntry(ref, prefix, { anchor: true })).join("");
}

function initReferenceNavigation() {
  document.addEventListener("click", (event) => {
    const expandBtn = event.target.closest("[data-action=expand-sources]");
    if (expandBtn) {
      const sidebar = expandBtn.closest(".sources-sidebar");
      sidebar?.classList.add("is-expanded");
      expandBtn.remove();
      return;
    }

    const docLink = event.target.closest(".ref-doc-link,[data-doc-id].ref-cite-link");
    if (docLink?.dataset?.docId) {
      event.preventDefault();
      viewDocument(docLink.dataset.docId);
      return;
    }
    const citeLink = event.target.closest("a.ref-cite-link, a.cite-pill");
    if (citeLink) {
      if (citeLink.dataset.external === "1" || /^https?:/i.test(citeLink.getAttribute("href") || "")) {
        return;
      }
      if (citeLink.hash) {
        const id = citeLink.hash.slice(1);
        const panel = citeLink.closest(".panel.active, .custom-task-detail, .answer-layout");
        const target =
          panel?.querySelector(`#${CSS.escape(id)}`) ||
          document.getElementById(id) ||
          document.querySelector(citeLink.hash);
        if (target) {
          event.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
          target.classList.add("ref-highlight");
          setTimeout(() => target.classList.remove("ref-highlight"), 1600);
        }
      }
    }
  });
}

const SOURCE_TYPE_ORDER = ["document", "diagnostic", "web", "patient_context", "inference", "unknown"];

const SOURCE_TYPE_CSS = {
  document: "source-document",
  diagnostic: "source-diagnostic",
  web: "source-web",
  patient_context: "source-context",
  inference: "source-inference",
  unknown: "source-unknown",
};

function renderSourceLabelsForm() {
  const form = $("#source-labels-form");
  if (!form) return;
  const labels = state.settings.source_labels || {};
  form.innerHTML = SOURCE_TYPE_ORDER.map((key) => {
    const entry = labels[key] || {};
    const cls = SOURCE_TYPE_CSS[key] || "source-document";
    return `
      <div class="source-label-row">
        <span class="source-tag ${cls}">${escapeHtml(entry.shorthand || "?")}</span>
        <label>Display name
          <input type="text" data-source-field="display" data-source-type="${key}" value="${escapeHtml(entry.display || "")}" maxlength="120">
        </label>
        <label>Shorthand
          <input type="text" data-source-field="shorthand" data-source-type="${key}" value="${escapeHtml(entry.shorthand || "")}" maxlength="12">
        </label>
      </div>`;
  }).join("");
}

function collectSourceLabelsFromForm() {
  const payload = {};
  SOURCE_TYPE_ORDER.forEach((key) => {
    const display = formValue(`[data-source-field="display"][data-source-type="${key}"]`);
    const shorthand = formValue(`[data-source-field="shorthand"][data-source-type="${key}"]`);
    payload[key] = { display, shorthand };
  });
  return payload;
}

function formValue(selector) {
  return $(selector)?.value.trim() || "";
}

async function saveSourceLabels() {
  const source_labels = collectSourceLabelsFromForm();
  for (const key of SOURCE_TYPE_ORDER) {
    if (!source_labels[key].display || !source_labels[key].shorthand) {
      return toast(`Display name and shorthand are required for ${key}`, "error");
    }
  }
  const data = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ source_labels }),
  });
  state.settings = { ...state.settings, ...data.settings };
  renderSourceLabelsForm();
  renderSourceLegend(state.sourceLegend);
  toast("Source labels saved");
  if ($("#panel-analyze")?.classList.contains("active")) {
    await loadLatestAssessment();
  }
  if ($("#panel-library")?.classList.contains("active")) {
    await refreshLibrary();
  }
  if ($("#panel-settings")?.classList.contains("active")) loadAuditTrail(true);
}

async function saveDocumentCitation() {
  if (!state.activeDocumentId) return;
  const value = $("#doc-citation-display-name")?.value.trim() || "";
  await api(`/api/documents/${state.activeDocumentId}/citation`, {
    method: "PATCH",
    body: JSON.stringify({ citation_display_name: value || null }),
  });
  toast("Citation name saved");
  await refreshLibrary({ page: state.libraryPage, sourceType: state.libraryFilter });
  if (state.latestAnalysis) await loadLatestAssessment();
  viewDocument(state.activeDocumentId);
  if ($("#panel-settings")?.classList.contains("active")) loadAuditTrail(true);
}

function openItemStatusLabel(status) {
  const s = String(status || "open").toLowerCase();
  if (s === "investigating") return "Investigating";
  if (s === "pending_review") return "Ready to review";
  if (s === "investigated") return "Investigated";
  if (s === "resolved") return "Resolved";
  if (s === "closed") return "Closed";
  return "Open";
}

const INVESTIGATION_GUIDANCE_PRESETS = [
  "Focus on documented evidence only — cite every claim with [SOURCE: …]",
  "What additional tests or records would resolve this gap?",
  "Summarize staging implications only if supported by imaging or pathology reports",
  "Compare conflicting information across stored documents",
  "Outline surgical vs systemic options relevant to this item",
  "Keep the response concise — bullet points preferred",
];

function initInvestigationGuidancePresets() {
  const container = $("#investigation-guidance-presets");
  if (!container) return;
  container.innerHTML = INVESTIGATION_GUIDANCE_PRESETS.map(
    (text, index) =>
      `<button type="button" class="btn ghost guidance-preset" data-index="${index}">${escapeHtml(text)}</button>`
  ).join("");
  container.querySelectorAll(".guidance-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = INVESTIGATION_GUIDANCE_PRESETS[Number(btn.dataset.index)];
      appendInvestigationGuidance(text);
    });
  });
}

function appendInvestigationGuidance(text) {
  const el = $("#open-item-guidance");
  if (!el || !text) return;
  const current = el.value.trim();
  el.value = current ? `${current}\n\n${text}` : text;
  el.focus();
}

function getInvestigationGuidanceInput() {
  return $("#open-item-guidance")?.value.trim() || "";
}

function setInvestigationGuidanceInput(text) {
  const el = $("#open-item-guidance");
  if (el) el.value = text || "";
}

function isOpenItemClosed(item) {
  const s = String(item?.status || "open").toLowerCase();
  return s === "resolved" || s === "closed";
}

function itemChipStatusClass(status) {
  const s = String(status || "open").toLowerCase().replace(/_/g, "-");
  return `item-chip item-chip-status status-${s.replace(/[^a-z0-9-]/g, "")}`;
}

function renderOpenItemComments(item) {
  const list = $("#open-item-comments-list");
  if (!list) return;
  const comments = item?.comments || [];
  if (!comments.length) {
    list.innerHTML = '<p class="muted small">No comments yet.</p>';
    return;
  }
  list.innerHTML = comments
    .map(
      (comment) => `
      <div class="open-item-comment">
        <time class="muted small">${escapeHtml(formatTimestamp(comment.created_at))}</time>
        <p>${escapeHtml(comment.text || "").replace(/\n/g, "<br>")}</p>
      </div>`
    )
    .join("");
}

function renderOpenItemPanel(item) {
  const panel = $("#open-item-panel");
  const meta = $("#open-item-meta");
  const acceptedBody = $("#open-item-investigation");
  const acceptedWrap = $("#open-item-accepted-review");
  const acceptedMeta = $("#open-item-accepted-meta");
  const draftWrap = $("#open-item-draft-review");
  const draftRaw = $("#open-item-draft-raw");
  const draftMeta = $("#open-item-draft-meta");
  const draftEdit = $("#open-item-draft-edit");
  const title = $("#open-item-panel-title");
  const resolveBtn = $("#btn-resolve-item");
  const reopenBtn = $("#btn-reopen-item");
  const investigateBtn = $("#btn-investigate-item");
  if (!panel || !item) return;

  panel.classList.remove("hidden");
  $("#open-item-scope-hint")?.classList.toggle("hidden", !state.latestAnalysis);
  if (title) title.textContent = truncate(item.item, 100);
  if (meta) {
    meta.innerHTML = `
      <span class="item-chip item-chip-type">${escapeHtml(item.type || item.item_type || "Item")}</span>
      <span class="${itemChipStatusClass(item.status)}">${escapeHtml(openItemStatusLabel(item.status))}</span>
      ${item.investigation_at ? `<span class="muted small">Accepted: ${escapeHtml(formatTimestamp(item.investigation_at))}</span>` : ""}
      ${item.investigation_model ? `<span class="badge">${escapeHtml(item.investigation_model)}</span>` : ""}`;
  }

  setInvestigationGuidanceInput(item.investigation_guidance || "");

  const hasDraft = Boolean(item.investigation_draft_response);
  const hasAccepted = Boolean(item.investigation_response);

  draftWrap?.classList.toggle("hidden", !hasDraft);
  acceptedWrap?.classList.toggle("hidden", !hasAccepted);

  if (hasDraft && draftRaw) {
    draftRaw.innerHTML = `<div class="sourced-text">${formatWithSources(item.investigation_draft_response)}</div>`;
    if (draftMeta) {
      draftMeta.textContent = [
        item.investigation_draft_at
          ? `Draft generated ${formatTimestamp(item.investigation_draft_at)}`
          : "Draft ready for review",
        item.investigation_draft_model ? `· ${item.investigation_draft_model}` : "",
        item.investigation_guidance ? `· Guidance applied` : "",
      ]
        .filter(Boolean)
        .join(" ");
    }
    if (draftEdit) draftEdit.value = item.investigation_draft_response;
  } else if (draftEdit) {
    draftEdit.value = "";
  }

  if (hasAccepted && acceptedBody) {
    acceptedBody.innerHTML = formatWithSources(item.investigation_response);
    if (acceptedMeta) {
      acceptedMeta.textContent = item.investigation_at
        ? `Accepted ${formatTimestamp(item.investigation_at)}`
        : "";
    }
  } else if (acceptedBody) {
    acceptedBody.innerHTML = "";
  }

  renderOpenItemComments(item);

  const closed = isOpenItemClosed(item);
  resolveBtn?.classList.toggle("hidden", closed);
  reopenBtn?.classList.toggle("hidden", !closed);
  if (investigateBtn) {
    investigateBtn.disabled = item.status === "investigating";
    investigateBtn.textContent =
      item.status === "investigating"
        ? "Investigation running…"
        : hasDraft
          ? "Run investigation again"
          : "Run investigation";
  }

  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function selectOpenItem(item) {
  state.selectedOpenItemId = item?.id || null;
  state.selectedOpenItem = item || null;
  $$(".open-item-row").forEach((row) => {
    row.classList.toggle("selected", row.dataset.id === item?.id);
  });
  const commentInput = $("#open-item-comment-input");
  if (!item) {
    commentInput && (commentInput.value = "");
    $("#open-item-panel")?.classList.add("hidden");
    return;
  }
  if (commentInput && commentInput.dataset.itemId !== item.id) {
    commentInput.value = "";
    commentInput.dataset.itemId = item.id;
  }
  renderOpenItemPanel(item);
}

async function loadOpenItem(id) {
  const data = await api(`/api/open-items/${id}`);
  return data.open_item;
}

async function investigateSelectedOpenItem() {
  const id = state.selectedOpenItemId;
  if (!id) return toast("Select an open item first", "error");

  const item = state.selectedOpenItem;
  if (item?.investigation_draft_response) {
    const ok = confirm("Replace the current draft investigation with a new run?");
    if (!ok) return;
  }

  const loading = $("#open-item-loading");
  const btn = $("#btn-investigate-item");
  loading?.classList.remove("hidden");
  if (btn) btn.disabled = true;

  try {
    selectOpenItem({ ...state.selectedOpenItem, status: "investigating" });
    const guidance = getInvestigationGuidanceInput();
    const data = await api(`/api/open-items/${id}/investigate`, {
      method: "POST",
      body: JSON.stringify({ guidance }),
    });
    selectOpenItem(data.open_item);
    updateOpenItemInState(data.open_item);
    toast("Draft investigation ready — review before accepting");
  } catch (err) {
    toast(err.message, "error");
    if (state.selectedOpenItemId) {
      try {
        const refreshed = await loadOpenItem(state.selectedOpenItemId);
        selectOpenItem(refreshed);
        updateOpenItemInState(refreshed);
      } catch {
        /* ignore */
      }
    }
  } finally {
    loading?.classList.add("hidden");
    if (btn) btn.disabled = false;
  }
}

async function acceptInvestigationDraft() {
  const id = state.selectedOpenItemId;
  if (!id) return toast("Select an open item first", "error");
  const edited = $("#open-item-draft-edit")?.value.trim();
  const body = edited ? { edited_response: edited } : {};
  try {
    const data = await api(`/api/open-items/${id}/investigate/accept`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    selectOpenItem(data.open_item);
    updateOpenItemInState(data.open_item);
    toast("Investigation accepted");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function discardInvestigationDraft() {
  const id = state.selectedOpenItemId;
  if (!id) return toast("Select an open item first", "error");
  if (!confirm("Discard this draft investigation?")) return;
  try {
    const data = await api(`/api/open-items/${id}/investigate/discard`, { method: "POST" });
    selectOpenItem(data.open_item);
    updateOpenItemInState(data.open_item);
    toast("Draft discarded");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function commentInvestigationDraft() {
  const id = state.selectedOpenItemId;
  if (!id) return toast("Select an open item first", "error");
  try {
    const data = await api(`/api/open-items/${id}/investigate/comment`, { method: "POST" });
    selectOpenItem(data.open_item);
    updateOpenItemInState(data.open_item);
    toast("Draft saved as comment");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function resolveSelectedOpenItem() {
  const id = state.selectedOpenItemId;
  if (!id) return toast("Select an open item first", "error");
  try {
    const data = await api(`/api/open-items/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "resolved" }),
    });
    selectOpenItem(data.open_item);
    updateOpenItemInState(data.open_item);
    toast("Item resolved");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function reopenSelectedOpenItem() {
  const id = state.selectedOpenItemId;
  if (!id) return toast("Select an open item first", "error");
  try {
    const data = await api(`/api/open-items/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "open" }),
    });
    selectOpenItem(data.open_item);
    updateOpenItemInState(data.open_item);
    toast("Item reopened");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function addCommentToSelectedOpenItem() {
  const id = state.selectedOpenItemId;
  const input = $("#open-item-comment-input");
  if (!id) return toast("Select an open item first", "error");
  const comment = input?.value.trim();
  if (!comment) return toast("Enter a comment", "error");
  try {
    const data = await api(`/api/open-items/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ comment }),
    });
    if (input) input.value = "";
    selectOpenItem(data.open_item);
    updateOpenItemInState(data.open_item);
    toast("Comment added");
  } catch (err) {
    toast(err.message, "error");
  }
}

function updateOpenItemInState(item) {
  if (!state.latestAnalysis?.open_items) return;
  state.latestAnalysis.open_items = state.latestAnalysis.open_items.map((oi) =>
    oi.id === item.id ? { ...oi, ...item } : oi
  );
  renderOpenItemsTable(state.latestAnalysis.open_items);
  selectOpenItem(item);
}

function renderOpenItemsTable(items) {
  const tbody = $("#open-items-body");
  if (!tbody) return;

  if (!items || !items.length) {
    tbody.innerHTML =
      '<tr class="empty-row"><td colspan="5" class="muted">No open items yet.</td></tr>';
    state.selectedOpenItemId = null;
    state.selectedOpenItem = null;
    $("#open-item-panel")?.classList.add("hidden");
    return;
  }

  tbody.innerHTML = items
    .map((item) => {
      const closed = isOpenItemClosed(item);
      const commentCount = (item.comments || []).length;
      return `
      <tr class="open-item-row${item.id === state.selectedOpenItemId ? " selected" : ""}${closed ? " resolved" : ""}" data-id="${escapeHtml(item.id || "")}">
        <td>${escapeHtml(String(item.priority || ""))}</td>
        <td class="open-item-text-cell">
          <span class="open-item-text">${escapeHtml(item.item || "")}</span>
          ${commentCount ? `<span class="item-comment-count">${commentCount} comment${commentCount === 1 ? "" : "s"}</span>` : ""}
        </td>
        <td class="item-chip-cell"><span class="item-chip item-chip-type">${escapeHtml(item.type || item.item_type || "Item")}</span></td>
        <td class="item-chip-cell"><span class="${itemChipStatusClass(item.status)}">${escapeHtml(openItemStatusLabel(item.status))}</span></td>
        <td class="item-chip-cell"><button type="button" class="item-chip item-chip-action btn-explore" data-id="${escapeHtml(item.id || "")}">Explore</button></td>
      </tr>`;
    })
    .join("");

  tbody.querySelectorAll(".btn-explore").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const item = items.find((i) => i.id === id);
      if (item) selectOpenItem(item);
    });
  });
}

function renderSourceAttributionNotice(analysis) {
  const el = $("#source-attribution-notice");
  if (!el) return;

  if (!analysis) {
    el.classList.add("hidden");
    return;
  }

  const level = analysis.source_attribution || "missing";
  if (level === "full") {
    el.classList.add("hidden");
    return;
  }

  let html = "";
  if (level === "normalized") {
    html =
      '<span class="notice-title">Partial source attribution.</span> ' +
      "Informal citations were converted to [SOURCE: …] tags. " +
      "Re-run baseline assessment for fully LLM-generated tags.";
  } else {
    html =
      '<span class="notice-title">Source tags missing.</span> ' +
      "Expand Run further analysis and run baseline assessment to regenerate with source attribution.";
  }

  el.classList.remove("hidden");
  el.className = `source-attribution-notice ${level === "normalized" ? "warn" : "error"}`;
  el.innerHTML = html;
}

function renderLatestAssessment(analysis) {
  state.latestAnalysis = analysis || null;
  const execTimeEl = $("#executive-summary-time");
  const execTextEl = $("#executive-summary-text");
  const legendWrap = $("#source-legend-wrap");
  const fullCard = $("#full-assessment-card");
  const fullBody = $("#full-assessment-body");

  if (!execTextEl) return;

  if (!analysis) {
    setSectionLastUpdated(execTimeEl, null);
    setSectionLastUpdated($("#full-assessment-time"), null);
    setSectionLastUpdated($("#open-items-time"), null);
    legendWrap?.removeAttribute("open");
    fullCard?.classList.add("hidden");
    renderSourcesSidebar({
      wrap: $("#home-sources-sidebar"),
      inner: $("#home-sources-sidebar-inner"),
      appendix: [],
      idPrefix: "home",
    });
    state.referenceRegistry = {};
    execTextEl.innerHTML = "";
    if (fullBody) fullBody.innerHTML = "";
    renderOpenItemsTable([]);
    selectOpenItem(null);
    renderSourceAttributionNotice(null);
    renderHomeState(false);
    renderAssessmentScopeCard();
    return;
  }

  const refPrefix = analysis.id;
  const summaryDisplay = analysis.executive_summary_display || analysis.executive_summary || "";
  const responseDisplay = analysis.response_display || analysis.response || "";
  const updatedAt = analysis.created_at;

  state.referenceRegistry = analysis.reference_registry || {};
  state.sourceLegend = analysis.source_legend || state.sourceLegend;
  renderSourceLegend(state.sourceLegend);

  setSectionLastUpdated(execTimeEl, summaryDisplay ? updatedAt : null);
  setSectionLastUpdated($("#full-assessment-time"), responseDisplay ? updatedAt : null);
  setSectionLastUpdated($("#open-items-time"), (analysis.open_items || []).length ? updatedAt : null);
  legendWrap?.removeAttribute("open");

  renderHomeState(true);
  renderSourceAttributionNotice(analysis);

  if (summaryDisplay) {
    execTextEl.innerHTML = `<div class="numbered-text">${formatNumberedReferences(summaryDisplay, state.referenceRegistry, refPrefix)}</div>`;
  } else {
    execTextEl.innerHTML = '<p class="muted">No assessment text was returned.</p>';
  }

  if (fullCard && fullBody) {
    if (responseDisplay) {
      fullCard.classList.remove("hidden");
      fullBody.innerHTML = formatNumberedReferences(responseDisplay, state.referenceRegistry, refPrefix);
    } else {
      fullCard.classList.add("hidden");
      fullBody.innerHTML = "";
    }
  }

  renderSourcesSidebar({
    wrap: $("#home-sources-sidebar"),
    inner: $("#home-sources-sidebar-inner"),
    appendix: analysis.references || [],
    idPrefix: refPrefix,
  });

  renderOpenItemsTable(analysis.open_items || []);
  renderAssessmentScopeCard();
}

async function loadLatestAssessment() {
  try {
    const data = await api("/api/analyses/latest");
    renderLatestAssessment(data.analysis);
    if (data.analysis) {
      state.analyses = [data.analysis, ...state.analyses.filter((a) => a.id !== data.analysis.id)];
    }
  } catch (err) {
    console.error("loadLatestAssessment failed", err);
    if (!state.latestAnalysis) {
      renderLatestAssessment(null);
    }
  } finally {
    updateHomeToolbar();
  }
}

async function exportAssessmentPdf() {
  if (!state.latestAnalysis) {
    return toast("No assessment to export", "error");
  }

  const btn = $("#btn-export-pdf");
  if (btn) btn.disabled = true;

  try {
    const result = await downloadAnalysisPdf(state.latestAnalysis.id, {
      silent: true,
      analysis: state.latestAnalysis,
    });
    if (!result) return;
    triggerPdfDownload(result.blob, result.filename);
    toast("PDF downloaded");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderHistory() {
  const list = $("#history-list");
  if (!state.analyses.length) {
    list.innerHTML = `<p class="muted">No assessments yet.</p>`;
    return;
  }

  const latestId = state.latestAnalysis?.id || state.analyses[0]?.id;

  list.innerHTML = state.analyses
    .map((a, index) => {
      const isLatest = a.id === latestId || index === 0;
      return `
      <details class="history-item${isLatest ? " history-item-latest" : ""}"${isLatest ? " open" : ""}>
        <summary>
          <div class="history-summary-main">
            <time class="history-date" datetime="${escapeHtml(a.created_at)}">${escapeHtml(formatTimestamp(a.created_at))}</time>
            <span class="history-query">${escapeHtml(truncate(a.query, 140))}</span>
          </div>
          <span class="badge">${escapeHtml(analysisTypeLabel(a.analysis_type))}</span>
          ${isLatest ? '<span class="badge">Latest</span>' : ""}
        </summary>
        <pre class="doc-text">${escapeHtml(a.response_display || a.response)}</pre>
      </details>`;
    })
    .join("");
}

async function loadHistory() {
  const data = await api("/api/analyses");
  state.analyses = data.analyses || [];
  if (!state.latestAnalysis && state.analyses.length) {
    state.latestAnalysis = state.analyses[0];
  }
  renderHistory();
}

function renderDicomMetaGrid(rows) {
  if (!rows?.length) return "";
  return rows
    .map(
      (row) =>
        `<div class="dicom-meta-item"><dt>${escapeHtml(row.label)}</dt><dd>${escapeHtml(row.value)}</dd></div>`
    )
    .join("");
}

function renderDicomViewer(data, doc) {
  const previewUrl = data.preview_url || "";
  const metaHtml = renderDicomMetaGrid(data.dicom_metadata || []);
  const previewBlock = previewUrl
    ? `<div class="dicom-preview-wrap">
        <img class="doc-file-image dicom-preview" src="${escapeHtml(previewUrl)}" alt="${escapeHtml(doc.title)}" loading="lazy">
        <p class="dicom-preview-fallback muted small hidden">Preview unavailable for this slice. Download the original DICOM file to open it in a PACS or DICOM viewer.</p>
      </div>`
    : `<p class="muted">Preview unavailable. Download the original DICOM file to view it in a certified imaging application.</p>`;

  return `<div class="dicom-viewer">
    ${previewBlock}
    ${metaHtml ? `<dl class="dicom-meta-grid">${metaHtml}</dl>` : ""}
    <p class="muted small dicom-viewer-note">Browser preview is for orientation only. Clinical decisions must use original DICOM files and certified imaging tools.</p>
  </div>`;
}

async function viewDocument(id) {
  switchTab("library");
  const data = await api(`/api/documents/${id}`);
  const panel = $("#doc-detail");
  const doc = data.document;
  if (!panel || !doc) return;

  state.activeDocumentId = id;
  panel.classList.remove("hidden");
  const info = doc.source_info || {};
  const displayName = info.display_name || doc.title;
  $("#doc-detail-title").textContent = displayName;

  const metaEl = $("#doc-detail-meta");
  if (metaEl) {
    const meta = doc.metadata || {};
    const sourceBadge = info.shorthand
      ? `<span class="source-tag ${escapeHtml(info.css_class || "source-document")}">${escapeHtml(info.shorthand)}</span>`
      : "";
    metaEl.innerHTML = `
      ${sourceBadge}
      <span class="badge">${escapeHtml(doc.source_type || "document")}</span>
      <span class="muted small">${escapeHtml(info.type_display || "")}</span>
      <span class="muted small">${escapeHtml(formatTimestamp(doc.created_at))}</span>
      ${meta.modality ? `<span class="badge">${escapeHtml(meta.modality)}</span>` : ""}
      ${meta.file_size_label ? `<span class="muted small">${escapeHtml(meta.file_size_label)}</span>` : ""}`;
  }

  const citationPanel = $("#doc-detail-citation");
  const citationInput = $("#doc-citation-display-name");
  if (citationPanel && citationInput) {
    citationPanel.classList.remove("hidden");
    citationInput.value = doc.citation_display_name || "";
  }

  const actionsEl = $("#doc-detail-actions");
  const sourceEl = $("#doc-detail-source");
  const textEl = $("#doc-detail-text");
  const textWrap = $("#doc-detail-text-wrap");

  if (actionsEl) {
    const links = [];
    if (data.file_url) {
      links.push(
        `<a class="btn secondary" href="${escapeHtml(data.file_url)}" target="_blank" rel="noopener">Open original file</a>`
      );
      links.push(
        `<a class="btn ghost" href="${escapeHtml(data.file_url)}" download>Download</a>`
      );
    }
    if (data.source_url) {
      links.push(
        `<a class="btn ghost" href="${escapeHtml(data.source_url)}" target="_blank" rel="noopener">Open source URL</a>`
      );
    }
    actionsEl.innerHTML = links.join("") || '<span class="muted small">No original file stored for this item.</span>';
  }

  if (sourceEl) {
    sourceEl.innerHTML = "";
    sourceEl.classList.add("hidden");
    if (data.file_url) {
      if (data.view_kind === "pdf") {
        sourceEl.innerHTML = `<iframe class="doc-file-frame" src="${escapeHtml(data.file_url)}" title="${escapeHtml(doc.title)}"></iframe>`;
        sourceEl.classList.remove("hidden");
      } else if (data.view_kind === "dicom") {
        sourceEl.innerHTML = renderDicomViewer(data, doc);
        sourceEl.classList.remove("hidden");
        const previewImg = sourceEl.querySelector(".dicom-preview");
        const fallback = sourceEl.querySelector(".dicom-preview-fallback");
        if (previewImg && fallback) {
          previewImg.addEventListener("error", () => {
            previewImg.classList.add("hidden");
            fallback.classList.remove("hidden");
          });
        }
      } else if (data.view_kind === "image") {
        sourceEl.innerHTML = `<img class="doc-file-image" src="${escapeHtml(data.file_url)}" alt="${escapeHtml(doc.title)}">`;
        sourceEl.classList.remove("hidden");
      } else if (data.view_kind === "video") {
        sourceEl.innerHTML = `<video class="doc-file-video" controls src="${escapeHtml(data.file_url)}"></video>`;
        sourceEl.classList.remove("hidden");
      } else if (data.view_kind === "download") {
        sourceEl.innerHTML = `<p class="muted">Preview is not available in the browser for this file type. Use <strong>Open original file</strong> or <strong>Download</strong> above.</p>`;
        sourceEl.classList.remove("hidden");
      }
    } else if (data.view_kind === "url" && data.source_url) {
      sourceEl.innerHTML = `<p class="muted">This item was ingested from a web source. Use <strong>Open source URL</strong> above to view it.</p>`;
      sourceEl.classList.remove("hidden");
    }
  }

  if (textEl) {
    textEl.textContent = data.extracted_text || "[No extracted text available]";
  }
  if (textWrap) {
    textWrap.classList.remove("hidden");
  }

  $$(".doc-item").forEach((row) => {
    row.classList.toggle("viewing", row.dataset.id === id);
  });

  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeDocumentDetail() {
  $("#doc-detail")?.classList.add("hidden");
  $$(".doc-item").forEach((row) => row.classList.remove("viewing"));
}

function toggleSelect(id, selected = null) {
  const next = selected ?? !state.selectedIds.has(id);
  if (next) state.selectedIds.add(id);
  else state.selectedIds.delete(id);
  saveSelectionToSession();
  renderDocuments();
  updateSelectedLabel();
}

async function deleteDocument(id) {
  if (!confirm("Delete this document and its stored files?")) return;
  await api(`/api/documents/${id}`, { method: "DELETE" });
  state.selectedIds.delete(id);
  saveSelectionToSession();
  state.documentIndex = state.documentIndex.filter((doc) => doc.id !== id);
  if ($("#doc-detail") && !$("#doc-detail").classList.contains("hidden")) {
    closeDocumentDetail();
  }
  const totalAfter = Math.max(0, (state.libraryTotal || 1) - 1);
  const maxPage = Math.max(1, Math.ceil(totalAfter / LIBRARY_PAGE_SIZE));
  const page = Math.min(state.libraryPage, maxPage);
  toast("Document deleted");
  await refreshLibrary({ page, sourceType: state.libraryFilter });
}

async function runAnalysis({ query = "", baseline = false, summarize = false, assessmentGuidance = "" } = {}) {
  if (state.analysisRunning) {
    toast("An analysis is already running. Please wait for it to finish.", "error");
    return;
  }

  const isCustomQuery = !baseline && !summarize && query.trim().length > 0;

  try {
    const { jobId, jobType } = await startAnalysisJob({ query, baseline, summarize, assessmentGuidance });
    setAnalysisRunning(true, jobId, jobType);
    if (isCustomQuery) {
      switchTab("custom-tasks");
      updateCustomTasksRunningBanner(true, query);
      toast("Custom task submitted");
    }
    const analysis = await pollAnalysisJob(jobId, { isCustomQuery });
    if (isCustomQuery) finishCustomTaskRun(analysis, query);
    else finishAnalysisRun(analysis);
  } catch (err) {
    if (err.status === 409) {
      toast("An analysis is already running — resuming progress.", "error");
      await resumeActiveAnalysisJob();
      return;
    }
    toast(err.message, "error");
    if (isCustomQuery) loadCustomTasks();
  } finally {
    setAnalysisRunning(false);
  }
}

function truncate(str, n) {
  const s = String(str || "");
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso || "";
  }
}

function safeOn(selector, event, handler) {
  const el = $(selector);
  if (el) el.addEventListener(event, handler);
}

function bootstrapUi() {
  initTheme();
  initScrollTop();
  initInvestigationGuidancePresets();
  initAssessmentGuidancePresets();
  initUploadResultBanner();
  initHowToNavigation();
  updateNativeShareButton();
  initReferenceNavigation();
  updateHomeToolbar();
}

async function loadInitialData() {
  loadSelectionFromSession();
  loadAssessmentGuidanceFromSession();
  try {
    await Promise.allSettled([loadDocumentIndex(), loadLatestAssessment(), loadCustomTasks()]);
  } finally {
    updateHomeToolbar();
  }
  resumeActiveAnalysisJobInBackground();
}

bootstrapUi();

// Tab navigation
$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => switchTab(tab.dataset.tab))
);

safeOn("#btn-dismiss-upload", "click", dismissUploadResult);
safeOn("#btn-close-detail", "click", () => closeDocumentDetail());
safeOn("#btn-refresh-docs", "click", () =>
  refreshLibrary({ page: state.libraryPage, sourceType: state.libraryFilter }).catch((e) =>
    toast(e.message, "error")
  )
);
safeOn("#library-type-filter", "change", (event) =>
  loadDocuments({ page: 1, sourceType: event.target.value }).catch((e) => toast(e.message, "error"))
);
safeOn("#btn-select-page", "click", () => selectDocumentsOnPage());
safeOn("#btn-select-filtered-type", "click", () => {
  if (state.libraryFilter) selectDocumentsByType(state.libraryFilter);
});
safeOn("#btn-select-by-type", "click", () => {
  const type = $("#library-select-type")?.value;
  if (!type) return toast("Choose a document type first", "error");
  selectDocumentsByType(type);
});
safeOn("#btn-select-all-docs", "click", () => selectAllDocuments());
safeOn("#btn-clear-selection", "click", () => clearDocumentSelection());
safeOn("#btn-library-prev", "click", () => {
  if (state.libraryPage <= 1) return;
  loadDocuments({ page: state.libraryPage - 1 }).catch((e) => toast(e.message, "error"));
});
safeOn("#btn-library-next", "click", () => {
  if (state.libraryPage >= libraryTotalPages()) return;
  loadDocuments({ page: state.libraryPage + 1 }).catch((e) => toast(e.message, "error"));
});
safeOn("#documents-list", "click", (event) => {
  const checkbox = event.target.closest(".doc-select-input");
  if (checkbox?.dataset.id) {
    event.stopPropagation();
    toggleSelect(checkbox.dataset.id, checkbox.checked);
    return;
  }
  const viewBtn = event.target.closest(".btn-view");
  if (viewBtn?.dataset.id) {
    viewDocument(viewBtn.dataset.id);
    return;
  }
  const selectBtn = event.target.closest(".btn-select");
  if (selectBtn?.dataset.id) {
    toggleSelect(selectBtn.dataset.id);
    return;
  }
  const deleteBtn = event.target.closest(".btn-delete");
  if (deleteBtn?.dataset.id) {
    deleteDocument(deleteBtn.dataset.id).catch((e) => toast(e.message, "error"));
  }
});
safeOn("#btn-refresh-history", "click", () => loadHistory().catch((e) => toast(e.message, "error")));

safeOn("#btn-ingest-text", "click", async () => {
  try {
    const title = $("#text-title").value.trim();
    const content = $("#text-content").value.trim();
    if (!title || !content) return toast("Title and content required", "error");
    const data = await api("/api/ingest/text", {
      method: "POST",
      body: JSON.stringify({ title, content }),
    });
    $("#text-content").value = "";
    showUploadResult(data.document);
    toast("Text saved");
    await openLibraryAfterIngest();
  } catch (e) {
    toast(e.message, "error");
  }
});

safeOn("#btn-ingest-url", "click", async () => {
  try {
    const url = $("#url-input").value.trim();
    const title = $("#url-title").value.trim() || null;
    if (!url) return toast("URL required", "error");
    const data = await api("/api/ingest/url", {
      method: "POST",
      body: JSON.stringify({ url, title }),
    });
    showUploadResult(data.document);
    toast("URL ingested");
    await openLibraryAfterIngest();
  } catch (e) {
    toast(e.message, "error");
  }
});

safeOn("#btn-ingest-youtube", "click", async () => {
  try {
    const url = $("#youtube-input").value.trim();
    const title = $("#youtube-title").value.trim() || null;
    if (!url) return toast("YouTube URL required", "error");
    const data = await api("/api/ingest/youtube", {
      method: "POST",
      body: JSON.stringify({ url, title }),
    });
    showUploadResult(data.document);
    toast("YouTube transcript ingested");
    await openLibraryAfterIngest();
  } catch (e) {
    toast(e.message, "error");
  }
});

safeOn("#btn-ingest-facebook", "click", async () => {
  try {
    const url = $("#facebook-input").value.trim();
    const title = $("#facebook-title").value.trim() || null;
    const notes = $("#facebook-notes")?.value.trim() || null;
    if (!url) return toast("Facebook URL required", "error");
    const btn = $("#btn-ingest-facebook");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Downloading…";
    }
    const data = await api("/api/ingest/facebook", {
      method: "POST",
      body: JSON.stringify({ url, title, notes }),
    });
    showUploadResult(data.document);
    if ($("#facebook-input")) $("#facebook-input").value = "";
    if ($("#facebook-title")) $("#facebook-title").value = "";
    if ($("#facebook-notes")) $("#facebook-notes").value = "";
    toast("Facebook video ingested");
    await openLibraryAfterIngest();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    const btn = $("#btn-ingest-facebook");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Ingest Facebook video";
    }
  }
});

safeOn("#btn-ingest-pdf", "click", async () => {
  try {
    const file = $("#pdf-file").files[0];
    if (!file) return toast("Choose a PDF file", "error");
    const fd = new FormData();
    fd.append("file", file);
    const title = $("#pdf-title").value.trim();
    if (title) fd.append("title", title);
    const data = await api("/api/ingest/pdf", { method: "POST", body: fd });
    showUploadResult(data.document);
    $("#pdf-file").value = "";
    $("#pdf-file").closest(".file-label")?.querySelector(".file-name")?.remove();
    const pdfTitle = $("#pdf-title");
    if (pdfTitle) {
      pdfTitle.value = "";
      delete pdfTitle.dataset.userEdited;
    }
    toast(`PDF uploaded · ${file.name}`);
    await openLibraryAfterIngest();
  } catch (e) {
    toast(e.message, "error");
  }
});

const IMAGING_EXTENSIONS = new Set([
  ".dcm",
  ".dicom",
  ".jpg",
  ".jpeg",
  ".png",
  ".gif",
  ".bmp",
  ".tif",
  ".tiff",
  ".webp",
  ".nii",
  ".nii.gz",
  ".mha",
  ".mhd",
  ".zip",
]);

let imagingSelection = [];
let imagingSelectionToken = 0;

const IMAGING_SKIP_NAMES = new Set(["DICOMDIR", "DESKTOP.INI", "THUMBS.DB"]);

function shouldSkipImagingFolderFile(file) {
  const name = String(file?.name || "").trim();
  if (!name || name.startsWith(".")) return true;
  return IMAGING_SKIP_NAMES.has(name.toUpperCase());
}

async function fileHasDicomMagic(file) {
  if (!file || file.size < 132) return false;
  try {
    const buf = await file.slice(128, 132).arrayBuffer();
    return new TextDecoder().decode(buf) === "DICM";
  } catch {
    return false;
  }
}

async function isImagingFileCandidate(file, { folderMode = false } = {}) {
  if (!file || shouldSkipImagingFolderFile(file)) return false;
  if (isImagingFile(file)) return true;
  if (folderMode || !imagingExtension(file.name)) {
    return fileHasDicomMagic(file);
  }
  return false;
}

async function setImagingSelection(files, { folderMode = false } = {}) {
  const token = ++imagingSelectionToken;
  const list = Array.from(files || []);
  const panel = $("#imaging-selection");
  const btn = $("#btn-ingest-imaging");
  if (panel && btn && list.length) {
    panel.classList.remove("hidden");
    panel.innerHTML = `<p class="imaging-selection-summary muted">Scanning ${list.length} file(s) for DICOM…</p>`;
    btn.disabled = true;
  }
  const checks = await Promise.all(list.map((file) => isImagingFileCandidate(file, { folderMode })));
  if (token !== imagingSelectionToken) return;
  imagingSelection = list.filter((_, index) => checks[index]);
  renderImagingSelection();
}

function imagingExtension(name) {
  const lower = String(name || "").toLowerCase();
  if (lower.endsWith(".nii.gz")) return ".nii.gz";
  const dot = lower.lastIndexOf(".");
  return dot >= 0 ? lower.slice(dot) : "";
}

function isImagingFile(file) {
  return IMAGING_EXTENSIONS.has(imagingExtension(file?.name));
}

function formatFileSize(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function renderImagingSelection() {
  const panel = $("#imaging-selection");
  const btn = $("#btn-ingest-imaging");
  if (!panel || !btn) return;

  if (!imagingSelection.length) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    btn.disabled = true;
    return;
  }

  const totalBytes = imagingSelection.reduce((sum, file) => sum + file.size, 0);
  const preview = imagingSelection
    .slice(0, 10)
    .map(
      (file) =>
        `<li>${escapeHtml(file.webkitRelativePath || file.name)} <span class="muted">(${formatFileSize(file.size)})</span></li>`
    )
    .join("");
  const remainder =
    imagingSelection.length > 10
      ? `<li class="muted">…and ${imagingSelection.length - 10} more</li>`
      : "";

  panel.classList.remove("hidden");
  btn.disabled = false;
  panel.innerHTML = `
    <p class="imaging-selection-summary">${imagingSelection.length} file(s) selected · ${formatFileSize(totalBytes)} total</p>
    <ul class="imaging-selection-list">${preview}${remainder}</ul>`;
}

function clearImagingSelection() {
  imagingSelectionToken += 1;
  imagingSelection = [];
  $("#imaging-files").value = "";
  $("#imaging-folder").value = "";
  renderImagingSelection();
}

function setImagingUploadProgress(message, visible) {
  const el = $("#imaging-upload-progress");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("hidden", !visible);
}

async function uploadImagingSelection() {
  if (!imagingSelection.length) return toast("Choose imaging files or a folder first", "error");

  const btn = $("#btn-ingest-imaging");
  const titlePrefix = $("#imaging-title-prefix")?.value.trim() || "";
  const notes = $("#imaging-notes")?.value.trim() || "";
  const total = imagingSelection.length;
  let uploaded = 0;
  let failed = 0;
  let lastDoc = null;

  btn.disabled = true;
  setImagingUploadProgress(`Uploading 0/${total}…`, true);

  try {
    for (const file of imagingSelection) {
      uploaded += 1;
      setImagingUploadProgress(`Uploading ${uploaded}/${total}: ${file.name}`, true);
      const fd = new FormData();
      fd.append("file", file, file.name);
      if (titlePrefix) fd.append("title_prefix", titlePrefix);
      if (notes) fd.append("notes", notes);
      const relativePath = file.webkitRelativePath || file.name;
      if (relativePath) fd.append("relative_path", relativePath);
      try {
        const data = await api("/api/ingest/imaging", { method: "POST", body: fd });
        lastDoc = data.document;
      } catch (err) {
        failed += 1;
        console.error(err);
      }
    }

    if (lastDoc) showUploadResult(lastDoc);
    if (failed === 0) {
      toast(`Uploaded ${total} imaging file${total === 1 ? "" : "s"}`);
    } else {
      toast(`Uploaded ${total - failed}/${total} imaging files (${failed} failed)`, "error");
    }
    clearImagingSelection();
    await openLibraryAfterIngest();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setImagingUploadProgress("", false);
    renderImagingSelection();
  }
}

$("#imaging-files")?.addEventListener("change", (event) => {
  $("#imaging-folder").value = "";
  setImagingSelection(event.target.files, { folderMode: false });
});

$("#imaging-folder")?.addEventListener("change", async (event) => {
  $("#imaging-files").value = "";
  const allFiles = Array.from(event.target.files || []);
  await setImagingSelection(allFiles, { folderMode: true });
  const skipped = allFiles.length - imagingSelection.length;
  if (skipped > 0) {
    toast(`Skipped ${skipped} non-DICOM/non-imaging file${skipped === 1 ? "" : "s"} in folder`);
  }
  if (!imagingSelection.length && allFiles.length) {
    toast("No DICOM or imaging files found in that folder", "error");
  }
});

$("#btn-clear-imaging")?.addEventListener("click", () => clearImagingSelection());
$("#btn-ingest-imaging")?.addEventListener("click", () =>
  uploadImagingSelection().catch((e) => toast(e.message, "error"))
);

safeOn("#btn-ingest-video", "click", async () => {
  try {
    const file = $("#video-file").files[0];
    if (!file) return toast("Choose a video file", "error");
    const fd = new FormData();
    fd.append("file", file);
    const title = $("#video-title").value.trim();
    const notes = $("#video-notes").value.trim();
    if (title) fd.append("title", title);
    if (notes) fd.append("notes", notes);
    const data = await api("/api/ingest/video", { method: "POST", body: fd });
    showUploadResult(data.document);
    $("#video-file").value = "";
    $("#video-file").closest(".file-label")?.querySelector(".file-name")?.remove();
    toast(`Video stored · ${file.name}`);
    await openLibraryAfterIngest();
  } catch (e) {
    toast(e.message, "error");
  }
});

safeOn("#btn-baseline", "click", () => confirmAndRunBaseline());
safeOn("#btn-reassess-baseline", "click", () => confirmAndRunBaseline());
safeOn("#btn-scope-library", "click", goToLibraryForScope);
safeOn("#btn-scope-select-all", "click", selectAllDocuments);
safeOn("#btn-scope-clear", "click", clearDocumentSelection);
safeOn("#btn-scope-match-last", "click", applyLastAssessmentScope);
safeOn("#btn-open-item-adjust-scope", "click", scrollToAssessmentScope);
safeOn("#btn-open-item-reassess", "click", reassessFromOpenItem);
safeOn("#btn-summarize", "click", () => runAnalysis({ summarize: true }));

safeOn("#btn-analyze", "click", () => {
  const query = $("#analyze-query").value.trim();
  if (!query) return toast("Enter a question, or use Run baseline assessment", "error");
  runAnalysis({ query });
});

$("#btn-save-settings")?.addEventListener("click", () =>
  saveModelSettings().catch((e) => toast(e.message, "error"))
);
$("#btn-save-patient")?.addEventListener("click", () =>
  savePatientContext().catch((e) => toast(e.message, "error"))
);
$("#btn-save-source-labels")?.addEventListener("click", () =>
  saveSourceLabels().catch((e) => toast(e.message, "error"))
);
$("#btn-save-doc-citation")?.addEventListener("click", () =>
  saveDocumentCitation().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-save-custom-task-annotations", "click", () =>
  saveCustomTaskAnnotations().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-export-custom-task-pdf", "click", () =>
  exportCustomTaskPdf().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-native-share-custom-task", "click", () =>
  nativeShareCustomTask().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-close-custom-task", "click", closeCustomTaskDetail);
safeOn("#btn-promote-custom-task", "click", () =>
  promoteCustomTask().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-discard-custom-task", "click", () =>
  discardCustomTask().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-refine-custom-task", "click", () =>
  refineCustomTask().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-run-custom-task", "click", () => {
  const query = $("#custom-task-query")?.value.trim();
  if (!query) return toast("Enter a question for your custom task", "error");
  runAnalysis({ query });
});
$("#settings-model")?.addEventListener("change", updateModelDescription);
$("#audit-filter")?.addEventListener("change", () => loadAuditTrail(true));
$("#btn-audit-load-more")?.addEventListener("click", () => loadAuditTrail(false));

$("#btn-export-pdf")?.addEventListener("click", () =>
  exportAssessmentPdf()
);
$("#btn-investigate-item")?.addEventListener("click", () =>
  investigateSelectedOpenItem()
);
$("#btn-accept-investigation")?.addEventListener("click", () =>
  acceptInvestigationDraft()
);
$("#btn-discard-investigation")?.addEventListener("click", () =>
  discardInvestigationDraft()
);
$("#btn-comment-investigation")?.addEventListener("click", () =>
  commentInvestigationDraft()
);
$("#btn-resolve-item")?.addEventListener("click", () =>
  resolveSelectedOpenItem()
);
$("#btn-reopen-item")?.addEventListener("click", () =>
  reopenSelectedOpenItem()
);
$("#btn-add-comment")?.addEventListener("click", () =>
  addCommentToSelectedOpenItem()
);
$("#btn-close-open-item")?.addEventListener("click", () => selectOpenItem(null));

async function initAuth() {
  try {
    const me = await api("/api/auth/me");
    if (me.authenticated && me.username) {
      $("#btn-signout")?.classList.remove("hidden");
    }
  } catch {
    /* redirect handled in api() */
  }
}

$("#btn-signout")?.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  window.location.href = "/login";
});

checkHealth();
loadAppVersion();
initAuth();
bindPdfFileInput();
bindFileInput("#video-file");
void loadInitialData();
