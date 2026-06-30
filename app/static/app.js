const state = {
  documents: [],
  selectedIds: new Set(),
  analyses: [],
  latestAnalysis: null,
  models: [],
  settings: {},
  selectedOpenItemId: null,
  selectedOpenItem: null,
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
  const res = await fetch(path, {
    credentials: "include",
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401 && !path.includes("/login")) {
    window.location.href = "/login";
    throw new Error("Please sign in");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.message || `Request failed (${res.status})`);
  }
  return data;
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

function showUploadResult(doc) {
  const panel = $("#upload-result");
  const title = $("#upload-result-title");
  const paths = $("#upload-result-paths");
  if (!panel || !doc) return;

  title.textContent = doc.title;
  paths.innerHTML = renderPathLines(docPathLines(doc));
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
    list.innerHTML = `<p class="muted">No documents yet. Add clinical notes, URLs, PDFs, or YouTube transcripts.</p>`;
    return;
  }

  list.innerHTML = state.documents
    .map((doc) => {
      const selected = state.selectedIds.has(doc.id);
      const meta = doc.metadata || {};
      const excerpt = meta.page_count ? `${meta.page_count} pages` : "";
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
  return withTags.replace(/\n/g, "<br>");
}

function openItemStatusLabel(status) {
  const s = String(status || "open").toLowerCase();
  if (s === "investigating") return "Investigating";
  if (s === "investigated") return "Investigated";
  if (s === "resolved") return "Resolved";
  if (s === "closed") return "Closed";
  return "Open";
}

function isOpenItemClosed(item) {
  const s = String(item?.status || "open").toLowerCase();
  return s === "resolved" || s === "closed";
}

function itemChipStatusClass(status) {
  const s = String(status || "open").toLowerCase();
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
  const body = $("#open-item-investigation");
  const title = $("#open-item-panel-title");
  const resolveBtn = $("#btn-resolve-item");
  const reopenBtn = $("#btn-reopen-item");
  if (!panel || !item) return;

  panel.classList.remove("hidden");
  if (title) title.textContent = truncate(item.item, 100);
  if (meta) {
    meta.innerHTML = `
      <span class="item-chip item-chip-type">${escapeHtml(item.type || item.item_type || "Item")}</span>
      <span class="${itemChipStatusClass(item.status)}">${escapeHtml(openItemStatusLabel(item.status))}</span>
      ${item.investigation_at ? `<span class="muted small">Investigation: ${escapeHtml(formatTimestamp(item.investigation_at))}</span>` : ""}
      ${item.investigation_model ? `<span class="badge">${escapeHtml(item.investigation_model)}</span>` : ""}`;
  }

  renderOpenItemComments(item);

  const closed = isOpenItemClosed(item);
  resolveBtn?.classList.toggle("hidden", closed);
  reopenBtn?.classList.toggle("hidden", !closed);

  if (body) {
    if (item.investigation_response) {
      body.innerHTML = formatWithSources(item.investigation_response);
    } else if (item.status === "investigating") {
      body.innerHTML = '<p class="muted">Investigation in progress…</p>';
    } else {
      body.innerHTML =
        '<p class="muted">No investigation yet. Use <strong>Run investigation</strong> for a focused analysis with source tags.</p>';
    }
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

  const loading = $("#open-item-loading");
  const btn = $("#btn-investigate-item");
  loading?.classList.remove("hidden");
  if (btn) btn.disabled = true;

  try {
    selectOpenItem({ ...state.selectedOpenItem, status: "investigating" });
    const data = await api(`/api/open-items/${id}/investigate`, { method: "POST" });
    const item = data.open_item;
    selectOpenItem(item);
    updateOpenItemInState(item);
    toast("Investigation complete");
  } catch (err) {
    toast(err.message, "error");
    if (state.selectedOpenItemId) {
      try {
        const item = await loadOpenItem(state.selectedOpenItemId);
        selectOpenItem(item);
        updateOpenItemInState(item);
      } catch {
        /* ignore */
      }
    }
  } finally {
    loading?.classList.add("hidden");
    if (btn) btn.disabled = false;
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
  const notices = ["#source-attribution-notice", "#assessment-source-notice"];
  if (!analysis) {
    notices.forEach((sel) => $(sel)?.classList.add("hidden"));
    return;
  }

  const level = analysis.source_attribution || "missing";
  let html = "";
  if (level === "full") {
    notices.forEach((sel) => $(sel)?.classList.add("hidden"));
    return;
  }
  if (level === "normalized") {
    html =
      "<strong>Partial source attribution</strong>" +
      "Informal citations like <code>(CT Report)</code> were converted to " +
      "<code>[SOURCE: Document \"…\"]</code> tags. Staging lines without document evidence are flagged as " +
      "<span class=\"source-tag-inline source-inference\">AI inference</span>. " +
      "Re-run <strong>baseline assessment</strong> for fully LLM-generated tags.";
  } else {
    html =
      "<strong>Source tags missing</strong>" +
      "This assessment has no <code>[SOURCE: …]</code> tags. " +
      "Click <strong>Run baseline assessment</strong> below to regenerate with mandatory source attribution.";
  }

  notices.forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    el.classList.remove("hidden");
    el.className = `source-attribution-notice ${level === "normalized" ? "warn" : "error"}`;
    el.innerHTML = html;
  });
}

function renderLatestAssessment(analysis) {
  state.latestAnalysis = analysis || null;
  const timeEl = $("#latest-assessment-time");
  const execTimeEl = $("#executive-summary-time");
  const metaEl = $("#latest-assessment-meta");
  const bodyEl = $("#latest-assessment-body");
  const execTextEl = $("#executive-summary-text");

  if (!bodyEl) return;

  if (!analysis) {
    if (timeEl) timeEl.textContent = "";
    if (execTimeEl) execTimeEl.textContent = "";
    $("#btn-export-pdf")?.classList.add("hidden");
    if (metaEl) metaEl.innerHTML = "";
    if (execTextEl) {
      execTextEl.innerHTML =
        '<p class="muted empty-assessment">Run an assessment to see a summary and open items here.</p>';
    }
    renderOpenItemsTable([]);
    selectOpenItem(null);
    renderSourceAttributionNotice(null);
    bodyEl.innerHTML =
      '<p class="muted empty-assessment">No assessment yet. Run an analysis below — your most recent result will always stay here.</p>';
    return;
  }

  const ts = formatTimestamp(analysis.created_at);
  if (timeEl) timeEl.textContent = ts;
  if (execTimeEl) execTimeEl.textContent = ts;
  $("#btn-export-pdf")?.classList.remove("hidden");
  if (metaEl) {
    metaEl.innerHTML = `
      <span class="badge">${escapeHtml(analysisTypeLabel(analysis.analysis_type))}</span>
      <span class="badge">${escapeHtml(analysis.model || "model")}</span>
      <span>${escapeHtml(truncate(analysis.query, 100))}</span>`;
  }

  const summary = analysis.executive_summary || "";
  if (execTextEl) {
    execTextEl.innerHTML = summary
      ? `<div class="sourced-text">${formatWithSources(summary)}</div>`
      : '<p class="muted">No executive summary extracted for this assessment.</p>';
  }

  renderOpenItemsTable(analysis.open_items || []);
  renderSourceAttributionNotice(analysis);
  if (bodyEl) {
    bodyEl.innerHTML = analysis.response
      ? `<div class="sourced-text assessment-body-text">${formatWithSources(analysis.response)}</div>`
      : '<p class="muted empty-assessment">No assessment yet.</p>';
  }
}

async function loadLatestAssessment() {
  const data = await api("/api/analyses/latest");
  renderLatestAssessment(data.analysis);
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
        <pre class="doc-text">${escapeHtml(a.response)}</pre>
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
}

async function viewDocument(id) {
  const data = await api(`/api/documents/${id}`);
  $("#doc-detail").classList.remove("hidden");
  $("#doc-detail-title").textContent = data.document.title;
  const pathsEl = $("#doc-detail-paths");
  if (pathsEl) {
    pathsEl.innerHTML = renderPathLines(docPathLines(data.document));
  }
  $("#doc-detail-text").textContent = data.extracted_text || "[No extracted text]";
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
  toast("Document deleted");
  await loadDocuments();
}

async function runAnalysis({ query = "", baseline = false, summarize = false } = {}) {
  const loading = $("#analyze-loading");
  loading.classList.remove("hidden");

  try {
    let data;
    const docIds = state.selectedIds.size ? [...state.selectedIds] : null;

    if (summarize) {
      const qs = docIds ? "?" + docIds.map((id) => `document_ids=${encodeURIComponent(id)}`).join("&") : "";
      data = await api(`/api/analyze/summarize${qs}`, { method: "POST" });
    } else {
      data = await api("/api/analyze", {
        method: "POST",
        body: JSON.stringify({
          query,
          document_ids: docIds,
          include_baseline_assessment: baseline,
        }),
      });
    }

    renderLatestAssessment(data.analysis);
    state.analyses = [data.analysis, ...state.analyses.filter((a) => a.id !== data.analysis.id)];
    toast(`Assessment saved · ${formatTimestamp(data.analysis.created_at)}`);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    loading.classList.add("hidden");
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

// Tab navigation
$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => switchTab(tab.dataset.tab))
);

$("#btn-close-detail").addEventListener("click", () => $("#doc-detail").classList.add("hidden"));
$("#btn-refresh-docs").addEventListener("click", () => loadDocuments().catch((e) => toast(e.message, "error")));
$("#btn-refresh-history").addEventListener("click", () => loadHistory().catch((e) => toast(e.message, "error")));

$("#btn-ingest-text").addEventListener("click", async () => {
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

$("#btn-ingest-url").addEventListener("click", async () => {
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

$("#btn-ingest-youtube").addEventListener("click", async () => {
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

$("#btn-ingest-pdf").addEventListener("click", async () => {
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
    toast(`PDF uploaded · ${file.name}`);
    switchTab("library");
  } catch (e) {
    toast(e.message, "error");
  }
});

$("#btn-ingest-video").addEventListener("click", async () => {
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

$("#btn-baseline").addEventListener("click", () => runAnalysis({ baseline: true }));
$("#btn-summarize").addEventListener("click", () => runAnalysis({ summarize: true }));
$("#btn-analyze").addEventListener("click", () => {
  const query = $("#analyze-query").value.trim();
  if (!query) return toast("Enter a question or use Baseline assessment", "error");
  runAnalysis({ query });
});

$("#btn-save-settings")?.addEventListener("click", () =>
  saveModelSettings().catch((e) => toast(e.message, "error"))
);
$("#btn-save-patient")?.addEventListener("click", () =>
  savePatientContext().catch((e) => toast(e.message, "error"))
);
$("#settings-model")?.addEventListener("change", updateModelDescription);

$("#btn-export-pdf")?.addEventListener("click", () =>
  exportAssessmentPdf()
);
$("#btn-investigate-item")?.addEventListener("click", () =>
  investigateSelectedOpenItem()
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
initTheme();
bindFileInput("#pdf-file");
bindFileInput("#video-file");
loadLatestAssessment().catch(() => {});
loadDocuments().catch(() => {});
