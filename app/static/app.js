const state = {
  documents: [],
  selectedIds: new Set(),
  analyses: [],
  models: [],
  settings: {},
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
    credentials: "same-origin",
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.message || `Request failed (${res.status})`);
  }
  return data;
}

async function checkHealth() {
  const pill = $("#llm-status");
  try {
    const data = await api("/api/health");
    const llm = data.llm || {};
    const active = llm.active || {};

    if (active.ready) {
      pill.textContent = `${active.provider} · ${active.model}`;
      pill.className = "status-pill ok";
    } else if (llm.configured_provider === "openrouter") {
      const err = llm.openrouter?.error || active.error || "Set OPENROUTER_API_KEY in .env";
      pill.textContent = `OpenRouter · ${err}`;
      pill.className = "status-pill bad";
    } else {
      pill.textContent = active.error || "No LLM available";
      pill.className = "status-pill bad";
    }
  } catch {
    pill.textContent = "API unreachable";
    pill.className = "status-pill bad";
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
  const current = $("#settings-current");
  if (current) {
    current.textContent = `Active: ${state.settings.openrouter_model || data.default_model}`;
  }
}

async function saveSettings() {
  const select = $("#settings-model");
  if (!select) return;
  const data = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ openrouter_model: select.value }),
  });
  state.settings = { ...state.settings, ...data.settings };
  toast("Model saved");
  const current = $("#settings-current");
  if (current) current.textContent = `Active: ${data.settings.openrouter_model}`;
  checkHealth();
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

function renderHistory() {
  const list = $("#history-list");
  if (!state.analyses.length) {
    list.innerHTML = `<p class="muted">No analyses yet.</p>`;
    return;
  }

  list.innerHTML = state.analyses
    .map(
      (a) => `
      <article class="history-item">
        <div class="doc-meta">
          <span>${formatDate(a.created_at)}</span>
          <span class="badge">${escapeHtml(a.model || "model")}</span>
        </div>
        <strong>${escapeHtml(truncate(a.query, 120))}</strong>
        <pre class="doc-text" style="margin-top:0.5rem;max-height:240px">${escapeHtml(a.response)}</pre>
      </article>`
    )
    .join("");
}

async function loadDocuments() {
  const data = await api("/api/documents");
  state.documents = data.documents || [];
  renderDocuments();
  updateSelectedLabel();
}

async function loadHistory() {
  const data = await api("/api/analyses");
  state.analyses = data.analyses || [];
  renderHistory();
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
  const result = $("#analyze-result");
  loading.classList.remove("hidden");
  result.classList.add("hidden");

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

    result.textContent = data.analysis.response;
    result.classList.remove("hidden");
    toast("Analysis complete");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    loading.classList.add("hidden");
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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
  saveSettings().catch((e) => toast(e.message, "error"))
);
$("#settings-model")?.addEventListener("change", updateModelDescription);

checkHealth();
initTheme();
bindFileInput("#pdf-file");
bindFileInput("#video-file");
loadDocuments().catch(() => {});
