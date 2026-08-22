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
  optionsChatSessions: [],
  optionsChatSessionId: null,
  optionsChatMessages: [],
  optionsChatStarters: [],
  optionsChatSending: false,
  chatObservations: [],
  chatObservationsPendingCount: 0,
  chatSelectionContext: { excerpt: "", messageId: null },
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
  imagingFacets: null,
  imagingFacetsError: null,
  imagingFilters: {},
  imagingMatch: null,
  imagingWorkflowIds: [],
  lastVisionDocumentId: null,
};

const backgroundTasks = new Map();
let backgroundStatusTimer = null;

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
  const externalSignal = options.signal;
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  const timeoutMs = options.timeoutMs ?? 120000;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const { timeoutMs: _timeoutMs, signal: _signal, ...fetchOptions } = options;

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
      if (externalSignal?.aborted) {
        throw new Error("Cancelled");
      }
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

function formatElapsedDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatTaskStartedAt(date) {
  const value = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(value.getTime())) return "";
  return value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function upsertBackgroundTask(task) {
  const existing = backgroundTasks.get(task.id) || {};
  backgroundTasks.set(task.id, {
    ...existing,
    ...task,
    startedAt: task.startedAt || existing.startedAt || new Date(),
  });
  renderBackgroundStatusBar();
  ensureBackgroundStatusTimer();
}

function removeBackgroundTask(taskId) {
  backgroundTasks.delete(taskId);
  renderBackgroundStatusBar();
  if (backgroundTasks.size === 0 && backgroundStatusTimer) {
    clearInterval(backgroundStatusTimer);
    backgroundStatusTimer = null;
  }
}

function renderBackgroundStatusBar() {
  const bar = $("#background-status-bar");
  const list = $("#background-status-list");
  if (!bar || !list) return;

  if (backgroundTasks.size === 0) {
    bar.classList.add("hidden");
    list.innerHTML = "";
    return;
  }

  bar.classList.remove("hidden");
  const now = Date.now();
  list.innerHTML = [...backgroundTasks.values()]
    .map((task) => {
      const elapsed = formatElapsedDuration(now - task.startedAt.getTime());
      const started = formatTaskStartedAt(task.startedAt);
      const detail = task.detail ? `<span class="bg-status-detail muted">${escapeHtml(task.detail)}</span>` : "";
      const cancelBtn =
        task.cancelable === false
          ? ""
          : `<button type="button" class="btn ghost bg-status-cancel" data-cancel-task="${escapeHtml(task.id)}">Cancel</button>`;
      return `
        <div class="bg-status-item" data-task-id="${escapeHtml(task.id)}">
          <span class="bg-status-spinner" aria-hidden="true"></span>
          <div class="bg-status-body">
            <span class="bg-status-label">${escapeHtml(task.label)}</span>
            <span class="bg-status-meta muted">Started ${escapeHtml(started)} · ${escapeHtml(elapsed)}</span>
            ${detail}
          </div>
          ${cancelBtn}
        </div>`;
    })
    .join("");
}

function ensureBackgroundStatusTimer() {
  if (backgroundStatusTimer) return;
  backgroundStatusTimer = setInterval(() => renderBackgroundStatusBar(), 1000);
}

async function cancelBackgroundTask(taskId) {
  const task = backgroundTasks.get(taskId);
  if (!task?.onCancel) return;
  await task.onCancel();
  if (task.kind !== "analysis") {
    removeBackgroundTask(taskId);
  }
}

async function withBackgroundTask({ id, label, run }) {
  let cancelled = false;
  const taskId = id || `task-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

  upsertBackgroundTask({
    id: taskId,
    kind: "upload",
    label,
    startedAt: new Date(),
    detail: "",
    cancelable: true,
    onCancel: () => {
      cancelled = true;
    },
  });

  try {
    return await run({
      setDetail: (detail) => upsertBackgroundTask({ id: taskId, detail }),
      isCancelled: () => cancelled,
    });
  } finally {
    removeBackgroundTask(taskId);
  }
}

function analysisTaskLabel(jobType, query) {
  if (jobType === "baseline") return "Baseline assessment";
  if (jobType === "summarize") return "Summarizing documents";
  const q = (query || "").trim();
  if (jobType === "query") return q ? `Custom analysis: ${truncate(q, 72)}` : "Custom analysis";
  return "Analysis";
}

function analysisTaskDetail(job) {
  const docs = job.document_ids?.length;
  const scope = docs ? `${docs} document${docs === 1 ? "" : "s"}` : "all documents";
  return `${jobStatusLabel(job.status)} · ${scope}`;
}

function beginAnalysisBackgroundTask(jobId, jobType, { query = "", startedAt = null } = {}) {
  const taskId = `analysis-${jobId}`;
  upsertBackgroundTask({
    id: taskId,
    kind: "analysis",
    label: analysisTaskLabel(jobType, query),
    startedAt: startedAt ? new Date(startedAt) : new Date(),
    detail: "Starting…",
    cancelable: true,
    onCancel: async () => {
      await api(`/api/analyze/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    },
  });
  return taskId;
}

function updateAnalysisBackgroundTask(taskId, job) {
  if (!taskId || !backgroundTasks.has(taskId)) return;
  upsertBackgroundTask({ id: taskId, detail: analysisTaskDetail(job) });
}

function initBackgroundStatusBar() {
  $("#background-status-list")?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-cancel-task]");
    if (!btn) return;
    const taskId = btn.dataset.cancelTask;
    btn.disabled = true;
    cancelBackgroundTask(taskId)
      .then(() => {
        if (backgroundTasks.has(taskId)) return;
        toast("Cancelled");
      })
      .catch((err) => {
        toast(err.message, "error");
        btn.disabled = false;
      });
  });
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
      const ollama = llm.ollama || {};
      const via =
        active.provider === "ollama"
          ? `Ollama VM · text: ${active.model || ollama.configured_model || "model"} · vision: ${ollama.configured_vision_model || state.settings?.ollama_vision_model || "—"}`
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
  const textModel = settings?.ollama_model || "—";
  const visionModel = settings?.ollama_vision_model || "—";
  const ollama = llmHealth?.ollama || {};
  const models = (ollama.available_models || []).slice(0, 8);
  const modelList = models.length ? models.join(", ") : "none reported";
  const textOk = ollama.model_available ? "available" : "missing on VM";
  const visionOk = ollama.vision_model_available ? "available" : "missing on VM";
  el.classList.remove("hidden");
  el.innerHTML = `
    <p><strong>Provider:</strong> ${escapeHtml(provider)} · <strong>Ollama URL:</strong> ${escapeHtml(base)}</p>
    <p><strong>Text model</strong> (Home analysis &amp; custom tasks): ${escapeHtml(textModel)} · ${escapeHtml(textOk)}</p>
    <p><strong>Vision model</strong> (Imaging tab): ${escapeHtml(visionModel)} · ${escapeHtml(visionOk)}</p>
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
  if (meta.dicom_series_description || meta.series_description) {
    lines.push({
      label: "Series",
      value: meta.dicom_series_description || meta.series_description,
    });
  }
  if (meta.dicom_series_number) lines.push({ label: "Series #", value: meta.dicom_series_number });
  if (meta.dicom_instance_number) lines.push({ label: "Instance #", value: meta.dicom_instance_number });
  if (meta.dicom_slice_location) lines.push({ label: "Slice location (mm)", value: meta.dicom_slice_location });
  if (meta.dicom_convolution_kernel) {
    lines.push({ label: "Kernel", value: meta.dicom_convolution_kernel });
  }
  if (meta.dicom_window_center && meta.dicom_window_width) {
    lines.push({
      label: "Window",
      value: `${meta.dicom_window_center} / ${meta.dicom_window_width}`,
    });
  }
  if (meta.dicom_protocol_name) lines.push({ label: "Protocol", value: meta.dicom_protocol_name });
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
  ["#btn-baseline", "#btn-summarize", "#btn-analyze", "#btn-library-baseline"].forEach((sel) => {
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

async function pollAnalysisJob(jobId, { isCustomQuery = false, taskId = null } = {}) {
  const deadline = Date.now() + 30 * 60 * 1000;
  while (Date.now() < deadline) {
    const data = await api(`/api/analyze/jobs/${jobId}`);
    const job = data.job;
    if (taskId) updateAnalysisBackgroundTask(taskId, job);
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
    if (job.status === "cancelled") {
      throw new Error("Analysis cancelled");
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
  loadChatObservations().catch(() => {});
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
  renderAnalysisRunChrome();
  updateHomeToolbar();
}

function renderAnalysisScopeSummary() {
  const el = $("#analysis-scope-summary");
  if (!el) return;
  const total = state.documentIndex.length;
  const nextIds = plannedAssessmentIds();
  const next = scopeSummaryFromIds(nextIds);
  const usingAll = state.selectedIds.size === 0;
  if (!total) {
    el.textContent = "Add documents in Library first.";
    return;
  }
  const breakdown = formatScopeBreakdown(next.byType);
  if (usingAll) {
    el.textContent = breakdown
      ? `All ${total} library documents · ${breakdown}`
      : `All ${total} library documents`;
  } else {
    el.textContent = breakdown ? `${next.count} selected · ${breakdown}` : `${next.count} selected`;
  }
}

function renderAnalysisRunChrome() {
  const hasAssessment = Boolean(state.latestAnalysis);
  const total = state.documentIndex.length;
  const title = hasAssessment ? "Update analysis" : "Run analysis";
  const hint = hasAssessment
    ? "Change sources or guidance, then re-run to refresh your assessment"
    : "Choose sources, then synthesize your library into an assessment";
  const lead = hasAssessment
    ? "Re-run when you've added reports or changed which documents are in scope."
    : "BeatIt reads your selected documents and produces an executive summary, full assessment, and open items to resolve.";
  const btnLabel = hasAssessment ? "Update analysis" : "Run analysis";

  const titleEl = $("#analysis-panel-title");
  const hintEl = $("#analysis-panel-hint");
  const leadEl = $("#analysis-run-lead");
  if (titleEl) titleEl.textContent = title;
  if (hintEl) hintEl.textContent = hint;
  if (leadEl) leadEl.textContent = lead;

  ["#btn-baseline", "#btn-library-baseline"].forEach((sel) => {
    const btn = $(sel);
    if (!btn) return;
    btn.textContent = btnLabel;
    btn.disabled = state.analysisRunning || !total;
  });

  renderAnalysisScopeSummary();
  renderChatObservationsQueueNote();
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
  let jobId = null;
  try {
    const data = await api("/api/analyze/jobs/active");
    if (!data.job) return;

    jobId = data.job.id;
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
    if (job.status === "cancelled") {
      return;
    }

    const taskId = beginAnalysisBackgroundTask(jobId, job.job_type, {
      query: job.query,
      startedAt: job.started_at || job.created_at,
    });
    setAnalysisRunning(true, jobId, job.job_type);
    if (isCustomQuery) {
      switchTab("custom-tasks");
      updateCustomTasksRunningBanner(true, job.query);
    }
    const analysis = await pollAnalysisJob(jobId, { isCustomQuery, taskId });
    if (isCustomQuery) finishCustomTaskRun(analysis, job.query);
    else finishAnalysisRun(analysis);
  } catch (err) {
    if (err.message === "Analysis cancelled") toast("Analysis cancelled");
    else if (err.message !== "Analysis cancelled") toast(err.message, "error");
  } finally {
    if (jobId) removeBackgroundTask(`analysis-${jobId}`);
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
  if (!VALID_TABS.has(name)) return;
  $$(".tab").forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
  if (!options.skipTabSave) {
    persistActiveTab(name);
  }
  if (name === "library" && !options.skipLibraryLoad) {
    loadDocuments().catch((e) => toast(e.message, "error"));
  }
  if (name === "imaging") loadImagingPanel();
  if (name === "history") loadHistory();
  if (name === "analyze") {
    loadLatestAssessment();
    loadChatObservations().catch(() => {});
  }
  if (name === "options-chat") loadOptionsChatPanel();
  if (name === "custom-tasks") loadCustomTasks();
  if (name === "settings") loadSettings();
  updateHomeToolbar();
}

function applyTabUi(name) {
  if (!VALID_TABS.has(name)) return;
  $$(".tab").forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
}

function readSavedTabName() {
  const hash = window.location.hash.replace(/^#/, "").trim();
  if (hash && VALID_TABS.has(hash)) return hash;
  try {
    const saved = sessionStorage.getItem(TAB_STORAGE_KEY);
    if (saved && VALID_TABS.has(saved)) return saved;
  } catch {
    /* ignore */
  }
  return null;
}

function persistActiveTab(name) {
  if (!VALID_TABS.has(name)) return;
  try {
    sessionStorage.setItem(TAB_STORAGE_KEY, name);
  } catch {
    /* ignore */
  }
  const hash = `#${name}`;
  if (window.location.hash !== hash) {
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
  }
}

function restoreActiveTabUiOnly() {
  const saved = readSavedTabName();
  if (!saved) return;
  applyTabUi(saved);
  updateHomeToolbar();
}

function restoreActiveTab() {
  const saved = readSavedTabName();
  if (!saved) return;
  switchTab(saved, { skipTabSave: true });
}

function initTabPersistence() {
  restoreActiveTabUiOnly();
  window.addEventListener("hashchange", () => {
    const tab = readSavedTabName();
    if (!tab) return;
    const panel = $(`#panel-${tab}`);
    if (panel && !panel.classList.contains("active")) {
      switchTab(tab, { skipTabSave: true });
    }
  });
}

function jobStatusLabel(status) {
  if (status === "pending") return "Queued";
  if (status === "running") return "Running";
  if (status === "completed") return "Complete";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
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
    const appendix = analysis.references || [];
    if (appendix.length) {
      answerEl.innerHTML += renderInlineReferenceAppendix(appendix, refPrefix);
    }
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
  let jobId = null;

  try {
    const data = await api(`/api/analyses/${id}/refine`, {
      method: "POST",
      body: JSON.stringify({
        query,
        refinement,
        document_ids: docIds,
      }),
    });
    jobId = data.job.id;
    state.refiningCustomTaskId = id;
    const taskId = beginAnalysisBackgroundTask(jobId, "query", { query });
    setAnalysisRunning(true, jobId, "query");
    updateCustomTasksRunningBanner(true, query, { refining: true });
    updateCustomTaskRefineControls(draft);
    toast("Refinement started");

    const analysis = await pollAnalysisJob(jobId, { isCustomQuery: true, taskId });
    finishCustomTaskRun(analysis, query);
    if ($("#custom-task-refine-notes")) $("#custom-task-refine-notes").value = "";
  } catch (err) {
    if (err.message === "Analysis cancelled") toast("Analysis cancelled");
    else if (err.status === 409) {
      toast("An analysis is already running — resuming progress.", "error");
      await resumeActiveAnalysisJob();
      return;
    } else {
      toast(err.message, "error");
    }
  } finally {
    if (jobId) removeBackgroundTask(`analysis-${jobId}`);
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
  const reviewerEl = $("#settings-reviewer-context");
  if (reviewerEl) {
    reviewerEl.value =
      state.settings.reviewer_context || data.default_reviewer_context || "";
  }

  const current = $("#settings-current");
  if (current) {
    const modelId = state.settings.openrouter_model || data.default_model;
    const provider = state.settings.llm_provider || "openrouter";
    if (provider === "auto") {
      current.textContent = `OpenRouter fallback model: ${modelId}`;
    } else {
      current.textContent = `Selected model: ${modelId}`;
    }
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

async function saveReviewerContext() {
  const el = $("#settings-reviewer-context");
  if (!el) return;
  const reviewer_context = el.value.trim();
  if (!reviewer_context) return toast("Clinical reviewer context cannot be empty", "error");
  const data = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ reviewer_context }),
  });
  state.settings = { ...state.settings, ...data.settings };
  toast("Clinical reviewer context saved");
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
const TAB_STORAGE_KEY = "beatit-active-tab";
const VALID_TABS = new Set([
  "analyze",
  "options-chat",
  "custom-tasks",
  "ingest",
  "library",
  "imaging",
  "history",
  "howto",
  "settings",
]);
const IMAGING_VISION_SLICE_LIMIT = 3;

const IMAGING_FILTER_SPECS = [
  { key: "series_key", label: "Series" },
  { key: "series_kind", label: "Series type" },
  { key: "convolution_kernel", label: "Reconstruction kernel" },
  { key: "anatomy_level", label: "Anatomical level" },
  { key: "study_description", label: "Study" },
  { key: "modality", label: "Modality" },
];

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
  const customScope = $("#custom-task-doc-scope");
  const label = selectionScopeLabel();
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

function documentDisplayTitle(doc) {
  if (!doc) return "Unknown document";
  return doc.source_info?.display_name || doc.citation_display_name || doc.title || "Untitled";
}

function getCitedDocumentIds() {
  const cited = new Set();
  const refs = state.latestAnalysis?.references || [];
  refs.forEach((ref) => {
    if (ref.document_id) cited.add(ref.document_id);
  });
  return cited;
}

function getAssessmentInclusion(docId) {
  const lastIds = new Set(state.latestAnalysis?.document_ids || []);
  const citedIds = getCitedDocumentIds();
  const nextIds = new Set(plannedAssessmentIds());
  const explicitSelection = state.selectedIds.size > 0;
  return {
    inLastAssessment: lastIds.has(docId),
    citedInAssessment: citedIds.has(docId),
    inNextScope: nextIds.has(docId),
    explicitSelection,
  };
}

function isNewForNextAssessment(docId) {
  const lastIds = state.latestAnalysis?.document_ids || [];
  if (!lastIds.length) return false;
  return plannedAssessmentIds().includes(docId) && !lastIds.includes(docId);
}

function partitionNextScopeIds(nextIds, lastIds = []) {
  const lastSet = new Set(lastIds);
  const newIds = [];
  const carriedIds = [];
  orderedDocsFromIds(nextIds).forEach((doc) => {
    if (lastSet.has(doc.id)) carriedIds.push(doc.id);
    else newIds.push(doc.id);
  });
  return { newIds, carriedIds };
}

function renderDocInclusionBadges(docId) {
  const inc = getAssessmentInclusion(docId);
  const badges = [];
  const isNew = isNewForNextAssessment(docId);

  if (isNew) {
    badges.push(
      '<span class="doc-status-badge doc-status-new" title="Not in the current assessment — will be included on the next analysis run">New for next run</span>'
    );
  }
  if (inc.inLastAssessment) {
    badges.push(
      '<span class="doc-status-badge doc-status-in-assessment" title="Included in the current executive summary">In assessment</span>'
    );
  }
  if (inc.citedInAssessment) {
    badges.push(
      '<span class="doc-status-badge doc-status-cited" title="Cited inline in the assessment text">Cited</span>'
    );
  }
  if (inc.explicitSelection && inc.inNextScope && !inc.inLastAssessment && !isNew) {
    badges.push(
      '<span class="doc-status-badge doc-status-selected" title="Selected for the next analysis run">Selected for next run</span>'
    );
  } else if (inc.explicitSelection && !inc.inNextScope) {
    badges.push(
      '<span class="doc-status-badge doc-status-excluded" title="Excluded from the next analysis run">Excluded</span>'
    );
  }
  return badges.join("");
}

function orderedDocsFromIds(ids) {
  const byId = new Map(state.documentIndex.map((doc) => [doc.id, doc]));
  return ids
    .map((id) => byId.get(id) || findDocumentById(id))
    .filter(Boolean);
}

function collectVisionAnalysisSliceIds() {
  const ids = new Set();
  (state.documentIndex || []).forEach((doc) => {
    const sliceIds = doc.metadata?.vision_source_slice_ids;
    if (Array.isArray(sliceIds)) {
      sliceIds.forEach((id) => ids.add(id));
    }
  });
  return ids;
}

function imagingFolderName(doc) {
  const meta = doc.metadata || {};
  const rel = String(meta.relative_path || meta.original_filename || doc.title || "")
    .replace(/\\/g, "/")
    .trim();
  if (rel.includes("/")) {
    return rel.split("/").filter(Boolean)[0] || "Imaging folder";
  }
  if (meta.dicom_study_description) return meta.dicom_study_description;
  if (meta.dicom_study_instance_uid) {
    return `Study …${String(meta.dicom_study_instance_uid).slice(-8)}`;
  }
  return "Imaging upload";
}

function imagingUsedInAnalysis(docId, { citedIds, visionSliceIds } = {}) {
  if (visionSliceIds?.has(docId)) return true;
  if (citedIds?.has(docId)) return true;
  return false;
}

function buildScopeDisplayEntries(docIds, { citedIds = null } = {}) {
  const cited = citedIds ?? getCitedDocumentIds();
  const visionSliceIds = collectVisionAnalysisSliceIds();
  const context = { citedIds: cited, visionSliceIds };
  const ordered = orderedDocsFromIds(docIds);
  const folderBuckets = new Map();

  ordered.forEach((doc) => {
    if (doc.source_type !== "imaging") return;
    if (imagingUsedInAnalysis(doc.id, context)) return;
    const key = imagingFolderName(doc);
    if (!folderBuckets.has(key)) {
      folderBuckets.set(key, { folderName: key, docs: [] });
    }
    folderBuckets.get(key).docs.push(doc);
  });

  const emittedFolders = new Set();
  const entries = [];
  ordered.forEach((doc) => {
    if (doc.source_type === "imaging" && !imagingUsedInAnalysis(doc.id, context)) {
      const key = imagingFolderName(doc);
      if (emittedFolders.has(key)) return;
      emittedFolders.add(key);
      const bucket = folderBuckets.get(key);
      entries.push({
        kind: "imaging-folder",
        folderName: bucket.folderName,
        count: bucket.docs.length,
        docIds: bucket.docs.map((item) => item.id),
      });
      return;
    }
    entries.push({ kind: "doc", doc });
  });
  return entries;
}

function renderScopeFolderInclusionBadges(docIds) {
  const seen = new Set();
  const parts = [];
  docIds.forEach((docId) => {
    const html = renderDocInclusionBadges(docId);
    if (html && !seen.has(html)) {
      seen.add(html);
      parts.push(html);
    }
  });
  return parts.join("");
}

function renderScopeDisplayEntry(entry, { showInclusionBadges = false } = {}) {
  if (entry.kind === "imaging-folder") {
    const type = escapeHtml(libraryTypeLabel("imaging"));
    const title = escapeHtml(truncate(entry.folderName, 72));
    const countLabel = `${entry.count} slice${entry.count === 1 ? "" : "s"}`;
    const badges =
      showInclusionBadges && entry.docIds?.length
        ? renderScopeFolderInclusionBadges(entry.docIds)
        : "";
    return `<li class="scope-doc-item scope-doc-folder">
      <span class="scope-doc-item-main"><span class="badge badge-sm">${type}</span> <strong>${title}</strong> <span class="muted small">(${countLabel})</span></span>
      ${badges ? `<span class="scope-doc-item-badges">${badges}</span>` : ""}
    </li>`;
  }

  const doc = entry.doc;
  const title = escapeHtml(truncate(documentDisplayTitle(doc), 72));
  const type = escapeHtml(libraryTypeLabel(doc.source_type));
  const badges = showInclusionBadges ? renderDocInclusionBadges(doc.id) : "";
  const highlightClass = showInclusionBadges && isNewForNextAssessment(doc.id) ? " scope-doc-item-new" : "";
  return `<li class="scope-doc-item${highlightClass}">
    <span class="scope-doc-item-main"><span class="badge badge-sm">${type}</span> <strong>${title}</strong></span>
    ${badges ? `<span class="scope-doc-item-badges">${badges}</span>` : ""}
  </li>`;
}

function renderScopeDocumentListItems(docIds, { showInclusionBadges = false, limit = null } = {}) {
  const entries = buildScopeDisplayEntries(docIds);
  const shown = limit ? entries.slice(0, limit) : entries;
  const more = limit && entries.length > limit ? entries.length - limit : 0;
  const items = shown.map((entry) => renderScopeDisplayEntry(entry, { showInclusionBadges })).join("");
  return `${items}${more ? `<li class="muted small scope-doc-more">…and ${more} more</li>` : ""}`;
}

function renderNextAssessmentScopeLists(nextIds, lastIds = []) {
  if (!nextIds.length) return "";

  const hasPrior = lastIds.length > 0 && state.latestAnalysis;
  if (!hasPrior) {
    return `<ul class="scope-doc-list assessment-scope-doc-list">${renderScopeDocumentListItems(nextIds, {
      showInclusionBadges: true,
    })}</ul>`;
  }

  const { newIds, carriedIds } = partitionNextScopeIds(nextIds, lastIds);
  const parts = [];

  if (newIds.length) {
    const breakdown = formatScopeBreakdown(scopeSummaryFromIds(newIds).byType);
    parts.push(`
      <div class="assessment-pending-scope assessment-pending-scope-prominent">
        <p class="assessment-pending-heading">New for next run (${newIds.length})</p>
        <p class="muted small assessment-pending-note">Not in the current assessment — these will be sent when you update analysis.</p>
        ${breakdown ? `<p class="muted small">${escapeHtml(breakdown)}</p>` : ""}
        <ul class="scope-doc-list assessment-scope-doc-list">${renderScopeDocumentListItems(newIds, {
          showInclusionBadges: true,
        })}</ul>
      </div>`);
  } else if (state.selectedIds.size) {
    parts.push(
      `<p class="muted small assessment-pending-note">No new documents — next run uses the same selection as the current assessment.</p>`
    );
  }

  if (carriedIds.length) {
    parts.push(`
      <details class="assessment-carried-scope"${newIds.length ? "" : " open"}>
        <summary class="assessment-carried-summary">Already in current assessment (${carriedIds.length})</summary>
        <ul class="scope-doc-list assessment-scope-doc-list scope-doc-list-muted">${renderScopeDocumentListItems(
          carriedIds,
          { showInclusionBadges: true }
        )}</ul>
      </details>`);
  }

  return parts.join("");
}

function renderSidebarScopeSection() {
  const analysis = state.latestAnalysis;
  if (!analysis?.document_ids?.length) return "";

  const lastIds = analysis.document_ids;
  const nextIds = plannedAssessmentIds();
  const { newIds } = partitionNextScopeIds(nextIds, lastIds);
  const pendingHtml = newIds.length
    ? `<div class="sidebar-pending-scope sidebar-pending-scope-prominent">
        <p class="sidebar-subheading">New for next run (${newIds.length})</p>
        <p class="sidebar-panel-note muted small">Not in this assessment yet — will be sent on the next analysis run.</p>
        <ul class="scope-doc-list scope-doc-list-compact">${renderScopeDocumentListItems(newIds, {
          showInclusionBadges: true,
        })}</ul>
      </div>`
    : "";

  const currentHtml = `<div class="sidebar-current-scope">
        <p class="sidebar-subheading">In this assessment (${lastIds.length})</p>
        <ul class="scope-doc-list scope-doc-list-compact">${renderScopeDocumentListItems(lastIds, {
          showInclusionBadges: true,
        })}</ul>
      </div>`;

  return `
    <section class="sidebar-panel sidebar-scope-panel">
      <div class="sidebar-panel-header row-between wrap">
        <h4>Assessment scope</h4>
        <button type="button" class="btn ghost btn-sm" id="btn-view-assessment-scope">Adjust</button>
      </div>
      <p class="sidebar-panel-note muted small">Imaging folders are summarized; individual slices appear when used in vision analysis or cited in text.</p>
      ${pendingHtml}
      ${currentHtml}
    </section>`;
}

function renderSidebarCitationsSection(appendix, idPrefix) {
  const enriched = sortSourcesForSidebar(enrichReferenceList(appendix));
  if (!enriched.length) {
    return `
      <section class="sidebar-panel sidebar-citations-panel">
        <h4>Cited in text (0)</h4>
        <p class="sidebar-panel-note muted small">No inline citations in the assessment text yet. Documents above may still have been included in scope.</p>
      </section>`;
  }

  const count = enriched.length;
  const hasMore = count > SOURCES_SIDEBAR_PREVIEW;
  const cards = enriched
    .map((ref, index) =>
      renderSourceSidebarCard(ref, idPrefix, {
        collapsed: hasMore && index >= SOURCES_SIDEBAR_PREVIEW,
      })
    )
    .join("");

  return `
    <section class="sidebar-panel sidebar-citations-panel">
      <h4>Cited in text (${count})</h4>
      <p class="sidebar-panel-note muted small">Inline tags from the assessment — often fewer than documents in scope.</p>
      <div class="sources-sidebar-list">${cards}</div>
      ${
        hasMore
          ? `<button type="button" class="btn ghost sources-show-all" data-action="expand-sources">Show all citations</button>`
          : ""
      }
    </section>`;
}

function renderHomeResultsSidebar(analysis) {
  const wrap = $("#home-sources-sidebar");
  const inner = $("#home-sources-sidebar-inner");
  if (!wrap || !inner) return;

  if (!analysis) {
    wrap.classList.add("hidden");
    inner.innerHTML = "";
    return;
  }

  const scopeHtml = renderSidebarScopeSection();
  const citationsHtml = renderSidebarCitationsSection(analysis.references || [], analysis.id);
  if (!scopeHtml && !citationsHtml) {
    wrap.classList.add("hidden");
    inner.innerHTML = "";
    return;
  }

  wrap.classList.remove("hidden");
  wrap.classList.remove("is-expanded");
  inner.innerHTML = `${scopeHtml}${citationsHtml}`;
}

function substantiveSummaryLength(text) {
  if (!text) return 0;
  return String(text)
    .replace(/\[SOURCE:\s*[^\]]+\]/gi, "")
    .replace(/SOURCE:\s*Document\s+"[^"]+"/gi, "")
    .replace(/\[(\d+)\]/g, "")
    .replace(/\s+/g, " ")
    .trim().length;
}

function effectiveExecutiveSummaryDisplay(analysis) {
  const summary = analysis.executive_summary_display || analysis.executive_summary || "";
  const response = analysis.response_display || analysis.response || "";
  if (substantiveSummaryLength(summary) >= 80) return { text: summary, usedFallback: false };
  if (substantiveSummaryLength(response) > substantiveSummaryLength(summary)) {
    return { text: response, usedFallback: true };
  }
  return { text: summary, usedFallback: false };
}

function renderExecutiveSummaryNotice(analysis, usedFallback) {
  const el = $("#executive-summary-notice");
  if (!el) return;
  if (!analysis) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const summary = analysis.executive_summary_display || analysis.executive_summary || "";
  if (usedFallback) {
    el.classList.remove("hidden");
    el.className = "executive-summary-notice warn";
    el.innerHTML =
      '<span class="notice-title">Summary was too short.</span> Showing content from the full assessment below. Update analysis to regenerate a proper executive summary.';
    return;
  }
  if (substantiveSummaryLength(summary) < 80 && substantiveSummaryLength(analysis.response || "") >= 80) {
    el.classList.remove("hidden");
    el.className = "executive-summary-notice warn";
    el.innerHTML =
      '<span class="notice-title">Summary looks incomplete.</span> Expand <strong>Full assessment</strong> below or update analysis for a fuller summary.';
    return;
  }
  el.classList.add("hidden");
  el.innerHTML = "";
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
  const hasAssessment = Boolean(state.latestAnalysis);
  const usingAll = state.selectedIds.size === 0;

  const nextEl = $("#assessment-scope-next");
  const lastEl = $("#assessment-scope-last");
  const warnEl = $("#assessment-scope-warning");
  const matchBtn = $("#btn-scope-match-last");

  if (nextEl) {
    const { newIds } = hasAssessment ? partitionNextScopeIds(nextIds, lastIds) : { newIds: [] };
    const newSummary =
      hasAssessment && newIds.length
        ? `<p class="assessment-scope-new-summary"><strong>${newIds.length} new</strong> for next run · ${Math.max(
            0,
            next.count - newIds.length
          )} already in current assessment</p>`
        : "";
    const nextList = next.count ? renderNextAssessmentScopeLists(nextIds, lastIds) : "";
    const parts = [];
    if (newSummary) parts.push(newSummary);
    if (nextList) parts.push(nextList);
    if (!usingAll && next.count < total) {
      parts.push(`<p class="muted small">Unselected documents will not be sent to the LLM.</p>`);
    }
    nextEl.innerHTML =
      parts.join("") ||
      (total ? `<p class="muted small">All library documents will be included.</p>` : "");
  }

  if (lastEl) {
    lastEl.classList.add("hidden");
    lastEl.innerHTML = "";
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
    const imagingCount = state.libraryCounts?.imaging || next.byType?.imaging || 0;
    const imagingNote = imagingCount
      ? ` (${imagingCount} imaging slices grouped by upload folder in the list below)`
      : "";
    warnings.push(
      `All ${total} library items will be sent${imagingNote}. Large imaging-only files may add little text — prefer selecting PDFs and clinical reports when possible.`
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

  renderAnalysisRunChrome();
  renderHomeResultsSidebar(state.latestAnalysis);
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
  setAnalyzeActionsExpanded(true);
  const card = $("#assessment-scope-card");
  requestAnimationFrame(() => scrollToElement(card));
}

function goToLibraryForScope() {
  switchTab("library");
  window.scrollTo({ top: 0, behavior: "smooth" });
  toast("Select documents with checkboxes, then run analysis from here or Home");
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
  let msg = hasAssessment ? `Update analysis using ${mode}?` : `Run analysis using ${mode}?`;
  if (breakdown) msg += `\n\nIncludes: ${breakdown}`;
  if (guidance) msg += `\n\nGuidance:\n${truncate(guidance, 500)}`;
  if (hasAssessment) msg += "\n\nIntegrates new sources with your current assessment.";
  if (!confirm(msg)) return;
  await runAnalysis({ baseline: true, assessmentGuidance: guidance });
}

function reassessFromOpenItem() {
  scrollToAssessmentScope();
  const guidanceDetails = $("#analysis-guidance-details");
  if (guidanceDetails) guidanceDetails.setAttribute("open", "");
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

function selectAllShownDocuments() {
  const filterType = state.libraryFilter || "";
  if (filterType) {
    selectDocumentsByType(filterType);
    return;
  }
  selectAllDocuments();
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
  const selectShownBtn = $("#btn-select-all-shown");
  const matchLastBtn = $("#btn-scope-match-last-lib");
  const counts = state.libraryCounts || {};
  const selected = state.selectedIds.size;
  const total = state.documentIndex.length || Object.values(counts).reduce((a, b) => a + b, 0);

  if (summary) {
    let line = "";
    if (!total) {
      line = "No documents stored yet.";
    } else if (selected === 0) {
      line = `No selection — assessments use all ${total} document${total === 1 ? "" : "s"}.`;
    } else if (selected === total) {
      line = `All ${selected} documents selected for assessment.`;
    } else {
      line = `${selected} of ${total} selected for assessment.`;
    }
    if (total && state.latestAnalysis) {
      line += " Badges show in-assessment, cited, and next-run status.";
    } else if (total && selected > 0) {
      line += " Selected items show a pending badge until you update analysis.";
    }
    summary.textContent = line;
  }

  if (selectShownBtn) {
    const filterType = state.libraryFilter || "";
    const shownCount = filterType ? counts[filterType] || 0 : total;
    if (filterType) {
      selectShownBtn.textContent = `Select all ${libraryTypeLabel(filterType)} (${shownCount})`;
    } else {
      selectShownBtn.textContent = `Select all (${shownCount})`;
    }
  }

  if (matchLastBtn) {
    const lastIds = state.latestAnalysis?.document_ids || [];
    matchLastBtn.classList.toggle("hidden", !lastIds.length);
  }

  const libraryBaselineBtn = $("#btn-library-baseline");
  if (libraryBaselineBtn) {
    const totalDocs = state.documentIndex.length || Object.values(counts).reduce((a, b) => a + b, 0);
    libraryBaselineBtn.disabled = state.analysisRunning || !totalDocs;
  }
  renderAnalysisRunChrome();
}

function renderDocuments() {
  if (state.libraryFilter === "imaging") {
    renderImagingLibraryGroups();
    return;
  }

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
    .map((doc) => renderLibraryDocItem(doc))
    .join("");

  renderLibraryPagination();
  syncImagingGroupCheckboxes();
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

function updateImagingFilterVisibility() {
  const libraryHint = $("#imaging-filter-card");
  const count = state.libraryCounts?.imaging || 0;
  if (libraryHint) {
    libraryHint.classList.toggle("hidden", count === 0);
  }
}

function isVisionReportDocument(doc) {
  if (!doc) return false;
  if (doc.metadata?.vision_read) return true;
  return String(doc.title || "").startsWith("Vision read —");
}

function listVisionReportDocuments() {
  return state.documentIndex
    .filter((doc) => isVisionReportDocument(doc))
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
}

function isExplicitAssessmentSelection() {
  return state.selectedIds.size > 0;
}

function isDocumentInNextAssessmentScope(docId) {
  if (!isExplicitAssessmentSelection()) return true;
  return state.selectedIds.has(docId);
}

function setVisionReportPendingInclusion(docId) {
  const known = existingDocumentIdSet();
  const lastIds = (state.latestAnalysis?.document_ids || []).filter((id) => known.has(id));
  if (lastIds.length) {
    state.selectedIds = new Set(lastIds.filter((id) => id !== docId));
  } else if (isExplicitAssessmentSelection()) {
    state.selectedIds.delete(docId);
  } else {
    state.selectedIds = new Set(
      state.documentIndex.map((doc) => doc.id).filter((id) => id !== docId)
    );
  }
  saveSelectionToSession();
}

function includeDocumentInOverallAssessment(docId) {
  if (!existingDocumentIdSet().has(docId)) {
    return toast("Document not found", "error");
  }
  if (!isExplicitAssessmentSelection()) {
    applyScopeFromLastAssessmentPlus([docId]);
  } else {
    state.selectedIds.add(docId);
    saveSelectionToSession();
  }
  updateSelectedLabel();
  renderVisionReportsPanel();
  renderAssessmentScopeCard();
  renderHomeResultsSidebar(state.latestAnalysis);
  if (state.documents.length) renderDocuments();
  toast("Included in overall assessment scope");
}

function excludeDocumentFromOverallAssessment(docId) {
  setVisionReportPendingInclusion(docId);
  updateSelectedLabel();
  renderVisionReportsPanel();
  renderAssessmentScopeCard();
  renderHomeResultsSidebar(state.latestAnalysis);
  if (state.documents.length) renderDocuments();
  toast("Excluded from overall assessment scope");
}

function renderVisionReportsPanel() {
  const list = $("#imaging-vision-reports-list");
  if (!list) return;
  const reports = listVisionReportDocuments();
  if (!reports.length) {
    list.innerHTML = "<p class=\"muted\">No imaging analysis reports yet. Run step 2 above.</p>";
    return;
  }

  list.innerHTML = reports
    .map((doc) => {
      const meta = doc.metadata || {};
      const sliceCount = meta.vision_source_slice_ids?.length || meta.slice_count || "?";
      const included = isDocumentInNextAssessmentScope(doc.id);
      const inLast = getAssessmentInclusion(doc.id).inLastAssessment;
      const statusClass = included ? "imaging-report-included" : "imaging-report-pending";
      const statusLabel = included
        ? inLast
          ? "In current assessment"
          : "Included for next run"
        : "Not in overall assessment";
      return `
        <article class="imaging-report-item ${statusClass}" data-id="${doc.id}">
          <div class="imaging-report-heading row-between wrap">
            <strong>${escapeHtml(doc.title)}</strong>
            <span class="doc-status-badge ${included ? "doc-status-selected" : "doc-status-excluded"}">${escapeHtml(statusLabel)}</span>
          </div>
          <p class="muted small">${sliceCount} slice${sliceCount === 1 ? "" : "s"} · ${formatDate(doc.created_at)}${meta.vision_model ? ` · ${escapeHtml(meta.vision_model)}` : ""}</p>
          <div class="imaging-report-actions">
            <button type="button" class="btn ghost btn-sm btn-view" data-id="${doc.id}">View report</button>
            ${
              included
                ? `<button type="button" class="btn ghost btn-sm btn-imaging-exclude" data-id="${doc.id}">Exclude from overall assessment</button>`
                : `<button type="button" class="btn secondary btn-sm btn-imaging-include" data-id="${doc.id}">Include in overall assessment</button>`
            }
          </div>
        </article>`;
    })
    .join("");
}

function renderImagingSlicePicker() {
  const el = $("#imaging-slice-picker");
  if (!el) return;
  const match = state.imagingMatch;
  const preview = match?.preview || [];
  const selected = new Set(imagingWorkflowIds());

  if (!preview.length) {
    el.innerHTML = '<p class="muted">Set filters to choose slices.</p>';
    return;
  }

  const total = match?.total || preview.length;
  const header =
    total > preview.length
      ? `<p class="imaging-slice-picker-note">Showing ${preview.length} of ${total} matches — use Sample 3 evenly for a spread across the full set.</p>`
      : "";

  el.innerHTML = `${header}<ul class="imaging-slice-list">${preview
    .map((row) => {
      const id = row.id;
      const checked = selected.has(id) ? " checked" : "";
      const disabled =
        !checked && selected.size >= IMAGING_VISION_SLICE_LIMIT ? " disabled" : "";
      const label = escapeHtml(row.title || row.id);
      const meta = [row.anatomy_level, row.series_kind, row.convolution_kernel]
        .filter(Boolean)
        .map((part) => escapeHtml(part))
        .join(" · ");
      const nonDiagnostic =
        row.series_kind && NON_DIAGNOSTIC_SERIES_KINDS.has(row.series_kind)
          ? ' <span class="imaging-slice-warn">(poor for AI read)</span>'
          : "";
      return `<li><label class="imaging-slice-option"><input type="checkbox" class="imaging-slice-check" data-id="${escapeHtml(id)}"${checked}${disabled}><span><strong>${label}</strong>${meta ? `<span class="muted"> · ${meta}</span>` : ""}${nonDiagnostic}</span></label></li>`;
    })
    .join("")}</ul>`;
}

async function loadImagingPanel() {
  await loadDocumentIndex();
  await loadImagingFacets();
  if (Object.keys(state.imagingFilters || {}).length) {
    await refreshImagingMatch();
  }
  renderImagingSlicePicker();
  renderImagingWorkflowSummary();
  renderVisionReportsPanel();
}

function imagingWorkflowIds() {
  return state.imagingWorkflowIds || [];
}

function setImagingWorkflowIds(ids) {
  state.imagingWorkflowIds = [...new Set(ids.filter(Boolean))];
  renderImagingWorkflowSummary();
}

const NON_DIAGNOSTIC_SERIES_KINDS = new Set([
  "Scout",
  "Axial MIP",
  "Coronal MIP",
  "MIP",
  "Dose report",
  "Administrative",
]);

function diagnosticSliceIds(ids) {
  if (!ids.length) return ids;
  const preview = state.imagingMatch?.preview || [];
  const kindById = new Map(preview.map((row) => [row.id, row.series_kind]));
  const filtered = ids.filter((id) => {
    const kind = kindById.get(id);
    return !kind || !NON_DIAGNOSTIC_SERIES_KINDS.has(kind);
  });
  return filtered.length ? filtered : ids;
}

function sampleIdsEvenly(ids, count) {
  if (!ids.length) return [];
  const pool = diagnosticSliceIds(ids);
  const limit = Math.max(1, Math.min(count, pool.length, IMAGING_VISION_SLICE_LIMIT));
  if (pool.length <= limit) return [...pool];
  if (limit === 1) return [pool[Math.floor(pool.length / 2)]];
  const step = (pool.length - 1) / (limit - 1);
  const picked = new Set();
  for (let i = 0; i < limit; i += 1) {
    picked.add(Math.round(i * step));
  }
  return [...picked].sort((a, b) => a - b).map((index) => pool[index]);
}

function renderImagingWorkflowSummary() {
  const el = $("#imaging-workflow-summary");
  if (!el) return;
  const ids = imagingWorkflowIds();
  if (!ids.length) {
    el.textContent = "Select slices above, or click Sample 3 evenly after filtering.";
    return;
  }
  el.innerHTML = `<strong>${ids.length}</strong> slice${ids.length === 1 ? "" : "s"} ready for imaging analysis (max ${IMAGING_VISION_SLICE_LIMIT}).`;
}

function sampleImagingSlicesEvenly() {
  const matched = imagingMatchIds();
  if (!matched.length) {
    return toast("Set filters and wait for matches first", "error");
  }
  setImagingWorkflowIds(sampleIdsEvenly(matched, IMAGING_VISION_SLICE_LIMIT));
  renderImagingSlicePicker();
  renderImagingWorkflowSummary();
}

function clearImagingSliceSelection() {
  setImagingWorkflowIds([]);
  renderImagingSlicePicker();
  renderImagingWorkflowSummary();
}

function toggleImagingSliceSelection(docId, checked) {
  const ids = new Set(imagingWorkflowIds());
  if (checked) {
    if (ids.size >= IMAGING_VISION_SLICE_LIMIT) {
      return toast(`Select at most ${IMAGING_VISION_SLICE_LIMIT} slices`, "error");
    }
    ids.add(docId);
  } else {
    ids.delete(docId);
  }
  setImagingWorkflowIds([...ids]);
  renderImagingSlicePicker();
  renderImagingWorkflowSummary();
}

function visionTaskDetail(job) {
  const progress = job.progress;
  if (progress?.slice_label) {
    const phase = progress.phase === "analyzing" ? "Analyzing" : "Preparing";
    return `${phase} slice ${progress.current}/${progress.total}: ${progress.slice_label}`;
  }
  if (job.status === "completed") return "Vision read complete";
  if (job.status === "failed") return job.error || "Vision analysis failed";
  return "Starting vision analysis…";
}

function beginVisionBackgroundTask(jobId) {
  const taskId = `vision-${jobId}`;
  upsertBackgroundTask({
    id: taskId,
    kind: "vision",
    label: "Imaging analysis",
    startedAt: new Date(),
    detail: "Starting…",
    cancelable: true,
    onCancel: async () => {
      await api(`/api/documents/imaging/vision-jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
      });
    },
  });
  return taskId;
}

async function pollVisionJob(jobId, { taskId = null } = {}) {
  const deadline = Date.now() + 45 * 60 * 1000;
  while (Date.now() < deadline) {
    const job = await api(`/api/documents/imaging/vision-jobs/${encodeURIComponent(jobId)}`);
    if (taskId) upsertBackgroundTask({ id: taskId, detail: visionTaskDetail(job) });
    if (job.status === "completed") return job.result;
    if (job.status === "failed") throw new Error(job.error || "Vision analysis failed");
    if (job.status === "cancelled") throw new Error("Vision analysis cancelled");
    await sleep(2000);
  }
  throw new Error("Vision analysis is taking longer than expected. Check Library for a new vision report.");
}

async function resolveVisionSliceIdsForAnalysis() {
  const workflow = imagingWorkflowIds();
  if (workflow.length) return workflow.slice(0, IMAGING_VISION_SLICE_LIMIT);

  readImagingFiltersFromUi();
  if (!Object.keys(state.imagingFilters || {}).length) {
    throw new Error("Set one or more imaging filters above to match slices, then click Analyze with vision.");
  }
  await refreshImagingMatch();
  const matched = imagingMatchIds();
  if (!matched.length) {
    throw new Error("No slices match the current filters.");
  }
  const limit = Math.min(IMAGING_VISION_SLICE_LIMIT, matched.length);
  const sampled = sampleIdsEvenly(matched, limit);
  setImagingWorkflowIds(sampled);
  return sampled;
}

function resolveVisionSliceIds() {
  const workflow = imagingWorkflowIds();
  if (workflow.length) return workflow.slice(0, IMAGING_VISION_SLICE_LIMIT);
  const matched = imagingMatchIds();
  if (matched.length) {
    return sampleIdsEvenly(matched, Math.min(IMAGING_VISION_SLICE_LIMIT, matched.length));
  }
  return [];
}

async function analyzeImagingWorkflowVision() {
  let ids;
  try {
    ids = await resolveVisionSliceIdsForAnalysis();
  } catch (err) {
    return toast(err.message, "error");
  }

  const filterParts = Object.values(state.imagingMatch?.filters || state.imagingFilters || {});
  const filterNote = filterParts.length ? `\nFilters: ${filterParts.join(" · ")}` : "";

  if (
    !confirm(
      `Run imaging analysis on ${ids.length} DICOM slice${ids.length === 1 ? "" : "s"}?${filterNote}\n\nCreates a separate text report. It will NOT be added to the overall assessment until you choose to include it. May take several minutes.`
    )
  ) {
    return;
  }

  const btn = $("#btn-imaging-analyze-vision");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/documents/imaging/analyze-vision", {
      method: "POST",
      body: JSON.stringify({ document_ids: ids }),
    });
    const jobId = data.job?.id;
    if (!jobId) throw new Error("Vision job did not start");

    const taskId = beginVisionBackgroundTask(jobId);
    const result = await pollVisionJob(jobId, { taskId });
    removeBackgroundTask(taskId);

    state.lastVisionDocumentId = result.document_id;
    await loadDocumentIndex();
    setVisionReportPendingInclusion(result.document_id);
    renderVisionReportsPanel();
    renderAssessmentScopeCard();
    updateSelectedLabel();
    renderImagingWorkflowSummary();
    if (state.documents.length) renderDocuments();

    toast(
      "Imaging analysis complete. Review the report below, then include it in the overall assessment when ready."
    );
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function existingDocumentIdSet() {
  const ids = new Set(state.documentIndex.map((doc) => doc.id));
  return ids;
}

function applyScopeFromLastAssessmentPlus(extraIds) {
  const known = existingDocumentIdSet();
  const lastIds = (state.latestAnalysis?.document_ids || []).filter((id) => known.has(id));
  const combined = new Set(lastIds);
  extraIds.forEach((id) => combined.add(id));
  state.selectedIds = combined;
  saveSelectionToSession();
  return { added: lastIds.length > 0 };
}

function getImagingFilterQueryParams() {
  const params = new URLSearchParams();
  Object.entries(state.imagingFilters || {}).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params;
}

function imagingDocExcerpt(meta) {
  if (!meta) return "";
  const parts = [];
  if (meta.dicom_series_description) parts.push(meta.dicom_series_description);
  if (meta.dicom_instance_number) parts.push(`#${meta.dicom_instance_number}`);
  if (meta.dicom_slice_location) parts.push(`${meta.dicom_slice_location} mm`);
  if (meta.dicom_convolution_kernel) parts.push(meta.dicom_convolution_kernel);
  return parts.join(" · ");
}

function formatDicomStudyDate(meta) {
  const raw = String(meta?.dicom_study_date || meta?.study_date || "").trim();
  if (raw.length === 8 && /^\d+$/.test(raw)) {
    return `${raw.slice(4, 6)}/${raw.slice(6, 8)}/${raw.slice(0, 4)}`;
  }
  return raw;
}

function imagingLibraryGroupKey(doc) {
  const meta = doc.metadata || {};
  const folder = imagingFolderName(doc);
  const modality = (meta.modality || meta.dicom_modality || "Imaging").trim() || "Imaging";
  const studyDateRaw = String(meta.dicom_study_date || meta.study_date || "").trim();
  const studyDate = formatDicomStudyDate(meta) || "Unknown date";
  return `${folder}|${modality}|${studyDateRaw || studyDate}`;
}

function buildImagingLibraryGroups(docs) {
  const groups = new Map();
  docs.forEach((doc) => {
    const meta = doc.metadata || {};
    const folder = imagingFolderName(doc);
    const modality = (meta.modality || meta.dicom_modality || "Imaging").trim() || "Imaging";
    const studyDateRaw = String(meta.dicom_study_date || meta.study_date || "").trim();
    const studyDate = formatDicomStudyDate(meta) || "Unknown date";
    const key = imagingLibraryGroupKey(doc);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        folder,
        modality,
        studyDate,
        studyDateRaw,
        docs: [],
      });
    }
    groups.get(key).docs.push(doc);
  });

  return [...groups.values()].sort((a, b) => {
    const dateCmp = (b.studyDateRaw || b.studyDate).localeCompare(a.studyDateRaw || a.studyDate);
    if (dateCmp) return dateCmp;
    const folderCmp = a.folder.localeCompare(b.folder);
    if (folderCmp) return folderCmp;
    return a.modality.localeCompare(b.modality);
  });
}

function imagingGroupSelectionState(docIds) {
  const selectedCount = docIds.filter((id) => state.selectedIds.has(id)).length;
  if (!selectedCount) return { checked: false, indeterminate: false };
  if (selectedCount === docIds.length) return { checked: true, indeterminate: false };
  return { checked: false, indeterminate: true };
}

function toggleImagingGroupSelection(groupKey, selected) {
  const docIds = state.imagingLibraryGroupMap?.get(groupKey) || [];
  docIds.forEach((id) => {
    if (selected) state.selectedIds.add(id);
    else state.selectedIds.delete(id);
  });
  saveSelectionToSession();
  renderDocuments();
  updateSelectedLabel();
  renderAssessmentScopeCard();
}

function renderLibraryDocItem(doc, { compact = false } = {}) {
  const selected = state.selectedIds.has(doc.id);
  const meta = doc.metadata || {};
  const excerpt = meta.page_count
    ? `${meta.page_count} pages`
    : doc.source_type === "imaging"
      ? imagingDocExcerpt(meta) || meta.modality || "DICOM"
      : meta.modality
        ? meta.modality
        : meta.imaging_format === "DICOM" || meta.is_dicom || [".dcm", ".dicom"].includes(meta.file_extension)
          ? "DICOM"
          : meta.file_size_label
            ? meta.file_size_label
            : "";
  const paths = compact ? "" : renderPathLines(docPathLines(doc));
  const info = doc.source_info || {};
  const sourceBadge = info.shorthand
    ? `<span class="source-tag ${escapeHtml(info.css_class || "source-document")}" title="${escapeHtml(info.type_display || "")}">${escapeHtml(info.shorthand)}</span>`
    : "";
  const displayName = info.display_name || doc.title;
  const inclusionBadges = renderDocInclusionBadges(doc.id);
  const newClass = isNewForNextAssessment(doc.id) ? " doc-item-new" : "";
  return `
    <article class="doc-item ${selected ? "selected" : ""}${compact ? " doc-item-compact" : ""}${newClass}" data-id="${doc.id}">
      <div class="doc-item-heading">
        <label class="doc-select-check" title="Include in assessment">
          <input type="checkbox" class="doc-select-input" data-id="${doc.id}"${selected ? " checked" : ""}>
        </label>
        ${sourceBadge}
        <strong>${escapeHtml(displayName)}</strong>
      </div>
      ${inclusionBadges ? `<div class="doc-inclusion-badges">${inclusionBadges}</div>` : ""}
      ${!compact && displayName !== doc.title ? `<p class="muted small doc-stored-title">Stored title: ${escapeHtml(doc.title)}</p>` : ""}
      <div class="doc-meta">
        <span class="badge">${escapeHtml(doc.source_type)}</span>
        ${compact ? "" : `<span>${formatDate(doc.created_at)}</span>`}
        ${excerpt ? `<span>${escapeHtml(excerpt)}</span>` : ""}
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
}

function renderImagingLibraryGroups() {
  const list = $("#documents-list");
  const summary = $("#library-summary");
  const pagination = $("#library-pagination");
  if (!list) return;

  const imagingDocs = state.documentIndex.filter((doc) => doc.source_type === "imaging");
  const groups = buildImagingLibraryGroups(imagingDocs);
  state.imagingLibraryGroupMap = new Map(groups.map((group) => [group.key, group.docs.map((doc) => doc.id)]));

  if (summary) {
    if (!imagingDocs.length) {
      summary.textContent = "No DICOM / imaging documents";
    } else {
      summary.textContent = `${groups.length} upload group${groups.length === 1 ? "" : "s"} · ${imagingDocs.length} slice${imagingDocs.length === 1 ? "" : "s"}`;
    }
  }

  if (!imagingDocs.length) {
    list.innerHTML = `<p class="muted">No DICOM or imaging files yet. Upload a study folder from Add data.</p>`;
    pagination?.classList.add("hidden");
    renderLibrarySelectionControls();
    return;
  }

  list.innerHTML = groups
    .map((group) => {
      const docIds = group.docs.map((doc) => doc.id);
      const selection = imagingGroupSelectionState(docIds);
      const selectedCount = docIds.filter((id) => state.selectedIds.has(id)).length;
      const title = `All ${group.modality} images from ${group.studyDate}`;
      const subtitle = `${group.folder} · ${group.docs.length} slice${group.docs.length === 1 ? "" : "s"}${
        selectedCount ? ` · ${selectedCount} selected` : ""
      }`;
      const slices = group.docs
        .slice()
        .sort((a, b) => {
          const ai = Number(a.metadata?.dicom_instance_number) || 0;
          const bi = Number(b.metadata?.dicom_instance_number) || 0;
          return ai - bi || String(a.title || "").localeCompare(String(b.title || ""));
        })
        .map((doc) => renderLibraryDocItem(doc, { compact: true }))
        .join("");
      return `
        <details class="library-imaging-group">
          <summary class="library-imaging-group-summary">
            <label class="library-imaging-group-check" title="Select all slices in this upload group">
              <input type="checkbox" class="imaging-group-select" data-group-key="${escapeHtml(group.key)}"${
                selection.checked ? " checked" : ""
              }>
            </label>
            <span class="library-imaging-group-text">
              <strong>${escapeHtml(title)}</strong>
              <span class="muted small">${escapeHtml(subtitle)}</span>
            </span>
          </summary>
          <div class="library-imaging-group-slices">${slices}</div>
        </details>`;
    })
    .join("");

  list.querySelectorAll(".imaging-group-select").forEach((input) => {
    const docIds = state.imagingLibraryGroupMap.get(input.dataset.groupKey) || [];
    const selection = imagingGroupSelectionState(docIds);
    input.checked = selection.checked;
    input.indeterminate = selection.indeterminate;
  });

  pagination?.classList.add("hidden");
  renderLibrarySelectionControls();
}

function syncImagingGroupCheckboxes() {
  $("#documents-list")?.querySelectorAll(".imaging-group-select").forEach((input) => {
    const docIds = state.imagingLibraryGroupMap?.get(input.dataset.groupKey) || [];
    const selection = imagingGroupSelectionState(docIds);
    input.checked = selection.checked;
    input.indeterminate = selection.indeterminate;
  });
}

function renderImagingFilterFields() {
  const container = $("#imaging-filter-fields");
  if (!container) return;

  const facetMap = Object.fromEntries(
    (state.imagingFacets?.facets || []).map((facet) => [facet.key, facet])
  );

  container.innerHTML = IMAGING_FILTER_SPECS.map((spec) => {
    const facet = facetMap[spec.key];
    const values = facet?.values || [];
    const current = state.imagingFilters[spec.key] || "";
    const options = [
      `<option value="">Any</option>`,
      ...values.map(
        (row) =>
          `<option value="${escapeHtml(row.value)}"${row.value === current ? " selected" : ""}>${escapeHtml(row.value)} (${row.count})</option>`
      ),
    ].join("");
    return `<div class="imaging-filter-field"><label for="imaging-filter-${escapeHtml(spec.key)}">${escapeHtml(spec.label)}<select id="imaging-filter-${escapeHtml(spec.key)}" data-imaging-filter="${escapeHtml(spec.key)}">${options}</select></label></div>`;
  }).join("");
}

function renderImagingReindexNote() {
  const note = $("#imaging-filter-reindex-note");
  if (!note) return;
  if (state.imagingFacetsError) {
    note.classList.remove("hidden");
    note.innerHTML = `${escapeHtml(state.imagingFacetsError)}. Refresh the page after restarting the server.`;
    return;
  }
  if (state.imagingFacets?.needs_reindex) {
    note.classList.remove("hidden");
    note.innerHTML =
      'Slice details are missing for older uploads. <button type="button" class="btn ghost btn-sm" id="btn-imaging-reindex">Refresh slice details</button>';
    $("#btn-imaging-reindex")?.addEventListener("click", () =>
      reindexImagingMetadata().catch((e) => toast(e.message, "error"))
    );
    return;
  }
  note.classList.add("hidden");
  note.innerHTML = "";
}

function renderImagingMatchSummary() {
  const summary = $("#imaging-filter-match-summary");
  const match = state.imagingMatch;
  if (!summary) return;

  if (!match || !Object.keys(state.imagingFilters || {}).length) {
    summary.textContent = "Set filters to see matching slices.";
    return;
  }

  const filterParts = Object.entries(match.filters || {}).map(([, value]) => `${value}`);
  summary.innerHTML = `<strong>${match.total}</strong> slice${match.total === 1 ? "" : "s"} match${
    filterParts.length ? `: ${escapeHtml(filterParts.join(" · "))}` : ""
  } · up to ${IMAGING_VISION_SLICE_LIMIT} per analysis`;
  renderImagingSlicePicker();
}

async function loadImagingFacets() {
  if (!(state.libraryCounts?.imaging > 0)) {
    state.imagingFacets = null;
    state.imagingFacetsError = null;
    updateImagingFilterVisibility();
    renderImagingFilterFields();
    renderImagingReindexNote();
    return;
  }
  updateImagingFilterVisibility();
  renderImagingFilterFields();
  try {
    state.imagingFacets = await api("/api/documents/imaging/facets");
    state.imagingFacetsError = null;
  } catch (err) {
    state.imagingFacets = null;
    state.imagingFacetsError = err.message || "Could not load imaging filters";
  }
  renderImagingReindexNote();
  renderImagingFilterFields();
  renderImagingWorkflowSummary();
}

async function refreshImagingMatch() {
  if (!Object.keys(state.imagingFilters || {}).length) {
    state.imagingMatch = null;
    renderImagingMatchSummary();
    return;
  }
  const params = getImagingFilterQueryParams();
  state.imagingMatch = await api(`/api/documents/imaging/match?${params}`);
  renderImagingMatchSummary();
  renderImagingSlicePicker();
}

function readImagingFiltersFromUi() {
  const filters = {};
  $$("[data-imaging-filter]").forEach((select) => {
    const key = select.dataset.imagingFilter;
    const value = select.value.trim();
    if (key && value) filters[key] = value;
  });
  state.imagingFilters = filters;
}

async function onImagingFilterChange() {
  readImagingFiltersFromUi();
  state.imagingWorkflowIds = [];
  await refreshImagingMatch();
  renderImagingSlicePicker();
  renderImagingWorkflowSummary();
}

function clearImagingFilters() {
  state.imagingFilters = {};
  state.imagingMatch = null;
  state.imagingWorkflowIds = [];
  renderImagingFilterFields();
  renderImagingMatchSummary();
  renderImagingSlicePicker();
  renderImagingWorkflowSummary();
}

async function reindexImagingMetadata() {
  const btn = $("#btn-imaging-reindex");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Refreshing…";
  }
  try {
    const result = await api("/api/documents/imaging/reindex-metadata", { method: "POST" });
    toast(`Refreshed imaging details for ${result.updated}/${result.total} files`);
    await loadImagingFacets();
    if (Object.keys(state.imagingFilters).length) {
      await refreshImagingMatch();
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Refresh slice details";
    }
  }
}

function imagingMatchIds() {
  return state.imagingMatch?.document_ids || [];
}

function scrollToImagingPanel() {
  $("#panel-imaging")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function goToImagingPanel() {
  switchTab("imaging");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function goToLibraryForImagingFilter() {
  goToImagingPanel();
}

function goToLibraryImagingType() {
  switchTab("library", { skipLibraryLoad: true });
  (async () => {
    const typeFilter = $("#library-type-filter");
    if (typeFilter && typeFilter.value !== "imaging") {
      typeFilter.value = "imaging";
    }
    await loadDocuments({ page: 1, sourceType: "imaging" });
    window.scrollTo({ top: 0, behavior: "smooth" });
  })().catch((e) => toast(e.message, "error"));
}

function initImagingFilterPanel() {
  $("#imaging-filter-fields")?.addEventListener("change", (event) => {
    if (event.target.matches("[data-imaging-filter]")) {
      onImagingFilterChange().catch((e) => toast(e.message, "error"));
    }
  });
  $("#imaging-slice-picker")?.addEventListener("change", (event) => {
    const input = event.target.closest(".imaging-slice-check");
    if (!input) return;
    toggleImagingSliceSelection(input.dataset.id, input.checked);
  });
  $("#imaging-vision-reports-list")?.addEventListener("click", (event) => {
    const viewBtn = event.target.closest(".btn-view");
    if (viewBtn?.dataset?.id) {
      viewDocument(viewBtn.dataset.id).catch((e) => toast(e.message, "error"));
      return;
    }
    const includeBtn = event.target.closest(".btn-imaging-include");
    if (includeBtn?.dataset?.id) {
      includeDocumentInOverallAssessment(includeBtn.dataset.id);
      return;
    }
    const excludeBtn = event.target.closest(".btn-imaging-exclude");
    if (excludeBtn?.dataset?.id) {
      excludeDocumentFromOverallAssessment(excludeBtn.dataset.id);
    }
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-nav-imaging]")) {
      event.preventDefault();
      goToImagingPanel();
    }
    if (event.target.closest("[data-nav-library-imaging]")) {
      event.preventDefault();
      goToLibraryImagingType();
    }
  });
  safeOn("#btn-imaging-filter-clear", "click", () => clearImagingFilters());
  safeOn("#btn-imaging-sample-slices", "click", () => sampleImagingSlicesEvenly());
  safeOn("#btn-imaging-clear-slices", "click", () => clearImagingSliceSelection());
  safeOn("#btn-imaging-analyze-vision", "click", () =>
    analyzeImagingWorkflowVision().catch((e) => toast(e.message, "error"))
  );
  safeOn("#btn-scope-imaging", "click", () => goToImagingPanel());
  renderImagingWorkflowSummary();
  renderVisionReportsPanel();
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
  updateImagingFilterVisibility();
  await loadImagingFacets().catch(() => {});
  renderImagingWorkflowSummary();
  renderVisionReportsPanel();
  if ($("#panel-imaging")?.classList.contains("active")) {
    renderImagingSlicePicker();
  }
  if ($("#panel-library")?.classList.contains("active")) {
    renderDocuments();
  }
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
  updateImagingFilterVisibility();
  if (state.libraryCounts?.imaging > 0 && !state.imagingFacets && !state.imagingFacetsError) {
    loadImagingFacets().catch(() => {});
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
      display_label: labels.unknown?.display || "Not in your library",
      type_display: labels.unknown?.display || "Not in your library",
    };
  }
  if (lower.startsWith("document")) {
    const titleMatch = inner.match(/^document\s+"([^"]+)"/i);
    const title = titleMatch?.[1] || inner;
    const doc = findDocumentByTitle(title);
    if (doc?.source_info) {
      const info = doc.source_info;
      return {
        ...info,
        display_label: info.display_label || info.display_name || title,
        document_id: info.document_id || doc.id,
        type_display: info.type_display || info.display || "",
      };
    }
    return {
      css_class: "source-document",
      shorthand: labels.document?.shorthand || "Doc",
      display_label: title,
      type_display: labels.document?.display || "Clinical record",
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
    return "Not backed by a stored library record — do not treat as verified fact";
  }
  if (meta.type_display) return meta.type_display;
  return meta.display_label || inner || "Source";
}

function renderInlineSourceCitation(meta, inner) {
  const title = sourceCitationTitle(meta, inner);
  return `<span class="source-cite-inline ${meta.css_class || "source-inference"}" title="${escapeHtml(title)}">${renderSourceBadge(meta)}</span>`;
}

function formatWithSources(text, idPrefix = null) {
  if (!text) return "";
  const { html, appendix, prefix } = formatTextWithBottomReferences(text, idPrefix);
  return html + renderInlineReferenceAppendix(appendix, prefix);
}

function buildClientReferenceRegistry(text) {
  const labels = [];
  const seen = new Set();
  const re = /\[SOURCE:\s*([^\]]+)\]/gi;
  let match;
  while ((match = re.exec(text || ""))) {
    const label = match[1].trim();
    const key = label.toLowerCase();
    if (!label || seen.has(key)) continue;
    seen.add(key);
    labels.push(label);
  }
  const registry = {};
  const appendix = labels.map((label, index) => {
    const num = index + 1;
    const meta = describeSourceTagInner(label);
    const titleMatch = label.match(/^document\s+"([^"]+)"/i);
    const doc = titleMatch ? findDocumentByTitle(titleMatch[1]) : null;
    const entry = enrichReference({
      num,
      label: meta.display_label || label,
      raw_label: label,
      display_label: meta.display_label || label,
      css_class: meta.css_class,
      shorthand: meta.shorthand,
      type: meta.type || meta.css_class?.replace(/^source-/, "") || "document",
      type_display: meta.type_display || "",
      document_id: meta.document_id || doc?.id || null,
    });
    registry[num] = entry;
    registry[String(num)] = entry;
    return entry;
  });
  return { registry, appendix };
}

function formatTextWithBottomReferences(text, idPrefix = null) {
  const prefix = idPrefix || `local-${Math.abs(hashString(text || "") % 1e9)}`;
  const { registry, appendix } = buildClientReferenceRegistry(text);
  if (!appendix.length) {
    return {
      html: formatMarkdownEmphasis(escapeHtml(text || "")).replace(/\n/g, "<br>"),
      appendix: [],
      prefix,
    };
  }
  return {
    html: formatNumberedReferences(text, registry, prefix),
    appendix,
    prefix,
  };
}

function hashString(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i += 1) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return hash;
}

function renderInlineReferenceAppendix(appendix, idPrefix = "ref") {
  if (!appendix?.length) return "";
  return `<div class="message-references">
    <p class="message-references-heading">References</p>
    <ol class="references-appendix-list">${appendix
      .map((ref) => renderReferenceEntry(ref, idPrefix, { anchor: true }))
      .join("")}</ol>
  </div>`;
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
  "not in your library",
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

function citePillText(ref, num) {
  const n = String(num ?? ref?.num ?? "?");
  const type = ref?.type || "";
  const css = ref?.css_class || "";
  if (type === "unknown" || css.includes("unknown")) return `[${n}]`;
  if (type === "inference" || css.includes("inference")) return `[${n}]`;
  if (type === "patient_context" || css.includes("context")) return `[${n}]`;
  // Number first so multiple Docs stay distinct even when titles are long.
  const short = citePillLabel(ref);
  if (!short || short === "Source") return `[${n}]`;
  return `[${n}] ${short}`;
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
  const type = ref.type || "";
  if (type === "unknown" || ref.css_class?.includes("unknown")) {
    return `<span class="source-card-unknown-title">Not in your library</span>`;
  }
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
    return "This claim is not backed by any stored record in your library. Treat it as unverified.";
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
      <p class="sources-sidebar-sub muted small">Inline citations from the assessment text — not the same as assessment scope</p>
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
  escaped = escaped.replace(
    /(?<!\[)\bSOURCE:\s*((?:Document\s+"[^"]+"|Unknown[^\n\[]*|Web[^\n\[]*))/gi,
    (_, inner) => `[SOURCE: ${inner.trim()}]`
  );
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
        return `<a href="${hash}" class="cite-pill ref-cite-link" title="${escapeHtml(fullTitle)}">[${escapeHtml(num)}]</a>`;
      }
      const cls = ref.css_class || sourceTagClass(ref.raw_label || ref.label || "");
      const docAttr = ref.document_id ? ` data-doc-id="${escapeHtml(ref.document_id)}"` : "";
      const href = hash;
      return `<a href="${escapeHtml(href)}" class="cite-pill ref-cite-link ${cls}" title="${escapeHtml(fullTitle)}"${docAttr}>${escapeHtml(citePillText(ref, num))}</a>`;
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
  const enriched = enrichReference(ref);
  const label = enriched.display_label || enriched.label || "";
  const actions = renderReferenceActions(enriched);
  const idAttr = anchor ? ` id="${escapeHtml(refEntryId(idPrefix, enriched.num))}"` : "";
  const typeHint = enriched.type_display || enriched.type || "";
  const typeLine = typeHint
    ? `<span class="ref-type muted small">${escapeHtml(typeHint)}</span>`
    : "";
  return `<li${idAttr} class="reference-entry">
    ${renderSourceBadge(enriched)}
    <span class="ref-num">[${escapeHtml(String(enriched.num))}]</span>
    <span class="ref-main">
      <span class="ref-label">${escapeHtml(label)}</span>
      ${typeLine}
    </span>
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
    if (event.target.closest("#btn-view-assessment-scope")) {
      scrollToAssessmentScope();
      return;
    }

    const expandBtn = event.target.closest("[data-action=expand-sources]");
    if (expandBtn) {
      const sidebar = expandBtn.closest(".sources-sidebar");
      sidebar?.classList.add("is-expanded");
      expandBtn.remove();
      return;
    }

    const docLink = event.target.closest(".ref-doc-link");
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
        const panel = citeLink.closest(".panel.active, .custom-task-detail, .answer-layout, .options-chat-bubble");
        const target =
          panel?.querySelector(`#${CSS.escape(id)}`) ||
          document.getElementById(id) ||
          document.querySelector(citeLink.hash);
        if (target) {
          event.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
          target.classList.add("ref-highlight");
          setTimeout(() => target.classList.remove("ref-highlight"), 1600);
          return;
        }
      }
      if (citeLink.dataset?.docId) {
        event.preventDefault();
        viewDocument(citeLink.dataset.docId);
      }
    }
  });
}

const SOURCE_TYPE_ORDER = ["document", "diagnostic", "web", "chat_observation", "patient_context", "inference", "unknown"];

const SOURCE_TYPE_CSS = {
  document: "source-document",
  diagnostic: "source-diagnostic",
  web: "source-web",
  chat_observation: "source-chat",
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
    draftRaw.innerHTML = `<div class="sourced-text">${formatWithSources(item.investigation_draft_response, `inv-draft-${item.id}`)}</div>`;
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
    acceptedBody.innerHTML = formatWithSources(item.investigation_response, `inv-${item.id}`);
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
      "Re-run analysis for fully LLM-generated tags.";
  } else {
    html =
      '<span class="notice-title">Source tags missing.</span> ' +
      "Expand Update analysis below and re-run to regenerate with source attribution.";
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
    renderHomeResultsSidebar(null);
    state.referenceRegistry = {};
    execTextEl.innerHTML = "";
    if (fullBody) fullBody.innerHTML = "";
    renderReferencesAppendix(null);
    renderOpenItemsTable([]);
    selectOpenItem(null);
    renderSourceAttributionNotice(null);
    renderExecutiveSummaryNotice(null, false);
    renderHomeState(false);
    renderAssessmentScopeCard();
    return;
  }

  const refPrefix = analysis.id;
  const summaryPick = effectiveExecutiveSummaryDisplay(analysis);
  const summaryDisplay = summaryPick.text;
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
  renderExecutiveSummaryNotice(analysis, summaryPick.usedFallback);

  if (summaryDisplay) {
    execTextEl.innerHTML = `<div class="numbered-text">${formatNumberedReferences(summaryDisplay, state.referenceRegistry, refPrefix)}</div>`;
  } else {
    execTextEl.innerHTML = '<p class="muted">No assessment text was returned.</p>';
  }

  if (fullCard && fullBody) {
    if (responseDisplay && !summaryPick.usedFallback) {
      fullCard.classList.remove("hidden");
      fullBody.innerHTML = formatNumberedReferences(responseDisplay, state.referenceRegistry, refPrefix);
    } else {
      fullCard.classList.add("hidden");
      fullBody.innerHTML = "";
    }
  }

  renderReferencesAppendix(analysis, { idPrefix: refPrefix });
  renderHomeResultsSidebar(analysis);

  renderOpenItemsTable(analysis.open_items || []);
  renderAssessmentScopeCard();
  if (state.documents.length) renderDocuments();
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
  renderAssessmentScopeCard();
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
  let jobId = null;

  try {
    const started = await startAnalysisJob({ query, baseline, summarize, assessmentGuidance });
    jobId = started.jobId;
    const jobType = started.jobType;
    const taskId = beginAnalysisBackgroundTask(jobId, jobType, { query });
    setAnalysisRunning(true, jobId, jobType);
    if (isCustomQuery) {
      switchTab("custom-tasks");
      updateCustomTasksRunningBanner(true, query);
      toast("Custom task submitted");
    }
    const analysis = await pollAnalysisJob(jobId, { isCustomQuery, taskId });
    if (isCustomQuery) finishCustomTaskRun(analysis, query);
    else finishAnalysisRun(analysis);
  } catch (err) {
    if (err.message === "Analysis cancelled") toast("Analysis cancelled");
    else if (err.status === 409) {
      toast("An analysis is already running — resuming progress.", "error");
      await resumeActiveAnalysisJob();
      return;
    } else {
      toast(err.message, "error");
    }
    if (isCustomQuery) loadCustomTasks();
  } finally {
    if (jobId) removeBackgroundTask(`analysis-${jobId}`);
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
  initTabPersistence();
  initScrollTop();
  initBackgroundStatusBar();
  initImagingFilterPanel();
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
    await Promise.allSettled([
      loadDocumentIndex(),
      loadLatestAssessment(),
      loadCustomTasks(),
      loadChatObservations(),
    ]);
  } finally {
    updateHomeToolbar();
  }
  restoreActiveTab();
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
safeOn("#library-type-filter", "change", (event) => {
  const sourceType = event.target.value;
  loadDocuments({ page: 1, sourceType }).catch((e) => toast(e.message, "error"));
  if (sourceType === "imaging") {
    loadImagingFacets().catch((e) => toast(e.message, "error"));
  }
});
safeOn("#btn-library-baseline", "click", () => confirmAndRunBaseline());
safeOn("#btn-select-all-shown", "click", () => selectAllShownDocuments());
safeOn("#btn-scope-match-last-lib", "click", () => applyLastAssessmentScope());
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
  const groupCheckbox = event.target.closest(".imaging-group-select");
  if (groupCheckbox?.dataset.groupKey) {
    event.stopPropagation();
    toggleImagingGroupSelection(groupCheckbox.dataset.groupKey, groupCheckbox.checked);
    return;
  }
  const groupCheckLabel = event.target.closest(".library-imaging-group-check");
  if (groupCheckLabel) {
    event.stopPropagation();
  }
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
  const url = $("#facebook-input").value.trim();
  const title = $("#facebook-title").value.trim() || null;
  const notes = $("#facebook-notes")?.value.trim() || null;
  if (!url) return toast("Facebook URL required", "error");
  const btn = $("#btn-ingest-facebook");

  try {
    await withBackgroundTask({
      id: `ingest-facebook-${Date.now()}`,
      label: "Ingesting Facebook video",
      run: async ({ setDetail, isCancelled }) => {
        if (btn) {
          btn.disabled = true;
          btn.textContent = "Downloading…";
        }
        setDetail("Downloading transcript…");
        const data = await api("/api/ingest/facebook", {
          method: "POST",
          body: JSON.stringify({ url, title, notes }),
          timeoutMs: 600000,
        });
        if (isCancelled()) return;
        showUploadResult(data.document);
        if ($("#facebook-input")) $("#facebook-input").value = "";
        if ($("#facebook-title")) $("#facebook-title").value = "";
        if ($("#facebook-notes")) $("#facebook-notes").value = "";
        toast("Facebook video ingested");
        await openLibraryAfterIngest();
      },
    });
  } catch (e) {
    if (e.message !== "Cancelled") toast(e.message, "error");
  } finally {
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
    await withBackgroundTask({
      id: `upload-pdf-${Date.now()}`,
      label: `Uploading PDF: ${file.name}`,
      run: async ({ setDetail, isCancelled }) => {
        setDetail("Processing document…");
        const fd = new FormData();
        fd.append("file", file);
        const title = $("#pdf-title").value.trim();
        if (title) fd.append("title", title);
        const data = await api("/api/ingest/pdf", { method: "POST", body: fd, timeoutMs: 600000 });
        if (isCancelled()) return;
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
      },
    });
  } catch (e) {
    if (e.message !== "Cancelled") toast(e.message, "error");
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

  try {
    await withBackgroundTask({
      id: `upload-imaging-${Date.now()}`,
      label: `Uploading ${total} imaging file${total === 1 ? "" : "s"}`,
      run: async ({ setDetail, isCancelled }) => {
        let uploaded = 0;
        let failed = 0;
        let lastDoc = null;

        btn.disabled = true;
        setImagingUploadProgress(`Uploading 0/${total}…`, true);

        for (const file of imagingSelection) {
          if (isCancelled()) {
            toast(`Upload cancelled after ${uploaded}/${total} file${uploaded === 1 ? "" : "s"}`);
            break;
          }
          uploaded += 1;
          const progress = `${uploaded}/${total}: ${file.name}`;
          setDetail(progress);
          setImagingUploadProgress(`Uploading ${progress}`, true);
          const fd = new FormData();
          fd.append("file", file, file.name);
          if (titlePrefix) fd.append("title_prefix", titlePrefix);
          if (notes) fd.append("notes", notes);
          const relativePath = file.webkitRelativePath || file.name;
          if (relativePath) fd.append("relative_path", relativePath);
          try {
            const data = await api("/api/ingest/imaging", {
              method: "POST",
              body: fd,
              timeoutMs: 600000,
            });
            lastDoc = data.document;
          } catch (err) {
            if (err.message === "Cancelled") break;
            failed += 1;
            console.error(err);
          }
        }

        if (isCancelled()) return;

        if (lastDoc) showUploadResult(lastDoc);
        if (failed === 0 && uploaded === total) {
          toast(`Uploaded ${total} imaging file${total === 1 ? "" : "s"}`);
          clearImagingSelection();
          await openLibraryAfterIngest();
        } else if (uploaded > failed) {
          toast(`Uploaded ${uploaded - failed}/${total} imaging files (${failed} failed)`, "error");
          clearImagingSelection();
          await openLibraryAfterIngest();
        } else if (failed > 0) {
          toast(`Upload failed for ${failed} file${failed === 1 ? "" : "s"}`, "error");
        }
      },
    });
  } catch (err) {
    if (err.message !== "Cancelled") toast(err.message, "error");
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
    await withBackgroundTask({
      id: `upload-video-${Date.now()}`,
      label: `Uploading video: ${file.name}`,
      run: async ({ setDetail, isCancelled }) => {
        setDetail("Processing video…");
        const fd = new FormData();
        fd.append("file", file);
        const title = $("#video-title").value.trim();
        const notes = $("#video-notes").value.trim();
        if (title) fd.append("title", title);
        if (notes) fd.append("notes", notes);
        const data = await api("/api/ingest/video", {
          method: "POST",
          body: fd,
          timeoutMs: 600000,
        });
        if (isCancelled()) return;
        showUploadResult(data.document);
        $("#video-file").value = "";
        $("#video-file").closest(".file-label")?.querySelector(".file-name")?.remove();
        toast(`Video stored · ${file.name}`);
        await openLibraryAfterIngest();
      },
    });
  } catch (e) {
    if (e.message !== "Cancelled") toast(e.message, "error");
  }
});

safeOn("#btn-baseline", "click", () => confirmAndRunBaseline());
safeOn("#btn-scope-library", "click", goToLibraryForScope);
safeOn("#btn-scope-select-all", "click", selectAllDocuments);
safeOn("#btn-scope-clear", "click", clearDocumentSelection);
safeOn("#btn-scope-match-last", "click", applyLastAssessmentScope);
safeOn("#btn-open-item-adjust-scope", "click", scrollToAssessmentScope);
safeOn("#btn-open-item-reassess", "click", reassessFromOpenItem);
safeOn("#btn-summarize", "click", () => runAnalysis({ summarize: true }));

safeOn("#btn-analyze", "click", () => {
  const query = $("#analyze-query").value.trim();
  if (!query) return toast("Enter a question, or use Run analysis", "error");
  runAnalysis({ query });
});

$("#btn-save-settings")?.addEventListener("click", () =>
  saveModelSettings().catch((e) => toast(e.message, "error"))
);
$("#btn-save-reviewer")?.addEventListener("click", () =>
  saveReviewerContext().catch((e) => toast(e.message, "error"))
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

function hideChatSelectionToolbar() {
  const toolbar = $("#chat-selection-toolbar");
  if (toolbar) toolbar.classList.add("hidden");
  state.chatSelectionContext = { excerpt: "", messageId: null };
}

function showChatSelectionToolbar(x, y, excerpt, messageId) {
  const toolbar = $("#chat-selection-toolbar");
  const text = (excerpt || "").trim();
  if (!toolbar || !text) {
    hideChatSelectionToolbar();
    return;
  }
  state.chatSelectionContext = { excerpt: text, messageId: messageId || null };
  toolbar.classList.remove("hidden");
  const left = Math.min(x, window.innerWidth - toolbar.offsetWidth - 12);
  const top = Math.max(12, y - toolbar.offsetHeight - 10);
  toolbar.style.left = `${left}px`;
  toolbar.style.top = `${top}px`;
}

function renderChatObservationsQueueNote() {
  const el = $("#chat-observations-queue-note");
  if (!el) return;
  const n = state.chatObservationsPendingCount || 0;
  if (n > 0) {
    el.textContent = `${n} chat observation${n === 1 ? "" : "s"} queued for the next analysis run.`;
    el.classList.remove("hidden");
  } else {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

function renderChatObservations() {
  const list = $("#options-chat-observations-list");
  if (!list) return;
  const observations = state.chatObservations || [];
  if (!observations.length) {
    list.innerHTML = `<p class="muted small">No pinned excerpts yet.</p>`;
    return;
  }
  list.innerHTML = observations
    .map((obs) => {
      const includeChecked = obs.include_in_analysis ? " checked" : "";
      const saved = obs.document_id ? "Saved to library" : "Not in library";
      return `<article class="options-chat-observation-item" data-observation-id="${escapeHtml(obs.id)}">
        <div class="options-chat-observation-item-head">
          <span class="options-chat-observation-title">${escapeHtml(truncate(obs.title || "Chat observation", 80))}</span>
          <label class="small muted">
            <input type="checkbox" class="chat-obs-include" data-id="${escapeHtml(obs.id)}"${includeChecked} />
            Include
          </label>
        </div>
        <p class="options-chat-observation-excerpt">${escapeHtml(truncate(obs.excerpt || "", 160))}</p>
        <p class="muted small">${escapeHtml(saved)}</p>
        <div class="options-chat-observation-actions">
          ${
            obs.document_id
              ? ""
              : `<button type="button" class="btn ghost btn-sm chat-obs-save-lib" data-id="${escapeHtml(obs.id)}">Save to library</button>`
          }
          <button type="button" class="btn ghost btn-sm chat-obs-delete" data-id="${escapeHtml(obs.id)}">Remove</button>
        </div>
      </article>`;
    })
    .join("");
}

async function loadChatObservations() {
  const data = await api("/api/options-chat/observations");
  state.chatObservations = data.observations || [];
  state.chatObservationsPendingCount = data.pending_count || 0;
  renderChatObservations();
  renderChatObservationsQueueNote();
}

async function pinChatExcerpt(excerpt, messageId = null) {
  const sessionId = state.optionsChatSessionId;
  if (!sessionId) throw new Error("Start or select a chat first");
  const text = (excerpt || "").trim();
  if (!text) throw new Error("Nothing to pin");
  const data = await api("/api/options-chat/observations", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      message_id: messageId,
      excerpt: text,
    }),
  });
  await loadChatObservations();
  return data.observation;
}

async function saveChatObservationToLibrary(observationId) {
  const result = await api(
    `/api/options-chat/observations/${encodeURIComponent(observationId)}/save-to-library`,
    { method: "POST" }
  );
  const docId = result.document?.id;
  if (docId) {
    toggleSelect(docId, true);
    await loadDocumentIndex();
  }
  await loadChatObservations();
  return result;
}

async function saveExcerptToLibrary(excerpt, messageId = null) {
  const obs = await pinChatExcerpt(excerpt, messageId);
  await saveChatObservationToLibrary(obs.id);
  toast("Saved to library");
}

function sendExcerptToIngest(excerpt, titleHint = "") {
  const text = (excerpt || "").trim();
  if (!text) return;
  switchTab("ingest");
  const titleEl = $("#text-title");
  const contentEl = $("#text-content");
  if (titleEl && !titleEl.value.trim()) {
    titleEl.value = titleHint || "Chat excerpt";
  }
  if (contentEl) contentEl.value = text;
  contentEl?.focus();
  toast("Paste into Add data — review title and save");
}

function optionsChatScopeNote() {
  const n = state.selectedIds.size;
  const scope = n === 0 ? "Using all library documents" : `${n} selected documents`;
  return `${scope} · includes current Home assessment`;
}

function renderOptionsChatSessions() {
  const list = $("#options-chat-session-list");
  if (!list) return;
  const sessions = state.optionsChatSessions || [];
  if (!sessions.length) {
    list.innerHTML = `<p class="muted">No chats yet.</p>`;
    return;
  }
  list.innerHTML = sessions
    .map((session) => {
      const active = session.id === state.optionsChatSessionId ? " active" : "";
      const count = session.message_count || 0;
      return `<button type="button" class="options-chat-session-item${active}" data-session-id="${escapeHtml(session.id)}">
        <span class="options-chat-session-title">${escapeHtml(truncate(session.title || "AI Chat", 72))}</span>
        <span class="options-chat-session-meta">${count} message${count === 1 ? "" : "s"} · ${escapeHtml(formatTimestamp(session.updated_at))}</span>
      </button>`;
    })
    .join("");
}

function renderOptionsChatStarters() {
  const el = $("#options-chat-starters");
  if (!el) return;
  const starters = state.optionsChatStarters || [];
  el.innerHTML = starters
    .map(
      (text, index) =>
        `<button type="button" class="btn secondary btn-sm options-chat-starter" data-starter-index="${index}">${escapeHtml(text)}</button>`
    )
    .join("");
}

function renderOptionsChatMessages() {
  const wrap = $("#options-chat-messages");
  const titleEl = $("#options-chat-title");
  const scopeEl = $("#options-chat-scope-note");
  const deleteBtn = $("#btn-options-chat-delete");
  if (!wrap) return;

  const session = (state.optionsChatSessions || []).find((s) => s.id === state.optionsChatSessionId);
  if (titleEl) titleEl.textContent = session?.title || "AI Chat";
  if (scopeEl) scopeEl.textContent = optionsChatScopeNote();
  deleteBtn?.classList.toggle("hidden", !state.optionsChatSessionId);

  const messages = state.optionsChatMessages || [];
  if (!messages.length) {
    const starters = (state.optionsChatStarters || [])
      .map(
        (text, index) =>
          `<button type="button" class="btn secondary btn-sm options-chat-starter" data-starter-index="${index}">${escapeHtml(text)}</button>`
      )
      .join("");
    wrap.innerHTML = `
      <div class="options-chat-empty" id="options-chat-empty">
        <p>Ask anything about options for this case. Start broad, then push deeper on the branch that matters.</p>
        <div id="options-chat-starters" class="options-chat-starters">${starters}</div>
      </div>`;
    return;
  }

  wrap.innerHTML = messages
    .map((msg) => {
      const role = msg.role === "user" ? "user" : "assistant";
      const label = role === "user" ? "You" : "AI Chat";
      const body =
        role === "assistant"
          ? formatWithSources(msg.content || "", msg.id || null)
          : escapeHtml(msg.content || "").replace(/\n/g, "<br>");
      const streaming = msg.streaming ? " streaming" : "";
      const pinBtn =
        role === "assistant" && !msg.streaming && msg.id
          ? `<button type="button" class="btn ghost btn-sm options-chat-pin-whole" data-message-id="${escapeHtml(msg.id)}" title="Pin whole reply">Pin reply</button>`
          : "";
      return `<article class="options-chat-bubble ${role}${streaming}" data-message-id="${escapeHtml(msg.id || "")}">
        <div class="options-chat-bubble-head row-between wrap">
          <span class="options-chat-role">${label}</span>
          ${pinBtn}
        </div>
        <div class="options-chat-body">${body}</div>
      </article>`;
    })
    .join("");
  wrap.scrollTop = wrap.scrollHeight;
}

function setOptionsChatStatus(text) {
  const el = $("#options-chat-status");
  if (el) el.textContent = text || "";
}

function setOptionsChatSending(sending) {
  state.optionsChatSending = sending;
  const btn = $("#btn-options-chat-send");
  const input = $("#options-chat-input");
  if (btn) btn.disabled = sending;
  if (input) input.disabled = sending;
  setOptionsChatStatus(sending ? "Thinking…" : "");
}

async function loadOptionsChatPanel() {
  try {
    const [sessionsData, startersData] = await Promise.all([
      api("/api/options-chat/sessions"),
      api("/api/options-chat/starters"),
    ]);
    state.optionsChatSessions = sessionsData.sessions || [];
    state.optionsChatStarters = startersData.starters || [];
    await loadChatObservations();
    renderOptionsChatSessions();
    renderOptionsChatStarters();

    if (state.optionsChatSessionId) {
      await selectOptionsChatSession(state.optionsChatSessionId);
    } else if (state.optionsChatSessions.length) {
      await selectOptionsChatSession(state.optionsChatSessions[0].id);
    } else {
      state.optionsChatMessages = [];
      renderOptionsChatMessages();
    }
  } catch (err) {
    toast(err.message, "error");
  }
}

async function selectOptionsChatSession(sessionId) {
  if (!sessionId) return;
  const data = await api(`/api/options-chat/sessions/${sessionId}`);
  state.optionsChatSessionId = data.session?.id || sessionId;
  state.optionsChatMessages = data.messages || [];
  const idx = state.optionsChatSessions.findIndex((s) => s.id === state.optionsChatSessionId);
  if (idx >= 0) state.optionsChatSessions[idx] = data.session;
  else if (data.session) state.optionsChatSessions.unshift(data.session);
  renderOptionsChatSessions();
  renderOptionsChatMessages();
}

async function createOptionsChatSession() {
  const docIds = state.selectedIds.size ? [...state.selectedIds] : null;
  const data = await api("/api/options-chat/sessions", {
    method: "POST",
    body: JSON.stringify({
      document_ids: docIds,
      include_latest_assessment: true,
    }),
  });
  state.optionsChatSessions = [data.session, ...state.optionsChatSessions.filter((s) => s.id !== data.session.id)];
  state.optionsChatSessionId = data.session.id;
  state.optionsChatMessages = data.messages || [];
  renderOptionsChatSessions();
  renderOptionsChatMessages();
  $("#options-chat-input")?.focus();
  return data.session;
}

async function deleteOptionsChatSession() {
  const id = state.optionsChatSessionId;
  if (!id) return;
  if (!confirm("Delete this AI Chat?")) return;
  await api(`/api/options-chat/sessions/${id}`, { method: "DELETE" });
  state.optionsChatSessions = state.optionsChatSessions.filter((s) => s.id !== id);
  state.optionsChatSessionId = state.optionsChatSessions[0]?.id || null;
  state.optionsChatMessages = [];
  if (state.optionsChatSessionId) await selectOptionsChatSession(state.optionsChatSessionId);
  else {
    renderOptionsChatSessions();
    renderOptionsChatMessages();
  }
  toast("Chat deleted");
}

async function ensureOptionsChatSession() {
  if (state.optionsChatSessionId) return state.optionsChatSessionId;
  const session = await createOptionsChatSession();
  return session.id;
}

async function sendOptionsChatMessage(rawText) {
  const content = (rawText || "").trim();
  if (!content) return toast("Enter a message", "error");
  if (state.optionsChatSending) return;

  const sessionId = await ensureOptionsChatSession();
  setOptionsChatSending(true);

  const optimisticUser = {
    id: `local-user-${Date.now()}`,
    role: "user",
    content,
    created_at: new Date().toISOString(),
  };
  const streamingAssistant = {
    id: `local-assistant-${Date.now()}`,
    role: "assistant",
    content: "",
    streaming: true,
    created_at: new Date().toISOString(),
  };
  state.optionsChatMessages = [...state.optionsChatMessages, optimisticUser, streamingAssistant];
  renderOptionsChatMessages();
  if ($("#options-chat-input")) $("#options-chat-input").value = "";

  try {
    const res = await fetch(`/api/options-chat/sessions/${sessionId}/messages`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content, stream: true }),
    });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Please sign in");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || data.message || `Chat failed (${res.status})`);
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error("Streaming is not supported in this browser");
    const decoder = new TextDecoder();
    let buffer = "";
    let finalAssistant = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l.startsWith("data:"));
        if (!line) continue;
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (event.type === "token") {
          streamingAssistant.content += event.content || "";
          renderOptionsChatMessages();
        } else if (event.type === "done") {
          finalAssistant = event.assistant_message;
          if (event.session) {
            const idx = state.optionsChatSessions.findIndex((s) => s.id === event.session.id);
            if (idx >= 0) state.optionsChatSessions[idx] = event.session;
            else state.optionsChatSessions.unshift(event.session);
          }
        } else if (event.type === "error") {
          throw new Error(event.error || "Chat failed");
        }
      }
    }

    if (finalAssistant) {
      state.optionsChatMessages = [
        ...state.optionsChatMessages.filter((m) => m !== optimisticUser && m !== streamingAssistant),
        { ...optimisticUser, id: finalAssistant.id ? `user-before-${finalAssistant.id}` : optimisticUser.id },
        finalAssistant,
      ];
      // Reload authoritative history so user message id/title stay in sync.
      await selectOptionsChatSession(sessionId);
    } else if (streamingAssistant.content) {
      streamingAssistant.streaming = false;
      renderOptionsChatMessages();
      await selectOptionsChatSession(sessionId);
    } else {
      throw new Error("No reply received");
    }
  } catch (err) {
    state.optionsChatMessages = state.optionsChatMessages.filter(
      (m) => m !== optimisticUser && m !== streamingAssistant
    );
    renderOptionsChatMessages();
    if ($("#options-chat-input") && !$("#options-chat-input").value) {
      $("#options-chat-input").value = content;
    }
    toast(err.message || "Chat failed", "error");
  } finally {
    setOptionsChatSending(false);
    renderOptionsChatSessions();
  }
}

$("#options-chat-session-list")?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-session-id]");
  if (!btn) return;
  selectOptionsChatSession(btn.dataset.sessionId).catch((e) => toast(e.message, "error"));
});

$("#options-chat-messages")?.addEventListener("click", (event) => {
  const pinBtn = event.target.closest(".options-chat-pin-whole");
  if (pinBtn?.dataset.messageId) {
    const msg = (state.optionsChatMessages || []).find((m) => m.id === pinBtn.dataset.messageId);
    if (msg?.content) {
      pinChatExcerpt(msg.content, msg.id)
        .then(() => toast("Pinned for next analysis"))
        .catch((e) => toast(e.message, "error"));
    }
    return;
  }
  const btn = event.target.closest("[data-starter-index]");
  if (!btn) return;
  const text = state.optionsChatStarters[Number(btn.dataset.starterIndex)];
  if (text) sendOptionsChatMessage(text).catch((e) => toast(e.message, "error"));
});

$("#options-chat-messages")?.addEventListener("mouseup", (event) => {
  const sel = window.getSelection();
  const text = sel?.toString().trim();
  if (!text) {
    hideChatSelectionToolbar();
    return;
  }
  const bubble = event.target.closest(".options-chat-bubble");
  const wrap = $("#options-chat-messages");
  if (!bubble || !wrap?.contains(bubble)) {
    hideChatSelectionToolbar();
    return;
  }
  const range = sel?.rangeCount ? sel.getRangeAt(0) : null;
  if (!range || !bubble.contains(range.commonAncestorContainer)) {
    hideChatSelectionToolbar();
    return;
  }
  const rect = range.getBoundingClientRect();
  showChatSelectionToolbar(rect.left, rect.top, text, bubble.dataset.messageId || null);
});

document.addEventListener("mousedown", (event) => {
  const toolbar = $("#chat-selection-toolbar");
  if (!toolbar || toolbar.classList.contains("hidden")) return;
  if (toolbar.contains(event.target)) return;
  if (event.target.closest(".options-chat-bubble")) return;
  hideChatSelectionToolbar();
});

$("#chat-selection-toolbar")?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-action]");
  if (!btn) return;
  const { excerpt, messageId } = state.chatSelectionContext;
  const action = btn.dataset.action;
  if (!excerpt) return;
  if (action === "pin-excerpt") {
    pinChatExcerpt(excerpt, messageId)
      .then(() => toast("Pinned for next analysis"))
      .catch((e) => toast(e.message, "error"));
    hideChatSelectionToolbar();
    return;
  }
  if (action === "save-excerpt-library") {
    saveExcerptToLibrary(excerpt, messageId).catch((e) => toast(e.message, "error"));
    hideChatSelectionToolbar();
    return;
  }
  if (action === "copy-excerpt") {
    navigator.clipboard?.writeText(excerpt).then(() => toast("Copied"));
    hideChatSelectionToolbar();
    return;
  }
  if (action === "send-excerpt-ingest") {
    sendExcerptToIngest(excerpt, "Chat excerpt");
    hideChatSelectionToolbar();
  }
});

$("#options-chat-observations-list")?.addEventListener("click", (event) => {
  const saveBtn = event.target.closest(".chat-obs-save-lib");
  if (saveBtn?.dataset.id) {
    saveChatObservationToLibrary(saveBtn.dataset.id)
      .then(() => toast("Saved to library"))
      .catch((e) => toast(e.message, "error"));
    return;
  }
  const delBtn = event.target.closest(".chat-obs-delete");
  if (delBtn?.dataset.id) {
    api(`/api/options-chat/observations/${encodeURIComponent(delBtn.dataset.id)}`, { method: "DELETE" })
      .then(() => loadChatObservations())
      .then(() => toast("Removed"))
      .catch((e) => toast(e.message, "error"));
  }
});

$("#options-chat-observations-list")?.addEventListener("change", (event) => {
  const input = event.target.closest(".chat-obs-include");
  if (!input?.dataset.id) return;
  api(`/api/options-chat/observations/${encodeURIComponent(input.dataset.id)}`, {
    method: "PATCH",
    body: JSON.stringify({ include_in_analysis: input.checked }),
  })
    .then(() => loadChatObservations())
    .catch((e) => toast(e.message, "error"));
});

$("#options-chat-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  sendOptionsChatMessage($("#options-chat-input")?.value || "").catch((e) => toast(e.message, "error"));
});

$("#options-chat-input")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendOptionsChatMessage($("#options-chat-input")?.value || "").catch((e) => toast(e.message, "error"));
  }
});

safeOn("#btn-options-chat-new", "click", () =>
  createOptionsChatSession().catch((e) => toast(e.message, "error"))
);
safeOn("#btn-options-chat-delete", "click", () =>
  deleteOptionsChatSession().catch((e) => toast(e.message, "error"))
);

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
