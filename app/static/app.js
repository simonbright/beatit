const state = {
  documents: [],
  documentIndex: [],
  libraryPage: 1,
  libraryFilter: "",
  libraryTotal: 0,
  libraryCounts: {},
  libraryView: "documents",
  diagImportSourceDocumentId: null,
  handlingFlags: { items: [], count: 0, critical_count: 0 },
  homeSection: "assessment",
  settingsSection: "patients",
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
  activePatientId: null,
  activeCaseId: null,
  diagnosticPresets: [],
  journalPresets: [],
  milestonePresets: [],
  journalDraft: { kind: "note", label: "", severity: null },
  diagMilestonePrefs: { enabled: true, selected: null },
  diagStatusFilter: "all",
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
$("#btn-howto")?.addEventListener("click", () => {
  switchTab("howto");
  window.scrollTo({ top: 0, behavior: "smooth" });
});
document.querySelectorAll("[data-library-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.libraryView;
    if (!view) return;
    if (!$("#panel-library")?.classList.contains("active")) {
      switchTab("library", { libraryView: view, skipLibraryLoad: view === "imaging" });
      return;
    }
    setLibraryView(view);
  });
});
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
    const files = Array.from(input.files || []);
    if (label) {
      let nameEl = label.querySelector(".file-name");
      if (!files.length) {
        nameEl?.remove();
        if (titleInput && titleInput.dataset.userEdited !== "1") titleInput.value = "";
        return;
      }
      if (!nameEl) {
        nameEl = document.createElement("span");
        nameEl.className = "file-name";
        label.appendChild(nameEl);
      }
      nameEl.textContent =
        files.length === 1
          ? `Selected: ${files[0].name}`
          : `Selected: ${files.length} PDFs`;
    }
    if (titleInput && titleInput.dataset.userEdited !== "1") {
      titleInput.value = files.length === 1 ? defaultPdfTitle(files[0]) : "";
      titleInput.disabled = files.length > 1;
      titleInput.placeholder =
        files.length > 1
          ? "Uses each PDF filename"
          : "Defaults to each PDF filename";
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
  ["#btn-scope-select-all", "#btn-scope-clear", "#btn-scope-match-last", "#btn-scope-library",
   "#btn-scope-main-sources", "#btn-scope-type-text", "#btn-scope-type-pdf", "#btn-scope-new-uploads",
   "#btn-lib-main-sources", "#btn-lib-type-text", "#btn-lib-type-pdf", "#btn-lib-new-uploads"].forEach((sel) => {
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
  refreshActivePatientProfile().catch(() => {});
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
  setHomeSection("assessment", { scroll: true });
}

function scrollToOpenItems() {
  if (!$("#panel-analyze")?.classList.contains("active")) {
    switchTab("analyze");
  }
  requestAnimationFrame(() => {
    setHomeSection("gaps", { scroll: true });
  });
}

function getStickyHeaderOffset(extra = 16) {
  const header = document.querySelector(".header");
  const subnav = document.querySelector(
    "#panel-analyze.active #home-subnav, #panel-settings.active #settings-subnav"
  );
  let height = header ? header.getBoundingClientRect().height : 96;
  if (subnav) height += subnav.getBoundingClientRect().height;
  return height + extra;
}

function syncStickyHeaderOffset() {
  const header = document.querySelector(".header");
  const h = header ? Math.ceil(header.getBoundingClientRect().height) : 88;
  document.documentElement.style.setProperty("--sticky-header-offset", `${h}px`);
}

function initHeaderCollapse() {
  const mq = window.matchMedia("(max-width: 600px)");
  const expandAt = 8;
  const collapseAt = 40;
  let compact = false;

  const apply = (next) => {
    if (!mq.matches) {
      if (document.body.classList.contains("header-compact")) {
        document.body.classList.remove("header-compact");
        compact = false;
        syncStickyHeaderOffset();
      }
      return;
    }
    if (next === compact) return;
    compact = next;
    document.body.classList.toggle("header-compact", next);
    syncStickyHeaderOffset();
  };

  const onScroll = () => {
    if (!mq.matches) {
      apply(false);
      return;
    }
    const y = window.scrollY || window.pageYOffset || 0;
    if (!compact && y > collapseAt) apply(true);
    else if (compact && y <= expandAt) apply(false);
  };

  window.addEventListener("scroll", onScroll, { passive: true });
  if (typeof mq.addEventListener === "function") mq.addEventListener("change", onScroll);
  else if (typeof mq.addListener === "function") mq.addListener(onScroll);
  onScroll();
  syncStickyHeaderOffset();
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
  const show = hasAssessment && onHome;
  $("#btn-export-pdf")?.classList.toggle("hidden", !show);
  $("#btn-export-pdf-icon")?.classList.toggle("hidden", !show);
}

function updateHomeWorkflow() {
  updateHomeToolbar();
  renderHandlingAlerts();
}

async function refreshHandlingFlags({ rescan = false } = {}) {
  try {
    const data = rescan
      ? await api("/api/handling/refresh", { method: "POST", timeoutMs: 120000 })
      : await api("/api/handling/flagged");
    state.handlingFlags = {
      items: data.items || [],
      count: data.count || 0,
      critical_count: data.critical_count || 0,
    };
  } catch {
    state.handlingFlags = { items: [], count: 0, critical_count: 0 };
  }
  renderHandlingAlerts();
  renderFlaggedList();
  return state.handlingFlags;
}

function renderHandlingAlerts() {
  const banner = $("#handling-alerts");
  const text = $("#handling-alerts-text");
  const countEl = $("#flagged-subtab-count");
  const flags = state.handlingFlags || { items: [], count: 0, critical_count: 0 };
  const count = flags.count || 0;
  const critical = flags.critical_count || 0;

  if (countEl) {
    if (count > 0) {
      countEl.textContent = String(count);
      countEl.classList.remove("hidden");
      countEl.classList.toggle("is-critical", critical > 0);
    } else {
      countEl.textContent = "";
      countEl.classList.add("hidden");
      countEl.classList.remove("is-critical");
    }
  }

  if (!banner || !text) return;
  const onHome = $("#panel-analyze")?.classList.contains("active");
  const onFlagged = state.homeSection === "flagged";
  if (!onHome || !count || onFlagged) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  banner.classList.toggle("handling-alerts-critical", critical > 0);
  if (critical > 0) {
    text.textContent =
      count === 1
        ? "1 lab or diagnostic report still needs handling before it can be trusted in analysis."
        : `${count} lab/diagnostic reports still need handling (${critical} critical).`;
  } else {
    text.textContent =
      count === 1
        ? "1 report is flagged for review."
        : `${count} reports are flagged for review.`;
  }
}

function renderFlaggedList() {
  const list = $("#flagged-list");
  if (!list) return;
  const items = state.handlingFlags?.items || [];
  if (!items.length) {
    list.innerHTML = `<p class="muted small">No flagged reports. Labs and diagnostic PDFs are clear.</p>`;
    return;
  }
  list.innerHTML = items
    .map((item) => {
      const severity = item.severity === "critical" ? "critical" : "warning";
      const kind = item.kind_label
        ? `<span class="badge badge-report-kind">${escapeHtml(item.kind_label)}</span>`
        : "";
      const reasons = (item.reason_labels || [])
        .map((r) => `<span class="flagged-reason">${escapeHtml(r)}</span>`)
        .join("");
      const actions = (item.actions || [])
        .map((a) => {
          const cls =
            a.id === "dismiss"
              ? "btn ghost btn-sm"
              : a.id === "import_labs" || a.id === "reextract"
                ? "btn primary btn-sm"
                : "btn secondary btn-sm";
          return `<button type="button" class="${cls} btn-flagged-action" data-action="${escapeHtml(a.id)}" data-id="${escapeHtml(item.document_id)}">${escapeHtml(a.label)}</button>`;
        })
        .join("");
      return `<article class="flagged-item flagged-${severity}" data-id="${escapeHtml(item.document_id)}">
        <div class="flagged-item-main">
          <div class="flagged-item-heading">
            <strong>${escapeHtml(item.title || "Untitled")}</strong>
            ${kind}
            <span class="badge badge-flag-${severity}">${severity === "critical" ? "Needs handling" : "Review"}</span>
          </div>
          <p class="flagged-item-message">${escapeHtml(item.message || "Needs review")}</p>
          <div class="flagged-reasons">${reasons}</div>
        </div>
        <div class="flagged-item-actions">${actions}</div>
      </article>`;
    })
    .join("");
}

async function handleFlaggedAction(action, docId) {
  if (!docId) return;
  if (action === "view") {
    await viewDocument(docId);
    return;
  }
  if (action === "reextract") {
    await reextractDocument(docId);
    await refreshHandlingFlags();
    return;
  }
  if (action === "import_labs") {
    toast("Review the readings, then Add selected — that clears the flag");
    await importDiagnosticsFromLibraryDocument(docId);
    return;
  }
  if (action === "dismiss") {
    if (!confirm("Dismiss this flag? Only do this after you have reviewed the report.")) return;
    await api(`/api/documents/${encodeURIComponent(docId)}/handling/dismiss`, {
      method: "POST",
    });
    toast("Flag dismissed");
    await refreshHandlingFlags({ rescan: true });
  }
}

function notifyLabImportResult(labImport, { fallbackToast, handling } = {}) {
  const flagged =
    labImport?.flagged ||
    handling?.status === "flagged" ||
    (labImport?.handling && labImport.handling.status === "flagged");
  if (!labImport) {
    if (flagged) {
      toast("Report flagged — review under Flagged", "error");
      refreshHandlingFlags().then(() => setHomeSection("flagged", { scroll: true }));
      return;
    }
    if (fallbackToast) toast(fallbackToast);
    return;
  }
  const n = labImport.added_count || 0;
  const title = labImport.document_title || "lab report";
  if (labImport.already_on_profile || (n === 0 && !flagged && labImport.skipped_duplicate > 0)) {
    toast(`Lab readings already on charts for ${title}`);
    refreshHandlingFlags({ rescan: true }).then(() => {
      switchTab("analyze");
      setHomeSection("flagged", { scroll: true });
    });
    return;
  }
  if (n > 0 && !flagged) {
    toast(`Added ${n} lab reading${n === 1 ? "" : "s"} from ${title}`);
    if (typeof applyProfileResponse === "function") {
      try {
        applyProfileResponse(labImport);
      } catch {
        /* ignore */
      }
    }
    switchTab("analyze");
    if (typeof setHomeSection === "function") {
      setHomeSection("diagnostics", { scroll: true });
    }
    refreshHandlingFlags().catch(() => {});
    return;
  }
  if (n > 0 && flagged) {
    toast(`Added ${n} reading(s) from ${title} — some items still need review`, "error");
    if (typeof applyProfileResponse === "function") {
      try {
        applyProfileResponse(labImport);
      } catch {
        /* ignore */
      }
    }
    refreshHandlingFlags().then(() => {
      switchTab("analyze");
      setHomeSection("flagged", { scroll: true });
    });
    return;
  }
  toast(
    flagged
      ? `Lab report flagged — open Flagged to Import to Labs or re-extract`
      : labImport.offer_manual_import
        ? "Tagged as lab report — open Flagged or Import to Labs to finish"
        : fallbackToast || "Lab report processed",
    "error"
  );
  refreshHandlingFlags().then(() => {
    switchTab("analyze");
    setHomeSection("flagged", { scroll: true });
  });
}

function renderHomeState(hasAssessment) {
  $("#analyze-results-section")?.classList.toggle("hidden", !hasAssessment);
  $("#home-assessment-empty")?.classList.toggle("hidden", Boolean(hasAssessment));
  $("#analyze-actions-card")?.classList.toggle("analyze-actions-secondary", hasAssessment);
  if (hasAssessment && !state.analysisRunning) {
    setAnalyzeActionsExpanded(false);
  } else if (!hasAssessment) {
    setAnalyzeActionsExpanded(true);
  }
  renderAnalysisRunChrome();
  updateHomeToolbar();
  if (!hasAssessment && state.homeSection === "assessment") {
    // keep assessment pane visible with empty state
  } else if (hasAssessment && state.homeSection === "run" && !state.analysisRunning) {
    // leave user on Run if they navigated there
  }
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
  const lastIds = state.latestAnalysis?.document_ids || [];
  const { newIds } = state.latestAnalysis
    ? partitionNextScopeIds(nextIds, lastIds)
    : { newIds: [] };
  const newBit = newIds.length ? ` · ${newIds.length} new since last run` : "";
  if (usingAll) {
    el.textContent = breakdown
      ? `All ${total} library documents · ${breakdown}${newBit}`
      : `All ${total} library documents${newBit}`;
  } else {
    el.textContent = breakdown
      ? `${next.count} selected · ${breakdown}${newBit}`
      : `${next.count} selected${newBit}`;
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
    const openAdd = btn.dataset.howtoOpenAdd === "1";
    switchTab(target, openAdd ? { openAdd: true } : {});
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
  // Legacy: Add data tab folded into Library
  if (name === "ingest") {
    name = "library";
    options = { ...options, openAdd: true };
  }
  // Imaging is a Library sub-view
  if (name === "imaging") {
    name = "library";
    options = { ...options, libraryView: "imaging" };
  }
  if (!VALID_TABS.has(name)) return;
  const navTab = MAIN_NAV_TABS.has(name) ? name : null;
  $$(".tab").forEach((t) => {
    const active = navTab != null && t.dataset.tab === navTab;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
  if (!options.skipTabSave) {
    persistActiveTab(name === "howto" ? "howto" : navTab || name);
  }
  if (name === "library") {
    if (!options.skipLibraryLoad) {
      loadDocuments().catch((e) => toast(e.message, "error"));
    }
    setLibraryView(options.libraryView || state.libraryView || "documents");
    if (options.openAdd) openLibraryAddPanel();
  }
  if (name === "history") loadHistory();
  if (name === "analyze") {
    loadLatestAssessment();
    loadChatObservations().catch(() => {});
    refreshHandlingFlags().catch(() => {});
    setHomeSection(state.homeSection || "assessment");
  }
  if (name === "options-chat") loadOptionsChatPanel();
  if (name === "custom-tasks") {
    loadCustomTasks();
    // Refresh last safety result from current profile if we have a patient
    if (state.activePatientId) {
      fetch(`/api/patients/${state.activePatientId}/medications/safety-review`, { credentials: "include" })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data?.medication_safety) renderMedSafetyResult(data.medication_safety);
        })
        .catch(() => {});
    }
  }
  if (name === "settings") {
    loadSettings();
    setSettingsSection(options.settingsSection || state.settingsSection || "patients", {
      focusSelector: options.settingsFocus || null,
      scroll: Boolean(options.settingsFocus),
    });
  }
  updateHomeToolbar();
  syncStickyHeaderOffset();
}

function setLibraryView(view) {
  const next = view === "imaging" ? "imaging" : "documents";
  state.libraryView = next;
  document.querySelectorAll("[data-library-view]").forEach((btn) => {
    const active = btn.dataset.libraryView === next;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  $("#library-view-documents")?.classList.toggle("hidden", next !== "documents");
  $("#library-view-imaging")?.classList.toggle("hidden", next !== "imaging");
  if (next === "imaging") {
    loadImagingPanel().catch((e) => toast(e.message, "error"));
  }
}

const HOME_SECTIONS = new Set([
  "assessment",
  "journal",
  "medications",
  "diagnostics",
  "flagged",
  "gaps",
  "run",
]);
const SETTINGS_SECTIONS = new Set(["patients", "profile", "analysis", "labels", "llm", "audit"]);

function setHomeSection(section, { scroll = false } = {}) {
  const next = HOME_SECTIONS.has(section) ? section : "assessment";
  const changed = state.homeSection !== next;
  state.homeSection = next;
  document.querySelectorAll("#home-subnav [data-home-section]").forEach((btn) => {
    const active = btn.dataset.homeSection === next;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll("[data-home-pane]").forEach((pane) => {
    pane.classList.toggle("hidden", pane.dataset.homePane !== next);
  });
  syncStickyHeaderOffset();
  if (changed && (next === "diagnostics" || next === "journal" || next === "medications")) {
    refreshActivePatientProfile().catch(() => {});
  }
  if (changed && next === "flagged") {
    refreshHandlingFlags().catch(() => {});
  }
  if (scroll) {
    requestAnimationFrame(() => {
      const pane = $(`[data-home-pane="${next}"]`);
      scrollToElement(pane || $("#home-subnav"));
    });
  }
}

function setSettingsSection(section, { scroll = false, focusSelector = null } = {}) {
  const next = SETTINGS_SECTIONS.has(section) ? section : "patients";
  state.settingsSection = next;
  document.querySelectorAll("#settings-subnav [data-settings-section]").forEach((btn) => {
    const active = btn.dataset.settingsSection === next;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll("[data-settings-pane]").forEach((pane) => {
    pane.classList.toggle("hidden", pane.dataset.settingsPane !== next);
  });
  syncStickyHeaderOffset();
  if (scroll || focusSelector) {
    requestAnimationFrame(() => {
      const target = focusSelector ? $(focusSelector) : $(`[data-settings-pane="${next}"]`);
      scrollToElement(target || $("#settings-subnav"));
      if (focusSelector) $(focusSelector)?.focus?.();
    });
  }
}

function openLibraryAddPanel({ focusText = false } = {}) {
  setLibraryView("documents");
  const panel = document.getElementById("library-add-panel");
  if (!panel) return;
  panel.open = true;
  requestAnimationFrame(() => {
    scrollToElement(panel);
    if (focusText) $("#text-content")?.focus();
  });
}

function closeLibraryAddPanel() {
  const panel = document.getElementById("library-add-panel");
  if (panel) panel.open = false;
}

function applyTabUi(name) {
  if (name === "imaging") name = "library";
  if (name === "ingest") name = "library";
  if (!VALID_TABS.has(name)) return;
  const navTab = MAIN_NAV_TABS.has(name) ? name : null;
  $$(".tab").forEach((t) => {
    const active = navTab != null && t.dataset.tab === navTab;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
}

function readSavedTabName() {
  const hash = window.location.hash.replace(/^#/, "").trim();
  if (hash === "ingest") return "library";
  if (hash && VALID_TABS.has(hash)) return hash;
  try {
    const saved = sessionStorage.getItem(TAB_STORAGE_KEY);
    if (saved === "ingest") return "library";
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
  "library",
  "imaging",
  "history",
  "howto",
  "settings",
]);
const MAIN_NAV_TABS = new Set([
  "analyze",
  "options-chat",
  "custom-tasks",
  "library",
  "history",
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
  chat_observation: "Chat observation",
  "kind:lab": "Lab reports",
  "kind:mri": "MRI reports",
  "kind:ultrasound": "Ultrasound reports",
  "kind:ct": "CT reports",
  "kind:pathology": "Pathology reports",
  "kind:cardiology": "Cardiology reports",
  "kind:other_report": "Clinical reports",
};

function libraryTypeLabel(type) {
  return LIBRARY_TYPE_LABELS[type] || type || "Unknown";
}

function clinicalReportKindBadge(doc) {
  const info = doc?.source_info || {};
  const meta = doc?.metadata || {};
  const kind = info.report_kind || meta.clinical_report_kind;
  if (!kind || kind === "unknown") return "";
  const label =
    info.report_kind_label ||
    meta.clinical_report_kind_label ||
    libraryTypeLabel(`kind:${kind}`) ||
    kind;
  return `<span class="badge badge-report-kind" title="Clinical report type">${escapeHtml(label)}</span>`;
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
  const hasPrior = Boolean(state.latestAnalysis);

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
  // Only show selected/excluded when there is a prior assessment to compare against
  if (hasPrior && inc.explicitSelection && inc.inNextScope && !inc.inLastAssessment && !isNew) {
    badges.push(
      '<span class="doc-status-badge doc-status-selected" title="Selected for the next analysis run">Selected for next run</span>'
    );
  } else if (hasPrior && inc.explicitSelection && !inc.inNextScope) {
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

function stripExecutiveSummarySection(response) {
  /** Remove the executive summary block from full assessment text (shown separately above). */
  if (!response) return "";
  const lines = String(response).split(/\r?\n/);
  const result = [];
  let skipping = false;
  let sawExecHeader = false;
  const normalize = (h) =>
    String(h || "")
      .toLowerCase()
      .replace(/[^a-z0-9 ]+/g, "")
      .trim();
  const isExec = (h) => h.includes("executive summary") || h === "1 executive summary";
  const plainSection =
    /^(?:what we know|what we do not know|uncertainties|critical gaps|staging(?:\s*&\s*|\s+and\s+)?workup|clinical status(?:\s*&\s*|\s+and\s+)?workup|treatment options|next steps|open items|questions for(?:\s+the)?\s+(?:oncology|cardiology|neurology|care)|questions for(?:\s+the)?\s+\w+\s+team|disclaimer|full assessment|latest assessment)\b/i;

  for (const line of lines) {
    const stripped = line.trim();
    let header = null;
    const md = stripped.match(/^(?:#{1,3}\s*|\d+\.\s+)(.+)$/);
    if (md) header = normalize(md[1]);
    else if (plainSection.test(stripped) && stripped.length < 80) header = normalize(stripped);

    if (header != null) {
      if (isExec(header)) {
        skipping = true;
        sawExecHeader = true;
        continue;
      }
      skipping = false;
    }
    if (!skipping) result.push(line);
  }

  const out = result.join("\n").trim();
  if (sawExecHeader && out.length < Math.max(200, Math.floor(String(response).length * 0.15))) {
    return String(response).trim();
  }
  return out;
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
      (total
        ? `<p class="muted small">All library documents will be included.</p>`
        : "");
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

  updateScopeQuickButtons();
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
  setHomeSection("run", { scroll: false });
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

function documentIdsOfTypes(sourceTypes) {
  const wanted = new Set(sourceTypes || []);
  if (!wanted.size) return [];
  return state.documentIndex
    .filter((doc) => wanted.has(doc.source_type))
    .map((doc) => doc.id);
}

function newDocumentIdsSinceLastAssessment() {
  const lastIds = new Set(state.latestAnalysis?.document_ids || []);
  if (!lastIds.size && !state.latestAnalysis) {
    return state.documentIndex.map((doc) => doc.id);
  }
  return state.documentIndex
    .filter((doc) => !lastIds.has(doc.id))
    .map((doc) => doc.id);
}

function selectDocumentIds(ids, { replace = false } = {}) {
  if (replace) state.selectedIds.clear();
  ids.forEach((id) => state.selectedIds.add(id));
  saveSelectionToSession();
  renderDocuments();
  updateSelectedLabel();
  renderAnalysisScopeSummary();
}

function selectDocumentsOnPage() {
  selectDocumentIds(state.documents.map((doc) => doc.id));
  toast(`Selected ${state.documents.length} on this page`);
}

function selectDocumentsByType(sourceType, { replace = true } = {}) {
  const ids = documentIdsOfType(sourceType);
  if (!ids.length) {
    toast(`No ${libraryTypeLabel(sourceType).toLowerCase()} documents to select`, "error");
    return;
  }
  selectDocumentIds(ids, { replace });
  toast(
    `Selected ${ids.length} ${libraryTypeLabel(sourceType).toLowerCase()} document${ids.length === 1 ? "" : "s"}`
  );
}

function selectDocumentsByTypes(sourceTypes, { replace = true, label = null } = {}) {
  const ids = documentIdsOfTypes(sourceTypes);
  const typeLabel =
    label ||
    sourceTypes.map((t) => libraryTypeLabel(t).toLowerCase()).join(" + ");
  if (!ids.length) {
    toast(`No ${typeLabel} documents to select`, "error");
    return;
  }
  selectDocumentIds(ids, { replace });
  const breakdown = formatScopeBreakdown(scopeSummaryFromIds(ids).byType);
  toast(`Selected ${ids.length} · ${breakdown || typeLabel}`);
}

function selectMainSources() {
  selectDocumentsByTypes(["text", "pdf"], {
    replace: true,
    label: "clinical notes + PDFs",
  });
}

function selectNewSinceLastAssessment({ includePrior = true } = {}) {
  const newIds = newDocumentIdsSinceLastAssessment();
  if (!newIds.length) {
    toast("No new documents since the current assessment", "error");
    return;
  }
  let ids = newIds;
  if (includePrior && state.latestAnalysis?.document_ids?.length) {
    const combined = new Set(state.latestAnalysis.document_ids);
    newIds.forEach((id) => combined.add(id));
    ids = [...combined];
  }
  selectDocumentIds(ids, { replace: true });
  const newCount = newIds.length;
  const priorCount = Math.max(0, ids.length - newCount);
  toast(
    priorCount
      ? `Selected last assessment (${priorCount}) + ${newCount} new upload${newCount === 1 ? "" : "s"}`
      : `Selected ${newCount} new upload${newCount === 1 ? "" : "s"}`
  );
  scrollToAssessmentScope();
}

function selectAllDocuments() {
  const ids = state.documentIndex.map((doc) => doc.id);
  if (!ids.length) return toast("No documents to select", "error");
  selectDocumentIds(ids, { replace: true });
  toast(`Selected all ${ids.length} documents`);
}

function selectAllShownDocuments() {
  const filterType = state.libraryFilter || "";
  if (filterType) {
    selectDocumentsByType(filterType, { replace: true });
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
  renderAnalysisScopeSummary();
  toast("Selection cleared — assessments will use all documents");
}

function updateScopeQuickButtons() {
  const counts = state.libraryCounts || {};
  const textCount = counts.text || documentIdsOfType("text").length;
  const pdfCount = counts.pdf || documentIdsOfType("pdf").length;
  const mainCount = textCount + pdfCount;
  const newIds = state.latestAnalysis ? newDocumentIdsSinceLastAssessment() : [];
  const newCount = newIds.length;

  const setBtn = (sel, enabled, label = null) => {
    const btn = $(sel);
    if (!btn) return;
    btn.disabled = !enabled || state.analysisRunning;
    if (label != null) btn.textContent = label;
  };

  setBtn("#btn-scope-main-sources", mainCount > 0, `Notes + PDFs (${mainCount})`);
  setBtn("#btn-scope-type-text", textCount > 0, `Clinical notes (${textCount})`);
  setBtn("#btn-scope-type-pdf", pdfCount > 0, `PDFs (${pdfCount})`);
  setBtn("#btn-lib-main-sources", mainCount > 0, `Notes + PDFs (${mainCount})`);
  setBtn("#btn-lib-type-text", textCount > 0, `Clinical notes (${textCount})`);
  setBtn("#btn-lib-type-pdf", pdfCount > 0, `PDFs (${pdfCount})`);

  ["#btn-scope-new-uploads", "#btn-lib-new-uploads"].forEach((sel) => {
    const btn = $(sel);
    if (!btn) return;
    const show = Boolean(state.latestAnalysis) && newCount > 0;
    btn.classList.toggle("hidden", !show);
    btn.disabled = !show || state.analysisRunning;
    if (show) {
      btn.textContent = `Last run + ${newCount} new`;
      btn.title =
        "Keep documents from the current assessment and add uploads that are not in it yet";
    }
  });
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
    libraryBaselineBtn.disabled = state.analysisRunning || !total;
  }

  updateScopeQuickButtons();
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
  const allCount = Object.entries(counts)
    .filter(([type]) => !String(type).startsWith("kind:"))
    .reduce((a, [, b]) => a + b, 0);
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
  const editable = doc.is_active_case !== false;
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
  const metaNeedsOcr = Boolean(meta.needs_ocr) || String(meta.extraction_method || "") === "empty";
  const ocrBadge = metaNeedsOcr
    ? `<span class="badge badge-warn" title="Scanned/image PDF — little or no text extracted">Needs OCR</span>`
    : meta.extraction_method === "ocr"
      ? `<span class="badge" title="Text recovered with OCR">OCR</span>`
      : "";
  const reportBadge = clinicalReportKindBadge(doc);
  const handlingBadge =
    String(meta.handling_status || "").toLowerCase() === "flagged"
      ? `<span class="badge badge-flagged" title="${escapeHtml(meta.handling_message || "Needs handling")}">Flagged</span>`
      : "";
  const reextractBtn =
    editable && String(doc.source_type || "").toLowerCase() === "pdf"
      ? `<button type="button" class="btn ghost btn-reextract" data-id="${doc.id}" title="Re-run text extraction / OCR">Re-extract</button>`
      : "";
  const replaceBtn =
    editable && String(doc.source_type || "").toLowerCase() === "pdf"
      ? `<button type="button" class="btn ghost btn-replace-file" data-id="${doc.id}" title="Re-upload the PDF if the stored file is missing">Replace file</button><input type="file" class="hidden doc-replace-file-input" data-id="${doc.id}" accept=".pdf,application/pdf">`
      : "";
  const deleteBtn = editable
    ? `<button class="btn danger btn-delete" data-id="${doc.id}">Delete</button>`
    : `<span class="muted small">Switch focus to edit</span>`;
  return `
    <article class="doc-item ${selected ? "selected" : ""}${compact ? " doc-item-compact" : ""}${newClass}${editable ? "" : " doc-item-readonly"}" data-id="${doc.id}">
      <div class="doc-item-heading">
        <label class="doc-select-check" title="Include in assessment">
          <input type="checkbox" class="doc-select-input" data-id="${doc.id}"${selected ? " checked" : ""}>
        </label>
        ${sourceBadge}
        <strong>${escapeHtml(displayName)}</strong>
        ${reportBadge}
        ${handlingBadge}
        ${ocrBadge}
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
        ${reextractBtn}
        ${replaceBtn}
        <button class="btn secondary btn-select" data-id="${doc.id}">
          ${selected ? "Deselect" : "Select for analysis"}
        </button>
        ${deleteBtn}
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
    list.innerHTML = `<p class="muted">No DICOM or imaging files yet. Use <strong>Add documents</strong> in Library to upload a study folder.</p>`;
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
  $("#library-view-imaging")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function goToImagingPanel() {
  switchTab("library", { libraryView: "imaging", skipLibraryLoad: true });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function goToLibraryForImagingFilter() {
  goToImagingPanel();
}

function goToLibraryImagingType() {
  switchTab("library", { libraryView: "documents", skipLibraryLoad: true });
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
  if ($("#library-view-imaging") && !$("#library-view-imaging").classList.contains("hidden")) {
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
  // Keep add panel open so multi-upload workflows stay convenient
  openLibraryAddPanel();
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
  setHomeSection("gaps");
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
    const fullText =
      responseDisplay && !summaryPick.usedFallback
        ? stripExecutiveSummarySection(responseDisplay)
        : "";
    if (fullText) {
      fullCard.classList.remove("hidden");
      fullBody.innerHTML = formatNumberedReferences(fullText, state.referenceRegistry, refPrefix);
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

  const btns = [$("#btn-export-pdf"), $("#btn-export-pdf-icon")].filter(Boolean);
  btns.forEach((btn) => {
    btn.disabled = true;
  });

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
    btns.forEach((btn) => {
      btn.disabled = false;
    });
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
      ${clinicalReportKindBadge(doc)}
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
    const hasFile = Boolean(data.has_file);
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
    const isPdf = String(doc.source_type || "").toLowerCase() === "pdf";
    if (isPdf) {
      links.push(
        `<button type="button" class="btn secondary btn-reextract" data-id="${escapeHtml(doc.id)}">Re-extract / OCR</button>`
      );
      links.push(
        `<button type="button" class="btn secondary btn-replace-file" data-id="${escapeHtml(doc.id)}">Replace file</button>`
      );
      links.push(
        `<input type="file" class="hidden doc-replace-file-input" data-id="${escapeHtml(doc.id)}" accept=".pdf,application/pdf">`
      );
    }
    const canImportLabs =
      isPdf ||
      String(data.view_kind || "").toLowerCase() === "pdf" ||
      String(data.view_kind || "").toLowerCase() === "image";
    if (canImportLabs && state.activePatientId) {
      links.push(
        `<button type="button" class="btn primary btn-import-labs" data-id="${escapeHtml(doc.id)}">Import to Labs</button>`
      );
    }
    const meta = doc.metadata || {};
    if (!hasFile && isPdf) {
      links.push(
        `<span class="muted small doc-file-missing-hint">Original PDF is missing on disk — use <strong>Replace file</strong> to upload it again, then Import to Labs.</span>`
      );
    } else if (meta.needs_ocr || meta.extraction_method === "empty") {
      links.push(
        `<span class="muted small">This looks like a scanned/image PDF. Re-extract runs OCR so analysis and chat can read it.</span>`
      );
    } else if (meta.extraction_method === "ocr") {
      links.push(`<span class="muted small">Text was recovered with OCR (${meta.extracted_chars || "?"} chars).</span>`);
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
  refreshHandlingFlags().catch(() => {});
}

async function reextractDocument(id) {
  await withBackgroundTask({
    id: `reextract-${id}-${Date.now()}`,
    label: "Re-extracting PDF / OCR…",
    run: async ({ setDetail }) => {
      setDetail("Running text extraction and OCR if needed…");
      const data = await api(`/api/documents/${encodeURIComponent(id)}/reextract`, {
        method: "POST",
        timeoutMs: 600000,
      });
      const method = data.document?.metadata?.extraction_method || "unknown";
      const needs = data.document?.metadata?.needs_ocr;
      const handling = data.handling || data.document?.handling;
      if (data.lab_import || handling?.status === "flagged") {
        notifyLabImportResult(data.lab_import, {
          fallbackToast: needs
            ? "Still little text — flagged for OCR review"
            : `Re-extracted (${method})`,
          handling,
        });
      } else if (needs) {
        toast("Still little text — OCR tools may be unavailable, or the scan is unreadable", "error");
        await refreshHandlingFlags();
      } else {
        const kindLabel = data.document?.metadata?.clinical_report_kind_label;
        toast(
          kindLabel
            ? `Re-extracted (${method}) · tagged as ${kindLabel}`
            : `Re-extracted (${method})`
        );
        await refreshHandlingFlags();
      }
      await loadDocuments();
      await loadDocumentIndex();
      if (state.activeDocumentId === id) {
        await viewDocument(id);
      }
    },
  });
}

async function replaceDocumentFile(id, file) {
  if (!id || !file) return;
  await withBackgroundTask({
    id: `replace-file-${id}-${Date.now()}`,
    label: "Replacing PDF and extracting…",
    run: async ({ setDetail }) => {
      setDetail(`Uploading ${file.name}…`);
      const fd = new FormData();
      fd.append("file", file);
      const data = await api(`/api/documents/${encodeURIComponent(id)}/replace-file`, {
        method: "POST",
        body: fd,
        timeoutMs: 600000,
      });
      const method = data.document?.metadata?.extraction_method || "unknown";
      const handling = data.handling || data.document?.handling;
      if (data.lab_import || handling?.status === "flagged") {
        notifyLabImportResult(data.lab_import, {
          fallbackToast: `File replaced · extracted (${method})`,
          handling,
        });
      } else {
        toast(`File replaced · extracted (${method})`);
        await refreshHandlingFlags();
      }
      await loadDocuments();
      await loadDocumentIndex();
      if (state.activeDocumentId === id) {
        await viewDocument(id);
      }
    },
  });
}

function pickReplaceDocumentFile(id) {
  const input =
    document.querySelector(`.doc-replace-file-input[data-id="${CSS.escape(id)}"]`) ||
    document.querySelector(`#doc-detail-actions .doc-replace-file-input[data-id="${CSS.escape(id)}"]`);
  if (!input) return toast("Replace file control missing", "error");
  input.value = "";
  input.click();
}

async function applyChatReplyToHome(messageId) {
  if (!state.optionsChatSessionId) {
    toast("No active chat session", "error");
    return;
  }
  if (state.analysisRunning) {
    toast("An analysis is already running", "error");
    return;
  }
  if (!confirm("Pin this chat reply and update the Home assessment?")) return;
  const selected = [...state.selectedIds];
  const data = await api("/api/options-chat/apply-to-home", {
    method: "POST",
    body: JSON.stringify({
      session_id: state.optionsChatSessionId,
      message_id: messageId,
      document_ids: selected.length ? selected : null,
    }),
  });
  toast("Home assessment update started");
  await loadChatObservations();
  switchTab("analyze");
  if (data.job?.id) {
    setAnalysisRunning(true, data.job.id, data.job.job_type || "baseline");
    resumeActiveAnalysisJobInBackground();
  }
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
  initSectionSubnav();
  initHeaderCollapse();
  syncStickyHeaderOffset();
  window.addEventListener("resize", syncStickyHeaderOffset);
}

function initSectionSubnav() {
  document.getElementById("home-subnav")?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-home-section]");
    if (!btn) return;
    setHomeSection(btn.dataset.homeSection, { scroll: true });
  });
  document.getElementById("panel-analyze")?.addEventListener("click", (event) => {
    const btn = event.target.closest("#home-assessment-empty [data-home-section]");
    if (!btn) return;
    setHomeSection(btn.dataset.homeSection, { scroll: true });
  });
  document.getElementById("settings-subnav")?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-settings-section]");
    if (!btn) return;
    setSettingsSection(btn.dataset.settingsSection, { scroll: true });
  });
  setHomeSection(state.homeSection || "assessment");
  setSettingsSection(state.settingsSection || "patients");
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
      refreshHandlingFlags(),
    ]);
  } finally {
    updateHomeToolbar();
    renderHandlingAlerts();
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
safeOn("#btn-flagged-refresh", "click", () => {
  refreshHandlingFlags({ rescan: true })
    .then((flags) => {
      toast(
        flags.count
          ? `${flags.count} still flagged`
          : "All lab/diagnostic reports are clear"
      );
    })
    .catch((e) => toast(e.message, "error"));
});
safeOn("#flagged-list", "click", (event) => {
  const btn = event.target.closest(".btn-flagged-action");
  if (!btn?.dataset?.id || !btn.dataset.action) return;
  handleFlaggedAction(btn.dataset.action, btn.dataset.id).catch((e) =>
    toast(e.message || "Action failed", "error")
  );
});
safeOn("#btn-handling-alerts-open", "click", () => {
  switchTab("analyze");
  setHomeSection("flagged", { scroll: true });
});
safeOn("#btn-close-detail", "click", () => closeDocumentDetail());
safeOn("#doc-detail-actions", "click", (event) => {
  const reextractBtn = event.target.closest(".btn-reextract");
  if (reextractBtn?.dataset.id) {
    reextractDocument(reextractBtn.dataset.id).catch((e) => toast(e.message, "error"));
    return;
  }
  const replaceBtn = event.target.closest(".btn-replace-file");
  if (replaceBtn?.dataset.id) {
    pickReplaceDocumentFile(replaceBtn.dataset.id);
    return;
  }
  const importLabsBtn = event.target.closest(".btn-import-labs");
  if (importLabsBtn?.dataset.id) {
    importDiagnosticsFromLibraryDocument(importLabsBtn.dataset.id).catch((e) =>
      toast(e.message, "error")
    );
  }
});
safeOn("#doc-detail-actions", "change", (event) => {
  const input = event.target.closest(".doc-replace-file-input");
  if (!input?.dataset.id || !input.files?.[0]) return;
  replaceDocumentFile(input.dataset.id, input.files[0]).catch((e) => toast(e.message, "error"));
});
safeOn("#btn-refresh-docs", "click", () =>
  refreshLibrary({ page: state.libraryPage, sourceType: state.libraryFilter }).catch((e) =>
    toast(e.message, "error")
  )
);
safeOn("#btn-library-add", "click", () => openLibraryAddPanel());
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
  const reextractBtn = event.target.closest(".btn-reextract");
  if (reextractBtn?.dataset.id) {
    reextractDocument(reextractBtn.dataset.id).catch((e) => toast(e.message, "error"));
    return;
  }
  const replaceBtn = event.target.closest(".btn-replace-file");
  if (replaceBtn?.dataset.id) {
    pickReplaceDocumentFile(replaceBtn.dataset.id);
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
safeOn("#documents-list", "change", (event) => {
  const input = event.target.closest(".doc-replace-file-input");
  if (!input?.dataset.id || !input.files?.[0]) return;
  replaceDocumentFile(input.dataset.id, input.files[0]).catch((e) => toast(e.message, "error"));
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
    const files = Array.from($("#pdf-file")?.files || []);
    if (!files.length) return toast("Choose one or more PDF files", "error");
    const customTitle =
      files.length === 1 ? ($("#pdf-title").value.trim() || "") : "";
    const total = files.length;
    await withBackgroundTask({
      id: `upload-pdf-${Date.now()}`,
      label:
        total === 1
          ? `Uploading PDF: ${files[0].name}`
          : `Uploading ${total} PDFs`,
      run: async ({ setDetail, isCancelled }) => {
        let ok = 0;
        let failed = 0;
        let lastDoc = null;
        let lastLabImport = null;
        let lastHandling = null;
        for (let i = 0; i < files.length; i++) {
          if (isCancelled()) return;
          const file = files[i];
          setDetail(`Processing ${i + 1} of ${total}: ${file.name}`);
          try {
            const fd = new FormData();
            fd.append("file", file);
            if (customTitle) fd.append("title", customTitle);
            const data = await api("/api/ingest/pdf", {
              method: "POST",
              body: fd,
              timeoutMs: 600000,
            });
            if (isCancelled()) return;
            lastDoc = data.document;
            if (data.lab_import) lastLabImport = data.lab_import;
            lastHandling = data.handling || data.document?.handling || lastHandling;
            ok += 1;
          } catch (err) {
            failed += 1;
            console.error(`PDF upload failed for ${file.name}`, err);
          }
        }
        $("#pdf-file").value = "";
        $("#pdf-file").closest(".file-label")?.querySelector(".file-name")?.remove();
        const pdfTitle = $("#pdf-title");
        if (pdfTitle) {
          pdfTitle.value = "";
          pdfTitle.disabled = false;
          pdfTitle.placeholder = "Defaults to each PDF filename";
          delete pdfTitle.dataset.userEdited;
        }
        if (lastDoc) showUploadResult(lastDoc);
        const shouldOpenFlagged =
          lastHandling?.status === "flagged" || lastLabImport?.flagged;
        if (ok && !failed) {
          if (lastLabImport || shouldOpenFlagged) {
            notifyLabImportResult(lastLabImport, {
              fallbackToast:
                ok === 1 ? `PDF uploaded · ${files[0].name}` : `${ok} PDFs uploaded`,
              handling: lastHandling,
            });
          } else {
            const kindLabel = lastDoc?.metadata?.clinical_report_kind_label;
            toast(
              ok === 1
                ? kindLabel
                  ? `PDF uploaded · tagged as ${kindLabel}`
                  : `PDF uploaded · ${files[0].name}`
                : `${ok} PDFs uploaded`
            );
            await refreshHandlingFlags();
          }
        } else if (ok && failed) {
          toast(`${ok} uploaded, ${failed} failed`, "error");
          await refreshHandlingFlags();
        } else {
          toast("PDF upload failed", "error");
        }
        if (ok) {
          const openedFlagged =
            shouldOpenFlagged ||
            (lastLabImport && !(lastLabImport.added_count > 0 && !lastLabImport.flagged));
          if (!openedFlagged && !(lastLabImport && lastLabImport.added_count > 0)) {
            await openLibraryAfterIngest();
          } else {
            await loadDocumentIndex();
            await refreshHandlingFlags();
          }
        }
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
safeOn("#btn-scope-main-sources", "click", () => selectMainSources());
safeOn("#btn-scope-type-text", "click", () => selectDocumentsByType("text", { replace: true }));
safeOn("#btn-scope-type-pdf", "click", () => selectDocumentsByType("pdf", { replace: true }));
safeOn("#btn-scope-new-uploads", "click", () => selectNewSinceLastAssessment({ includePrior: true }));
safeOn("#btn-lib-main-sources", "click", () => selectMainSources());
safeOn("#btn-lib-type-text", "click", () => selectDocumentsByType("text", { replace: true }));
safeOn("#btn-lib-type-pdf", "click", () => selectDocumentsByType("pdf", { replace: true }));
safeOn("#btn-lib-new-uploads", "click", () => selectNewSinceLastAssessment({ includePrior: true }));
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
$("#btn-export-pdf-icon")?.addEventListener("click", () =>
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
  switchTab("library", { openAdd: true });
  const titleEl = $("#text-title");
  const contentEl = $("#text-content");
  if (titleEl && !titleEl.value.trim()) {
    titleEl.value = titleHint || "Chat excerpt";
  }
  if (contentEl) contentEl.value = text;
  openLibraryAddPanel({ focusText: true });
  toast("Ready in Library → Add documents — review title and save");
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
          ? `<button type="button" class="btn ghost btn-sm options-chat-pin-whole" data-message-id="${escapeHtml(msg.id)}" title="Pin whole reply">Pin reply</button>
             <button type="button" class="btn secondary btn-sm options-chat-apply-home" data-message-id="${escapeHtml(msg.id)}" title="Pin this reply and update the Home assessment">Update Home</button>`
          : "";
      return `<article class="options-chat-bubble ${role}${streaming}" data-message-id="${escapeHtml(msg.id || "")}">
        <div class="options-chat-bubble-head row-between wrap">
          <span class="options-chat-role">${label}</span>
          <span class="options-chat-bubble-actions">${pinBtn}</span>
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
  const applyBtn = event.target.closest(".options-chat-apply-home");
  if (applyBtn?.dataset.messageId) {
    applyChatReplyToHome(applyBtn.dataset.messageId).catch((e) => toast(e.message, "error"));
    return;
  }
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

// ------------------------------------------------------------------
// Patient / Case management
// ------------------------------------------------------------------

function patientInitials(label) {
  const parts = String(label || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function fillSelect(select, items, selectedId) {
  if (!select) return;
  select.innerHTML = "";
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = item.label;
    if (item.id === selectedId) opt.selected = true;
    select.appendChild(opt);
  }
}

async function activatePatientCase(patientId, caseId, { label } = {}) {
  showSwitchProgress(label || "Switching patient / case…");
  try {
    const res = await fetch("/api/cases/activate", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patient_id: patientId, case_id: caseId }),
    });
    if (!res.ok) {
      hideSwitchProgress();
      clearSwitchBusyState();
      alert("Could not switch case");
      return;
    }
    location.reload();
  } catch (err) {
    hideSwitchProgress();
    clearSwitchBusyState();
    alert(err.message || "Could not switch case");
  }
}

function showSwitchProgress(message) {
  const overlay = document.getElementById("switch-progress-overlay");
  const title = document.getElementById("switch-progress-title");
  if (title) title.textContent = message || "Switching…";
  overlay?.classList.remove("hidden");
  document.body.style.cursor = "wait";
}

function hideSwitchProgress() {
  document.getElementById("switch-progress-overlay")?.classList.add("hidden");
  document.body.style.cursor = "";
}

function setSwitchBusyState(btn, message) {
  const list = document.getElementById("switch-case-list");
  const status = document.getElementById("switch-case-status");
  const closeBtn = document.getElementById("btn-cancel-switch-case");
  list?.querySelectorAll(".switch-case-btn").forEach((el) => {
    el.disabled = true;
  });
  if (btn) {
    btn.classList.add("switching");
    if (!btn.querySelector(".switch-case-btn-spinner")) {
      const spin = document.createElement("span");
      spin.className = "switch-case-btn-spinner";
      spin.setAttribute("aria-hidden", "true");
      btn.appendChild(spin);
    }
  }
  if (status) {
    status.innerHTML = `<span class="switch-case-btn-spinner" aria-hidden="true"></span><span>${escapeHtml(message || "Switching…")}</span>`;
    status.classList.remove("hidden");
  }
  if (closeBtn) closeBtn.disabled = true;
}

function clearSwitchBusyState() {
  const list = document.getElementById("switch-case-list");
  const status = document.getElementById("switch-case-status");
  const closeBtn = document.getElementById("btn-cancel-switch-case");
  list?.querySelectorAll(".switch-case-btn").forEach((el) => {
    el.disabled = false;
    el.classList.remove("switching");
    el.querySelector(".switch-case-btn-spinner")?.remove();
  });
  status?.classList.add("hidden");
  if (status) status.innerHTML = "";
  if (closeBtn) closeBtn.disabled = false;
}

async function loadCaseContext() {
  try {
    const r = await fetch("/api/patients");
    const data = await r.json();
    const ctx = data.active || {};
    const patients = data.patients || [];
    const nameEl = document.getElementById("header-patient-name");
    const subEl = document.getElementById("header-patient-sub");
    const initialsEl = document.getElementById("header-patient-initials");
    const photoEl = document.getElementById("header-patient-photo");
    const photoBtn = document.getElementById("btn-patient-photo");
    const patientSelectWrap = document.getElementById("header-patient-select-wrap");
    const patientSelect = document.getElementById("header-patient-select");
    const caseSelect = document.getElementById("header-case-select");
    const settingsPatient = document.getElementById("settings-current-patient");
    const settingsCase = document.getElementById("settings-current-case");

    const label = ctx.patient_label || "No patient";
    if (nameEl) nameEl.textContent = label;
    if (initialsEl) initialsEl.textContent = patientInitials(label);
    if (settingsPatient) settingsPatient.textContent = label;
    if (settingsCase) settingsCase.textContent = ctx.case_label || "No case";

    if (photoEl && photoBtn) {
      if (ctx.photo_url) {
        photoEl.src = ctx.photo_url;
        photoEl.alt = label;
        photoEl.classList.remove("hidden");
        photoBtn.classList.add("has-photo");
      } else {
        photoEl.removeAttribute("src");
        photoEl.classList.add("hidden");
        photoBtn.classList.remove("has-photo");
      }
    }

    if (patients.length >= 1) {
      patientSelectWrap?.classList.remove("hidden");
      fillSelect(patientSelect, patients, ctx.patient_id);
    } else {
      patientSelectWrap?.classList.add("hidden");
    }

    const activePatient = patients.find((p) => p.id === ctx.patient_id);
    const cases = activePatient?.cases || ctx.cases || [];
    fillSelect(caseSelect, cases.length ? cases : [{ id: "", label: "No cases yet" }], ctx.case_id);
    if (caseSelect) caseSelect.disabled = !cases.length;

    state.activePatientId = ctx.patient_id || null;
    state.activeCaseId = ctx.case_id || null;
    state.diagStatusFilter = "all";
    if (ctx.patient_id) {
      await refreshActivePatientProfile();
    } else {
      renderPatientProfile({}, null);
    }
    if (subEl && !ctx.patient_id) subEl.textContent = "";
  } catch { /* ignore */ }
}

function ageFromDob(dob) {
  if (!dob) return null;
  const d = new Date(`${String(dob).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  const today = new Date();
  let age = today.getFullYear() - d.getFullYear();
  const m = today.getMonth() - d.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < d.getDate())) age -= 1;
  return age >= 0 ? age : null;
}

function formatPatientSubline(profile) {
  const bits = [];
  const age = ageFromDob(profile.date_of_birth);
  if (age != null) bits.push(`Age ${age}`);
  if (profile.gender) bits.push(String(profile.gender));
  return bits.join(" · ");
}

function bmiFor(heightCm, weightKg) {
  const h = Number(heightCm);
  const w = Number(weightKg);
  if (!(h > 0) || !(w > 0)) return null;
  const bmi = w / ((h / 100) ** 2);
  return Number.isFinite(bmi) ? bmi.toFixed(1) : null;
}

function renderPatientProfile(profile, patientId, extras = {}) {
  const dobEl = document.getElementById("profile-dob");
  const genderEl = document.getElementById("profile-gender");
  const hintEl = document.getElementById("profile-age-hint");
  const listEl = document.getElementById("patient-measurements-list");
  const diagListEl = document.getElementById("patient-diagnostics-list");
  const journalListEl = document.getElementById("patient-journal-list");
  const medListEl = document.getElementById("patient-medications-list");
  const measureDate = document.getElementById("measure-date");
  const diagDate = document.getElementById("diag-date");
  const presets = extras.diagnostic_presets || state.diagnosticPresets || [];
  if (presets.length) state.diagnosticPresets = presets;
  if (extras.journal_presets?.length) state.journalPresets = extras.journal_presets;
  if (extras.milestone_presets?.length) state.milestonePresets = extras.milestone_presets;

  const nameList = document.getElementById("diag-name-presets");
  const unitList = document.getElementById("diag-unit-presets");
  if (nameList) {
    nameList.innerHTML = (state.diagnosticPresets || [])
      .map((p) => `<option value="${escapeHtml(p.name)}"></option>`)
      .join("");
  }
  if (unitList) {
    const units = [...new Set((state.diagnosticPresets || []).map((p) => p.unit).filter(Boolean))];
    unitList.innerHTML = units.map((u) => `<option value="${escapeHtml(u)}"></option>`).join("");
  }

  if (!patientId) {
    if (dobEl) dobEl.value = "";
    if (genderEl) genderEl.value = "";
    if (hintEl) hintEl.textContent = "Select or create a patient first";
    if (listEl) listEl.innerHTML = "<p class='muted small'>No patient selected.</p>";
    if (diagListEl) diagListEl.innerHTML = "<p class='muted small'>No patient selected.</p>";
    if (journalListEl) journalListEl.innerHTML = "<p class='muted small'>No patient selected.</p>";
    if (medListEl) medListEl.innerHTML = "<p class='muted small'>No patient selected.</p>";
    renderDiagnosticsCharts(null, []);
    renderJournalHome(null, []);
    renderMedicationsHome(null);
    return;
  }
  if (dobEl) dobEl.value = profile.date_of_birth ? String(profile.date_of_birth).slice(0, 10) : "";
  if (genderEl) genderEl.value = profile.gender || "";
  const age = ageFromDob(profile.date_of_birth);
  if (hintEl) {
    hintEl.textContent = age != null
      ? `Age ${age} · used in analysis prompts for the active patient`
      : "Used in analysis prompts for the active patient";
  }
  const today = new Date().toISOString().slice(0, 10);
  if (measureDate && !measureDate.value) measureDate.value = today;
  if (diagDate && !diagDate.value) diagDate.value = today;

  const measurements = profile.measurements || [];
  if (listEl) {
    if (!measurements.length) {
      listEl.innerHTML = "<p class='muted small'>No measurements yet.</p>";
    } else {
      listEl.innerHTML = measurements.map((m) => {
        const parts = [];
        if (m.height_cm != null) parts.push(`${m.height_cm} cm`);
        if (m.weight_kg != null) parts.push(`${m.weight_kg} kg`);
        const bmi = bmiFor(m.height_cm, m.weight_kg);
        if (bmi) parts.push(`BMI ${bmi}`);
        if (m.notes) parts.push(m.notes);
        return `<div class="patient-measurement-row" data-id="${escapeHtml(m.id)}">
          <div><strong>${escapeHtml(m.recorded_at || "")}</strong> · ${escapeHtml(parts.join(" · ") || "—")}</div>
          <button type="button" class="btn ghost btn-sm btn-delete-measurement" data-id="${escapeHtml(m.id)}">Remove</button>
        </div>`;
      }).join("");
    }
  }

  const diagnostics = profile.diagnostics || [];
  if (diagListEl) {
    if (!diagnostics.length) {
      diagListEl.innerHTML = "<p class='muted small'>No diagnostic readings yet.</p>";
    } else {
      diagListEl.innerHTML = diagnostics.map((d) => {
        const unit = d.unit ? ` ${d.unit}` : "";
        const note = d.notes ? ` · ${d.notes}` : "";
        return `<div class="patient-measurement-row" data-id="${escapeHtml(d.id)}">
          <div><strong>${escapeHtml(d.name || "")}</strong> · ${escapeHtml(String(d.value))}${escapeHtml(unit)} · ${escapeHtml(d.recorded_at || "")}${escapeHtml(note)}</div>
          <button type="button" class="btn ghost btn-sm btn-delete-diagnostic" data-id="${escapeHtml(d.id)}">Remove</button>
        </div>`;
      }).join("");
    }
  }

  const journal = profile.journal || [];
  if (journalListEl) {
    if (!journal.length) {
      journalListEl.innerHTML = "<p class='muted small'>No self-reports yet. Log from Home → How are you?</p>";
    } else {
      journalListEl.innerHTML = journal.map((j) => formatJournalListRow(j)).join("");
    }
  }

  renderMedicationsSettings(profile);
  renderMilestonesSettings(profile);
  const series = resolveDiagnosticSeries(profile, extras);
  state.patientProfile = profile || null;
  state.diagnosticSeriesCache = extras.diagnostic_series || series;
  renderDiagnosticsCharts(profile, series);
  const journalSeries = extras.journal_series || groupJournalClient(profile);
  renderJournalHome(profile, journalSeries);
  renderMedicationsHome(profile);
  renderMedSafetyResult(profile?.medication_safety);
}

function parseConditionsInput(raw) {
  return String(raw || "")
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function formatMedicationDoseLine(m) {
  const bits = [];
  if (m.dosage) bits.push(m.dosage);
  if (m.frequency) bits.push(m.frequency);
  return bits.join(" · ") || "Dosage not set";
}

function formatMedicationConditions(m) {
  const conditions = m.conditions || [];
  if (!conditions.length) return "";
  return `<div class="medication-conditions">${conditions
    .map((c) => `<span class="medication-condition-chip">${escapeHtml(c)}</span>`)
    .join("")}</div>`;
}

function formatMedicationHistory(m) {
  const hist = m.dosage_history || [];
  if (!hist.length) return "";
  const items = [...hist]
    .reverse()
    .map((h) => {
      const dose = [h.dosage, h.frequency].filter(Boolean).join(" · ") || "—";
      const when = h.effective_at
        ? formatDiagDate(h.effective_at)
        : formatJournalDateTime(h.changed_at);
      const note = h.note ? ` · ${escapeHtml(h.note)}` : "";
      return `<li><strong>${escapeHtml(dose)}</strong> until ${escapeHtml(when)}${note}</li>`;
    })
    .join("");
  const current = formatMedicationDoseLine(m);
  return `<details class="medication-history">
    <summary>Changes (${hist.length})</summary>
    <ul>
      <li><strong>${escapeHtml(current)}</strong> · current</li>
      ${items}
    </ul>
  </details>`;
}

function renderMedicationsHome(profile) {
  const el = document.getElementById("medications-home-list");
  if (!el) return;
  renderMedSafetyHomeStatus(profile);
  if (!profile) {
    el.innerHTML = `<p class="muted small">Select a patient to see medications.</p>`;
    return;
  }
  const meds = profile.medications || [];
  if (!meds.length) {
    el.innerHTML = `<p class="muted small">No medications yet. Add them in Settings.</p>`;
    return;
  }
  const active = meds.filter((m) => (m.status || "active") === "active");
  const stopped = meds.filter((m) => m.status === "stopped");
  const rowHtml = (m, { stopped: isStopped } = {}) => {
    const started = m.started_at ? `since ${formatDiagDate(m.started_at)}` : "";
    const ended = m.stopped_at ? `ended ${formatDiagDate(m.stopped_at)}` : "";
    const meta = [formatMedicationDoseLine(m), started, ended].filter(Boolean).join(" · ");
    return `<div class="medication-home-row${isStopped ? " is-stopped" : ""}" data-id="${escapeHtml(m.id || "")}">
      <div class="medication-row-main">
        <strong>${escapeHtml(m.name || "")}</strong>${formatMedicationIdentityBadge(m)}${
          isStopped ? `<span class="medication-stopped-pill">Stopped</span>` : ""
        }
        <p class="medication-row-meta">${escapeHtml(meta)}</p>
        ${formatMedicationConditions(m)}
        ${formatMedicationHistory(m)}
      </div>
      <div class="medication-row-actions">${formatMedicationFixActions(m)}</div>
    </div>`;
  };
  let html = active.map((m) => rowHtml(m)).join("");
  if (stopped.length) {
    html += `<h5 class="medication-stopped-heading">History / stopped</h5>`;
    html += stopped.map((m) => rowHtml(m, { stopped: true })).join("");
  }
  el.innerHTML = html;
}

function medicationChartEvents(medications) {
  const events = [];
  const short = (text, max = 48) => {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
  };
  const doseBits = (d, f) => [d, f].filter((x) => x && String(x).trim()).map((x) => String(x).trim()).join(" · ");
  const dateOnly = (raw) => {
    const s = String(raw || "").slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : null;
  };
  const compactDate = (iso) => {
    const raw = dateOnly(iso);
    if (!raw) return "";
    const d = new Date(`${raw}T12:00:00`);
    if (Number.isNaN(d.getTime())) return raw;
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    // Always include year — med starts can predate the chart by years
    return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
  };
  const nameKey = (name) => String(name || "").toLowerCase().replace(/\s+/g, " ").trim();
  const dayMs = (iso) => {
    const d = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
    return Number.isFinite(d.getTime()) ? d.getTime() : null;
  };
  const pushEvent = (ev) => {
    const id = `${ev.date}|${ev.kind}|${ev.medication_id || ""}|${ev.body || ev.label}`;
    events.push({ ...ev, id, label: short(ev.label) });
  };
  for (const med of medications || []) {
    const name = String(med.name || "").trim() || "Medication";
    const medId = String(med.id || "");
    const hist = (med.dosage_history || []).filter((h) => h && typeof h === "object");
    const started = dateOnly(med.started_at);
    if (started) {
      const initial = hist.length
        ? doseBits(hist[0].dosage, hist[0].frequency)
        : doseBits(med.dosage, med.frequency);
      const body = `Started ${name}${initial ? ` ${initial}` : ""}`;
      pushEvent({
        date: started,
        label: `${compactDate(started)} · ${body}`,
        body,
        kind: "start",
        medication_id: medId,
        medication_name: name,
      });
    }
    hist.forEach((row, i) => {
      const effective = dateOnly(row.effective_at) || dateOnly(row.changed_at);
      if (!effective) return;
      const oldBits = doseBits(row.dosage, row.frequency) || "?";
      const newBits =
        i + 1 < hist.length
          ? doseBits(hist[i + 1].dosage, hist[i + 1].frequency) || "?"
          : doseBits(med.dosage, med.frequency) || "?";
      const note = String(row.note || "").trim();
      const body = note ? `${name}: ${note}` : `${name}: ${oldBits} → ${newBits}`;
      pushEvent({
        date: effective,
        label: `${compactDate(effective)} · ${body}`,
        body,
        kind: "dose_change",
        medication_id: medId,
        medication_name: name,
      });
    });
    const stopped = dateOnly(med.stopped_at);
    if (stopped && med.status === "stopped") {
      const body = `Stopped ${name}`;
      pushEvent({
        date: stopped,
        label: `${compactDate(stopped)} · ${body}`,
        body,
        kind: "stop",
        medication_id: medId,
        medication_name: name,
      });
    }
  }

  // Merge stop + nearby start of the same drug into one dose-change marker
  const medsById = new Map((medications || []).filter((m) => m?.id).map((m) => [String(m.id), m]));
  const stops = events.map((e, i) => ({ e, i })).filter(({ e }) => e.kind === "stop");
  const starts = events.map((e, i) => ({ e, i })).filter(({ e }) => e.kind === "start");
  const used = new Set();
  const merged = [];
  const maxGapMs = 3 * 86400000;
  for (const { e: stop, i: si } of stops) {
    if (used.has(si)) continue;
    const stopT = dayMs(stop.date);
    if (stopT == null) continue;
    let best = null;
    for (const { e: start, i: ti } of starts) {
      if (used.has(ti)) continue;
      if (nameKey(start.medication_name) !== nameKey(stop.medication_name)) continue;
      const startT = dayMs(start.date);
      if (startT == null) continue;
      const gap = startT - stopT;
      if (gap < -86400000 || gap > maxGapMs) continue;
      const absGap = Math.abs(gap);
      if (!best || absGap < best.absGap) best = { start, ti, absGap };
    }
    if (!best) continue;
    used.add(si);
    used.add(best.ti);
    const stopMed = medsById.get(String(stop.medication_id || "")) || {};
    const startMed = medsById.get(String(best.start.medication_id || "")) || {};
    const oldBits = String(stopMed.dosage || "").trim() || "?";
    const newBits = String(startMed.dosage || "").trim() || "?";
    const name = stop.medication_name || best.start.medication_name || "Medication";
    const when = best.start.date || stop.date;
    const body = `${name} ${oldBits} → ${newBits}`;
    merged.push({
      date: when,
      label: short(`${compactDate(when)} · ${body}`),
      body,
      kind: "dose_change",
      medication_id: best.start.medication_id || stop.medication_id || "",
      medication_name: name,
      coalesced_from: "stop_start",
      id: `${when}|dose_change|${best.start.medication_id || stop.medication_id || ""}|${body}`,
    });
  }
  const kept = events.filter((_, i) => !used.has(i)).concat(merged);

  const kindOrder = { stop: 0, dose_change: 1, start: 2 };
  kept.sort(
    (a, b) =>
      String(a.date).localeCompare(String(b.date)) ||
      (kindOrder[a.kind] ?? 9) - (kindOrder[b.kind] ?? 9) ||
      String(a.label).localeCompare(String(b.label))
  );
  const seen = new Set();
  return kept.filter((e) => {
    if (seen.has(e.id)) return false;
    seen.add(e.id);
    return true;
  });
}

const MILESTONE_COLORS = [
  "#0f766e", // teal
  "#c2410c", // orange
  "#7c3aed", // violet
  "#0369a1", // blue
  "#be123c", // rose
  "#15803d", // green
  "#a16207", // amber
  "#4338ca", // indigo
  "#0e7490", // cyan
  "#a21caf", // fuchsia
];

const DEFAULT_MILESTONE_PRESETS = [
  { label: "Exercise regularly", kind: "exercise" },
  { label: "Change in diet", kind: "diet" },
  { label: "Started sleep routine", kind: "lifestyle" },
  { label: "Quit / cut smoking", kind: "lifestyle" },
  { label: "Stress management", kind: "lifestyle" },
  { label: "Weight goal started", kind: "lifestyle" },
  { label: "Travel / schedule change", kind: "lifestyle" },
  { label: "Other", kind: "other" },
];

function milestoneColorKey(ev) {
  const name = String(ev?.medication_name || "")
    .trim()
    .toLowerCase();
  if (name) return `medname:${name}`;
  if (ev?.medication_id) return `med:${ev.medication_id}`;
  return `life:${ev?.id || ev?.label || "milestone"}`;
}

function colorizeMilestoneEvents(events) {
  // Sequential palette so adjacent meds / lifestyle events are obviously different
  const keyIndex = new Map();
  for (const ev of events || []) {
    const key = milestoneColorKey(ev);
    if (!keyIndex.has(key)) keyIndex.set(key, keyIndex.size);
  }
  return (events || []).map((ev) => {
    const idx = keyIndex.get(milestoneColorKey(ev)) || 0;
    return { ...ev, color: MILESTONE_COLORS[idx % MILESTONE_COLORS.length] };
  });
}

function customMilestoneEvents(milestones) {
  const short = (text, max = 48) => {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
  };
  const compactDate = (iso) => {
    const raw = String(iso || "").slice(0, 10);
    const d = new Date(`${raw}T12:00:00`);
    if (Number.isNaN(d.getTime())) return raw;
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
  };
  const out = [];
  for (const row of milestones || []) {
    const when = String(row.date || row.recorded_at || "").slice(0, 10);
    const labelBody = String(row.label || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(when) || !labelBody) continue;
    const note = String(row.notes || "").trim();
    const body = note ? `${labelBody} — ${note}` : labelBody;
    const kind = String(row.kind || "lifestyle");
    out.push({
      id: row.id || `${when}|lifestyle|${labelBody}`,
      date: when,
      label: short(`${compactDate(when)} · ${body}`),
      body,
      kind: ["start", "dose_change", "stop"].includes(kind) ? "lifestyle" : kind,
      medication_id: "",
      medication_name: "",
      source: "custom",
    });
  }
  return out;
}

function allChartMilestones(profile) {
  const med = medicationChartEvents(profile?.medications);
  const custom = customMilestoneEvents(profile?.milestones);
  const merged = [...med, ...custom].sort(
    (a, b) => String(a.date).localeCompare(String(b.date)) || String(a.label).localeCompare(String(b.label))
  );
  return colorizeMilestoneEvents(merged);
}

function milestonePresets() {
  return state.milestonePresets?.length ? state.milestonePresets : DEFAULT_MILESTONE_PRESETS;
}

function fillMilestonePresetSelects() {
  const presets = milestonePresets();
  for (const id of ["diag-milestone-preset", "ms-preset"]) {
    const el = document.getElementById(id);
    if (!el) continue;
    const cur = el.value;
    el.innerHTML = presets
      .map((p) => `<option value="${escapeHtml(p.label)}" data-kind="${escapeHtml(p.kind)}">${escapeHtml(p.label)}</option>`)
      .join("");
    if ([...el.options].some((o) => o.value === cur)) el.value = cur;
  }
  syncMilestoneCustomVisibility();
}

function syncMilestoneCustomVisibility() {
  const pairs = [
    ["diag-milestone-preset", "diag-milestone-custom"],
    ["ms-preset", "ms-custom-wrap"],
  ];
  for (const [selId, customId] of pairs) {
    const sel = document.getElementById(selId);
    const custom = document.getElementById(customId);
    if (!sel || !custom) continue;
    const other = String(sel.value || "").toLowerCase() === "other";
    custom.classList.toggle("hidden", !other);
  }
}

function diagMilestoneStorageKey(patientId) {
  return `beatit.diagMilestones.${patientId || "none"}`;
}

function loadDiagMilestonePrefs(patientId, allEvents) {
  const defaults = {
    enabled: true,
    selected: allEvents.map((e) => e.id),
  };
  try {
    const raw = localStorage.getItem(diagMilestoneStorageKey(patientId));
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    const known = new Set(allEvents.map((e) => e.id));
    let selected = Array.isArray(parsed.selected) ? parsed.selected.filter((id) => known.has(id)) : null;
    // New milestones default on
    if (selected) {
      for (const ev of allEvents) {
        if (!selected.includes(ev.id) && !(parsed.seen || []).includes(ev.id)) {
          selected.push(ev.id);
        }
      }
    } else {
      selected = defaults.selected;
    }
    return {
      enabled: parsed.enabled !== false,
      selected,
      seen: allEvents.map((e) => e.id),
    };
  } catch {
    return defaults;
  }
}

function saveDiagMilestonePrefs(patientId, prefs) {
  try {
    localStorage.setItem(
      diagMilestoneStorageKey(patientId),
      JSON.stringify({
        enabled: !!prefs.enabled,
        selected: prefs.selected || [],
        seen: prefs.seen || prefs.selected || [],
      })
    );
  } catch {
    /* ignore quota */
  }
}

function visibleDiagMilestones(allEvents, prefs) {
  if (!prefs?.enabled) return [];
  const selected = new Set(prefs.selected || []);
  return (allEvents || []).filter((e) => selected.has(e.id));
}

function seriesDateSpan(seriesList) {
  let min = null;
  let max = null;
  for (const s of seriesList || []) {
    for (const r of s.readings || []) {
      const d = String(r.recorded_at || "").slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) continue;
      if (!min || d < min) min = d;
      if (!max || d > max) max = d;
    }
  }
  return min && max ? { start: min, end: max } : null;
}

/** Days before/after the lab span to keep medication overlays (starts often land after the last draw). */
const DIAG_MILESTONE_PAD_DAYS = 60;

function milestonesForLabSpan(allEvents, span) {
  if (!span) return allEvents || [];
  return filterMilestonesForRange(allEvents, span.start, span.end, DIAG_MILESTONE_PAD_DAYS);
}

function renderDiagnosticsMilestoneControls(profile, allEvents, seriesList = []) {
  const wrap = document.getElementById("diagnostics-milestone-controls");
  const list = document.getElementById("diag-milestones-list");
  const enabledEl = document.getElementById("diag-milestones-enabled");
  const metaEl = document.getElementById("diag-milestones-summary-meta");
  if (!wrap || !list || !enabledEl) return;
  const span = seriesDateSpan(seriesList);
  // Include milestones shortly after the last lab (e.g. med started Sep 1, labs through late Aug)
  const events = milestonesForLabSpan(allEvents, span);
  wrap.classList.remove("hidden");
  if (!events.length) {
    state.diagMilestonePrefs = { enabled: true, selected: [] };
    if (metaEl) metaEl.textContent = "add milestones below";
    list.innerHTML = `<p class="muted small">No medication or lifestyle milestones in this labs date range yet.</p>`;
    fillMilestonePresetSelects();
    const dateEl = document.getElementById("diag-milestone-date");
    if (dateEl && !dateEl.value) {
      const today = new Date();
      dateEl.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    }
    return;
  }
  const prefs = loadDiagMilestonePrefs(state.activePatientId, events);
  state.diagMilestonePrefs = prefs;
  enabledEl.checked = !!prefs.enabled;
  wrap.classList.toggle("is-disabled", !prefs.enabled);
  const selectedCount = events.filter((e) => (prefs.selected || []).includes(e.id)).length;
  if (metaEl) {
    metaEl.textContent = prefs.enabled
      ? `${selectedCount}/${events.length} on charts`
      : "hidden";
  }
  list.innerHTML = events
    .map((ev) => {
      const checked = (prefs.selected || []).includes(ev.id);
      const color = ev.color || "#0f766e";
      return `<label class="diag-milestone-chip${checked ? "" : " is-off"}" title="${escapeHtml(ev.label)}" style="border-color:${escapeHtml(color)};background:color-mix(in srgb, ${escapeHtml(color)} 14%, var(--surface))">
        <input type="checkbox" class="diag-milestone-toggle" data-id="${escapeHtml(ev.id)}" ${checked ? "checked" : ""}>
        <span class="diag-milestone-swatch" style="background:${escapeHtml(color)}" aria-hidden="true"></span>
        <span>${escapeHtml(ev.label)}</span>
      </label>`;
    })
    .join("");
  fillMilestonePresetSelects();
  const dateEl = document.getElementById("diag-milestone-date");
  if (dateEl && !dateEl.value) {
    const today = new Date();
    dateEl.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  }
}

function applyDiagMilestonePrefsAndRedraw() {
  const prefs = state.diagMilestonePrefs || { enabled: true, selected: [] };
  const profile = state.patientProfile || null;
  const allEvents = allChartMilestones(profile);
  const series = resolveDiagnosticSeries(profile, {
    diagnostic_series: state.diagnosticSeriesCache,
  });
  const span = seriesDateSpan(series);
  const inSpan = milestonesForLabSpan(allEvents, span);
  prefs.seen = inSpan.map((e) => e.id);
  saveDiagMilestonePrefs(state.activePatientId, prefs);
  renderDiagnosticsMilestoneControls(profile, allEvents, series);
  renderDiagnosticsCharts(profile, series, { skipControls: true });
}

function filterMilestonesForRange(events, start, end, padDays = 0) {
  if (!events?.length || !start || !end) return [];
  const toDay = (iso) => {
    const d = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
    return Number.isFinite(d.getTime()) ? d.getTime() : null;
  };
  const t0 = toDay(start);
  const t1 = toDay(end);
  if (t0 == null || t1 == null) return [];
  const pad = padDays * 86400000;
  return events.filter((e) => {
    const t = toDay(e.date);
    return t != null && t >= t0 - pad && t <= t1 + pad;
  });
}

function formatMedicationIdentityBadge(m) {
  const status = m.identity_status || "known";
  if (status === "known") return "";
  if (status === "uncertain") {
    const tip = m.identity_match ? `Did you mean ${m.identity_match}? Click to fix.` : "Name may need checking. Click to fix.";
    return `<button type="button" class="medication-identity-badge uncertain btn-fix-medication" data-id="${escapeHtml(m.id || "")}" title="${escapeHtml(tip)}">Check name</button>`;
  }
  return `<button type="button" class="medication-identity-badge unknown btn-fix-medication" data-id="${escapeHtml(m.id || "")}" title="Not found in known medication list. Click to fix.">Unknown</button>`;
}

const MED_DOSE_UNITS = ["mg", "mcg", "µg", "g", "mL", "IU", "units", "%", "mg/mL", "mcg/mL"];

function parseDosageParts(raw) {
  const text = String(raw || "").trim();
  if (!text) return { amount: "", unit: "", other: "" };
  const compact = text.replace(/\s+/g, " ");
  const re = new RegExp(
    `^(\\d+(?:\\.\\d+)?)\\s*(${MED_DOSE_UNITS.map((u) => u.replace("/", "\\/")).join("|")}|ug|mcgs?|mgs?|mls?|ius?)\\b(.*)$`,
    "i"
  );
  const m = compact.match(re);
  if (m) {
    let unit = m[2];
    const rest = (m[3] || "").trim();
    const lower = unit.toLowerCase();
    if (lower === "ug" || lower === "mcgs") unit = "mcg";
    else if (lower === "mgs") unit = "mg";
    else if (lower === "mls" || lower === "ml") unit = "mL";
    else if (lower === "ius" || lower === "iu") unit = "IU";
    else {
      const hit = MED_DOSE_UNITS.find((u) => u.toLowerCase() === lower);
      unit = hit || unit;
    }
    if (rest) return { amount: compact, unit: "other", other: "" };
    return { amount: m[1], unit, other: "" };
  }
  // "300mg" without space already covered; free-text fallback
  return { amount: compact, unit: "other", other: "" };
}

function composeDosageFromForm() {
  const amount = document.getElementById("med-dose-amount")?.value.trim() || "";
  const unitSel = document.getElementById("med-dose-unit")?.value || "";
  const other = document.getElementById("med-dose-unit-other")?.value.trim() || "";
  if (!amount && !unitSel && !other) return null;
  if (unitSel === "other") {
    if (other) return `${amount} ${other}`.trim();
    return amount || null;
  }
  if (unitSel) return `${amount} ${unitSel}`.trim();
  return amount || null;
}

function syncMedDoseUnitOtherVisibility() {
  const unit = document.getElementById("med-dose-unit")?.value;
  const wrap = document.getElementById("med-dose-unit-other-wrap");
  wrap?.classList.toggle("hidden", unit !== "other");
}

function setMedicationDosageFields(dosage) {
  const parts = parseDosageParts(dosage);
  const amountEl = document.getElementById("med-dose-amount");
  const unitEl = document.getElementById("med-dose-unit");
  const otherEl = document.getElementById("med-dose-unit-other");
  if (amountEl) amountEl.value = parts.amount;
  if (unitEl) {
    if (parts.unit && MED_DOSE_UNITS.includes(parts.unit)) unitEl.value = parts.unit;
    else if (parts.unit === "other" || (parts.amount && !parts.unit)) unitEl.value = parts.unit === "other" ? "other" : "";
    else unitEl.value = "";
  }
  if (otherEl) otherEl.value = parts.other;
  // If free-text didn't match a known unit, keep full string in amount with Other
  if (parts.unit === "other" && parts.amount && !/^\d/.test(String(dosage || "").trim())) {
    if (amountEl) amountEl.value = String(dosage || "").trim();
    if (unitEl) unitEl.value = "other";
  }
  syncMedDoseUnitOtherVisibility();
}

function updateMedIdentityHint(m) {
  const el = document.getElementById("med-identity-hint");
  if (!el) return;
  const status = m?.identity_status || "known";
  if (!m || status === "known") {
    el.innerHTML = "";
    el.classList.add("hidden");
    return;
  }
  if (status === "uncertain" && m.identity_match) {
    el.innerHTML = `Name may need checking — similar to <strong>${escapeHtml(m.identity_match)}</strong>.
      <button type="button" class="btn secondary btn-sm" id="btn-med-use-suggested" data-name="${escapeHtml(m.identity_match)}">Use ${escapeHtml(m.identity_match)}</button>`;
  } else if (status === "unknown") {
    el.innerHTML = `Not found on the known medication list. Rename to a standard brand or generic if you can, or keep as written.`;
  } else {
    el.innerHTML = `Check the medication name and dose, then save.`;
  }
  el.classList.remove("hidden");
}

function formatMedicationFixActions(m) {
  const status = m.identity_status || "known";
  const bits = [];
  bits.push(
    `<button type="button" class="btn ghost btn-sm btn-edit-medication" data-id="${escapeHtml(m.id || "")}">Edit</button>`
  );
  if (status !== "known") {
    bits.push(
      `<button type="button" class="btn secondary btn-sm btn-fix-medication" data-id="${escapeHtml(m.id || "")}">Fix</button>`
    );
  }
  if (status === "uncertain" && m.identity_match) {
    bits.push(
      `<button type="button" class="btn secondary btn-sm btn-accept-med-name" data-id="${escapeHtml(m.id || "")}" data-name="${escapeHtml(m.identity_match)}">Use ${escapeHtml(m.identity_match)}</button>`
    );
  }
  return bits.join("");
}

function formatMedSafetyWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderMedSafetyHomeStatus(profile) {
  const el = document.getElementById("med-safety-home-status");
  if (!el) return;
  const saved = profile?.medication_safety;
  if (!saved?.ran_at) {
    el.textContent = "";
    el.classList.add("hidden");
    return;
  }
  const result = saved.result || {};
  const when = formatMedSafetyWhen(saved.ran_at);
  if (result.all_clear) {
    el.textContent = `Last oversight ${when}: no concerning interactions or dosage issues flagged.`;
  } else {
    const n =
      (result.interactions || []).length + (result.dosage_concerns || []).length;
    el.textContent = `Last oversight ${when}: ${n} item${n === 1 ? "" : "s"} to review in Tasks.`;
  }
  el.classList.remove("hidden");
}

function renderMedSafetyResult(saved) {
  const el = document.getElementById("med-safety-result");
  if (!el) return;
  if (!saved?.result) {
    el.innerHTML = "";
    el.classList.add("hidden");
    return;
  }
  const r = saved.result;
  const when = formatMedSafetyWhen(saved.ran_at);
  let html = `<p class="muted small">Ran ${escapeHtml(when)}</p>`;
  html += `<p class="med-safety-summary${r.all_clear ? " all-clear" : ""}">${escapeHtml(r.overall_summary || "")}</p>`;

  if ((r.interactions || []).length) {
    html += `<div class="med-safety-section"><h4>Possible interactions</h4><ul class="med-safety-list">`;
    for (const item of r.interactions) {
      const drugs = (item.drugs || []).join(" + ");
      html += `<li><span class="med-safety-sev ${escapeHtml(item.severity || "moderate")}">${escapeHtml(item.severity || "moderate")}</span>
        <strong>${escapeHtml(drugs)}</strong> — ${escapeHtml(item.summary || "")}
        ${item.advice ? `<br><span class="muted small">${escapeHtml(item.advice)}</span>` : ""}</li>`;
    }
    html += `</ul></div>`;
  }

  if ((r.dosage_concerns || []).length) {
    html += `<div class="med-safety-section"><h4>Possible dosage concerns</h4><ul class="med-safety-list">`;
    for (const item of r.dosage_concerns) {
      html += `<li><span class="med-safety-sev ${escapeHtml(item.issue || "unclear")}">${escapeHtml(item.issue || "unclear")}</span>
        <strong>${escapeHtml(item.drug || "")}</strong> — ${escapeHtml(item.summary || "")}
        ${item.advice ? `<br><span class="muted small">${escapeHtml(item.advice)}</span>` : ""}</li>`;
    }
    html += `</ul></div>`;
  }

  if ((r.unidentified || []).length) {
    html += `<div class="med-safety-section"><h4>Not identified as known medications</h4><ul class="med-safety-list">`;
    for (const name of r.unidentified) {
      html += `<li>${escapeHtml(name)}</li>`;
    }
    html += `</ul></div>`;
  }

  if (r.disclaimer) {
    html += `<p class="muted small">${escapeHtml(r.disclaimer)}</p>`;
  }
  el.innerHTML = html;
  el.classList.remove("hidden");
}

function renderMilestonesSettings(profile) {
  fillMilestonePresetSelects();
  const el = document.getElementById("patient-milestones-list");
  if (!el) return;
  const rows = profile?.milestones || [];
  if (!rows.length) {
    el.innerHTML = `<p class="muted small">No lifestyle milestones yet. Add exercise, diet changes, and similar dated events here or from Labs overlays.</p>`;
    return;
  }
  el.innerHTML = rows
    .map((m) => {
      const when = formatDiagDate(m.date) || m.date || "";
      const notes = m.notes ? ` · ${escapeHtml(m.notes)}` : "";
      const tint =
        colorizeMilestoneEvents([{ id: m.id, label: m.label, source: "custom" }])[0]?.color ||
        MILESTONE_COLORS[0];
      return `<div class="medication-row" data-id="${escapeHtml(m.id || "")}">
        <div class="medication-row-main">
          <strong><span class="diag-milestone-swatch" style="background:${escapeHtml(tint)};margin-right:0.35rem"></span>${escapeHtml(m.label || "")}</strong>
          <p class="medication-row-meta">${escapeHtml(when)}${notes}</p>
        </div>
        <div class="medication-row-actions">
          <button type="button" class="btn ghost btn-sm btn-delete-milestone" data-id="${escapeHtml(m.id || "")}">Remove</button>
        </div>
      </div>`;
    })
    .join("");
}

function resolveMilestoneLabelAndKind(presetSelId, customId) {
  const sel = document.getElementById(presetSelId);
  const custom = document.getElementById(customId);
  const presetLabel = sel?.value || "";
  const kind = sel?.selectedOptions?.[0]?.dataset?.kind || "lifestyle";
  if (String(presetLabel).toLowerCase() === "other") {
    const label = custom?.value?.trim() || "";
    return { label, kind: "other" };
  }
  return { label: presetLabel, kind };
}

async function addMilestoneFromForm({ presetSelId, customId, dateId, notesId }) {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  const { label, kind } = resolveMilestoneLabelAndKind(presetSelId, customId);
  if (!label) return toast("Enter a milestone label", "error");
  const date = document.getElementById(dateId)?.value || "";
  if (!date) return toast("Pick a date", "error");
  const notes = notesId ? document.getElementById(notesId)?.value.trim() || null : null;
  const res = await fetch(`/api/patients/${state.activePatientId}/milestones`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, date, kind, notes }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Could not add milestone");
  }
  const data = await res.json();
  applyProfileResponse(data);
  toast("Milestone added");
  return data;
}

function renderMedicationsSettings(profile) {
  const el = document.getElementById("patient-medications-list");
  if (!el) return;
  const meds = profile?.medications || [];
  if (!meds.length) {
    el.innerHTML = `<p class="muted small">No medications yet.</p>`;
    return;
  }
  const active = meds.filter((m) => (m.status || "active") === "active");
  const stopped = meds.filter((m) => m.status === "stopped");
  const rowHtml = (m, { stopped: isStopped } = {}) => {
    const started = m.started_at ? `Started ${formatDiagDate(m.started_at)}` : "";
    const endedBit = m.stopped_at ? `Ended ${formatDiagDate(m.stopped_at)}` : "";
    const notes = m.notes ? escapeHtml(m.notes) : "";
    const meta = [formatMedicationDoseLine(m), started, endedBit, notes].filter(Boolean).join(" · ");
    const actions = `${formatMedicationFixActions(m)}
         ${isStopped ? "" : `<button type="button" class="btn ghost btn-sm btn-stop-medication" data-id="${escapeHtml(m.id)}">Stop</button>`}
         <button type="button" class="btn ghost btn-sm btn-delete-medication" data-id="${escapeHtml(m.id)}">Remove</button>`;
    return `<div class="medication-row" data-id="${escapeHtml(m.id)}">
      <div class="medication-row-main">
        <strong>${escapeHtml(m.name || "")}</strong>${formatMedicationIdentityBadge(m)}
        <p class="medication-row-meta">${escapeHtml(meta)}</p>
        ${formatMedicationConditions(m)}
        ${formatMedicationHistory(m)}
      </div>
      <div class="medication-row-actions">${actions}</div>
    </div>`;
  };
  let html = active.map((m) => rowHtml(m)).join("");
  if (stopped.length) {
    html += `<h5 class="medication-stopped-heading">Stopped</h5>`;
    html += stopped.map((m) => rowHtml(m, { stopped: true })).join("");
  }
  el.innerHTML = html || `<p class="muted small">No medications yet.</p>`;
}

function clearMedicationForm() {
  const idEl = document.getElementById("med-edit-id");
  if (idEl) idEl.value = "";
  ["med-name", "med-dose-amount", "med-dose-unit-other", "med-frequency", "med-conditions", "med-notes", "med-history-note"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const unitEl = document.getElementById("med-dose-unit");
  if (unitEl) unitEl.value = "";
  syncMedDoseUnitOtherVisibility();
  updateMedIdentityHint(null);
  const started = document.getElementById("med-started");
  if (started) started.value = "";
  const ended = document.getElementById("med-ended");
  if (ended) ended.value = "";
  const effective = document.getElementById("med-effective-at");
  if (effective) effective.value = "";
  document.querySelectorAll(".med-history-note-wrap").forEach((el) => el.classList.add("hidden"));
  const saveBtn = document.getElementById("btn-save-medication");
  if (saveBtn) saveBtn.textContent = "Add medication";
  document.getElementById("btn-cancel-med-edit")?.classList.add("hidden");
}

function fillMedicationForm(m) {
  document.getElementById("med-edit-id").value = m.id || "";
  document.getElementById("med-name").value = m.name || "";
  setMedicationDosageFields(m.dosage || "");
  document.getElementById("med-frequency").value = m.frequency || "";
  document.getElementById("med-conditions").value = (m.conditions || []).join(", ");
  document.getElementById("med-notes").value = m.notes || "";
  document.getElementById("med-started").value = m.started_at ? String(m.started_at).slice(0, 10) : "";
  document.getElementById("med-ended").value = m.stopped_at ? String(m.stopped_at).slice(0, 10) : "";
  document.getElementById("med-history-note").value = "";
  const effective = document.getElementById("med-effective-at");
  if (effective) {
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    effective.value = iso;
  }
  document.querySelectorAll(".med-history-note-wrap").forEach((el) => el.classList.remove("hidden"));
  updateMedIdentityHint(m);
  const saveBtn = document.getElementById("btn-save-medication");
  if (saveBtn) saveBtn.textContent = "Save changes";
  document.getElementById("btn-cancel-med-edit")?.classList.remove("hidden");
  document.getElementById("med-name")?.focus();
}

async function openMedicationEditor(medId, { focusDose = false } = {}) {
  if (!state.activePatientId || !medId) return toast("Select a patient first", "error");
  const res = await fetch(`/api/patients/${state.activePatientId}/profile`);
  if (!res.ok) return toast("Could not load medication", "error");
  const data = await res.json();
  const med = (data.profile?.medications || []).find((m) => m.id === medId);
  if (!med) return toast("Medication not found", "error");
  fillMedicationForm(med);
  switchTab("settings", {
    settingsSection: "profile",
    settingsFocus: focusDose ? "#med-dose-amount" : "#med-name",
  });
  requestAnimationFrame(() => {
    document.getElementById("medication-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

async function acceptSuggestedMedName(medId, suggestedName) {
  if (!state.activePatientId || !medId || !suggestedName) return;
  const res = await fetch(`/api/patients/${state.activePatientId}/medications/${medId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: suggestedName }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return toast(err.detail || "Could not rename medication", "error");
  }
  applyProfileResponse(await res.json());
  toast(`Renamed to ${suggestedName}`);
}

function setMedImportStatus(text, { error = false } = {}) {
  const el = document.getElementById("med-import-status");
  if (!el) return;
  if (!text) {
    el.textContent = "";
    el.classList.add("hidden");
    return;
  }
  el.textContent = text;
  el.classList.toggle("error-text", error);
  el.classList.remove("hidden");
}

function clearMedImportReview() {
  const wrap = document.getElementById("med-import-review");
  const list = document.getElementById("med-import-review-list");
  if (list) list.innerHTML = "";
  wrap?.classList.add("hidden");
  const file = document.getElementById("med-import-file");
  if (file) file.value = "";
  setMedImportStatus("");
}

function setDiagImportStatus(text, { error = false } = {}) {
  const el = document.getElementById("diag-import-status");
  if (!el) return;
  if (!text) {
    el.textContent = "";
    el.classList.add("hidden");
    return;
  }
  el.textContent = text;
  el.classList.toggle("error-text", error);
  el.classList.remove("hidden");
}

function clearDiagImportReview() {
  const wrap = document.getElementById("diag-import-review");
  const list = document.getElementById("diag-import-review-list");
  if (list) list.innerHTML = "";
  wrap?.classList.add("hidden");
  const file = document.getElementById("diag-import-file");
  if (file) file.value = "";
  state.diagImportSourceDocumentId = null;
  setDiagImportStatus("");
}

function renderDiagImportReview(data) {
  const wrap = document.getElementById("diag-import-review");
  const list = document.getElementById("diag-import-review-list");
  if (!wrap || !list) return;
  // Ensure Settings → Profile is visible for review when importing from Library
  switchTab("settings", { settingsSection: "profile", settingsFocus: "#diag-import-review" });
  const proposed = data.proposed || [];
  const meta = data.extraction_meta || {};
  state.diagImportSourceDocumentId = meta.document_id || null;
  const warnings = data.warnings || [];
  const method = meta.extraction_method || meta.source || "unknown";
  const chars = meta.extracted_chars != null ? `${meta.extracted_chars} chars` : "";
  const bits = [`Extracted via ${method}${chars ? ` · ${chars}` : ""}`];
  if (meta.title) bits.push(String(meta.title));
  if (warnings.length) bits.push(warnings.join(" · "));
  setDiagImportStatus(bits.join(" — "));

  if (!proposed.length) {
    list.innerHTML = `<p class="muted small">No lab readings detected. Try Re-extract / OCR on the document, or a clearer PDF.</p>`;
    wrap.classList.remove("hidden");
    return;
  }

  list.innerHTML = proposed
    .map((d, i) => {
      const dateVal = d.recorded_at ? String(d.recorded_at).slice(0, 10) : "";
      return `<div class="med-import-row" data-idx="${i}">
        <label class="med-import-check">
          <input type="checkbox" class="diag-import-select" checked aria-label="Include ${escapeHtml(d.name || "reading")}">
        </label>
        <div class="med-import-row-fields">
          <label>Name<input type="text" class="diag-import-name" maxlength="120" list="diag-name-presets" value="${escapeHtml(d.name || "")}"></label>
          <label>Value<input type="number" class="diag-import-value" step="any" value="${escapeHtml(d.value != null ? String(d.value) : "")}"></label>
          <label>Unit<input type="text" class="diag-import-unit" maxlength="40" list="diag-unit-presets" value="${escapeHtml(d.unit || "")}"></label>
          <label>Date<input type="date" class="diag-import-date" value="${escapeHtml(dateVal)}"></label>
          <label class="med-import-span-2">Notes<input type="text" class="diag-import-notes" maxlength="500" value="${escapeHtml(d.notes || "")}"></label>
        </div>
      </div>`;
    })
    .join("");
  wrap.classList.remove("hidden");
  requestAnimationFrame(() => {
    wrap.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function collectDiagImportSelected() {
  const rows = document.querySelectorAll("#diag-import-review-list .med-import-row");
  const out = [];
  rows.forEach((row) => {
    const checked = row.querySelector(".diag-import-select")?.checked;
    if (!checked) return;
    const name = row.querySelector(".diag-import-name")?.value.trim();
    const valueRaw = row.querySelector(".diag-import-value")?.value;
    const recordedAt = row.querySelector(".diag-import-date")?.value;
    if (!name || valueRaw === "" || valueRaw == null || !recordedAt) return;
    const value = Number(valueRaw);
    if (!Number.isFinite(value)) return;
    out.push({
      name,
      value,
      unit: row.querySelector(".diag-import-unit")?.value.trim() || null,
      recorded_at: recordedAt,
      notes: row.querySelector(".diag-import-notes")?.value.trim() || null,
    });
  });
  return out;
}

async function confirmDiagImportAndShowCharts() {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  const diagnostics = collectDiagImportSelected();
  if (!diagnostics.length) {
    return toast("Select readings with name, value, and collection date", "error");
  }
  const btn = document.getElementById("btn-diag-import-confirm");
  if (btn) btn.disabled = true;
  try {
    const data = await api(`/api/patients/${state.activePatientId}/diagnostics/import/confirm`, {
      method: "POST",
      body: JSON.stringify({
        diagnostics,
        source_document_id: state.diagImportSourceDocumentId || null,
      }),
    });
    applyProfileResponse(data);
    clearDiagImportReview();
    toast(`Added ${data.added_count || diagnostics.length} lab reading(s)`);
    switchTab("analyze");
    setHomeSection("diagnostics", { scroll: true });
    refreshHandlingFlags({ rescan: true }).catch(() => {});
  } catch (err) {
    toast(err.message || "Could not add lab readings", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function importDiagnosticsFromLibraryDocument(docId) {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  if (!docId) return;
  setDiagImportStatus("Parsing lab results from document…");
  try {
    const data = await api(`/api/patients/${state.activePatientId}/diagnostics/import/from-document`, {
      method: "POST",
      body: JSON.stringify({ document_id: docId }),
      timeoutMs: 300000,
    });
    renderDiagImportReview(data);
    toast(`Parsed ${(data.proposed || []).length} lab reading(s)`);
  } catch (err) {
    setDiagImportStatus(err.message || "Import failed", { error: true });
    switchTab("settings", { settingsSection: "profile", settingsFocus: "#diag-import-status" });
    toast(err.message || "Import failed", "error");
  }
}

function renderMedImportReview(data) {
  const wrap = document.getElementById("med-import-review");
  const list = document.getElementById("med-import-review-list");
  if (!wrap || !list) return;
  const proposed = data.proposed || [];
  const meta = data.extraction_meta || {};
  const warnings = data.warnings || [];
  const method = meta.extraction_method || "unknown";
  const chars = meta.extracted_chars != null ? `${meta.extracted_chars} chars` : "";
  const bits = [`Extracted via ${method}${chars ? ` · ${chars}` : ""}`];
  if (warnings.length) bits.push(warnings.join(" · "));
  setMedImportStatus(bits.join(" — "));

  if (!proposed.length) {
    list.innerHTML = `<p class="muted small">No medications detected. Try a clearer photo or PDF.</p>`;
    wrap.classList.remove("hidden");
    return;
  }

  list.innerHTML = proposed
    .map((m, i) => {
      const conditions = Array.isArray(m.conditions) ? m.conditions.join(", ") : m.conditions || "";
      return `<div class="med-import-row" data-idx="${i}">
        <label class="med-import-check">
          <input type="checkbox" class="med-import-select" checked aria-label="Include ${escapeHtml(m.name || "medication")}">
        </label>
        <div class="med-import-row-fields">
          <label>Name<input type="text" class="med-import-name" maxlength="120" value="${escapeHtml(m.name || "")}"></label>
          <label>Dosage<input type="text" class="med-import-dosage" maxlength="80" value="${escapeHtml(m.dosage || "")}"></label>
          <label>Frequency<input type="text" class="med-import-frequency" maxlength="80" value="${escapeHtml(m.frequency || "")}"></label>
          <label>Started<input type="date" class="med-import-started" value="${escapeHtml(m.started_at ? String(m.started_at).slice(0, 10) : "")}"></label>
          <label>Ended<input type="date" class="med-import-ended" value="${escapeHtml(m.ended_at ? String(m.ended_at).slice(0, 10) : "")}"></label>
          <label class="med-import-span-2">Conditions<input type="text" class="med-import-conditions" maxlength="240" value="${escapeHtml(conditions)}"></label>
          <label class="med-import-span-2">Notes<input type="text" class="med-import-notes" maxlength="500" value="${escapeHtml(m.notes || "")}"></label>
        </div>
      </div>`;
    })
    .join("");
  wrap.classList.remove("hidden");
  wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function collectMedImportSelected() {
  const rows = document.querySelectorAll("#med-import-review-list .med-import-row");
  const out = [];
  rows.forEach((row) => {
    const checked = row.querySelector(".med-import-select")?.checked;
    if (!checked) return;
    const name = row.querySelector(".med-import-name")?.value.trim();
    if (!name) return;
    out.push({
      name,
      dosage: row.querySelector(".med-import-dosage")?.value.trim() || null,
      frequency: row.querySelector(".med-import-frequency")?.value.trim() || null,
      conditions: parseConditionsInput(row.querySelector(".med-import-conditions")?.value),
      notes: row.querySelector(".med-import-notes")?.value.trim() || null,
      started_at: row.querySelector(".med-import-started")?.value || null,
      ended_at: row.querySelector(".med-import-ended")?.value || null,
    });
  });
  return out;
}

function formatJournalDateTime(iso) {
  const raw = String(iso || "");
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) {
    const day = raw.slice(0, 10);
    return day || "—";
  }
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()} · ${hh}:${mm}`;
}

function formatJournalListRow(j) {
  const sev = j.severity != null ? ` · sev ${j.severity}/5` : "";
  const text = j.text ? ` · ${j.text}` : "";
  const caseBit = j.case_id ? " · case-linked" : "";
  return `<div class="patient-measurement-row" data-id="${escapeHtml(j.id)}">
    <div><span class="journal-kind-tag">${escapeHtml(j.kind || "note")}</span><strong>${escapeHtml(j.label || "")}</strong>${escapeHtml(sev)}${escapeHtml(text)} · ${escapeHtml(formatJournalDateTime(j.recorded_at))}${escapeHtml(caseBit)}</div>
    <button type="button" class="btn ghost btn-sm btn-delete-journal" data-id="${escapeHtml(j.id)}">Remove</button>
  </div>`;
}

function defaultJournalPresets() {
  return [
    // Positive first — so a day can read headache → med → better
    { label: "Feeling good", kind: "feeling" },
    { label: "Better", kind: "feeling" },
    { label: "OK / normal", kind: "feeling" },
    { label: "Energetic", kind: "feeling" },
    { label: "Pain-free", kind: "feeling" },
    { label: "Weak", kind: "feeling" },
    { label: "Headache", kind: "symptom" },
    { label: "Nauseous", kind: "symptom" },
    { label: "Dizzy", kind: "symptom" },
    { label: "Fatigue", kind: "symptom" },
    { label: "Pain", kind: "symptom" },
    { label: "Anxiety", kind: "feeling" },
    { label: "Took medication", kind: "medication" },
    { label: "Ate", kind: "note" },
    { label: "Slept", kind: "note" },
    { label: "Note", kind: "note" },
  ];
}

const POSITIVE_JOURNAL_LABELS = new Set([
  "feeling good",
  "better",
  "ok / normal",
  "energetic",
  "pain-free",
]);

function ensureJournalChips() {
  const feelingEl = document.getElementById("journal-feeling-chips");
  const actionEl = document.getElementById("journal-action-chips");
  if (!feelingEl || !actionEl) return;
  const presets = state.journalPresets.length ? state.journalPresets : defaultJournalPresets();
  const sig = presets.map((p) => `${p.kind}:${p.label}`).join("|");
  if (feelingEl.dataset.sig === sig) return;
  const feelings = presets.filter((p) => p.kind === "symptom" || p.kind === "feeling");
  const actions = presets.filter((p) => p.kind === "medication" || p.kind === "note");
  const chipHtml = (p) =>
    `<button type="button" class="journal-chip" data-kind="${escapeHtml(p.kind)}" data-label="${escapeHtml(p.label)}">${escapeHtml(p.label)}</button>`;
  feelingEl.innerHTML = feelings.map(chipHtml).join("");
  actionEl.innerHTML = actions.map(chipHtml).join("");
  feelingEl.dataset.sig = sig;
  actionEl.dataset.sig = sig;
}

function updateJournalDraftUi() {
  const draft = state.journalDraft || { kind: "note", label: "", severity: null };
  document.querySelectorAll(".journal-chip").forEach((btn) => {
    const on =
      btn.dataset.label === draft.label && btn.dataset.kind === draft.kind;
    btn.classList.toggle("is-selected", on);
  });
  document.querySelectorAll(".journal-sev-btn").forEach((btn) => {
    btn.classList.toggle("is-selected", String(draft.severity || "") === btn.dataset.sev);
  });
  const severityRow = document.getElementById("journal-severity-row");
  const severityLabel = severityRow?.querySelector(":scope > .muted, :scope > span");
  if (severityRow) {
    const show = draft.kind === "symptom" || draft.kind === "feeling";
    severityRow.classList.toggle("hidden", !show);
    const positive = POSITIVE_JOURNAL_LABELS.has(String(draft.label || "").toLowerCase());
    if (severityLabel) {
      severityLabel.textContent = positive ? "How good (optional)" : "Severity";
    }
    document.querySelectorAll(".journal-sev-btn").forEach((btn) => {
      const n = btn.dataset.sev;
      if (positive) {
        btn.title = n === "1" ? "A bit better" : n === "5" ? "Great" : "";
      } else {
        btn.title = n === "1" ? "Mild" : n === "3" ? "Moderate" : n === "5" ? "Severe" : "";
      }
    });
  }
  const hint = document.getElementById("journal-selected-hint");
  if (hint) {
    if (draft.label) {
      const sev = draft.severity ? ` · severity ${draft.severity}` : "";
      hint.textContent = `${draft.label}${sev}`;
    } else {
      hint.textContent = "Pick a chip or type a note";
    }
  }
}

function groupJournalClient(profile) {
  const groups = new Map();
  for (const row of profile?.journal || []) {
    const label = String(row.label || "").trim();
    if (!label) continue;
    const key = label.toLowerCase();
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name: label,
        label,
        kind: row.kind || "note",
        entries: [],
      });
    }
    const g = groups.get(key);
    if ((row.kind === "symptom" || row.kind === "feeling") && g.kind !== "symptom" && g.kind !== "feeling") {
      g.kind = row.kind;
    }
    g.entries.push({
      id: row.id,
      recorded_at: row.recorded_at,
      day: String(row.recorded_at || "").slice(0, 10),
      severity: row.severity != null ? Number(row.severity) : null,
      kind: row.kind,
      text: row.text,
    });
  }
  return [...groups.values()]
    .map((g) => {
      const entries = [...g.entries].sort((a, b) =>
        String(a.recorded_at || "").localeCompare(String(b.recorded_at || ""))
      );
      const byDay = new Map();
      for (const e of entries) {
        if (!e.day) continue;
        if (!byDay.has(e.day)) byDay.set(e.day, []);
        byDay.get(e.day).push(e);
      }
      const readings = [...byDay.keys()].sort().map((day) => {
        const rows = byDay.get(day);
        const sevs = rows.map((r) => r.severity).filter((v) => v != null && Number.isFinite(v));
        if (sevs.length) {
          return {
            recorded_at: day,
            value: Math.round((sevs.reduce((a, b) => a + b, 0) / sevs.length) * 100) / 100,
            count: rows.length,
            metric: "severity_avg",
          };
        }
        return { recorded_at: day, value: rows.length, count: rows.length, metric: "count" };
      });
      return {
        ...g,
        readings,
        latest: readings[readings.length - 1] || null,
        point_count: readings.length,
        entry_count: entries.length,
        unit: readings.some((r) => r.metric === "severity_avg") ? "sev" : "count",
      };
    })
    .sort((a, b) => {
      const kindRank = (k) => (k === "symptom" || k === "feeling" ? 0 : 1);
      return kindRank(a.kind) - kindRank(b.kind) || b.entry_count - a.entry_count || a.name.localeCompare(b.name);
    })
    .slice(0, 8);
}

function renderJournalHome(profile, series) {
  ensureJournalChips();
  updateJournalDraftUi();
  const recentEl = document.getElementById("journal-recent");
  const chartsEl = document.getElementById("journal-charts");
  if (!recentEl || !chartsEl) return;

  if (!profile) {
    recentEl.innerHTML = `<p class="muted small">Select a patient to log how you feel.</p>`;
    chartsEl.innerHTML = "";
    return;
  }

  const entries = profile.journal || [];
  if (!entries.length) {
    recentEl.innerHTML = `<p class="muted small">Nothing logged yet — use the heart to log.</p>`;
  } else {
    recentEl.innerHTML = entries
      .slice(0, 4)
      .map((j) => {
        const sev = j.severity != null ? ` · ${j.severity}/5` : "";
        const rawText = String(j.text || "").trim();
        const shortText =
          rawText.length > 72 ? `${rawText.slice(0, 71)}…` : rawText;
        const text = shortText ? ` · ${escapeHtml(shortText)}` : "";
        return `<div class="journal-recent-row">
          <div class="journal-recent-main">
            <span class="journal-kind-tag">${escapeHtml(j.kind || "note")}</span>
            <strong>${escapeHtml(j.label || "")}</strong>${escapeHtml(sev)}${text}
            <div class="muted small">${escapeHtml(formatJournalDateTime(j.recorded_at))}${j.case_id ? " · case-linked" : ""}</div>
          </div>
          <button type="button" class="btn ghost btn-sm btn-delete-journal" data-id="${escapeHtml(j.id)}">Remove</button>
        </div>`;
      })
      .join("");
  }

  const cards = (series || []).slice(0, 3);
  if (!cards.length) {
    chartsEl.innerHTML = "";
    return;
  }
  chartsEl.innerHTML = cards
    .map((s) => {
      const unitLabel = s.unit === "sev" ? "avg severity" : "reports/day";
      const latest = s.latest;
      const latestLabel = latest
        ? `${formatDiagValue(latest.value)} ${unitLabel} · ${formatDiagDate(latest.recorded_at)}`
        : "—";
      return `<article class="journal-chart-card">
        <div class="journal-chart-head">
          <h4 class="journal-chart-title">${escapeHtml(s.name || s.label)}</h4>
          <span class="muted small">${escapeHtml(latestLabel)}</span>
        </div>
        ${buildSparklineSvg(s.readings || [], { stroke: "var(--accent-warm, var(--accent))" })}
        <p class="journal-chart-meta">${escapeHtml(s.kind || "note")} · ${s.entry_count || 0} log${(s.entry_count || 0) === 1 ? "" : "s"}</p>
      </article>`;
    })
    .join("");
}

function applyProfileResponse(data) {
  renderPatientProfile(data.profile || {}, state.activePatientId, {
    diagnostic_series: data.diagnostic_series,
    diagnostic_presets: data.diagnostic_presets,
    journal_series: data.journal_series,
    journal_presets: data.journal_presets,
    milestone_presets: data.milestone_presets,
  });
  const subEl = document.getElementById("header-patient-sub");
  if (subEl) subEl.textContent = formatPatientSubline(data.profile || {});
}

function resolveDiagnosticSeries(profile, extras = {}) {
  const fromClient = groupDiagnosticsClient(profile || {});
  const fromServer = extras.diagnostic_series;
  // Prefer server series (reference bands + status + date dedupe).
  if (Array.isArray(fromServer) && fromServer.length) return fromServer;
  return fromClient;
}

function dedupeReadingsByDate(readings) {
  const byDate = new Map();
  for (const r of [...(readings || [])].sort((a, b) =>
    String(a.recorded_at || "").localeCompare(String(b.recorded_at || ""))
  )) {
    const day = String(r.recorded_at || "").slice(0, 10);
    if (!day) continue;
    byDate.set(day, r);
  }
  return [...byDate.values()];
}

function groupDiagnosticsClient(profile) {
  const groups = new Map();
  for (const row of profile.diagnostics || []) {
    const name = String(row.name || "").trim();
    if (!name || row.value == null) continue;
    const key = name.toLowerCase();
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name,
        unit: row.unit || null,
        category: row.category || "blood",
        readings: [],
      });
    }
    const g = groups.get(key);
    if (row.unit && !g.unit) g.unit = row.unit;
    if (row.category === "blood") g.category = "blood";
    g.readings.push({
      id: row.id,
      recorded_at: String(row.recorded_at || "").slice(0, 10),
      value: row.value,
      notes: row.notes,
    });
  }
  return [...groups.values()]
    .map((g) => {
      const raw = [...g.readings].sort((a, b) =>
        String(a.recorded_at || "").localeCompare(String(b.recorded_at || ""))
      );
      const readings = dedupeReadingsByDate(raw);
      return {
        ...g,
        readings,
        latest: readings[readings.length - 1] || null,
        point_count: readings.length,
        raw_count: raw.length,
      };
    })
    .sort((a, b) => {
      const catRank = (c) => (c === "blood" ? 0 : c === "vital" ? 1 : c === "imaging" ? 2 : 3);
      const multi = (s) => (s.point_count > 1 ? 0 : 1);
      return (
        catRank(a.category) - catRank(b.category) ||
        multi(a) - multi(b) ||
        b.point_count - a.point_count ||
        a.name.localeCompare(b.name)
      );
    });
}

function weightSeriesFromProfile(profile) {
  const points = dedupeReadingsByDate(
    (profile?.measurements || [])
      .filter((m) => m.weight_kg != null)
      .map((m) => ({
        recorded_at: String(m.recorded_at || "").slice(0, 10),
        value: Number(m.weight_kg),
        id: m.id,
      }))
  ).sort((a, b) => String(a.recorded_at || "").localeCompare(String(b.recorded_at || "")));
  if (!points.length) return null;
  return {
    key: "weight",
    name: "Weight",
    unit: "kg",
    readings: points,
    latest: points[points.length - 1],
    point_count: points.length,
  };
}

function bmiSeriesFromProfile(profile) {
  const points = dedupeReadingsByDate(
    (profile?.measurements || [])
      .map((m) => {
        const bmi = bmiFor(m.height_cm, m.weight_kg);
        if (bmi == null) return null;
        return {
          recorded_at: String(m.recorded_at || "").slice(0, 10),
          value: Number(bmi),
          id: m.id,
        };
      })
      .filter(Boolean)
  ).sort((a, b) => String(a.recorded_at || "").localeCompare(String(b.recorded_at || "")));
  if (points.length < 1) return null;
  return {
    key: "bmi",
    name: "BMI",
    unit: null,
    readings: points,
    latest: points[points.length - 1],
    point_count: points.length,
  };
}

function formatDiagDate(iso) {
  const raw = String(iso || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw || "—";
  const d = new Date(`${raw}T12:00:00`);
  if (Number.isNaN(d.getTime())) return raw;
  // Explicit parts avoid locale truncation in tight SVG/HTML layouts
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

function formatDiagDateAxis(iso) {
  const raw = String(iso || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw || "—";
  const d = new Date(`${raw}T12:00:00`);
  if (Number.isNaN(d.getTime())) return raw;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // Compact but complete: Nov 14 · 2026
  return `${months[d.getMonth()]} ${d.getDate()} · ${d.getFullYear()}`;
}

function formatDiagValue(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value ?? "");
  if (Number.isInteger(n)) return String(n);
  const abs = Math.abs(n);
  if (abs >= 100) return n.toFixed(0);
  if (abs >= 10) return n.toFixed(1);
  return String(parseFloat(n.toFixed(2)));
}

function formatReferenceMeta(ref) {
  if (!ref) return "";
  const bits = [ref.label].filter(Boolean);
  if (ref.note) bits.push(ref.note);
  return bits.join(" · ");
}

function statusColor(status) {
  if (status === "green") return "#15803d";
  if (status === "yellow") return "#ca8a04";
  if (status === "red") return "#b91c1c";
  return "var(--accent)";
}

function statusLabel(status) {
  if (status === "green") return "On target";
  if (status === "yellow") return "Near target (±10%)";
  if (status === "red") return "Off target";
  return "";
}

const DIAG_STATUS_FILTERS = [
  { id: "all", label: "All" },
  { id: "red", label: "Off target" },
  { id: "yellow", label: "Near target" },
  { id: "green", label: "On target" },
];

function diagSeriesStatus(s) {
  const latest = s?.latest;
  return (
    s?.status ||
    latest?.status ||
    clientStatusForValue(latest?.value, s?.reference) ||
    null
  );
}

function diagStatusSortRank(status) {
  if (status === "red") return 0;
  if (status === "yellow") return 1;
  if (status === "green") return 2;
  return 3;
}

function renderDiagnosticsStatusFilter(cards) {
  const bar = document.getElementById("diagnostics-status-filter");
  if (!bar) return;
  if (!cards.length) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return;
  }
  const counts = { all: cards.length, red: 0, yellow: 0, green: 0, none: 0 };
  for (const s of cards) {
    const st = diagSeriesStatus(s);
    if (st === "red" || st === "yellow" || st === "green") counts[st] += 1;
    else counts.none += 1;
  }
  const active = DIAG_STATUS_FILTERS.some((f) => f.id === state.diagStatusFilter)
    ? state.diagStatusFilter
    : "all";
  state.diagStatusFilter = active;
  bar.classList.remove("hidden");
  bar.innerHTML = DIAG_STATUS_FILTERS.map((f) => {
    const n = counts[f.id] ?? 0;
    const pressed = active === f.id;
    const tone = f.id === "all" ? "" : ` diag-filter-${f.id}`;
    return `<button type="button" class="diag-status-filter-btn${tone}${pressed ? " is-active" : ""}" data-diag-status-filter="${f.id}" aria-pressed="${pressed ? "true" : "false"}">${escapeHtml(f.label)} <span class="diag-status-filter-count">${n}</span></button>`;
  }).join("");
}

function clientStatusForValue(value, reference) {
  if (value == null || !reference) return null;
  const v = Number(value);
  if (!Number.isFinite(v)) return null;
  const low = reference.low != null && Number.isFinite(Number(reference.low)) ? Number(reference.low) : null;
  const high = reference.high != null && Number.isFinite(Number(reference.high)) ? Number(reference.high) : null;
  const direction = reference.direction || "range";
  const band = (distance, threshold) => {
    if (!threshold) return Math.abs(distance) > 0 ? "yellow" : "green";
    const pct = Math.abs(distance) / Math.abs(threshold);
    if (pct <= 1e-9) return "green";
    if (pct <= 0.1) return "yellow";
    return "red";
  };
  if (direction === "lower_better" && high != null) {
    if (v <= high) return "green";
    return band(v - high, high);
  }
  if (direction === "higher_better" && low != null) {
    if (v >= low) return "green";
    return band(low - v, low);
  }
  if (low != null && high != null) {
    if (v >= low && v <= high) return "green";
    if (v < low) return band(low - v, low);
    return band(v - high, high);
  }
  if (high != null) {
    if (v <= high) return "green";
    return band(v - high, high);
  }
  if (low != null) {
    if (v >= low) return "green";
    return band(low - v, low);
  }
  return null;
}

function buildSparklineSvg(readings, { stroke = "var(--accent)", reference = null, milestones = null } = {}) {
  const points = dedupeReadingsByDate(readings || [])
    .map((r) => ({
      date: String(r.recorded_at || "").slice(0, 10),
      value: Number(r.value),
      status: r.status || clientStatusForValue(r.value, reference),
    }))
    .filter((p) => p.date && Number.isFinite(p.value))
    .sort((a, b) => a.date.localeCompare(b.date));
  if (!points.length) {
    return `<div class="diag-chart-plot"><svg class="diag-chart-svg" viewBox="0 0 360 150" role="img"><text x="180" y="75" text-anchor="middle" fill="currentColor" font-size="16">No data</text></svg></div>`;
  }

  const refLow = reference && Number.isFinite(Number(reference.low)) ? Number(reference.low) : null;
  const refHigh = reference && Number.isFinite(Number(reference.high)) ? Number(reference.high) : null;
  const hasRef = refLow != null || refHigh != null;
  const latestStatus = points[points.length - 1]?.status;
  const lineStroke = latestStatus ? statusColor(latestStatus) : stroke;

  if (points.length === 1) {
    const p = points[0];
    const color = statusColor(p.status);
    const refLine = hasRef
      ? `<text x="180" y="128" text-anchor="middle" fill="currentColor" font-size="13" opacity="0.75">${escapeHtml(reference.label || "Reference")}</text>`
      : "";
    return `<div class="diag-chart-plot">
      <svg class="diag-chart-svg" viewBox="0 0 360 150" role="img" aria-label="Single reading">
        <text x="180" y="58" text-anchor="middle" fill="${color}" font-size="36" font-weight="700">${escapeHtml(formatDiagValue(p.value))}</text>
        <text x="180" y="92" text-anchor="middle" fill="currentColor" font-size="15" opacity="0.85">${escapeHtml(formatDiagDate(p.date))}</text>
        ${refLine}
      </svg>
    </div>`;
  }

  const padX = 32;
  const padTop = 28;
  const padBottom = 28;
  const w = 360;
  const h = 150;
  const values = points.map((p) => p.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (refLow != null) min = Math.min(min, refLow);
  if (refHigh != null) max = Math.max(max, refHigh);
  const pad = (max - min) * 0.08 || Math.abs(max) * 0.05 || 0.2;
  min -= pad;
  max += pad;
  const span = max - min || 1;
  const yFor = (value) => h - padBottom - ((value - min) / span) * (h - padTop - padBottom);
  const toDay = (iso) => new Date(`${String(iso).slice(0, 10)}T12:00:00`).getTime();
  const readMin = toDay(points[0].date);
  const readMax = toDay(points[points.length - 1].date);
  const dayMs = 86400000;
  // Stretch the axis so meds started just after the last lab still plot with visible headroom
  const inRange = filterMilestonesForRange(
    milestones,
    points[0].date,
    points[points.length - 1].date,
    DIAG_MILESTONE_PAD_DAYS
  );
  let tMin = readMin;
  let tMax = readMax;
  const pre = [];
  const post = [];
  for (const ev of inRange) {
    const t = toDay(ev.date);
    if (!Number.isFinite(t)) continue;
    if (t < readMin) pre.push(t);
    if (t > readMax) post.push(t);
  }
  if (pre.length) {
    const earliest = Math.min(...pre);
    const minLead = Math.max((readMax - readMin) * 0.18, 40 * dayMs);
    tMin = Math.min(earliest, readMin - minLead);
  }
  if (post.length) {
    const latest = Math.max(...post);
    // Enough empty time after the last lab so a Sep 1 start isn't glued to Aug 27
    const minHead = Math.max((readMax - readMin) * 0.22, 50 * dayMs);
    tMax = Math.max(latest, readMax + minHead);
  }
  const tSpan = tMax - tMin || 1;
  const xFor = (iso) => padX + ((toDay(iso) - tMin) / tSpan) * (w - padX * 2);
  const coords = points.map((p) => ({ ...p, x: xFor(p.date), y: yFor(p.value) }));

  let milestoneLayer = "";
  let milestoneLegend = "";
  if (inRange.length) {
    const markers = inRange.slice(0, 6).map((ev) => ({
      ...ev,
      x: xFor(ev.date),
    }));
    markers.sort((a, b) => a.x - b.x || String(a.date).localeCompare(String(b.date)));
    const minGap = 18;
    for (let i = 1; i < markers.length; i++) {
      if (markers[i].x - markers[i - 1].x < minGap) {
        markers[i].x = Math.min(w - padX, markers[i - 1].x + minGap);
      }
    }
    milestoneLayer = markers
      .map((ev) => {
        let x = ev.x;
        if (!Number.isFinite(x)) return "";
        x = Math.min(w - padX, Math.max(padX, x));
        const color = ev.color || "#0f766e";
        const top = padTop - 2;
        const bot = h - padBottom;
        return `<g class="diag-milestone-mark">
          <line x1="${x.toFixed(1)}" y1="${top}" x2="${x.toFixed(1)}" y2="${bot}" stroke="${color}" stroke-width="2.6" stroke-dasharray="6 4" opacity="1">
            <title>${escapeHtml(ev.label)}</title>
          </line>
          <polygon points="${(x - 5.5).toFixed(1)},${top} ${(x + 5.5).toFixed(1)},${top} ${x.toFixed(1)},${(top + 9).toFixed(1)}" fill="${color}" />
          <circle cx="${x.toFixed(1)}" cy="${bot}" r="3.6" fill="${color}" stroke="#fff" stroke-width="1" />
        </g>`;
      })
      .join("");
    milestoneLegend = `<div class="diag-milestone-legend" aria-label="Timeline milestones on this chart">
      ${markers
        .map((ev) => {
          const short =
            String(ev.label || "").length > 42
              ? `${String(ev.label).slice(0, 41)}…`
              : ev.label;
          const color = ev.color || "#0f766e";
          return `<span class="diag-milestone-badge" title="${escapeHtml(ev.label)}" style="border-color:${escapeHtml(color)};background:color-mix(in srgb, ${escapeHtml(color)} 16%, var(--surface));color:color-mix(in srgb, ${escapeHtml(color)} 82%, #0f172a)"><span class="diag-milestone-badge-dot" style="background:${escapeHtml(color)}" aria-hidden="true"></span>${escapeHtml(short)}</span>`;
        })
        .join("")}
    </div>`;
  }

  let refLayer = "";
  if (hasRef) {
    const bandTop = yFor(refHigh != null ? refHigh : max);
    const bandBottom = yFor(refLow != null ? refLow : min);
    const y1 = Math.min(bandTop, bandBottom);
    const y2 = Math.max(bandTop, bandBottom);
    refLayer = `<rect x="${padX}" y="${y1.toFixed(1)}" width="${(w - padX * 2).toFixed(1)}" height="${Math.max(2, y2 - y1).toFixed(1)}" fill="${lineStroke}" opacity="0.10"></rect>`;
    if (refHigh != null) {
      const y = yFor(refHigh);
      refLayer += `<line x1="${padX}" y1="${y.toFixed(1)}" x2="${w - padX}" y2="${y.toFixed(1)}" stroke="${lineStroke}" stroke-width="1.4" stroke-dasharray="4 3" opacity="0.55"></line>`;
    }
    if (refLow != null) {
      const y = yFor(refLow);
      refLayer += `<line x1="${padX}" y1="${y.toFixed(1)}" x2="${w - padX}" y2="${y.toFixed(1)}" stroke="${lineStroke}" stroke-width="1.4" stroke-dasharray="4 3" opacity="0.55"></line>`;
    }
  }

  const poly = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
  const dots = coords
    .map((c) => {
      const label = formatDiagValue(c.value);
      const fill = statusColor(c.status);
      const valueY = c.y - 12;
      // White stroke halo keeps values readable when a milestone dash crosses them
      return `<g>
        <circle cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="4.2" fill="${fill}" stroke="#fff" stroke-width="1.5">
          <title>${escapeHtml(formatDiagDate(c.date))}: ${escapeHtml(label)}${c.status ? ` (${statusLabel(c.status)})` : ""}</title>
        </circle>
        <text x="${c.x.toFixed(1)}" y="${valueY.toFixed(1)}" text-anchor="middle" fill="currentColor" font-size="13" font-weight="700" stroke="#fff" stroke-width="4" paint-order="stroke fill">${escapeHtml(label)}</text>
      </g>`;
    })
    .join("");

  // Axis labels from readings only — milestone dates use the badges below (avoids Sep/Aug mash)
  const dateLabels = coords
    .map((c, i) => {
      const anchor = i === 0 ? "start" : i === coords.length - 1 ? "end" : "middle";
      return `<text x="${c.x.toFixed(1)}" y="${(h - 8).toFixed(1)}" text-anchor="${anchor}" fill="currentColor" font-size="10" opacity="0.85" stroke="#fff" stroke-width="3" paint-order="stroke fill">${escapeHtml(formatDiagDateAxis(c.date))}</text>`;
    })
    .join("");

  // Draw order: ref band → milestones (behind) → series → value/date labels (on top)
  return `<div class="diag-chart-plot">
    <svg class="diag-chart-svg" viewBox="0 0 360 150" role="img" aria-label="Trend">
      ${refLayer}
      ${milestoneLayer}
      <polyline fill="none" stroke="${lineStroke}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" points="${poly}" />
      ${dots}
      ${dateLabels}
    </svg>
    ${milestoneLegend}
  </div>`;
}

function renderDiagnosticsCharts(profile, series, opts = {}) {
  const wrap = document.getElementById("diagnostics-charts");
  if (!wrap) return;
  const bloodFirst = [...(series || [])];
  const extras = [];
  const weight = weightSeriesFromProfile(profile);
  const bmi = bmiSeriesFromProfile(profile);
  if (weight && weight.point_count >= 2) extras.push({ ...weight, category: "vital" });
  if (bmi && bmi.point_count >= 2) {
    const ref = {
      low: 18.5,
      high: 24.9,
      label: "Healthy 18.5–24.9",
      direction: "range",
      note: "WHO adult BMI",
      meaning: "Body mass index relates weight to height. It is a screening tool, not a complete health measure.",
      info_url: "https://medlineplus.gov/ency/article/007196.htm",
      info_source: "MedlinePlus",
    };
    const readings = (bmi.readings || []).map((r) => ({
      ...r,
      status: clientStatusForValue(r.value, ref),
    }));
    const latest = readings[readings.length - 1] || null;
    extras.push({
      ...bmi,
      category: "vital",
      reference: ref,
      readings,
      latest,
      status: latest?.status || null,
    });
  }

  const cards = [...bloodFirst, ...extras];
  const allEvents = allChartMilestones(profile);
  if (!opts.skipControls) {
    renderDiagnosticsMilestoneControls(profile, allEvents, cards);
  }
  if (!cards.length) {
    renderDiagnosticsStatusFilter([]);
    wrap.innerHTML = `<p class="muted small" id="diagnostics-empty">No blood-test trends yet. Add lab readings in Settings using each report’s collection / date of service.</p>`;
    return;
  }

  const ranked = cards
    .map((s, i) => ({ s, i, status: diagSeriesStatus(s) }))
    .sort((a, b) => {
      const byStatus = diagStatusSortRank(a.status) - diagStatusSortRank(b.status);
      if (byStatus) return byStatus;
      return a.i - b.i;
    });
  renderDiagnosticsStatusFilter(ranked.map((r) => r.s));
  const filter = DIAG_STATUS_FILTERS.some((f) => f.id === state.diagStatusFilter)
    ? state.diagStatusFilter
    : "all";
  const visible =
    filter === "all" ? ranked : ranked.filter((r) => r.status === filter);

  const span = seriesDateSpan(cards);
  const inSpan = milestonesForLabSpan(allEvents, span);
  const prefs = state.diagMilestonePrefs || loadDiagMilestonePrefs(state.activePatientId, inSpan);
  const milestones = opts.milestones || visibleDiagMilestones(inSpan, prefs);

  if (!visible.length) {
    const label =
      DIAG_STATUS_FILTERS.find((f) => f.id === filter)?.label || "this filter";
    wrap.innerHTML = `<p class="muted small" id="diagnostics-empty">No labs marked ${escapeHtml(label.toLowerCase())}. Choose All to see every chart.</p>`;
    return;
  }

  wrap.innerHTML = visible
    .map(({ s, status }) => {
      const unit = s.unit ? ` ${s.unit}` : "";
      const latest = s.latest;
      const latestLabel = latest
        ? `${formatDiagValue(latest.value)}${unit} · ${formatDiagDate(latest.recorded_at)}`
        : "—";
      const stroke = statusColor(status);
      const cat = s.category === "imaging" ? "Imaging" : s.category === "vital" ? "Vitals" : "Blood";
      const dateSpan =
        s.point_count > 1
          ? `${formatDiagDate(s.readings[0].recorded_at)} → ${formatDiagDate(s.readings[s.readings.length - 1].recorded_at)}`
          : formatDiagDate(latest?.recorded_at);
      const ref = s.reference || null;
      const meaning = ref?.meaning || "";
      const infoUrl = ref?.info_url || "";
      const infoSource = ref?.info_source || "Learn more";
      const statusBit = status
        ? `<span class="diag-status-pill diag-status-${status}" title="${escapeHtml(statusLabel(status))}">${escapeHtml(
            statusLabel(status)
          )}</span>`
        : "";
      const infoLink = infoUrl
        ? `<a class="diag-chart-info-link" href="${escapeHtml(infoUrl)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(
            meaning || `Open ${infoSource}`
          )}">What is this? · ${escapeHtml(infoSource)}</a>`
        : meaning
          ? `<span class="diag-chart-info-link muted" title="${escapeHtml(meaning)}">What is this?</span>`
          : "";
      return `<article class="diag-chart-card diag-status-card-${status || "none"}" data-category="${escapeHtml(
        s.category || "blood"
      )}" title="${escapeHtml(meaning || s.name)}">
      <div class="diag-chart-head">
        <h4 class="diag-chart-title">${escapeHtml(s.name)}</h4>
        <span class="diag-chart-latest" style="color:${stroke}">${escapeHtml(latestLabel)}</span>
      </div>
      ${buildSparklineSvg(s.readings || [], { stroke, reference: ref, milestones })}
      <p class="diag-chart-meta">${statusBit ? `${statusBit} ` : ""}${escapeHtml(cat)} · ${s.point_count} reading${
        s.point_count === 1 ? "" : "s"
      } · ${escapeHtml(dateSpan)}</p>
      ${
        ref
          ? `<p class="diag-chart-ref">Ref: ${escapeHtml(ref.label || "")}${
              ref.note ? ` · ${escapeHtml(ref.note)}` : ""
            }</p>`
          : ""
      }
      ${infoLink ? `<p class="diag-chart-info">${infoLink}</p>` : ""}
    </article>`;
    })
    .join("");
}

async function refreshActivePatientProfile() {
  if (!state.activePatientId) {
    renderDiagnosticsCharts(null, []);
    renderJournalHome(null, []);
    return;
  }
  const r = await fetch(`/api/patients/${state.activePatientId}/profile`);
  if (!r.ok) return;
  const data = await r.json();
  applyProfileResponse(data);
}

function showModal(id) { document.getElementById(id)?.classList.remove("hidden"); }
function hideModal(id) { document.getElementById(id)?.classList.add("hidden"); }

async function openSwitchPatientCaseModal() {
  const list = document.getElementById("switch-case-list");
  if (!list) return;
  list.innerHTML = `<p class="muted small">Loading…</p>`;
  showModal("modal-switch-case");
  try {
    const r = await fetch("/api/patients");
    const data = await r.json();
    const patients = data.patients || [];
    const activePid = data.active?.patient_id;
    const activeCid = data.active?.case_id;
    if (!patients.length) {
      list.innerHTML = `<p class="muted small">No patients yet. Create one in Settings.</p>`;
      return;
    }
    list.innerHTML = patients
      .map((p) => {
        const cases = p.cases || [];
        const caseBtns = cases.length
          ? cases
              .map((c) => {
                const active =
                  p.id === activePid && c.id === activeCid ? " active-case" : "";
                return `<button type="button" class="switch-case-btn${active}" data-patient-id="${escapeHtml(p.id)}" data-case-id="${escapeHtml(c.id)}"><span class="switch-case-btn-label">${escapeHtml(c.label || c.id)}</span></button>`;
              })
              .join("")
          : `<p class="muted small">No cases — create one after selecting this patient.</p>
             <button type="button" class="switch-case-btn" data-patient-id="${escapeHtml(p.id)}" data-new-case="1"><span class="switch-case-btn-label">New case…</span></button>`;
        return `<div class="switch-patient-group">
          <h4>${escapeHtml(p.label || p.id)}${p.id === activePid ? " · current" : ""}</h4>
          ${caseBtns}
        </div>`;
      })
      .join("");
  } catch (err) {
    list.innerHTML = `<p class="muted small">Could not load patients.</p>`;
  }
}

function openNewPatientModal() {
  const input = document.getElementById("input-new-patient-label");
  if (input) input.value = "";
  showModal("modal-new-patient");
  input?.focus();
}

async function openNewCaseModal() {
  const r = await fetch("/api/cases/active");
  const ctx = await r.json();
  let patientId = ctx.patient_id;
  let patientLabel = ctx.patient_label;
  if (!patientId) {
    alert("Add a patient first.");
    openNewPatientModal();
    return;
  }
  document.getElementById("new-case-patient-label").textContent = `for ${patientLabel}`;
  document.getElementById("input-new-case-label").value = "";
  document.getElementById("input-new-case-context").value = "";
  document.getElementById("modal-new-case").dataset.patientId = patientId;
  showModal("modal-new-case");
  document.getElementById("input-new-case-label")?.focus();
}

document.querySelectorAll(".js-new-patient").forEach((btn) => {
  btn.addEventListener("click", openNewPatientModal);
});
document.querySelectorAll(".js-new-case").forEach((btn) => {
  btn.addEventListener("click", () => { void openNewCaseModal(); });
});

document.getElementById("btn-cancel-new-patient")?.addEventListener("click", () => hideModal("modal-new-patient"));
document.getElementById("btn-confirm-new-patient")?.addEventListener("click", async () => {
  const label = document.getElementById("input-new-patient-label")?.value.trim();
  if (!label) return;
  const res = await fetch("/api/patients", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  if (!res.ok) {
    alert("Failed to create patient");
    return;
  }
  const created = await res.json();
  hideModal("modal-new-patient");
  document.getElementById("new-case-patient-label").textContent = `for ${created.patient.label}`;
  document.getElementById("input-new-case-label").value = "";
  document.getElementById("input-new-case-context").value = "";
  document.getElementById("modal-new-case").dataset.patientId = created.patient.id;
  showModal("modal-new-case");
  document.getElementById("input-new-case-label")?.focus();
});

document.getElementById("btn-cancel-new-case")?.addEventListener("click", () => hideModal("modal-new-case"));
document.getElementById("btn-confirm-new-case")?.addEventListener("click", async () => {
  const patientId = document.getElementById("modal-new-case").dataset.patientId;
  const label = document.getElementById("input-new-case-label")?.value.trim();
  if (!label) return;
  const patientContext = document.getElementById("input-new-case-context")?.value.trim() || null;
  const res = await fetch(`/api/patients/${patientId}/cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, patient_context: patientContext }),
  });
  if (!res.ok) {
    alert("Failed to create case");
    return;
  }
  const caseData = await res.json();
  await activatePatientCase(patientId, caseData.case.id);
});

async function openRenameCaseModal() {
  const r = await fetch("/api/cases/active");
  const ctx = await r.json();
  if (!ctx.patient_id || !ctx.case_id) {
    alert("Select a case first.");
    return;
  }
  const modal = document.getElementById("modal-rename-case");
  if (!modal) return;
  modal.dataset.patientId = ctx.patient_id;
  modal.dataset.caseId = ctx.case_id;
  const input = document.getElementById("input-rename-case-label");
  if (input) input.value = ctx.case_label || "";
  showModal("modal-rename-case");
  input?.focus();
  input?.select();
}

document.getElementById("btn-rename-case-settings")?.addEventListener("click", () => openRenameCaseModal());
document.getElementById("btn-cancel-rename-case")?.addEventListener("click", () => hideModal("modal-rename-case"));
document.getElementById("btn-confirm-rename-case")?.addEventListener("click", async () => {
  const modal = document.getElementById("modal-rename-case");
  const patientId = modal?.dataset.patientId;
  const caseId = modal?.dataset.caseId;
  const label = document.getElementById("input-rename-case-label")?.value.trim();
  if (!patientId || !caseId || !label) return;
  const res = await fetch(`/api/patients/${patientId}/cases/${caseId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || "Failed to rename case");
    return;
  }
  hideModal("modal-rename-case");
  await loadCaseContext();
  toast(`Case renamed to ${label}`);
});

document.getElementById("header-case-select")?.addEventListener("change", async (event) => {
  const caseId = event.target.value;
  if (!caseId) return;
  const r = await fetch("/api/patients");
  const data = await r.json();
  const patientId = data.active?.patient_id;
  if (!patientId) return;
  if (caseId === data.active?.case_id) return;
  const label =
    event.target.selectedOptions?.[0]?.textContent?.trim() || "Switching case…";
  event.target.disabled = true;
  await activatePatientCase(patientId, caseId, { label: `Switching to ${label}…` });
});

document.getElementById("header-patient-select")?.addEventListener("change", async (event) => {
  const patientId = event.target.value;
  const r = await fetch("/api/patients");
  const data = await r.json();
  const patient = (data.patients || []).find((p) => p.id === patientId);
  if (!patient) return;
  const firstCase = (patient.cases || [])[0];
  if (!firstCase) {
    document.getElementById("modal-new-case").dataset.patientId = patient.id;
    document.getElementById("new-case-patient-label").textContent = `for ${patient.label}`;
    showModal("modal-new-case");
    return;
  }
  if (patientId === data.active?.patient_id && firstCase.id === data.active?.case_id) return;
  event.target.disabled = true;
  await activatePatientCase(patientId, firstCase.id, {
    label: `Switching to ${patient.label}…`,
  });
});

document.getElementById("btn-patient-photo")?.addEventListener("click", () => {
  document.getElementById("input-patient-photo")?.click();
});
document.getElementById("input-patient-photo")?.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  const r = await fetch("/api/cases/active");
  const ctx = await r.json();
  if (!ctx.patient_id) {
    alert("Add a patient first.");
    return;
  }
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(`/api/patients/${ctx.patient_id}/photo`, { method: "POST", body });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || "Could not save photo");
    return;
  }
  await loadCaseContext();
});

document.getElementById("btn-cancel-switch-case")?.addEventListener("click", () => {
  if (!document.getElementById("switch-progress-overlay")?.classList.contains("hidden")) return;
  hideModal("modal-switch-case");
});

document.getElementById("btn-header-switch-patient")?.addEventListener("click", () => {
  openSwitchPatientCaseModal();
});

document.getElementById("switch-case-list")?.addEventListener("click", async (event) => {
  const btn = event.target.closest(".switch-case-btn");
  if (!btn || btn.disabled) return;
  const patientId = btn.dataset.patientId;
  if (!patientId) return;
  if (btn.dataset.newCase === "1") {
    hideModal("modal-switch-case");
    const modal = document.getElementById("modal-new-case");
    if (modal) modal.dataset.patientId = patientId;
    const labelEl = document.getElementById("new-case-patient-label");
    if (labelEl) labelEl.textContent = `for ${btn.closest(".switch-patient-group")?.querySelector("h4")?.textContent || "patient"}`;
    showModal("modal-new-case");
    return;
  }
  const caseId = btn.dataset.caseId;
  if (!caseId) return;
  const patientLabel =
    btn.closest(".switch-patient-group")?.querySelector("h4")?.textContent?.replace(/\s·\scurrent$/, "").trim() ||
    "patient";
  const caseLabel = btn.querySelector(".switch-case-btn-label")?.textContent?.trim() || "case";
  setSwitchBusyState(btn, `Switching to ${patientLabel} · ${caseLabel}…`);
  await activatePatientCase(patientId, caseId, {
    label: `Switching to ${patientLabel} · ${caseLabel}…`,
  });
});

document.getElementById("modal-switch-case")?.addEventListener("click", (event) => {
  if (event.target.id === "modal-switch-case") {
    if (document.getElementById("switch-progress-overlay") && !document.getElementById("switch-progress-overlay").classList.contains("hidden")) {
      return;
    }
    hideModal("modal-switch-case");
  }
});

document.getElementById("btn-save-profile")?.addEventListener("click", async () => {
  if (!state.activePatientId) {
    toast("Select a patient first", "error");
    return;
  }
  const res = await fetch(`/api/patients/${state.activePatientId}/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      date_of_birth: document.getElementById("profile-dob")?.value || null,
      gender: document.getElementById("profile-gender")?.value || null,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    toast(err.detail || "Could not save profile", "error");
    return;
  }
  const data = await res.json();
  applyProfileResponse(data);
  toast("Demographics saved");
});

document.getElementById("btn-add-measurement")?.addEventListener("click", async () => {
  if (!state.activePatientId) {
    toast("Select a patient first", "error");
    return;
  }
  const recordedAt = document.getElementById("measure-date")?.value;
  const heightRaw = document.getElementById("measure-height")?.value;
  const weightRaw = document.getElementById("measure-weight")?.value;
  const notes = document.getElementById("measure-notes")?.value.trim() || null;
  const height_cm = heightRaw === "" || heightRaw == null ? null : Number(heightRaw);
  const weight_kg = weightRaw === "" || weightRaw == null ? null : Number(weightRaw);
  if (!recordedAt) {
    toast("Choose a measurement date", "error");
    return;
  }
  if (height_cm == null && weight_kg == null) {
    toast("Enter height and/or weight", "error");
    return;
  }
  const res = await fetch(`/api/patients/${state.activePatientId}/measurements`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recorded_at: recordedAt, height_cm, weight_kg, notes }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    toast(err.detail || "Could not add measurement", "error");
    return;
  }
  const data = await res.json();
  document.getElementById("measure-height").value = "";
  document.getElementById("measure-weight").value = "";
  document.getElementById("measure-notes").value = "";
  applyProfileResponse(data);
  toast("Measurement added");
});

document.getElementById("btn-add-diagnostic")?.addEventListener("click", async () => {
  if (!state.activePatientId) {
    toast("Select a patient first", "error");
    return;
  }
  const name = document.getElementById("diag-name")?.value.trim();
  const valueRaw = document.getElementById("diag-value")?.value;
  const unit = document.getElementById("diag-unit")?.value.trim() || null;
  const recordedAt = document.getElementById("diag-date")?.value;
  const notes = document.getElementById("diag-notes")?.value.trim() || null;
  if (!name) {
    toast("Enter a diagnostic name", "error");
    return;
  }
  if (valueRaw === "" || valueRaw == null) {
    toast("Enter a value", "error");
    return;
  }
  if (!recordedAt) {
    toast("Choose a date", "error");
    return;
  }
  // Auto-fill unit from preset when blank
  if (!unit) {
    const preset = (state.diagnosticPresets || []).find(
      (p) => p.name.toLowerCase() === name.toLowerCase()
    );
    if (preset?.unit) document.getElementById("diag-unit").value = preset.unit;
  }
  const res = await fetch(`/api/patients/${state.activePatientId}/diagnostics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      value: Number(valueRaw),
      unit: document.getElementById("diag-unit")?.value.trim() || null,
      recorded_at: recordedAt,
      notes,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    toast(err.detail || "Could not add diagnostic", "error");
    return;
  }
  const data = await res.json();
  document.getElementById("diag-value").value = "";
  document.getElementById("diag-notes").value = "";
  applyProfileResponse(data);
  toast("Diagnostic reading added");
  switchTab("analyze", { skipTabSave: false });
  setHomeSection("diagnostics", { scroll: true });
});

document.getElementById("diag-name")?.addEventListener("change", () => {
  const name = document.getElementById("diag-name")?.value.trim().toLowerCase();
  const unitEl = document.getElementById("diag-unit");
  if (!name || !unitEl || unitEl.value.trim()) return;
  const preset = (state.diagnosticPresets || []).find((p) => p.name.toLowerCase() === name);
  if (preset?.unit) unitEl.value = preset.unit;
});

document.getElementById("patient-measurements-list")?.addEventListener("click", async (event) => {
  const btn = event.target.closest(".btn-delete-measurement");
  if (!btn || !state.activePatientId) return;
  const id = btn.dataset.id;
  if (!id) return;
  const res = await fetch(`/api/patients/${state.activePatientId}/measurements/${id}`, { method: "DELETE" });
  if (!res.ok) {
    toast("Could not remove measurement", "error");
    return;
  }
  const data = await res.json();
  applyProfileResponse(data);
});

document.getElementById("patient-diagnostics-list")?.addEventListener("click", async (event) => {
  const btn = event.target.closest(".btn-delete-diagnostic");
  if (!btn || !state.activePatientId) return;
  const id = btn.dataset.id;
  if (!id) return;
  const res = await fetch(`/api/patients/${state.activePatientId}/diagnostics/${id}`, { method: "DELETE" });
  if (!res.ok) {
    toast("Could not remove reading", "error");
    return;
  }
  const data = await res.json();
  applyProfileResponse(data);
});

async function deleteJournalEntry(entryId) {
  if (!state.activePatientId || !entryId) return;
  const res = await fetch(`/api/patients/${state.activePatientId}/journal/${entryId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    toast("Could not remove self-report", "error");
    return;
  }
  const data = await res.json();
  applyProfileResponse(data);
}

document.getElementById("patient-journal-list")?.addEventListener("click", async (event) => {
  const btn = event.target.closest(".btn-delete-journal");
  if (!btn) return;
  await deleteJournalEntry(btn.dataset.id);
});

document.getElementById("journal-recent")?.addEventListener("click", async (event) => {
  const btn = event.target.closest(".btn-delete-journal");
  if (!btn) return;
  await deleteJournalEntry(btn.dataset.id);
});

document.getElementById("btn-journal")?.addEventListener("click", () => {
  ensureJournalChips();
  updateJournalDraftUi();
  showModal("modal-journal");
  document.getElementById("journal-text")?.focus();
});

document.getElementById("btn-journal-open-home")?.addEventListener("click", () => {
  document.getElementById("btn-journal")?.click();
});

document.getElementById("btn-close-journal")?.addEventListener("click", () => {
  hideModal("modal-journal");
});

document.getElementById("modal-journal")?.addEventListener("click", (event) => {
  if (event.target?.id === "modal-journal") hideModal("modal-journal");
});

document.getElementById("journal-feeling-chips")?.addEventListener("click", (event) => {
  const chip = event.target.closest(".journal-chip");
  if (!chip) return;
  state.journalDraft = {
    ...state.journalDraft,
    kind: chip.dataset.kind || "symptom",
    label: chip.dataset.label || "",
  };
  updateJournalDraftUi();
});

document.getElementById("journal-action-chips")?.addEventListener("click", (event) => {
  const chip = event.target.closest(".journal-chip");
  if (!chip) return;
  state.journalDraft = {
    ...state.journalDraft,
    kind: chip.dataset.kind || "note",
    label: chip.dataset.label || "",
    severity: chip.dataset.kind === "medication" || chip.dataset.kind === "note"
      ? null
      : state.journalDraft.severity,
  };
  updateJournalDraftUi();
});

document.getElementById("journal-severity")?.addEventListener("click", (event) => {
  const btn = event.target.closest(".journal-sev-btn");
  if (!btn) return;
  const sev = Number(btn.dataset.sev);
  state.journalDraft = {
    ...state.journalDraft,
    severity: state.journalDraft.severity === sev ? null : sev,
  };
  updateJournalDraftUi();
});

document.getElementById("btn-journal-clear-sev")?.addEventListener("click", () => {
  state.journalDraft = { ...state.journalDraft, severity: null };
  updateJournalDraftUi();
});

document.getElementById("btn-journal-log")?.addEventListener("click", async () => {
  if (!state.activePatientId) {
    toast("Select a patient first", "error");
    return;
  }
  const text = document.getElementById("journal-text")?.value.trim() || "";
  let kind = state.journalDraft.kind || "note";
  let label = (state.journalDraft.label || "").trim();
  let detail = text || null;
  if (!label && text) {
    kind = "note";
    label = text.slice(0, 80);
    detail = null;
  }
  if (!label) {
    toast("Pick a chip or type a detail", "error");
    return;
  }
  const linkCase = document.getElementById("journal-link-case")?.checked;
  const whenRaw = document.getElementById("journal-when")?.value;
  let recorded_at = null;
  if (whenRaw) {
    const local = new Date(whenRaw);
    recorded_at = Number.isNaN(local.getTime()) ? whenRaw : local.toISOString();
  }
  const body = {
    kind,
    label,
    text: detail,
    severity:
      kind === "symptom" || kind === "feeling" ? state.journalDraft.severity || null : null,
    recorded_at,
    case_id: linkCase && state.activeCaseId ? state.activeCaseId : null,
  };

  const btn = document.getElementById("btn-journal-log");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/patients/${state.activePatientId}/journal`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Could not save self-report");
    }
    const data = await res.json();
    const textEl = document.getElementById("journal-text");
    if (textEl) textEl.value = "";
    const whenEl = document.getElementById("journal-when");
    if (whenEl) whenEl.value = "";
    state.journalDraft = { kind: "note", label: "", severity: null };
    applyProfileResponse(data);
    hideModal("modal-journal");
    toast("Logged");
  } catch (err) {
    toast(err.message || "Could not save", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("btn-diagnostics-settings")?.addEventListener("click", () => {
  switchTab("settings", { settingsSection: "profile", settingsFocus: "#diag-name" });
});

document.getElementById("btn-medications-settings")?.addEventListener("click", () => {
  switchTab("settings", { settingsSection: "profile", settingsFocus: "#med-name" });
});

document.getElementById("btn-med-safety-home")?.addEventListener("click", () => {
  switchTab("custom-tasks");
  requestAnimationFrame(() => {
    document.getElementById("med-safety-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

async function runMedicationSafetyReview() {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  const btn = document.getElementById("btn-med-safety-review");
  if (btn) btn.disabled = true;
  const resultEl = document.getElementById("med-safety-result");
  if (resultEl) {
    resultEl.classList.remove("hidden");
    resultEl.innerHTML = `<p class="muted small">Running health oversight review…</p>`;
  }
  try {
    const data = await api(`/api/patients/${state.activePatientId}/medications/safety-review`, {
      method: "POST",
      body: JSON.stringify({}),
      timeoutMs: 300000,
    });
    applyProfileResponse(data);
    renderMedSafetyResult(data.medication_safety);
    const r = data.medication_safety?.result;
    if (r?.all_clear) toast("Health oversight: no concerning interactions or dosage issues flagged");
    else toast("Health oversight complete — review flagged items");
  } catch (err) {
    if (resultEl) {
      resultEl.innerHTML = `<p class="muted small error-text">${escapeHtml(err.message || "Review failed")}</p>`;
    }
    toast(err.message || "Safety review failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.getElementById("btn-med-safety-review")?.addEventListener("click", () => {
  runMedicationSafetyReview();
});

document.getElementById("btn-cancel-med-edit")?.addEventListener("click", () => clearMedicationForm());

document.getElementById("btn-save-medication")?.addEventListener("click", async () => {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  const editId = document.getElementById("med-edit-id")?.value || "";
  const name = document.getElementById("med-name")?.value.trim();
  if (!name) return toast("Enter a medication name", "error");
  const body = {
    name,
    dosage: composeDosageFromForm(),
    frequency: document.getElementById("med-frequency")?.value.trim() || null,
    conditions: parseConditionsInput(document.getElementById("med-conditions")?.value),
    notes: document.getElementById("med-notes")?.value.trim() || null,
    started_at: document.getElementById("med-started")?.value || null,
    ended_at: document.getElementById("med-ended")?.value || null,
  };
  const btn = document.getElementById("btn-save-medication");
  if (btn) btn.disabled = true;
  try {
    let res;
    if (editId) {
      body.history_note = document.getElementById("med-history-note")?.value.trim() || null;
      body.effective_at = document.getElementById("med-effective-at")?.value || null;
      res = await fetch(`/api/patients/${state.activePatientId}/medications/${editId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } else {
      res = await fetch(`/api/patients/${state.activePatientId}/medications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Could not save medication");
    }
    const data = await res.json();
    clearMedicationForm();
    applyProfileResponse(data);
    const savedMed = data.medication;
    if (savedMed?.identity_status === "unknown") {
      toast("Medication added — name not found on known medication list", "error");
    } else if (savedMed?.identity_status === "uncertain") {
      toast(
        savedMed.identity_match
          ? `Saved — check name (similar to ${savedMed.identity_match})`
          : "Saved — check medication name"
      );
    } else {
      toast(editId ? "Medication updated" : "Medication added");
    }
  } catch (err) {
    toast(err.message || "Could not save", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("patient-medications-list")?.addEventListener("click", async (event) => {
  const editBtn = event.target.closest(".btn-edit-medication, .btn-fix-medication");
  const acceptBtn = event.target.closest(".btn-accept-med-name");
  const stopBtn = event.target.closest(".btn-stop-medication");
  const delBtn = event.target.closest(".btn-delete-medication");
  if (!state.activePatientId) return;

  if (acceptBtn) {
    await acceptSuggestedMedName(acceptBtn.dataset.id, acceptBtn.dataset.name);
    return;
  }

  if (editBtn) {
    await openMedicationEditor(editBtn.dataset.id, {
      focusDose: editBtn.classList.contains("btn-fix-medication"),
    });
    return;
  }

  if (stopBtn) {
    const id = stopBtn.dataset.id;
    if (!id || !confirm("Mark this medication as stopped?")) return;
    const res = await fetch(`/api/patients/${state.activePatientId}/medications/${id}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) return toast("Could not stop medication", "error");
    applyProfileResponse(await res.json());
    toast("Medication stopped");
    return;
  }

  if (delBtn) {
    const id = delBtn.dataset.id;
    if (!id || !confirm("Remove this medication record?")) return;
    const res = await fetch(`/api/patients/${state.activePatientId}/medications/${id}`, {
      method: "DELETE",
    });
    if (!res.ok) return toast("Could not remove medication", "error");
    applyProfileResponse(await res.json());
    if (document.getElementById("med-edit-id")?.value === id) clearMedicationForm();
    toast("Medication removed");
  }
});

document.getElementById("medications-home-list")?.addEventListener("click", async (event) => {
  const acceptBtn = event.target.closest(".btn-accept-med-name");
  const editBtn = event.target.closest(".btn-edit-medication, .btn-fix-medication");
  if (acceptBtn) {
    await acceptSuggestedMedName(acceptBtn.dataset.id, acceptBtn.dataset.name);
    return;
  }
  if (editBtn) {
    await openMedicationEditor(editBtn.dataset.id, {
      focusDose: editBtn.classList.contains("btn-fix-medication"),
    });
  }
});

document.getElementById("med-dose-unit")?.addEventListener("change", () => {
  syncMedDoseUnitOtherVisibility();
});

document.getElementById("med-identity-hint")?.addEventListener("click", (event) => {
  const btn = event.target.closest("#btn-med-use-suggested");
  if (!btn) return;
  const name = btn.dataset.name || "";
  const nameEl = document.getElementById("med-name");
  if (nameEl && name) {
    nameEl.value = name;
    toast(`Name set to ${name} — save to apply`);
    nameEl.focus();
  }
});

document.getElementById("btn-import-medications")?.addEventListener("click", async () => {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  const fileInput = document.getElementById("med-import-file");
  const file = fileInput?.files?.[0];
  if (!file) return toast("Choose a PDF or image first", "error");
  const btn = document.getElementById("btn-import-medications");
  if (btn) btn.disabled = true;
  setMedImportStatus("Extracting and parsing medications…");
  try {
    const fd = new FormData();
    fd.append("file", file);
    const data = await api(`/api/patients/${state.activePatientId}/medications/import`, {
      method: "POST",
      body: fd,
      timeoutMs: 300000,
    });
    renderMedImportReview(data);
    toast(`Parsed ${(data.proposed || []).length} medication(s)`);
  } catch (err) {
    setMedImportStatus(err.message || "Import failed", { error: true });
    toast(err.message || "Import failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("btn-med-import-select-all")?.addEventListener("click", () => {
  document.querySelectorAll("#med-import-review-list .med-import-select").forEach((el) => {
    el.checked = true;
  });
});

document.getElementById("btn-med-import-clear")?.addEventListener("click", () => clearMedImportReview());

document.getElementById("btn-med-import-confirm")?.addEventListener("click", async () => {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  const medications = collectMedImportSelected();
  if (!medications.length) return toast("Select at least one medication with a name", "error");
  const btn = document.getElementById("btn-med-import-confirm");
  if (btn) btn.disabled = true;
  try {
    const data = await api(`/api/patients/${state.activePatientId}/medications/import/confirm`, {
      method: "POST",
      body: JSON.stringify({ medications }),
    });
    applyProfileResponse(data);
    clearMedImportReview();
    toast(`Added ${data.added_count || medications.length} medication(s)`);
  } catch (err) {
    toast(err.message || "Could not add medications", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("btn-import-diagnostics")?.addEventListener("click", async () => {
  if (!state.activePatientId) return toast("Select a patient first", "error");
  const fileInput = document.getElementById("diag-import-file");
  const file = fileInput?.files?.[0];
  if (!file) return toast("Choose a lab PDF or image first", "error");
  const btn = document.getElementById("btn-import-diagnostics");
  if (btn) btn.disabled = true;
  setDiagImportStatus("Extracting and parsing lab readings…");
  try {
    const fd = new FormData();
    fd.append("file", file);
    const data = await api(`/api/patients/${state.activePatientId}/diagnostics/import`, {
      method: "POST",
      body: fd,
      timeoutMs: 300000,
    });
    renderDiagImportReview(data);
    toast(`Parsed ${(data.proposed || []).length} lab reading(s)`);
  } catch (err) {
    setDiagImportStatus(err.message || "Import failed", { error: true });
    toast(err.message || "Import failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("btn-diag-import-select-all")?.addEventListener("click", () => {
  document.querySelectorAll("#diag-import-review-list .diag-import-select").forEach((el) => {
    el.checked = true;
  });
});

document.getElementById("btn-diag-import-clear")?.addEventListener("click", () => clearDiagImportReview());

document.getElementById("btn-diag-import-confirm")?.addEventListener("click", () => {
  confirmDiagImportAndShowCharts().catch((e) => toast(e.message, "error"));
});

document.getElementById("btn-export-diagnostics-pdf")?.addEventListener("click", async () => {
  const patientId = state.activePatientId;
  if (!patientId) return toast("Select a patient first", "error");
  const btn = document.getElementById("btn-export-diagnostics-pdf");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/patients/${patientId}/diagnostics/export.pdf`, {
      credentials: "include",
    });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Export failed (${res.status})`);
    }
    const blob = await res.blob();
    const stamp = new Date()
      .toISOString()
      .replace(/[:.]/g, "-")
      .slice(0, 19);
    const filename = filenameFromContentDisposition(
      res,
      `beatit-diagnostics-${stamp}.pdf`
    );
    triggerPdfDownload(blob, filename);
    toast("Diagnostics PDF downloaded");
  } catch (err) {
    toast(err.message || "Export failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("diag-milestones-enabled")?.addEventListener("change", (e) => {
  const prefs = state.diagMilestonePrefs || { enabled: true, selected: [] };
  prefs.enabled = !!e.target.checked;
  state.diagMilestonePrefs = prefs;
  applyDiagMilestonePrefsAndRedraw();
});

document.getElementById("diagnostics-status-filter")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-diag-status-filter]");
  if (!btn) return;
  const next = btn.getAttribute("data-diag-status-filter") || "all";
  if (next === state.diagStatusFilter) return;
  state.diagStatusFilter = next;
  const profile = state.patientProfile || null;
  const series = resolveDiagnosticSeries(profile, {
    diagnostic_series: state.diagnosticSeriesCache,
  });
  renderDiagnosticsCharts(profile, series, { skipControls: true });
});

document.getElementById("diag-milestones-all")?.addEventListener("click", () => {
  const profile = state.patientProfile || null;
  const allEvents = allChartMilestones(profile);
  state.diagMilestonePrefs = {
    enabled: true,
    selected: allEvents.map((e) => e.id),
    seen: allEvents.map((e) => e.id),
  };
  const enabledEl = document.getElementById("diag-milestones-enabled");
  if (enabledEl) enabledEl.checked = true;
  applyDiagMilestonePrefsAndRedraw();
});

document.getElementById("diag-milestones-none")?.addEventListener("click", () => {
  const profile = state.patientProfile || null;
  const allEvents = allChartMilestones(profile);
  state.diagMilestonePrefs = {
    ...(state.diagMilestonePrefs || {}),
    enabled: true,
    selected: [],
    seen: allEvents.map((e) => e.id),
  };
  applyDiagMilestonePrefsAndRedraw();
});

document.getElementById("diag-milestones-list")?.addEventListener("change", (e) => {
  const input = e.target.closest(".diag-milestone-toggle");
  if (!input) return;
  const prefs = state.diagMilestonePrefs || { enabled: true, selected: [] };
  const id = input.dataset.id;
  const selected = new Set(prefs.selected || []);
  if (input.checked) selected.add(id);
  else selected.delete(id);
  prefs.selected = [...selected];
  prefs.enabled = true;
  state.diagMilestonePrefs = prefs;
  const enabledEl = document.getElementById("diag-milestones-enabled");
  if (enabledEl) enabledEl.checked = true;
  applyDiagMilestonePrefsAndRedraw();
});

document.getElementById("diag-milestone-preset")?.addEventListener("change", () => syncMilestoneCustomVisibility());
document.getElementById("ms-preset")?.addEventListener("change", () => syncMilestoneCustomVisibility());

document.getElementById("btn-diag-milestone-add")?.addEventListener("click", async () => {
  const btn = document.getElementById("btn-diag-milestone-add");
  if (btn) btn.disabled = true;
  try {
    await addMilestoneFromForm({
      presetSelId: "diag-milestone-preset",
      customId: "diag-milestone-custom",
      dateId: "diag-milestone-date",
    });
    const custom = document.getElementById("diag-milestone-custom");
    if (custom) custom.value = "";
  } catch (err) {
    toast(err.message || "Could not add milestone", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("btn-save-milestone")?.addEventListener("click", async () => {
  const btn = document.getElementById("btn-save-milestone");
  if (btn) btn.disabled = true;
  try {
    await addMilestoneFromForm({
      presetSelId: "ms-preset",
      customId: "ms-custom",
      dateId: "ms-date",
      notesId: "ms-notes",
    });
    const custom = document.getElementById("ms-custom");
    if (custom) custom.value = "";
    const notes = document.getElementById("ms-notes");
    if (notes) notes.value = "";
  } catch (err) {
    toast(err.message || "Could not add milestone", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById("patient-milestones-list")?.addEventListener("click", async (event) => {
  const del = event.target.closest(".btn-delete-milestone");
  if (!del || !state.activePatientId) return;
  const id = del.dataset.id;
  if (!id || !confirm("Remove this milestone?")) return;
  const res = await fetch(`/api/patients/${state.activePatientId}/milestones/${id}`, { method: "DELETE" });
  if (!res.ok) return toast("Could not remove milestone", "error");
  applyProfileResponse(await res.json());
  toast("Milestone removed");
});

// Cross-case browsing folded into the unified patient-wide Library list
async function loadCrossCaseSiblings() {
  const section = document.getElementById("cross-case-section");
  if (section) section.classList.add("hidden");
}

async function loadCrossCaseDocs(caseId, caseLabel, tabBtn) {
  document.querySelectorAll(".cross-case-tab").forEach(b => b.classList.remove("active"));
  if (tabBtn) tabBtn.classList.add("active");
  const docsEl = document.getElementById("cross-case-docs");
  docsEl.innerHTML = "<p class='muted small'>Loading…</p>";
  try {
    const r = await fetch(`/api/cases/siblings/${caseId}/documents`);
    const data = await r.json();
    if (!data.documents || !data.documents.length) {
      docsEl.innerHTML = "<p class='muted small'>No documents in this case.</p>";
      return;
    }
    docsEl.innerHTML = "";
    for (const doc of data.documents) {
      const item = document.createElement("div");
      item.className = "cross-case-doc-item";
      item.innerHTML = `<span>${doc.title || doc.id}</span><span class="doc-type">${doc.source_type || ""}</span>`;
      docsEl.appendChild(item);
    }
  } catch { docsEl.innerHTML = "<p class='muted small'>Failed to load.</p>"; }
}

// Load context on startup
loadCaseContext();
loadCrossCaseSiblings();

void loadInitialData();
