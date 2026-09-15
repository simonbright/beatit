# BeatIt — App Functionality

BeatIt is a local-first **patient care workspace** for organizing clinical research material (notes, PDFs, imaging, links, transcripts) and synthesizing **sourced** AI assessments via **Ollama** or **OpenRouter**.

It is **not medical advice**. Verify clinical decisions with qualified care teams.

Live production (example): [beatit-m9n1.onrender.com](https://beatit-m9n1.onrender.com) · Project page: [simonbrightman.com/projects/beatit](https://simonbrightman.com/projects/beatit/)

---

## At a glance

| Area | What it does |
|------|----------------|
| **Patients & cases** | Multiple people; each person can have multiple cases with separate libraries |
| **Library** | Ingest notes, URLs, YouTube, Facebook, PDFs/images, video, DICOM |
| **Home assessment** | Sourced AI baseline with executive summary, open items, references |
| **Labs** | Charted/tabled blood trends; Canada SI ↔ US units; PDF import |
| **Meds** | Prescriptions, OTC, remedies; photo import; safety oversight |
| **Log** | Feelings, symptoms, meds taken, food/drink, exercise — quick tiles + journal |
| **AI Chat** | Multi-turn treatment discussion scoped to library + Home assessment |
| **Custom Tasks** | Focused draft analyses you can promote to the record |
| **Imaging vision** | Optional Ollama vision reads of selected DICOM slices |
| **PDF export** | Assessments, logs, meds, labs, coverage, and multi-section bundles |
| **Auth** | Optional sign-in (required on typical Render deploys) |

---

## Global shell

- **Main tabs:** Home · Tasks · Library · Settings
- **Header:** Patient photo/name (switch who/case), Export PDF, AI Chat, How are you?, How-to, day/night theme, Sign out
- **Status:** LLM connection pill, background job bar, toasts, version footer
- **Export PDF modal:** Choose Assessment / Logs / Medications / Labs (All or None); empty sections are skipped; log range defaults to last 7 days (Today → All)

---

## 1. Sign-in & access

- Email/username + password when auth is enabled (`AUTH_USERNAME` / `AUTH_PASSWORD`, optional per-user `AUTH_USER_PASSWORDS`)
- Sign out from the header
- **Settings → Access:** add, update, or remove disk users (useful on Render without editing secrets)
- Local development can run without auth (treated as user `local`)

---

## 2. Patients & cases

**Patients** are people. **Cases** are workstreams under a person (e.g. Cardiac workup vs Chronic Fatigue).

- Create patients and cases; rename a case’s display label
- Switch active patient + case from the header (Who/Case modal); a progress overlay loads the case
- Upload a patient photo (header or Settings → Profile)
- Browse **sibling-case** documents in Library as read-only when comparing cases

**Important split**

| Stored per case | Shared across cases (patient-wide) |
|-----------------|-------------------------------------|
| Library documents | Profile: DOB, gender, photo |
| Assessments / analysis history | Labs, medications, journal/log |
| Case-specific analysis context | Height/weight, milestones, log tiles |

Wrong case context can produce the wrong assessment — Settings warns when the active case matters.

---

## 3. Home — Log (self-reports)

Daily/symptom logging for the **active patient** (not tied to case).

- **Quick tiles** on Home: one-tap or 1–5 severity scales
- **How are you?** modal: feeling/action chips, meds (including ones normally hidden from Log), food/drink, severity, free-text details, optional datetime
- **Exercise:** Walked (minutes) or Weights (weight, reps, time + how it felt)
- History as **Timeline** or **List**; range Today → All
- Edit/delete entries; export a log PDF for the selected range
- Derived **log observations** appear with recent entries

**Customization (Settings → Profile)**

- Add/reorder **custom log tiles** (per person)
- Manage **Ate / Drank** quick picks
- Browse all self-reports

Medications can be marked **Show on Log** (chips) or kept off Log (typical for daily Rx).

---

## 4. Home — Summary (official assessment)

The official case view after a successful Home analysis:

- Executive summary and full assessment body
- Numbered **source references** with a citation legend (Document / Diagnostic / Context / AI / unknown)
- Source sidebar for inspecting citations
- Attribution / quality notices when present

Chat replies and Custom Task drafts do **not** replace Home until you explicitly promote them or use **Update Home**.

---

## 5. Home — Medications

- Active prescriptions, OTC, and remedies on Home
- Full CRUD in **Settings → Profile → Medications**
- Fields: official name vs “name I use”, dosage, frequency, category, start/stop dates, dose-change history
- **Photo / file import** → review → confirm
- **Health oversight** safety review (also on Tasks)
- Export PDF: Rx / Non-Rx / History / All

Official names are preferred for safety review and analysis. Dose-change dates can overlay lab charts as milestones.

---

## 6. Home — Labs (diagnostics)

Blood and related test trends for the patient.

### Viewing

- **Charts** or **Table** (flippable matrix: tests × dates or dates × tests)
- **Canada (SI)** vs **United States (conventional)** unit toggle — originals from each report are kept
- Date labels follow the same regional toggle (Canada: Day Month Year; US: Month Day, Year)
- Traffic-light status vs approximate reference bands (green / yellow / red; gray = no reference)
- Expand a single analyte; filter by status; **Gaps** for never-recorded or due-for-update metrics
- **Timeline overlays:** medication or lifestyle milestones (dashed markers on charts)
- Export labs PDF (uses the active unit system)

### Adding data

- Manual entry in Settings → Profile → Labs
- Upload/parse lab PDFs or photos (one tap uploads and parses)
- Import from a Library document; confirm when identity mismatch or incomplete parse
- **Multi-visit LifeLabs-style exports** become one reading per analyte per date of service (not latest-only)
- Prefer **collection / date of service**, not print date
- Height/weight measurements live under Profile → Basics and can chart as vitals

---

## 7. Home — Flagged

Clinical PDFs/photos that still need handling:

- Stay flagged until labs are imported, OCR succeeds, or you dismiss after review
- Refresh the list; act on suggested actions (Import to Labs, re-extract, dismiss, …)
- Home banner: “Review flagged” when items exist
- Count badge on the Flagged subtab

---

## 8. Home — Coverage & Gaps

### Coverage

Patient-wide documentation inventory across **all** cases:

- Filters: All / By type / By month / Needs attention
- Checklist kinds: lab, MRI, CT, ultrasound, pathology, cardiology, other report
- Attention: needs OCR, flagged, missing file
- Export coverage PDF; refresh

### Gaps (open items)

From the last assessment:

- Prioritized open items table
- **Explore** → focused investigation with guidance presets
- Review draft → Accept / Comment only / Discard
- Resolve / Reopen; add comments
- If a gap looks wrong, adjust scope or re-run analysis

---

## 9. Home — Run analysis

Kick off the official Home assessment for the **active case**:

- Choose document scope (Library picker; quick filters: Notes+PDFs, notes, PDFs, last run + new, all/clear/match last)
- Empty selection = all documents in the case
- Optional guidance text + presets
- Run baseline analysis as an **async job** (cancelable; may take minutes)
- Quick asks: “What am I missing?” / “What's wrong with me?”
- Summarize documents; start imaging vision from here
- One-off custom question → opens as a **Custom Tasks draft** (does not change Home until promoted)
- Note when pinned chat observations will feed the next run

---

## 10. Library

### Add documents

| Source | Notes |
|--------|--------|
| Clinical text | Paste notes |
| URL | Fetch web pages |
| YouTube | Needs captions |
| Facebook | Public reel/video |
| PDF / images | Multi-file; lab photos processed in order; clear labs may auto-import |
| Video file | Manual notes until transcription is connected |
| DICOM / folder / ZIP / images | Imaging catalog |

### Documents list

- Filter, paginate, multi-select for analysis
- Detail: extracted text, citation display name, preview/file, re-extract, replace file, **Import to Labs**, delete
- Sibling-case docs (read-only)
- Run analysis from current selection
- Export coverage from the toolbar

Citation **display name** can differ from stored title; matching still uses the title.

---

## 11. Imaging vision

Separate from the overall Home assessment:

- Filter DICOM by study / series / kernel / level; reindex metadata
- Select up to **3** slices (even sampling; prefer Axial over Scout/MIP)
- Async vision analysis via Ollama vision model; cancel jobs
- Review text reports in Library; decide later whether to include them in Home

---

## 12. AI Chat

Multi-turn **treatment-options** discussion:

- Scoped to selected library documents + current Home assessment
- New / list / delete sessions; starter prompts
- Pin reply excerpts for the next analysis; save to library; copy
- **Update Home** on a reply (pins + refreshes the main assessment)
- Can ask the model to read a document by name

Chat does **not** auto-replace Home unless you Update Home / promote.

---

## 13. Custom Tasks

Focused analyses that stay as **drafts** until promoted:

- Run custom questions; refine & re-run
- Annotate title/notes; export PDF / native Share
- **Add to record** (promote) or discard
- Running jobs list + drafts list + source sidebar
- **Health oversight** card on the same tab (medication safety)

Trial searches work better when patient context (line of therapy, biomarkers, etc.) is filled in Settings.

---

## 14. PDF export

| Export | Contents |
|--------|----------|
| Bundle (header) | Assessment + Logs (+ range) + Medications + Labs |
| Assessment | Latest or selected analysis with references |
| Journal / Logs | Selected date range |
| Medications | Rx / Non-Rx / History / All |
| Diagnostics | Charts/table data; SI or US units & date order |
| Coverage | Document inventory |
| Custom task | That draft/analysis |

Exports use Eastern timestamps where applicable; lab charts embed print-quality sparklines.

---

## 15. Settings

| Pane | Capabilities |
|------|----------------|
| **Patients** | Current who/case; add patient/case; rename case |
| **Profile → Basics** | Photo, DOB, gender; height/weight history (cm/kg) |
| **Profile → Labs** | Upload & parse; manual readings; import review |
| **Profile → Medications** | Full med list, photo import, remedy quick-add, export |
| **Profile → Log tiles** | Order/add Home log options |
| **Profile → Ate/Drank** | Food/drink quick picks |
| **Profile → Timeline** | Lifestyle milestones for lab overlays |
| **Profile → Self-reports** | Full journal history |
| **Analysis** | Clinical reviewer context; case patient context |
| **Historical Assessments** | Past assessments (newest first) |
| **Labels** | Customize source badge display names |
| **LLM** | OpenRouter fallback model; connection status; Ollama info |
| **Access** | Sign-in users |
| **Audit** | Filterable permanent audit trail |

---

## 16. How-to & themes

- In-app **How BeatIt works** (header): Library → context → Run; open items; chat; tasks; citations; settings
- Day/night theme (`localStorage` key `beatit-theme`)
- Version footer (`/api/version`); health check (`/api/health`)

---

## AI & citations

Assessments and chat use tagged sources such as Document, Web, Chat observation, Patient context, and AI inference. The UI maps inline numbers to a references list tied to library records when possible.

**LLM providers**

- OpenRouter (cloud)
- Ollama (local or remote, e.g. Tailscale — see `docs/TAILSCALE_OLLAMA_SETUP.md`)
- Auto: Ollama when reachable, else OpenRouter

---

## Architecture (for orientation)

```
app/
  ingest/       # URL, PDF, YouTube, text, video, imaging
  storage/      # SQLite + filesystem
  services/     # LLM, synthesis, labs, meds, PDF, handling, …
  static/       # Web UI
  api/          # FastAPI routes
data/           # Runtime data (gitignored)
```

Key services: `case_manager` (patients/profile), `synthesis` + `analysis_jobs` (assessments), `diagnostic_import` + `lab_units` (labs), `clinical_report_handling` (flagged), `options_chat` (AI Chat), `pdf_export`, `imaging_*` / `vision_jobs`.

Full HTTP surface: `app/api/routes.py`.

---

## What BeatIt is not

- Not a calendar/scheduling product
- Not billing or insurance software
- Not a multi-tenant clinician messaging platform
- Not a substitute for your care team

Video files currently expect manual notes until transcription is connected.

---

## Related docs

- [README.md](../README.md) — setup, deploy, citations overview
- [docs/TAILSCALE_OLLAMA_SETUP.md](TAILSCALE_OLLAMA_SETUP.md) — remote Ollama
- [CONTRIBUTING.md](../CONTRIBUTING.md) · [SECURITY.md](../SECURITY.md)
