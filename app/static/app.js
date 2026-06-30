const state = {
  documents: [],
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
    return new Date(`${isoDate}T12:00:00`).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return isoDate;
  }
}

function renderAppVersion(data) {
  const el = $("#app-version");
  if (!el || !data?.version) return;
  const updated = formatVersionUpdated(data.updated);
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
  const model =
    state.settings.openrouter_model ||
    llm.openrouter?.model ||
    active.model ||
    "Unknown model";

  if (active.ready) {
    if (pill) pill.classList.add("hidden");
    if (settingsConn) {
      settingsConn.textContent = `Connected · ${active.provider} · ${active.model || model}`;
      settingsConn.className = "settings-llm-connection ok";
    }
    return;
  }

  let headerText = "LLM unavailable";
  let settingsText = "LLM is not available.";
  if (llm.configured_provider === "openrouter") {
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

function setAnalysisRunning(running, jobId = null) {
  state.analysisRunning = running;
  state.analysisJobId = running ? jobId : null;
  $("#analyze-running-banner")?.classList.toggle("hidden", !running);
  $("#analyze-loading")?.classList.toggle("hidden", !running);
  if (running) {
    $("#analyze-actions-card")?.setAttribute("open", "");
  }
  ["#btn-baseline", "#btn-summarize", "#btn-analyze"].forEach((sel) => {
    const btn = $(sel);
    if (!btn) return;
    btn.disabled = running;
    btn.setAttribute("aria-disabled", running ? "true" : "false");
  });
}

async function pollAnalysisJob(jobId) {
  const deadline = Date.now() + 30 * 60 * 1000;
  while (Date.now() < deadline) {
    const data = await api(`/api/analyze/jobs/${jobId}`);
    const job = data.job;
    if (job.status === "completed") {
      if (job.analysis) return job.analysis;
      const latest = await api("/api/analyses/latest");
      if (latest.analysis) return latest.analysis;
      throw new Error("Analysis finished but results could not be loaded. Refresh the page.");
    }
    if (job.status === "failed") {
      throw new Error(job.error || "Analysis failed");
    }
    await sleep(2000);
  }
  const latest = await api("/api/analyses/latest");
  if (latest.analysis) return latest.analysis;
  throw new Error("Analysis is taking longer than expected. Refresh the page to check status.");
}

async function startAnalysisJob({ query = "", baseline = false, summarize = false } = {}) {
  const docIds = state.selectedIds.size ? [...state.selectedIds] : null;

  if (summarize) {
    const qs = docIds ? "?" + docIds.map((id) => `document_ids=${encodeURIComponent(id)}`).join("&") : "";
    const data = await api(`/api/analyze/summarize${qs}`, { method: "POST" });
    return data.job.id;
  }

  const data = await api("/api/analyze", {
    method: "POST",
    body: JSON.stringify({
      query,
      document_ids: docIds,
      include_baseline_assessment: baseline,
    }),
  });
  return data.job.id;
}

function finishAnalysisRun(analysis) {
  switchTab("analyze");
  renderLatestAssessment(analysis);
  state.analyses = [analysis, ...state.analyses.filter((a) => a.id !== analysis.id)];
  toast(`Assessment saved · ${formatTimestamp(analysis.created_at)}`);
  scrollToAssessmentResults();
}

function scrollToAssessmentResults() {
  const target = $("#executive-summary-card") || $("#analyze-results-section");
  if (!target) return;
  target.scrollIntoView({ behavior: "smooth", block: "start" });
}

function scrollToOpenGaps() {
  if (!$("#panel-analyze")?.classList.contains("active")) {
    switchTab("analyze");
  }
  requestAnimationFrame(() => {
    $("#open-items-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function initCaseStatusNavigation() {
  $("#case-status-summary")?.addEventListener("click", (event) => {
    const link = event.target.closest("[data-case-nav]");
    if (!link) return;
    event.preventDefault();
    const target = link.dataset.caseNav;
    if (target === "library") switchTab("library");
    else if (target === "open-gaps") scrollToOpenGaps();
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

function renderCaseStatus() {
  const summary = $("#case-status-summary");
  const cta = $("#case-status-cta");
  const runBtn = $("#btn-case-run-baseline");
  const docCount = state.documents.length;
  const analysis = state.latestAnalysis;
  const openCount = analysis?.open_items?.length || 0;

  $("#btn-export-pdf")?.classList.toggle("hidden", !analysis);

  if (summary) {
    const parts = [`<span class="case-status-heading">Case status</span>`];
    const docLabel = `${docCount} document${docCount === 1 ? "" : "s"}`;
    parts.push(
      `<a href="#" class="case-status-link" data-case-nav="library">${docLabel}</a>`
    );

    if (analysis) {
      parts.push(
        `<span class="case-status-sep">·</span>`,
        `<span>${escapeHtml(analysisTypeLabel(analysis.analysis_type))}</span>`,
        `<span class="case-status-sep">·</span>`,
        `<span>${escapeHtml(formatEasternTimestamp(analysis.created_at))}</span>`,
        `<span class="case-status-sep">·</span>`,
        `<a href="#" class="case-status-link" data-case-nav="open-gaps">${openCount} open gap${openCount === 1 ? "" : "s"}</a>`
      );
    } else {
      parts.push(
        `<span class="case-status-sep">·</span>`,
        `<span>${docCount === 0 ? "No documents yet" : "No assessment"}</span>`
      );
    }

    summary.innerHTML = parts.join(" ");
  }

  if (cta && runBtn) {
    if (analysis) {
      cta.classList.add("hidden");
    } else {
      cta.classList.remove("hidden");
      runBtn.textContent = docCount === 0 ? "Add data" : "Run baseline assessment";
    }
  }
}

function updateHomeWorkflow() {
  renderCaseStatus();
}

function renderHomeState(hasAssessment) {
  $("#analyze-results-section")?.classList.toggle("hidden", !hasAssessment);
  $("#analyze-actions-card")?.classList.toggle("analyze-actions-secondary", hasAssessment);
  if (hasAssessment && !state.analysisRunning) {
    setAnalyzeActionsExpanded(false);
  } else if (!hasAssessment) {
    setAnalyzeActionsExpanded(true);
  }
  renderCaseStatus();
}

async function resumeActiveAnalysisJob() {
  try {
    const data = await api("/api/analyze/jobs/active");
    if (!data.job) return;

    const jobId = data.job.id;
    const payload = await api(`/api/analyze/jobs/${jobId}`);
    const job = payload.job;

    if (job.status === "completed" && job.analysis) {
      finishAnalysisRun(job.analysis);
      return;
    }
    if (job.status === "failed") {
      toast(job.error || "Analysis failed", "error");
      return;
    }

    setAnalysisRunning(true, jobId);
    const analysis = await pollAnalysisJob(jobId);
    finishAnalysisRun(analysis);
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

function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
  if (name === "library") loadDocuments();
  if (name === "history") loadHistory();
  if (name === "analyze") loadLatestAssessment();
  if (name === "settings") loadSettings();
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
  renderModelSelect();

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

  try {
    const health = await api("/api/health");
    updateLlmStatusDisplay(health, $("#llm-status"), $("#settings-llm-connection"));
  } catch {
    /* checkHealth handles errors on its own schedule */
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
  const details = (event.details || [])
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join("");
  return `
    <article class="audit-event">
      <div class="audit-event-header">
        <strong class="audit-event-label">${escapeHtml(event.label || event.event_type || "Event")}</strong>
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

function updateSelectedLabel() {
  const el = $("#selected-count");
  if (state.selectedIds.size === 0) {
    el.textContent = "Using all documents";
  } else {
    el.textContent = `Using ${state.selectedIds.size} selected document(s)`;
  }
}

function renderDocuments() {
  const list = $("#documents-list");
  if (!state.documents.length) {
    list.innerHTML = `<p class="muted">No documents yet. Add clinical notes, URLs, PDFs, imaging, or YouTube transcripts.</p>`;
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
          : meta.file_size_label
            ? meta.file_size_label
            : "";
      const paths = renderPathLines(docPathLines(doc));
      return `
        <article class="doc-item ${selected ? "selected" : ""}" data-id="${doc.id}">
          <strong>${escapeHtml(doc.title)}</strong>
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

  list.querySelectorAll(".btn-view").forEach((btn) =>
    btn.addEventListener("click", () => viewDocument(btn.dataset.id))
  );
  list.querySelectorAll(".btn-select").forEach((btn) =>
    btn.addEventListener("click", () => toggleSelect(btn.dataset.id))
  );
  list.querySelectorAll(".btn-delete").forEach((btn) =>
    btn.addEventListener("click", () => deleteDocument(btn.dataset.id))
  );
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

function formatReportDateTime(date) {
  const compact = window.matchMedia("(max-width: 600px)").matches;
  if (compact) {
    return date.toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
  }
  return date.toLocaleString("en-US", {
    timeZone: "America/New_York",
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

function updateReportDateTime(iso) {
  const el = $("#report-datetime");
  if (!el) return;
  const date = iso ? new Date(iso) : new Date();
  if (Number.isNaN(date.getTime())) {
    el.textContent = iso || "";
    el.removeAttribute("datetime");
    return;
  }
  el.textContent = formatReportDateTime(date);
  el.dateTime = iso || date.toISOString();
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sourceTagClass(tagText) {
  const lower = tagText.toLowerCase();
  if (lower.includes("document")) return "source-document";
  if (lower.includes("patient context")) return "source-context";
  if (lower.includes("inference") || lower.includes("not verified")) return "source-inference";
  if (lower.includes("unknown")) return "source-unknown";
  return "source-inference";
}

function formatMarkdownEmphasis(escaped) {
  return escaped.replace(/\*\*([^*\n]+)\*\*/g, '<span class="text-emphasis">$1</span>');
}

function formatWithSources(text) {
  if (!text) return "";
  const escaped = escapeHtml(text);
  const withTags = escaped.replace(
    /\[SOURCE:\s*([^\]]+)\]/gi,
    (_, inner) => {
      const cls = sourceTagClass(inner);
      return `<span class="source-tag-inline ${cls}" title="Source attribution">[SOURCE: ${inner}]</span>`;
    }
  );
  return formatMarkdownEmphasis(withTags).replace(/\n/g, "<br>");
}

function formatNumberedReferences(text) {
  if (!text) return "";
  const escaped = escapeHtml(text);
  return formatMarkdownEmphasis(escaped)
    .replace(/\[(\d+)\]/g, '<sup class="ref-cite" title="Reference $1">[$1]</sup>')
    .replace(/\n/g, "<br>");
}

function renderReferenceList(refs, heading = "References") {
  if (!refs || !refs.length) return "";
  const items = refs
    .map((ref) => `<li><span class="ref-num">[${escapeHtml(String(ref.num))}]</span> ${escapeHtml(ref.label || "")}</li>`)
    .join("");
  return `
    <div class="section-references-inner">
      <h5>${escapeHtml(heading)}</h5>
      <ol class="reference-list">${items}</ol>
    </div>`;
}

function renderReferencesBlock(element, refs, heading = "References") {
  if (!element) return;
  if (!refs || !refs.length) {
    element.classList.add("hidden");
    element.innerHTML = "";
    return;
  }
  element.classList.remove("hidden");
  element.innerHTML = renderReferenceList(refs, heading);
}

function renderReferencesAppendix(analysis) {
  const wrap = $("#references-appendix");
  const list = $("#references-appendix-list");
  const appendix = analysis?.references || [];
  if (!wrap || !list) return;
  if (!appendix.length) {
    wrap.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  wrap.classList.remove("hidden");
  list.innerHTML = appendix
    .map((ref) => `<li><span class="ref-num">[${escapeHtml(String(ref.num))}]</span> ${escapeHtml(ref.label || "")}</li>`)
    .join("");
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
  const fullRefs = $("#full-assessment-refs");
  const summaryRefs = $("#executive-summary-refs");

  if (!execTextEl) return;

  if (!analysis) {
    updateReportDateTime();
    if (execTimeEl) {
      execTimeEl.textContent = "";
      execTimeEl.removeAttribute("datetime");
    }
    legendWrap?.removeAttribute("open");
    fullCard?.classList.add("hidden");
    renderReferencesBlock(summaryRefs, []);
    renderReferencesBlock(fullRefs, []);
    renderReferencesAppendix(null);
    execTextEl.innerHTML = "";
    if (fullBody) fullBody.innerHTML = "";
    renderOpenItemsTable([]);
    selectOpenItem(null);
    renderSourceAttributionNotice(null);
    renderHomeState(false);
    renderCaseStatus();
    return;
  }

  updateReportDateTime(analysis.created_at);
  if (execTimeEl) {
    execTimeEl.textContent = formatEasternTimestamp(analysis.created_at);
    execTimeEl.dateTime = analysis.created_at;
  }
  legendWrap?.removeAttribute("open");

  const summaryDisplay = analysis.executive_summary_display || analysis.executive_summary || "";
  const responseDisplay = analysis.response_display || analysis.response || "";

  renderHomeState(true);
  renderSourceAttributionNotice(analysis);
  renderCaseStatus();

  if (summaryDisplay) {
    execTextEl.innerHTML = `<div class="numbered-text">${formatNumberedReferences(summaryDisplay)}</div>`;
  } else {
    execTextEl.innerHTML = '<p class="muted">No assessment text was returned.</p>';
  }
  renderReferencesBlock(summaryRefs, analysis.executive_summary_refs || []);

  if (fullCard && fullBody) {
    if (responseDisplay) {
      fullCard.classList.remove("hidden");
      fullBody.innerHTML = formatNumberedReferences(responseDisplay);
      renderReferencesBlock(fullRefs, analysis.response_refs || []);
    } else {
      fullCard.classList.add("hidden");
      fullBody.innerHTML = "";
      renderReferencesBlock(fullRefs, []);
    }
  }

  renderReferencesAppendix(analysis);
  renderOpenItemsTable(analysis.open_items || []);
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
    renderCaseStatus();
  }
}

async function exportAssessmentPdf() {
  if (!state.latestAnalysis) {
    return toast("No assessment to export", "error");
  }

  const btn = $("#btn-export-pdf");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/analyses/latest/export.pdf", {
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
    const created = state.latestAnalysis.created_at || "";
    const datePart = created.slice(0, 10) || "export";
    const filename = `beatit-assessment-${datePart}.pdf`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
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

async function loadDocuments() {
  const data = await api("/api/documents");
  state.documents = data.documents || [];
  renderDocuments();
  updateSelectedLabel();
  updateHomeWorkflow();
}

async function viewDocument(id) {
  switchTab("library");
  const data = await api(`/api/documents/${id}`);
  const panel = $("#doc-detail");
  const doc = data.document;
  if (!panel || !doc) return;

  panel.classList.remove("hidden");
  $("#doc-detail-title").textContent = doc.title;

  const metaEl = $("#doc-detail-meta");
  if (metaEl) {
    const meta = doc.metadata || {};
    metaEl.innerHTML = `
      <span class="badge">${escapeHtml(doc.source_type || "document")}</span>
      <span class="muted small">${escapeHtml(formatTimestamp(doc.created_at))}</span>
      ${meta.modality ? `<span class="badge">${escapeHtml(meta.modality)}</span>` : ""}
      ${meta.file_size_label ? `<span class="muted small">${escapeHtml(meta.file_size_label)}</span>` : ""}`;
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

function toggleSelect(id) {
  if (state.selectedIds.has(id)) state.selectedIds.delete(id);
  else state.selectedIds.add(id);
  renderDocuments();
  updateSelectedLabel();
}

async function deleteDocument(id) {
  if (!confirm("Delete this document and its stored files?")) return;
  await api(`/api/documents/${id}`, { method: "DELETE" });
  state.selectedIds.delete(id);
  if ($("#doc-detail") && !$("#doc-detail").classList.contains("hidden")) {
    closeDocumentDetail();
  }
  toast("Document deleted");
  await loadDocuments();
}

async function runAnalysis({ query = "", baseline = false, summarize = false } = {}) {
  if (state.analysisRunning) {
    toast("An analysis is already running. Please wait for it to finish.", "error");
    return;
  }

  try {
    const jobId = await startAnalysisJob({ query, baseline, summarize });
    setAnalysisRunning(true, jobId);
    const analysis = await pollAnalysisJob(jobId);
    finishAnalysisRun(analysis);
  } catch (err) {
    if (err.status === 409) {
      toast("An analysis is already running — resuming progress.", "error");
      await resumeActiveAnalysisJob();
      return;
    }
    toast(err.message, "error");
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
  initUploadResultBanner();
  initCaseStatusNavigation();
  updateReportDateTime();
  renderCaseStatus();
}

async function loadInitialData() {
  try {
    await Promise.allSettled([loadDocuments(), loadLatestAssessment()]);
  } finally {
    renderCaseStatus();
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
safeOn("#btn-refresh-docs", "click", () => loadDocuments().catch((e) => toast(e.message, "error")));
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
    switchTab("library");
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
    switchTab("library");
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
    switchTab("library");
  } catch (e) {
    toast(e.message, "error");
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
    switchTab("library");
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

function setImagingSelection(files) {
  imagingSelection = Array.from(files || []).filter(isImagingFile);
  renderImagingSelection();
}

function clearImagingSelection() {
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
    switchTab("library");
    await loadDocuments();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setImagingUploadProgress("", false);
    renderImagingSelection();
  }
}

$("#imaging-files")?.addEventListener("change", (event) => {
  $("#imaging-folder").value = "";
  setImagingSelection(event.target.files);
});

$("#imaging-folder")?.addEventListener("change", (event) => {
  $("#imaging-files").value = "";
  const allFiles = Array.from(event.target.files || []);
  setImagingSelection(allFiles);
  const skipped = allFiles.length - imagingSelection.length;
  if (skipped > 0) {
    toast(`Skipped ${skipped} non-imaging file${skipped === 1 ? "" : "s"} in folder`, "error");
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
    switchTab("library");
  } catch (e) {
    toast(e.message, "error");
  }
});

safeOn("#btn-baseline", "click", () => runAnalysis({ baseline: true }));
safeOn("#btn-summarize", "click", () => runAnalysis({ summarize: true }));
safeOn("#btn-case-add-data", "click", () => switchTab("ingest"));
safeOn("#btn-case-run-baseline", "click", () => {
  if (state.documents.length === 0) {
    switchTab("ingest");
    return;
  }
  runAnalysis({ baseline: true });
});

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
window.matchMedia("(max-width: 600px)").addEventListener("change", () => {
  updateReportDateTime(state.latestAnalysis?.created_at);
});
void loadInitialData();
