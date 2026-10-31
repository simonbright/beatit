# Bright Health — App Functionality

Bright Health is a local-first **patient care workspace** for organizing clinical research material (notes, PDFs, imaging, links, transcripts) and synthesizing **sourced** AI assessments via **Ollama** or **OpenRouter**.

It is **not medical advice**. Verify clinical decisions with qualified care teams.

Live production: [brighthealth.onrender.com](https://brighthealth.onrender.com) · Project page: [simonbrightman.com/projects/beatit](https://simonbrightman.com/projects/beatit/)

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
| **Timeline** | Facts-only family timeline: who was in, tests, moves, meds, family updates — each with its source; WhatsApp import; copyable family update |
| **Questions** | What to ask which doctor; copy a list before the visit; mark asked / answered |
| **Ask** | Family agent over the whole stay — who has the ball, open items, discharge, Internal Medicine / attending — plus deeper treatment discussion |
| **Custom Tasks** | Focused draft analyses you can promote to the record |
| **Imaging vision** | Optional Ollama vision reads of selected DICOM slices |
| **PDF export** | Assessments, logs, meds, labs, coverage, and multi-section bundles |
| **Auth** | Optional sign-in (required on typical Render deploys) |
| **Notifications** | Web Push on this device when someone else records a visit, timeline note, or WhatsApp import |

---

## Global shell

- **Views:** **Simple** (default) shows **Log** (heart), Timeline, **Ask**, Labs, and Questions. A header toggle switches to **Advanced** (Home · Labs · Plan · Timeline · Ask · Questions · Tasks · Library · Settings). The choice is remembered.
- **Main tabs (Advanced):** Home · Labs · Plan · Timeline · Ask · Questions · Tasks · Library · Settings
- **Header:** Simple/Advanced toggle, patient photo/name (switch who/case), Export PDF, Ask, update notifications, How are you?, How-to, day/night theme, Sign out
- **Status:** LLM connection pill, background job bar, toasts, version footer
- **Export PDF modal:** Choose Assessment / Logs / Medications / Labs (All or None); empty sections are skipped; log range defaults to last 7 days (Today → All)

---

## 1. Sign-in & access

- Email/username + password when auth is enabled (`AUTH_USERNAME` / `AUTH_PASSWORD`, optional per-user `AUTH_USER_PASSWORDS`)
- Sign out from the header
- **Settings → Access:** add, update, or remove disk users (useful on Render without editing secrets)
- **Settings → Notifications:** turn Web Push on or off for this device. The Timeline also offers a first-time prompt. The person who saves an update is not notified.
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

- **Simple view:** Log is a top-level heart tab (Home extras stay in Advanced)
- **Quick tiles** on Home / Simple Log: one-tap or 1–5 severity scales
- **Readings** shortcut: temperature, heart rate, blood pressure, oxygen saturation, blood sugar — logged as vitals and shown on Labs
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

## 12. Ask

Family agent over the whole stay, plus deeper treatment discussion:

- Default questions: **Who has the ball?**, **Which items are open?**, **What is the plan for discharge?**, **Who is the Internal Medicine care team / attending?**
- Uses the timeline, who has the ball, care/family team, open actions, doctor questions, stay/discharge notes, labs, library, and the current Home assessment
- On Simple as its own **Ask** tab; same defaults also sit on Timeline
- New / list / delete sessions; pin excerpts; **Update Home** on a reply
- Can ask the model to read a document by name
- When Ask, Home, Timeline, or Questions names someone who is not a saved contact, a **+ Add to contacts** chip saves them to the care team or Family

Chat does **not** auto-replace Home unless you Update Home / promote.

---

## 12a. Timeline (family updates)

A chronological, **facts-only** view of the patient's care for the whole family. Nothing on it is AI-generated; every row shows where it came from.

**What feeds it**

| Source | Shows as | Provenance line |
|--------|----------|-----------------|
| **Log what happened** (manual) | Practitioner visit, call, family meeting, test ordered/done, result shared, procedure, medication change, admitted / transfer / discharged, status observed, family note | "Recorded by &lt;user&gt;" (+ "edited by" when changed) |
| **WhatsApp import** | Each message verbatim, with sender and time sent | "From WhatsApp · sent by &lt;name&gt;" |
| Labs | One row per collection date with values | "From the Labs record" |
| Medications | Start / dose change / stop | "From the Medications record" |
| Plan | Scheduled items ("Coming up"), done items, sent/call activity | "From the Plan" |
| Profile milestones | Milestones | "From Profile → Timeline milestones" |
| Library | Uploads (imaging collapsed per day) | "Library upload" |

**What's missing** (top of the tab)

Only days or results that were **not logged** — not hospital status, and not overdue tasks (those stay on Action items). A gap may still have happened.

- **Hospital stay** is a quiet status line above the panel ("In hospital since Mon Oct 26 · day 6"), not a missing item.
- For each day of the stay (last 14 days): **no care-team visit**, **no tests or results**, **no family update**. Past days are warnings; today is shown softly as "today so far".
- When that day's WhatsApp messages mention the care team or tests, the flag says so ("9 family messages mention the care team; use Classify to record it").
- **Results not shared**: a test ordered/done more than 24 hours ago with no result or lab reading after it.
- **Not seen recently**: a practitioner seen during the current stay but not in the last 2+ days.
- Each flag has a one-tap action (Log a visit, Log a test, Add the result). Day headers in the timeline carry the same flags.
- The **Family update** lists what wasn't recorded for the period.
- **Family** and **Professional care team** start collapsed; tap the heading to open. Add / Share stay visible.

**Family care team**

- A **role board** shows every family role at a glance — who holds it and since when. Unfilled roles are flagged *Unassigned*.
- Preset roles: Point person, Bedside rotation, Medical liaison, Records keeper, Family communicator, Admin & insurance, Logistics, Emotional support. Custom roles can be added per person (e.g. *Pharmacy runs*).
- Tap a role tile to reassign it (tick who covers it). Tap a person's card to edit their two-letter icon, photo, Doctor / Nurse icon, or a Simpsons / Star Wars emoji, plus relationship, contact (phone, WhatsApp, email, best way to reach them), availability, notes, and roles.
- **Share with staff** builds a dated family contact list (as of today, or any date you pick) to copy or send to the hospital or clinic team.
- **From the family chat**: people who posted in the imported WhatsApp group but aren't on the team yet appear as one-tap suggestions.
- Every role change is recorded on the timeline (*Family role* entries: "Aron — Point person", "Hadas no longer Point person") with who made the change, and the **Family update** starts with the current roles.

**Professional care team directory**

- **+ Add person**: name, role, and where they work — one or several places (e.g. *Hospital — inpatient, Cancer centre, Home care*).
- Each person can have a photo, custom two-letter icon, Doctor or Nurse icon, or a Simpsons / Star Wars / people emoji on their card.
- Contact info (all optional): phone, WhatsApp, email, other (pager, portal, assistant, nurse station…), **preferred way to reach them**, and notes (rounds time, best via the resident, clinic day…). The card shows the preferred method first; phone, WhatsApp, and email are tap-to-call / chat / email links.
- **Share with family** builds a dated professional contact list to copy or send to the family.
- Logging a visit with a new name adds that person automatically; their role fills in from the directory next time.
- **Edit** on a card changes name, role or places. Renaming updates that person's past timeline entries. **Remove from team** keeps past entries as they are.
- When more than one place is recorded, a place filter appears above the cards.

**Visit transcripts**

- **+ Add**: one form for a visit, test, or note. Files and a next action are optional.
- Each file is saved **word for word** to the active case's Library (timing cues removed, speaker labels kept) and linked to one *Visit transcript* entry on the timeline. PDFs keep the original file; a scan with no extractable text is still stored so it can be opened from the Library.
- Notes are summarized into a few factual bullets on the timeline. *Key points for the timeline* is optional — leave it blank to auto-summarize, or write your own.
- Requires the patient (and a case) to be active, because the files are stored in that case's Library.

**Using it**

- **Care team** cards: everyone in the directory or recorded on a visit — role, places, when they were last in, number of contacts. Tap one to filter the timeline to that person; dashed cards are people recorded on visits but not yet added to the team.
- Filters: **Last 5** (default) / Today / 3 / 7 / 30 days / All; Care team, Tests & results, Moves & procedures, Meds, Family updates, Plan, Documents; free-text search. Last 5 still respects those type filters — switch the range to see more.
- Short chat replies ("thanks", "ok") are imported but hidden by default — **Show chat replies** reveals them.
- **Who has the ball** stays at the top of Timeline: who is in charge **right now**, and who to call for clarity. One person or a few. Optional **Call first** when several people share it. Record **what we expect them to do** and **what success looks like**. From / to take a date and a time. Title and department fill from the selected Family or care-team card. Expired to-times drop off the list. Empty is “nobody is in charge.” A **Next check-in** block records who checks in, what they will cover, and the date/time. The section uses a sports-ball visual — **basketball by default** — and Update lets you switch to soccer, football, baseball, tennis, volleyball, rugby, or softball.
- Staff visits can add an optional next action with a date and time. The timeline opens on the **last 5** items.
- **Readings** (temperature, heart rate, blood pressure, oxygen, blood sugar) appear on Log and as a **Vitals check** on Timeline, not mixed into lab results.
- Action items take a date and an optional time (Timeline row, visit close-out, and Advanced Plan).
- **Alert** messages someone about their open tasks using their preferred contact (WhatsApp, text, or email) from the Family or care team card.
- **Classify** a WhatsApp message to turn it into a structured fact (e.g. *Result shared*, practitioner, role, or **vitals check**) while keeping the original text and sender.
- **Family update**: builds a plain-text digest (Today / 24h / 3 days / 7 days) from timeline entries only — Copy or Share into the family chat. Questions and older chat messages are left out of the digest.
- **Timeline** (the day-by-day list) is its own section under the care team. It opens on the last 5 items; use the range and type filters to see more.
- **Import WhatsApp**: tap the button to jump to the import panel. Chat name defaults to the last named chat and can be changed. On a phone, group → name → *Export Chat → Without Media*; upload the `.txt` or `.zip`. A full re-export is fine — messages already on the timeline are skipped (same sender, minute, and wording), and only new ones are added. The import panel hides when it finishes.
- **Dismiss** hides any timeline row (visits, WhatsApp, labs, meds, plan, documents) without deleting the record. **Show dismissed** brings them back so you can restore one.
- WhatsApp cannot be read live. On this Mac, while Bright Health is running, a new *Export Chat* file saved to Downloads is imported in the background. Manual Import is unchanged.

Timeline entries are patient-wide (not case-scoped) and stored in the patient profile (`care_events`). Add, edit, delete and imports are written to the audit trail (Settings → Audit → *timeline*).

---

## 12b. Questions for the doctor

A family list of what to ask at the next visit, stored on the patient (not a case).

- Add a question, pick **which doctor** from the professional care team (or type a name), and an optional specialty / context note
- Filter: To ask · Asked · Answered · All
- Grouped by doctor, with **Copy for visit** and a page-level **Copy open questions**
- Mark asked after the appointment; add what they said and mark answered
- Visible in Simple view alongside Timeline and Labs

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

- In-app **How Bright Health works** (header): Library → context → Run; open items; chat; tasks; Plan calendar; citations; settings
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

## What Bright Health is not

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
