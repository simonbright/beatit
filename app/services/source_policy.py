SOURCE_ATTRIBUTION_RULES = """
SOURCE ATTRIBUTION (mandatory — every clinical claim must be tagged):
- [SOURCE: Document "<exact document title>"] — fact directly supported by stored document text
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
1. Executive summary (every sentence tagged with a SOURCE)
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

TRIAL_SEARCH_QUERY_INSTRUCTIONS = """
The user is asking for CLINICAL TRIALS and/or THERAPEUTIC OPTIONS — not a case summary.

In ### Direct answer, provide a numbered list with AT LEAST 10 distinct items when possible.
Each item MUST use this format:

1. Trial or therapy name — phase/approval status — setting (e.g. metastatic PDAC, 1L, maintenance)
   - Regimen / mechanism (short)
   - Why it may fit THIS patient (age, metastatic disease, biomarkers, prior therapy from documents/context) [SOURCE: …]
   - Key eligibility or exclusion considerations for this patient [SOURCE: …]
   - Verification note [SOURCE: AI inference — not verified] if not from stored documents

Rules:
- If stored documents mention specific trials, regimens, or referrals, list those FIRST with [SOURCE: Document "..."].
- For trials/therapies from general oncology knowledge, tag [SOURCE: AI inference — not verified] and state that NCT numbers and availability must be verified on ClinicalTrials.gov or with the treating team.
- Include both clinical trials (where applicable) AND standard approved systemic options if relevant to the question.
- Do NOT respond with only a pancreatic cancer overview or duplicate the baseline assessment.
- In ### Limitations & verification, give ClinicalTrials.gov search terms tailored to this patient (e.g. metastatic pancreatic, liver metastasis, biomarker if known).
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
