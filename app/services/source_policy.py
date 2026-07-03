SOURCE_ATTRIBUTION_RULES = """
SOURCE ATTRIBUTION (mandatory — every clinical claim must be tagged):
- [SOURCE: Document "<exact document title>"] — fact directly supported by stored document text (includes URL-ingested pages — the link is shown automatically)
- [SOURCE: Web — https://example.org/page] — external web page where you found the fact (ClinicalTrials.gov, NCI, journal, guideline site). Use the full https URL.
- [SOURCE: Web — NCT01234567] — clinical trial by NCT ID (links to ClinicalTrials.gov automatically)
- [SOURCE: Patient context] — from configured patient context only (not verified clinical record)
- [SOURCE: AI inference — not verified] — your interpretation; NOT hard data
- [SOURCE: Unknown] — gap not supported by stored documents; do NOT present as established fact

FORMAT RULES (non-negotiable):
- Do NOT use markdown bold (**text**) or other markdown emphasis — write plain prose only.
- Do NOT use informal citations like "(CT Report)" or "per the CT" — ONLY the [SOURCE: …] format above.
- Every bullet in sections 1–3 and every staging statement MUST end with a [SOURCE: …] tag.
- Use the EXACT document titles listed in STORED DOCUMENTS (copy title from the ### header).
- If you cannot tie a claim to a document, use [SOURCE: Unknown] — never state it as fact.

EXAMPLE (copy this pattern exactly):
- Pancreatic mass in uncinate process (17×25×20 mm) [SOURCE: Document "CT Abdomen Report — Jun 26 2026"]
- Patient is 74 years old [SOURCE: Patient context]
- ECOG performance status [SOURCE: Unknown]
- Likely Stage IV if liver lesions are confirmed metastases [SOURCE: AI inference — not verified]

STAGING RULES (critical):
- TNM stage, resectability, metastasis status, and "Stage IV" labels require [SOURCE: Document "..."] with quoted or paraphrased evidence.
- Never state staging from general medical knowledge alone.
- If imaging suggests but does not confirm metastasis, say "suspicious for" with document source, or [SOURCE: Unknown].
- In staging sections, use two subsections: "Documented in stored records" vs "Not yet established / needs verification".
"""

RESPONSE_STRUCTURE_WITH_SOURCES = """
Structure your response with these sections:
1. Executive summary — at least 4 complete sentences covering diagnosis, key findings, staging status, and immediate priorities. Every sentence must include clinical content AND a [SOURCE: …] tag. Never output only a source tag line.
2. What we know — hard data only, each bullet tagged [SOURCE: Document "..."]
3. What we do not know / uncertainties — tag [SOURCE: Unknown]
4. Critical gaps to close (prioritized numbered list)
5. Staging & workup — ONLY documented findings first; separate "unconfirmed/suggested" items clearly
6. Treatment options (broad range; tag inference vs guideline vs document)
7. Next steps and open items (prioritized numbered list)
8. Questions for the oncology team (numbered list)
9. Disclaimer

Do NOT include a separate "Source key" section — references are compiled automatically in the report appendix.
"""

BASELINE_GAP_RULES = """
OPEN ITEMS / GAPS (baseline assessment only):
- Before listing something as a gap or open item, search ALL provided document text and titles.
- Do NOT flag as missing any finding, report, test, or date that appears in any stored document above, even partially or in an appendix.
- Only list gaps for information genuinely absent from the STORED DOCUMENTS section.
- If a clinical report exists in the library (e.g. CT, MRI, pathology), do not claim that report is missing — cite it or state what it does not contain.
"""

BASELINE_GUIDANCE_SECTION = """
=== ASSESSMENT GUIDANCE (user instructions — follow carefully) ===
{guidance}

Apply this guidance when reading STORED DOCUMENTS and writing the assessment.
Prioritize sources the user mentions by title, author, facility, or type (e.g. video, PDF, pathology) when they appear in the library above.
"""

CUSTOM_QUERY_RESPONSE_STRUCTURE = """
This is a CUSTOM TASK — answer ONLY the user's question below.
Do NOT repeat a full baseline case assessment, executive case overview, or restate the entire chart unless it directly supports your answer.

Structure your response with these sections:
### Executive summary
2–4 sentences that directly answer the user's question. Every sentence must have a [SOURCE: …] tag.

### Direct answer
The main response to the user query. Use ### subheadings if helpful.
Lead with the requested deliverable (list, comparison, plan, etc.) — not a case recap.

### Case-specific notes (brief)
Only document-backed or context-backed factors that change your answer for THIS patient.
Skip this section if not relevant.

### Limitations & verification
What is missing from stored documents; what the user should verify externally.
Tag gaps with [SOURCE: Unknown].

### Disclaimer

Do NOT include baseline-only sections such as "What we know", "Staging & workup", or "Questions for the oncology team" unless the user explicitly asked for them.
"""

LIST_ITEM_SOURCE_RULES = """
LIST & TABLE SOURCE RULES (mandatory for numbered lists of trials, drugs, regimens, tests, or options):

Each numbered list item MUST include exactly ONE "- Sources:" sub-bullet at the end of that item (not on every sub-line):

- Sources: [SOURCE: Document "<exact title>"] — when the item or trial is named in stored documents (copy title from ### headers).
- Sources: Not in stored records — verify on ClinicalTrials.gov or with the treating team. Suggested search: "<terms>" [SOURCE: Unknown]
  Use this when the item comes from general oncology knowledge and there is NO matching document or URL in the library.
- Sources: [SOURCE: Web — https://clinicaltrials.gov/study/NCT…] when you cite a specific trial page or external guideline URL.
- Sources: [SOURCE: Patient context] — only when the list item is purely about a patient fact from settings (e.g. a biomarker), not for therapies/trials.

Do NOT put [SOURCE: AI inference — not verified] on every mechanism or relevance sub-bullet. Put source attribution once on the "- Sources:" line.
Sub-bullets (mechanism, relevance, verification) may omit SOURCE tags unless they cite a specific document or patient fact.

If a trial name, NCT ID, or URL appears in stored documents, you MUST cite that document — do not use AI inference for it.
"""

TRIAL_SEARCH_QUERY_INSTRUCTIONS = """
The user is asking for CLINICAL TRIALS and/or THERAPEUTIC OPTIONS — not a case summary.

In ### Direct answer, provide a numbered list with AT LEAST 10 distinct items when possible.
Each item MUST use this format:

1. Trial or therapy name — phase/approval status — setting (e.g. metastatic PDAC, 1L, maintenance)
   - Regimen / mechanism (short)
   - Why it may fit THIS patient (biomarkers, prior therapy, age — cite document or context only when factual)
   - Key eligibility or exclusion considerations for this patient
   - Sources: (required — pick ONE pattern below)
     • [SOURCE: Document "<exact title>"] if mentioned in stored records
     • Not in stored records — verify on ClinicalTrials.gov. Suggested search: "<disease, biomarker, setting>" [SOURCE: Unknown]

Rules:
- List document-backed trials/regimens FIRST with Document sources.
- For items from general knowledge: say explicitly "Not in stored records" on the Sources line — do NOT imply they came from the chart.
- Include NCT IDs or URLs ONLY if they appear in stored documents; otherwise say they must be looked up.
- Include both clinical trials (where applicable) AND standard approved systemic options if relevant.
- Do NOT respond with only a disease overview or duplicate the baseline assessment.
- In ### Limitations & verification, give ClinicalTrials.gov search terms tailored to this patient.
"""

INVESTIGATION_PROMPT_TEMPLATE = """
You are investigating ONE open item from an oncology case review.

OPEN ITEM:
{item}

ITEM TYPE: {item_type}

=== STORED DOCUMENTS ===
{corpus_text}

=== PATIENT CONTEXT ===
{patient_context}

=== USER GUIDANCE ===
{guidance}

Provide a focused investigation (separate from any prior full assessment) with these sections:
1. What the documents actually say (quote or paraphrase with [SOURCE: Document "<title>"])
2. What is NOT in the documents — tag [SOURCE: Unknown]
3. Recommended next steps to resolve this item (tag actions that need new data)
4. Staging impact (if relevant) — ONLY cite hard documented data for any staging statements

Do not repeat a full case assessment. Do not include a separate source key section. Stay focused on this open item only.
Do not mention palliative care, hospice, or comfort care.
"""
