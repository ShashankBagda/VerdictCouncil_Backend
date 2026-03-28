# Part 1: User Stories

---

## 1.1 Case Intake & Setup

### US-001: Upload New Case

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to upload case documents for AI processing, so that VerdictCouncil can analyse the case and provide decision-support recommendations.

**Acceptance Criteria:**
- System accepts PDF, JPEG, PNG, and plain text file uploads
- Files are stored via the OpenAI Files API and associated with a unique case ID
- SCT cases require a claim amount field before submission is accepted
- Traffic cases require a valid offence code before submission is accepted
- System validates file integrity (non-corrupt, readable, within size limits) before queuing
- Upload initiates the 9-agent pipeline automatically upon successful validation
- Judge receives confirmation with case ID, file count, and estimated processing time

**Happy Flow:**
1. Judge selects "New Case" and chooses the domain (SCT or Traffic).
2. Judge fills in required metadata — party names, filing date, and domain-specific fields (claim amount for SCT; offence code for Traffic).
3. Judge selects one or more documents from their local machine and attaches them to the case.
4. System validates file types, sizes, and readability, displaying any rejected files with reasons.
5. System validates domain-specific fields (claim amount within SCT jurisdiction range; offence code against known code list).
6. Judge reviews the upload summary — file list, metadata, domain — and confirms submission.
7. System stores files via the OpenAI Files API, creates the case record, and enqueues the case into the Agent 1 (Case Processing) stage.
8. System displays the new case ID and redirects the judge to the processing status view.

**Domain Notes:**
- SCT: Claim amount is mandatory; system pre-validates that the amount falls within the $20,000 limit (or $30,000 if judge indicates both parties have filed consent).
- Traffic: Offence code is mandatory; system validates against a maintained list of traffic offence codes and rejects unknown codes with a prompt to correct.

---

### US-002: View Document Processing Status

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to monitor real-time pipeline progress across the 9 agents, so that I know when analysis is complete and can identify any stalled or failed stages.

**Acceptance Criteria:**
- Status view displays all 9 pipeline stages with their current state (pending, in-progress, completed, failed)
- The currently active agent is visually distinguished from completed and pending stages
- Elapsed time is shown per stage and for the overall pipeline
- Updates are delivered in real time via SSE or polling (no manual refresh required)
- Failed stages display an error summary and offer a retry option
- Judge can navigate away and return without losing status tracking

**Happy Flow:**
1. Judge opens the case detail view for a case that is currently being processed.
2. System displays a pipeline visualisation showing all 9 agents in sequence, with completed stages marked with a checkmark and elapsed time.
3. The currently active agent is highlighted, showing a progress indicator and the agent name (e.g., "Agent 4: Fact Reconstruction — In Progress").
4. As each agent completes, the UI updates in real time — the completed agent receives a checkmark and the next agent begins highlighting.
5. If a stage encounters an error, it is marked as failed with a brief error summary; the judge can click to view details or trigger a retry.
6. Upon full pipeline completion, the status view transitions to show "Analysis Complete" with total elapsed time and a link to the results dashboard.
7. Judge clicks through to begin reviewing the analysis outputs.

---

### US-003: Receive Jurisdiction Validation Result

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to see whether a case passes jurisdiction checks, so that I can confirm the tribunal has authority to hear the matter before investing time in full analysis.

**Acceptance Criteria:**
- Jurisdiction validation runs automatically as part of Agent 1 (Case Processing)
- SCT validation checks: claim amount <= $20,000 (or <= $30,000 with consent), claim filed within 2 years of cause of action
- Traffic validation checks: offence code is valid and recognised, offence is not time-barred under the applicable limitation period
- Result is displayed as pass, fail, or warning (borderline cases)
- Failed checks include a specific reason citing the relevant statutory threshold or limitation
- Borderline cases (e.g., claim amount exactly at threshold) are flagged for judge review rather than auto-rejected

**Happy Flow:**
1. System completes document ingestion and extracts case metadata during Agent 1 processing.
2. System evaluates jurisdiction criteria against the extracted metadata and domain rules.
3. Jurisdiction result is recorded against the case record with a pass, fail, or warning status.
4. Judge opens the case and sees the jurisdiction status prominently displayed at the top of the case overview.
5. For a passing case, the status shows "Jurisdiction Confirmed" with a summary of the checked criteria (e.g., "Claim amount: $8,500 — within $20,000 limit; Filed: 14 months from cause of action — within 2-year limit").
6. Judge proceeds to review the remaining pipeline outputs with confidence that jurisdiction is established.

**Domain Notes:**
- SCT: Validates claim amount against the $20,000 cap (or $30,000 with filed consent from both parties) and checks the 2-year limitation period from the date the cause of action arose.
- Traffic: Validates the offence code against the maintained statutory list and checks that the charge is not time-barred under the applicable limitation period for the offence category.

---

### US-004: Handle Rejected Cases

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to view rejection reasons and optionally override them with justification, so that I retain final authority over case acceptance while understanding the AI's reasoning.

**Acceptance Criteria:**
- Rejected cases are clearly marked with a "Rejected" status and are accessible from the case list
- Each rejection includes a specific, cited reason (e.g., "Claim amount $25,000 exceeds $20,000 SCT limit without filed consent")
- Judge can override the rejection by providing a written justification
- Override action is logged in the audit trail with the judge's justification, timestamp, and user ID
- Judge can alternatively close the case, which archives it with the rejection reason preserved
- Overridden cases resume pipeline processing from the point of rejection
- Closed cases remain searchable and viewable but cannot be reopened without creating a new case

**Happy Flow:**
1. Judge opens the case list and filters for cases with "Rejected" status.
2. Judge selects a rejected case and views the rejection detail panel, which displays the specific reason (e.g., "Offence date 15 March 2023 exceeds the 12-month limitation period for this offence category").
3. Judge reviews the cited statutory basis and the extracted case data that triggered the rejection.
4. Judge determines that the rejection is incorrect (e.g., the extracted date was wrong) and clicks "Override Rejection".
5. System presents a justification form; judge enters the reason for override (e.g., "Offence date incorrectly extracted — actual date is 15 March 2025 per charge sheet paragraph 2").
6. System logs the override in the audit trail, updates the case status to "Processing", and resumes pipeline execution.
7. Judge is redirected to the processing status view to monitor the resumed analysis.

---

### US-005: Re-upload or Add Documents to Existing Case

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to add supplementary documents to an existing case after initial upload, so that late-arriving evidence or corrected filings are incorporated into the analysis.

**Acceptance Criteria:**
- Additional documents can be uploaded to any case that is not in "Closed" status
- New documents are stored via the OpenAI Files API and appended to the existing case file set
- System identifies which pipeline stages are affected by the new material and re-triggers only those stages
- Prior analysis from unaffected stages is preserved, not discarded
- Judge can see which stages were re-triggered and which retained their prior results
- A document version history is maintained, showing upload timestamps and file names for all documents in the case
- Re-processing status is displayed using the same pipeline view as initial processing

**Happy Flow:**
1. Judge opens an existing case that has completed or is in-progress and selects "Add Documents".
2. Judge selects one or more supplementary files from their local machine (e.g., a late-filed witness statement).
3. System validates the new files for type, size, and readability.
4. Judge confirms the addition, optionally noting the reason (e.g., "Additional witness statement filed by respondent on 26 March 2026").
5. System stores the new files via the OpenAI Files API and appends them to the case record.
6. System analyses the new material to determine affected pipeline stages (e.g., new witness statement affects Evidence Analysis, Fact Reconstruction, and Witness Analysis) and marks those stages for re-processing.
7. Re-triggered stages execute while unaffected stages retain their prior outputs; the pipeline status view shows which stages are re-running.
8. Upon completion, the judge sees updated analysis that incorporates the new documents alongside the preserved prior analysis.

---

## 1.2 Evidence & Facts

### US-006: Review Evidence Analysis Dashboard

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review a dashboard showing per-item evidence strength, admissibility flags, contradictions, gaps, and corroborations, so that I can quickly assess the evidential landscape of the case.

**Acceptance Criteria:**
- Dashboard lists every piece of evidence identified by Agent 3 (Evidence Analysis)
- Each evidence item displays a strength rating (e.g., strong, moderate, weak) with a brief rationale
- Admissibility flags are shown where relevant (e.g., hearsay, best evidence rule, relevance concerns)
- Contradictions between evidence items are highlighted with links to both conflicting items
- Corroborations between evidence items are linked, showing supporting relationships
- Evidence gaps are surfaced with references to the legal elements they relate to
- Judge can sort and filter evidence by type, strength, or flag status

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Evidence" tab.
2. System displays the evidence dashboard with a summary bar showing counts — e.g., "12 items: 5 strong, 4 moderate, 2 weak, 1 inadmissible".
3. Judge scans the evidence list, each row showing the item description, source document, strength rating, and any flags.
4. Judge notices a contradiction flag on two items and clicks to expand the contradiction detail, which shows both items side-by-side with the nature of the conflict explained.
5. Judge clicks on an admissibility flag to review the AI's reasoning for why a particular item may face an admissibility challenge (e.g., "Photograph lacks metadata — authenticity may be challenged under s 35 Evidence Act").
6. Judge reviews the corroboration map, which visually links evidence items that support each other.
7. Judge filters by "weak" evidence to focus preparation on items that may require additional scrutiny during the hearing.

---

### US-007: View Fact Timeline

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to view a chronological timeline of extracted facts with source citations and confidence ratings, so that I can understand the sequence of events and identify areas of factual dispute.

**Acceptance Criteria:**
- Timeline displays facts in chronological order as extracted by Agent 4 (Fact Reconstruction)
- Each fact shows the date/time (or estimated range), a description, and a source citation linking to the originating document
- Confidence rating is displayed per fact (high, medium, low) with a brief basis
- Facts are categorised as agreed (both parties), disputed, or unilateral (one party only)
- Disputed facts are visually distinguished and link to the dispute detail view (US-009)
- Timeline supports zoom and scroll for cases with many events
- Judge can click any fact to view the underlying source document excerpt

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Facts" tab.
2. System renders a chronological timeline spanning the relevant period of the dispute.
3. Each node on the timeline represents an extracted fact — e.g., "12 Jan 2026: Claimant delivered goods to Respondent's premises (Source: Invoice #1042, para 3; Delivery receipt signed by R)".
4. Agreed facts are displayed in a neutral colour; disputed facts are highlighted in amber with an indicator showing both parties' versions exist.
5. Judge hovers over a fact node to see the confidence rating and its basis (e.g., "High — corroborated by invoice and signed delivery receipt").
6. Judge clicks on a disputed fact to expand it, revealing both parties' versions side-by-side with their respective evidence citations.
7. Judge scrolls through the full timeline, building a chronological understanding of the case narrative before the hearing.

---

### US-008: Drill Down to Source Document

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to click any cited reference and see the original document excerpt highlighted alongside the AI extraction, so that I can verify the AI's interpretation against the source material.

**Acceptance Criteria:**
- Every AI-generated citation, reference, or extracted fact includes a clickable link to its source
- Clicking a reference opens a split view: AI extraction on one side, original document excerpt on the other
- The relevant passage in the original document is highlighted or bordered for quick identification
- For image-based documents (scanned PDFs, photos), the relevant region is indicated
- Judge can navigate to the full document from the excerpt view
- Source view includes document metadata (filename, upload date, page number)

**Happy Flow:**
1. Judge is reviewing the evidence dashboard or fact timeline and sees a citation — e.g., "(Source: Claimant's Statement of Claim, para 7)".
2. Judge clicks the citation link.
3. System opens a split-pane view: the left pane shows the AI's extracted fact or analysis; the right pane shows the original document scrolled to the relevant passage.
4. The cited passage in the original document is highlighted in yellow, making it immediately identifiable.
5. Judge reads the original text and compares it to the AI's extraction to verify accuracy.
6. Judge notices the AI's extraction is slightly incomplete and makes a mental note to probe this area during the hearing.
7. Judge clicks "View Full Document" to see the complete source document if further context is needed, then returns to the analysis view.

---

### US-009: Flag Disputed Facts

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to see all facts where parties disagree, with both versions presented side-by-side and linked evidence, so that I can identify the core disputes requiring resolution at the hearing.

**Acceptance Criteria:**
- System identifies disputed facts automatically based on contradictory claims in party submissions
- All disputed facts are collected in a dedicated "Disputes" view, accessible from the case analysis
- Each dispute shows both parties' versions side-by-side with their respective evidence citations
- Evidence supporting each version is linked and accessible via drill-down (US-008)
- Disputes are ranked by impact — how materially the disputed fact affects the legal outcome
- Judge can annotate disputes with personal notes for hearing preparation
- The number of disputed facts is visible in the case summary

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Disputed Facts" section.
2. System displays a list of all disputed facts, ranked by impact on the legal outcome (e.g., "High Impact: Whether defective goods were delivered" ranked above "Low Impact: Exact time of delivery").
3. Judge selects a high-impact dispute to expand.
4. System shows a side-by-side comparison — Claimant's version: "Goods delivered were defective and not as described" (Source: Statement of Claim, para 5) vs Respondent's version: "Goods matched the agreed specification" (Source: Defence, para 3).
5. Judge clicks on the Claimant's evidence citation to drill down to the source document and verify the claim.
6. Judge returns and reviews the linked evidence for the Respondent's version.
7. Judge adds a personal annotation: "Ask Respondent to produce the agreed specification document at hearing."
8. Judge moves to the next disputed fact and repeats the review process.

---

### US-010: Review Evidence Gaps

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to see what evidence is expected but missing, linked to legal requirements, with an impact assessment, so that I can understand potential weaknesses in each party's case and direct enquiries appropriately.

**Acceptance Criteria:**
- Agent 3 (Evidence Analysis) identifies evidence gaps based on the legal elements required to establish or defend the claim
- Each gap specifies what evidence is missing, which legal element it relates to, and which party bears the burden of proof
- Impact assessment rates each gap (critical, significant, minor) based on its effect on the legal outcome
- Gaps are linked to the relevant statutory provisions or legal tests
- Judge can mark gaps as "addressed" if the evidence is provided later (e.g., via US-005 re-upload)
- Gaps are reflected in the evidence dashboard (US-006) summary

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Evidence Gaps" section.
2. System displays identified gaps, grouped by the party bearing the burden of proof — e.g., "Claimant Gaps: 2 critical, 1 minor; Respondent Gaps: 1 significant".
3. Judge expands a critical gap: "Missing: Independent expert report on goods quality — Required to establish defect under s 13 Sale of Goods Act — Impact: Critical — without this, Claimant's defect claim rests solely on their own assertion."
4. Judge reviews the linked statutory provision to confirm the legal basis for the gap identification.
5. Judge notes this gap for potential direction at the hearing.
6. Judge checks a minor gap: "Missing: Exact delivery time log — Nice to have for timeline precision but not legally determinative — Impact: Minor."
7. Judge moves to the Respondent's gaps and reviews similarly, building a comprehensive picture of evidentiary completeness.

---

## 1.3 Witness Analysis

### US-011: Review Witness Profiles and Credibility Scores

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review each witness's identification, role, bias indicators, and credibility score with a breakdown, so that I can assess witness reliability and prepare targeted questioning.

**Acceptance Criteria:**
- Agent 5 (Witness Analysis) generates a profile for each identified witness
- Profile includes: full name, role in the matter (e.g., claimant, respondent, independent witness, expert), relationship to parties
- Bias indicators are listed with explanations (e.g., financial interest, familial relationship, employment relationship)
- Credibility score is a numerical value from 0 to 100 with a breakdown by factor (consistency, corroboration, bias, specificity)
- Score breakdown shows how each factor contributed to the overall score
- Judge can view the evidence basis for each credibility factor
- Credibility scores are clearly labelled as AI-generated assessments, not determinative findings

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Witnesses" tab.
2. System displays a list of all identified witnesses with summary cards showing name, role, and overall credibility score.
3. Judge selects a witness — e.g., "Tan Wei Ming — Independent Witness — Credibility: 72/100".
4. System expands the witness profile showing: role description ("Neighbour who witnessed the delivery"), bias indicators ("No identified financial interest; minor social relationship with Claimant"), and the credibility score breakdown.
5. Judge reviews the breakdown: Consistency: 80 (statements are internally consistent), Corroboration: 65 (partially corroborated by delivery receipt timing), Bias: 75 (minor social connection noted), Specificity: 68 (provides general descriptions but lacks precise detail on goods condition).
6. Judge clicks on the "Consistency" factor to see the underlying evidence — the specific statements that were compared and assessed for consistency.
7. Judge uses this profile to prepare targeted questions for the hearing, noting areas where credibility could be tested.

---

### US-012: View Anticipated Testimony (Traffic Only)

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to view simulated testimony summaries based on written statements for traffic cases, so that I can prepare for the hearing by anticipating the likely evidence to be given.

**Acceptance Criteria:**
- Feature is available only for Traffic domain cases
- Simulated testimony is generated by Agent 5 (Witness Analysis) based on written statements filed by prosecution and defence
- Each simulated testimony summary includes key points the witness is likely to cover, potential vulnerabilities, and areas of uncertainty
- All simulated testimony is prominently marked "Simulated — For Judicial Preparation Only"
- Simulated testimony is clearly distinguished from actual filed statements
- Judge can toggle between the simulated summary and the original written statement
- System indicates confidence level for each simulated point

**Happy Flow:**
1. Judge opens a Traffic case and navigates to the "Witnesses" tab.
2. System displays witness profiles with an additional "Anticipated Testimony" section available for each witness who has filed a written statement.
3. Judge selects the prosecution's key witness and clicks "View Anticipated Testimony".
4. System displays the simulated testimony summary, headed with a prominent banner: "Simulated — For Judicial Preparation Only. This is an AI-generated anticipation based on filed written statements and is not actual testimony."
5. Judge reads the key anticipated points — e.g., "Witness is likely to testify that the accused's vehicle ran the red light at the junction of Orchard Road and Scotts Road at approximately 14:30 on 10 Feb 2026 (High confidence — consistent with written statement para 3 and traffic camera timestamp)."
6. Judge notes a vulnerability flagged by the AI: "Witness's stated position may not have provided clear line of sight to the traffic signal — potential area for cross-examination."
7. Judge toggles to view the original written statement side-by-side to verify the basis for the simulation.
8. Judge uses the anticipated testimony to prepare hearing questions.

**Domain Notes:**
- Traffic: This feature is exclusive to Traffic cases where written witness statements are filed in advance. It helps judges prepare for trials where witnesses will give oral evidence.
- SCT: Not applicable. SCT proceedings are typically based on documents and oral submissions rather than witness testimony.

---

### US-013: Review Suggested Judicial Questions

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review AI-generated probing questions tagged by type and linked to case weaknesses, so that I can conduct a thorough and focused hearing.

**Acceptance Criteria:**
- Agent 5 (Witness Analysis) generates suggested questions for each witness and for general case issues
- Each question is tagged by type: factual_clarification, evidence_gap, credibility_probe, or legal_interpretation
- Each question is linked to the specific weakness, gap, or issue it addresses
- Judge can edit question text, add new questions, delete questions, and reorder the list
- Questions can be exported or saved as part of the hearing pack (US-020)
- Judge's edits are preserved and do not trigger pipeline re-processing
- Suggested questions are marked as AI-generated suggestions, not mandatory lines of enquiry

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Suggested Questions" section.
2. System displays questions grouped by witness, each tagged with its type (e.g., "[evidence_gap] Ask Claimant: Can you provide documentation of the alleged defect, such as photographs taken at the time of delivery?").
3. Judge reviews the linked weakness for a question — clicking the link shows "This question addresses Evidence Gap: No contemporaneous photographic evidence of alleged defect (Impact: Critical)."
4. Judge decides to rephrase a question for clarity and edits the text directly in the interface.
5. Judge adds a custom question that occurred to them during review: "What was the agreed delivery timeline as per the original quotation?"
6. Judge deletes a question they consider irrelevant or inappropriate for judicial enquiry.
7. Judge reorders the questions to match their preferred hearing flow — starting with factual_clarification, then evidence_gap, then credibility_probe.
8. Judge saves the updated question list, which is now available for inclusion in the hearing pack.

---

## 1.4 Legal Research

### US-014: Review Applicable Statutes

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review matched statutory provisions with verbatim text, relevance scores, and application to case facts, so that I can confirm the legal framework applicable to the case.

**Acceptance Criteria:**
- Agent 6 (Legal Knowledge) identifies applicable statutory provisions from the relevant vector store
- SCT cases pull from the vs_sct vector store; Traffic cases pull from the vs_traffic vector store
- Each provision displays: Act name, section number, verbatim text of the relevant subsection, and a relevance score
- Application narrative explains how the provision applies to the specific facts of the case
- Provisions are ranked by relevance score (highest first)
- Judge can expand or collapse individual provisions
- Judge can flag a provision as "not applicable" with a note, for their own reference

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Legal Framework" tab.
2. System displays a list of matched statutory provisions, ranked by relevance — e.g., "s 13(1) Sale of Goods Act (Cap 393) — Relevance: 95%".
3. Judge expands the top provision to see the verbatim text: "Where the seller sells goods in the course of a business, there is an implied condition that the goods supplied under the contract are of satisfactory quality."
4. Judge reads the application narrative: "Applicable because Respondent sold goods in the course of business to Claimant. Claimant alleges goods were not of satisfactory quality. This provision establishes the implied condition that is the basis of the claim."
5. Judge reviews the second-ranked provision — e.g., "s 35 Sale of Goods Act — Acceptance of goods — Relevance: 78%" — and notes its relevance to the Respondent's potential defence.
6. Judge flags a lower-ranked provision as "not applicable" with a note: "This provision relates to international sales and is not relevant to a domestic transaction."
7. Judge is satisfied with the legal framework identified and moves to review precedent cases.

**Domain Notes:**
- SCT: Statutes are sourced from the vs_sct vector store, which contains consumer protection, sale of goods, supply of services, and related legislation.
- Traffic: Statutes are sourced from the vs_traffic vector store, which contains the Road Traffic Act, Motor Vehicles (Third-Party Risks and Compensation) Act, and related subsidiary legislation.

---

### US-015: Review Precedent Cases

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review similar past cases with citations, outcomes, reasoning, similarity scores, and distinguishing factors, so that I can ensure consistency with established jurisprudence.

**Acceptance Criteria:**
- Agent 6 (Legal Knowledge) retrieves precedent cases from both the curated vector store and live judiciary search results
- Each precedent displays: case citation, court, date, outcome, key reasoning, and similarity score
- Distinguishing factors are listed — how the precedent differs from the current case
- Precedents supporting both sides of the dispute are presented (not only those favouring one outcome)
- Source is tagged as "curated" (from vector store) or "live_search" (from judiciary search)
- Judge can sort by similarity score, date, or court level
- Judge can mark precedents as "relevant" or "distinguished" for their own reference

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Precedents" tab.
2. System displays a list of precedent cases, showing both those favouring the claimant/prosecution and those favouring the respondent/defence.
3. Judge selects the top precedent: "Lim Ah Kow v Tan Bee Hoon [2024] SGSCT 42 — Similarity: 88% — Source: curated".
4. System expands to show the outcome ("Claim allowed — defective goods, damages awarded at $4,200"), key reasoning ("Court found seller had breached implied condition of satisfactory quality; buyer had not accepted goods within meaning of s 35"), and distinguishing factors ("In the precedent, defect was visible on delivery; in current case, defect is alleged to have manifested after 2 weeks of use").
5. Judge reviews a counter-precedent favouring the respondent: "Wong Mei Ling v Koh Trading Pte Ltd [2023] SGSCT 28 — Similarity: 71% — Outcome: Claim dismissed — buyer deemed to have accepted goods."
6. Judge marks the first precedent as "relevant" and the second as "distinguished" based on the factual differences.
7. Judge notes the distinguishing factors for both precedents to inform their hearing preparation.

---

### US-016: Search Live Precedent Database

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to trigger a live search of the PAIR Search API (search.pair.gov.sg), so that I can access binding higher court case law from eLitigation beyond the curated vector store.

**Acceptance Criteria:**
- Judge can initiate a live precedent search from the case analysis view
- Search queries the PAIR Search API using the search_precedents tool on Agent 6
- Results are tagged "live_search" to distinguish them from "curated" vector store results
- Live results include: case citation, court, date, catch words, snippet, relevance score, and eLitigation URL
- Results are clearly labelled as higher court authority (SGHC, SGCA, etc.) — PAIR does not cover SCT or lower State Courts
- Results are integrated into the precedents list alongside curated results (US-015)
- Search accepts custom keywords or uses AI-generated search terms based on case facts
- System indicates when the live search was last performed and its result count

**Happy Flow:**
1. Judge is reviewing precedents (US-015) and wants to check for more recent case law not in the curated store.
2. Judge clicks "Search Live Database" from the Precedents tab.
3. System presents a search panel with AI-suggested search terms based on the case facts (e.g., "defective goods sale satisfactory quality SCT") and an option to enter custom keywords.
4. Judge accepts the suggested terms and adds a custom keyword: "latent defect".
5. System executes the search_precedents tool on Agent 6, querying the PAIR Search API (search.pair.gov.sg/api/v1/search).
6. Results are returned and displayed, each tagged "live_search" — e.g., "Ong Siew Kee v FurnitureMart Pte Ltd [2025] SGSCT 61 — live_search — Relevance: 74%", with a link to the full judgment on eLitigation.
7. Judge reviews the new results and finds a recent precedent directly on point. The result is now integrated into the precedents list alongside curated results.
8. System records the search timestamp ("Live search performed: 27 Mar 2026, 14:32 — 8 results returned") for audit purposes.

---

### US-017: View Knowledge Base Status

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to see vector store metadata and health status, so that I can have confidence in the currency and completeness of the legal knowledge underpinning the analysis.

**Acceptance Criteria:**
- Dashboard displays metadata for each vector store: document count, last updated date, and coverage description
- Health status is shown (healthy, degraded, unavailable) based on connectivity and data freshness checks
- Stale data warnings are displayed if a vector store has not been updated beyond a defined threshold
- Judge can see which vector store was used for the current case (vs_sct or vs_traffic)
- Information is accessible from the case analysis view and from a global settings/status page
- Last update timestamps are in Singapore Time (SGT)

**Happy Flow:**
1. Judge opens the system status page or clicks the "Knowledge Base" indicator in the case analysis view.
2. System displays the status of each vector store in a summary panel.
3. Judge sees: "vs_sct — 342 documents — Last updated: 15 Mar 2026 — Status: Healthy" and "vs_traffic — 218 documents — Last updated: 20 Mar 2026 — Status: Healthy".
4. Judge checks the coverage description for vs_sct: "Covers: Consumer Protection (Fair Trading) Act, Sale of Goods Act, Supply of Goods and Services Act, SCT Practice Directions, curated SCT judgments (2018–2026)."
5. Judge notes both stores are healthy and recently updated, giving confidence in the analysis.
6. If a vector store showed "Degraded" or "Stale" status, the judge would see a warning banner on the case analysis view advising that the legal knowledge may be incomplete.
7. Judge returns to the case analysis view, satisfied with the knowledge base status.

---

## 1.5 Arguments & Deliberation

### US-018: Review Both Sides' Arguments

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review the arguments for both sides with strength comparisons and weaknesses noted, so that I can approach the hearing with a balanced understanding of each party's position.

**Acceptance Criteria:**
- Agent 7 (Argument Construction) generates structured arguments for both sides
- Traffic cases present prosecution vs defence arguments with contested issues identified
- SCT cases present a balanced assessment with strength comparison percentages for each key issue
- Weaknesses in each side's arguments are explicitly noted with reasoning
- All argument analysis is marked "Internal Analysis for Judicial Review Only"
- Arguments are linked to their supporting evidence and statutory provisions
- Judge can expand each argument to see the underlying evidence chain

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Arguments" tab.
2. System displays a prominent banner: "Internal Analysis for Judicial Review Only — Not for disclosure to parties."
3. For an SCT case, the system shows a balanced assessment: "Issue 1: Were the goods of satisfactory quality? — Claimant strength: 68% — Respondent strength: 32%."
4. Judge expands the Claimant's argument: "Claimant argues goods were defective based on photographic evidence and independent inspection report. Strength: Supported by contemporaneous evidence. Weakness: Inspection report was obtained 3 weeks after delivery — Respondent may argue intervening use caused the damage."
5. Judge expands the Respondent's argument: "Respondent argues goods matched specifications and were accepted without complaint for 2 weeks. Strength: Delay in complaint weakens inference of delivery defect. Weakness: No documentation of agreed specifications produced."
6. Judge reviews the contested issues list, which identifies the factual and legal questions that divide the parties.
7. Judge clicks through to the supporting evidence for each argument point, verifying the strength assessment.

**Domain Notes:**
- SCT: Arguments are presented as a balanced assessment with strength comparison percentages, reflecting the tribunal's inquisitorial role.
- Traffic: Arguments are presented in a prosecution vs defence structure, identifying contested issues (e.g., "Contested: Whether accused had a green light"), reflecting the adversarial nature of traffic proceedings.

---

### US-019: Review Deliberation Reasoning Chain

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to follow the step-by-step reasoning from evidence to preliminary conclusion, so that I can evaluate the AI's analytical process and identify any logical weaknesses.

**Acceptance Criteria:**
- Agent 8 (Deliberation) produces a structured reasoning chain from evidence through legal analysis to preliminary conclusion
- Each reasoning step cites the source agent and the evidence or legal provision it relies on
- Steps with low confidence are visually flagged (e.g., amber or red indicator)
- Uncertainty factors that could change the outcome are explicitly listed
- The reasoning chain is navigable — judge can click on any step to see its supporting detail
- The chain clearly separates factual findings from legal analysis from conclusion
- Reasoning is presented as the AI's analysis, not as a predetermined outcome

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Deliberation" tab.
2. System displays the reasoning chain as a structured sequence of numbered steps, grouped into Factual Findings, Legal Analysis, and Preliminary Conclusion.
3. Judge reads Step 1 (Factual Finding): "The goods delivered on 12 Jan 2026 were found to have a structural defect (Source: Agent 3 — Evidence Analysis, Item E-04: Independent inspection report)." — Confidence: High.
4. Judge reads Step 4 (Legal Analysis): "The 2-week delay before complaint does not constitute acceptance under s 35 Sale of Goods Act, as the defect was latent and not reasonably discoverable on delivery (Source: Agent 6 — Legal Knowledge, Provision P-02)." — Confidence: Medium. Flagged in amber.
5. Judge clicks the amber-flagged step to see why confidence is medium: "Latent defect argument depends on whether a reasonable buyer would have inspected the goods more thoroughly. Respondent may argue a visual inspection would have revealed the issue."
6. Judge reviews the Uncertainty Factors section: "1. Whether defect was truly latent (could shift outcome if found to be patent). 2. Weight to be given to the 2-week delay in complaint."
7. Judge reaches the Preliminary Conclusion and evaluates whether the reasoning chain supports it logically, noting areas they wish to explore further at the hearing.

---

### US-020: Prepare Hearing Pack

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to access a consolidated pre-hearing summary with key facts, legal issues, suggested questions, and weak points per side, so that I can walk into the hearing fully prepared.

**Acceptance Criteria:**
- Hearing pack consolidates outputs from multiple agents into a single coherent document
- Includes: case summary, key facts (agreed and disputed), legal issues, applicable statutes, suggested questions, weak points per side, and evidence gaps
- Judge can annotate any section with personal notes
- Judge can add custom items to the hearing pack
- Pack can be saved as a hearing checklist with checkable items
- Pack is exportable (for printing or offline reference)
- Content is drawn from the latest pipeline outputs, reflecting any re-processing from document additions

**Happy Flow:**
1. Judge opens the case analysis view and clicks "Prepare Hearing Pack".
2. System generates a consolidated hearing pack with sections: Case Overview, Key Facts, Disputed Issues, Legal Framework, Suggested Questions, Strengths & Weaknesses per Side, and Evidence Gaps.
3. Judge reviews the Case Overview section: a concise 2-3 paragraph summary of the matter, parties, and claim.
4. Judge scrolls to the Disputed Issues section and adds a personal note to one dispute: "Focus on this — parties' accounts are directly contradictory with no independent witness."
5. Judge reviews the Suggested Questions section (carried over from US-013, including any edits) and reorders two questions for better hearing flow.
6. Judge adds a custom checklist item: "Confirm whether Respondent has brought the original quotation document."
7. Judge saves the hearing pack as a checklist, converting each key section into checkable items they can mark during the hearing.
8. Judge exports the pack as a PDF for offline reference during the hearing.

---

### US-021: Compare Alternative Outcomes

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to see the recommended verdict alongside at least one alternative with reasoning, so that I can consider different outcomes and understand what factors could shift the result.

**Acceptance Criteria:**
- Agent 8 (Deliberation) produces a recommended outcome and at least one alternative outcome
- Each outcome includes: the verdict/order, the reasoning chain, confidence score, and the key factors supporting it
- The comparison identifies which specific evidence or legal interpretations differ between outcomes
- Uncertainty factors that could shift the outcome from recommended to alternative are listed
- Outcomes are presented neutrally — neither is positioned as "correct"
- Judge can request additional alternative scenarios (e.g., "what if the latent defect argument fails?")

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Outcomes" tab.
2. System displays the recommended outcome: "Recommended: Claim allowed — Damages of $4,800 — Confidence: 72%."
3. Below it, the system shows an alternative outcome: "Alternative: Claim dismissed — Buyer deemed to have accepted goods — Confidence: 28%."
4. Judge expands the recommended outcome to see its reasoning: "Based on finding that defect was latent, s 13 implied condition breached, and acceptance under s 35 did not occur."
5. Judge expands the alternative outcome: "Based on finding that a reasonable buyer should have inspected goods within a reasonable time, 2-week delay constitutes acceptance, and Claimant loses right to reject."
6. Judge reviews the pivot factors: "The outcome turns primarily on whether the defect is classified as latent (favouring Claimant) or patent (favouring Respondent). Secondary factor: weight given to the 2-week complaint delay."
7. Judge considers both outcomes and their reasoning, forming a preliminary judicial view while remaining open to the evidence presented at the hearing.

---

## 1.6 Verdict & Governance

### US-022: Review Verdict Recommendation

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review the AI's verdict recommendation with a confidence score, so that I have a structured starting point for my judicial decision-making.

**Acceptance Criteria:**
- Agent 9 (Governance & Verdict) produces a final verdict recommendation
- SCT recommendations include: recommended order type (e.g., damages, specific performance, dismissal) and amount where applicable
- Traffic recommendations include: verdict (guilty/not guilty) and sentence (fine amount, demerit points) where applicable
- Confidence score is displayed as 0-100 with a brief basis
- Recommendation is clearly and prominently labelled "RECOMMENDATION — Subject to Judicial Determination"
- Recommendation is consistent with the deliberation reasoning chain (US-019)
- Judge can proceed to record their actual decision (US-025) from this view

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Verdict" tab.
2. System displays a prominent header: "RECOMMENDATION — Subject to Judicial Determination. This is an AI-generated recommendation and does not constitute a judicial decision."
3. The recommendation is displayed: for an SCT case, "Recommended Order: Damages in favour of Claimant — Amount: $4,800 — Confidence: 72/100."
4. Judge reads the confidence basis: "Confidence reflects strong evidence of defect (inspection report) tempered by uncertainty around the latent/patent defect classification and the 2-week complaint delay."
5. Judge reviews the recommendation's link to the deliberation reasoning chain, confirming alignment between the reasoning and the recommended outcome.
6. Judge notes the recommendation and proceeds to review the fairness audit (US-023) before recording their decision.

**Domain Notes:**
- SCT: Recommendation includes the order type (damages, repair/replacement, specific performance, dismissal) and a monetary amount where applicable.
- Traffic: Recommendation includes the verdict (guilty or not guilty) and, if guilty, the proposed sentence (fine quantum, demerit points, disqualification period where applicable).

---

### US-023: Review Fairness and Bias Audit

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review a governance audit checking for balance, unsupported claims, logical fallacies, bias, and evidence completeness, so that I can be confident the AI's analysis is fair and methodologically sound.

**Acceptance Criteria:**
- Agent 9 (Governance & Verdict) performs an automated fairness audit on the full analysis
- Audit checks include: balance of treatment between parties, unsupported claims, logical fallacies, demographic bias indicators, evidence completeness assessment, and precedent cherry-picking detection
- Each check produces a pass, warning, or fail status with an explanation
- Critical issues (any fail status) are flagged prominently at the top of the audit report
- Audit results are linked to the specific analysis elements they concern
- Judge can acknowledge or override audit findings with a recorded justification
- Audit is performed automatically and cannot be skipped or disabled

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Fairness Audit" tab.
2. System displays the audit report as a checklist of governance checks, each with a status indicator.
3. Judge reviews the results: "Balance: Pass — Both parties' arguments given comparable depth and consideration." "Unsupported Claims: Pass — All factual assertions linked to evidence." "Logical Fallacies: Warning — Potential post hoc reasoning in Step 4 of deliberation chain." "Demographic Bias: Pass — No demographic factors detected in analysis." "Evidence Completeness: Pass — All identified evidence items considered." "Precedent Selection: Pass — Precedents cited for both sides; no cherry-picking detected."
4. Judge clicks on the "Logical Fallacies: Warning" item to see details: "Step 4 infers causation from temporal sequence (defect appeared after delivery, therefore delivery caused defect). This may be valid but should be scrutinised."
5. Judge acknowledges the warning, noting it as an area to probe at the hearing.
6. No critical (fail) issues are flagged, so the judge proceeds with confidence in the analysis methodology.
7. Judge moves to record their judicial decision (US-025).

---

### US-024: Handle Escalated Cases

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to review cases that have been escalated by the AI agents, so that I can apply human judgment to matters the system has identified as requiring special attention.

**Acceptance Criteria:**
- Cases can be escalated by Agent 2 (Complexity & Routing) based on complexity thresholds or by Agent 9 (Governance & Verdict) based on governance concerns
- Escalated cases are prominently flagged in the case list and dashboard
- Escalation reason is displayed clearly (e.g., "Complexity score exceeds threshold", "Governance audit identified critical bias concern")
- All available analysis up to the point of escalation is accessible to the judge
- Judge can decide next steps: continue with available analysis, request re-processing with adjusted parameters, refer case to a senior judge, or proceed to hearing without AI support
- Escalation decision and rationale are logged in the audit trail

**Happy Flow:**
1. Judge opens the case list and sees an escalation indicator on a case: "Case TC-2026-0142 — Escalated: High complexity."
2. Judge opens the case and sees the escalation banner: "This case was escalated by Agent 2 (Complexity & Routing). Reason: Complexity score 92/100 — multiple overlapping offences, conflicting expert evidence, and novel legal issue (automated vehicle liability)."
3. Judge reviews the available analysis: Agents 1-2 have completed, but remaining agents have not run due to the escalation.
4. Judge evaluates the escalation reason and determines that the complexity is manageable with the available AI support.
5. Judge selects "Continue Processing" — the system resumes the pipeline from Agent 3 onwards.
6. Alternatively, if the judge agreed the case was too complex, they could select "Refer to Senior Judge" and add a note explaining the referral.
7. The judge's decision and rationale are recorded in the audit trail.
8. The case proceeds according to the judge's direction.

---

### US-025: Record Judicial Decision

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to record my actual decision — accepting, modifying, or rejecting the recommendation — with reasoning, so that there is a clear record of the judicial outcome alongside the AI recommendation.

**Acceptance Criteria:**
- Judge can record one of three decision types: accept_as_is, modify, or reject
- "Accept as is" records the recommendation as the judicial decision without changes
- "Modify" allows the judge to edit the recommendation (e.g., adjust the amount, change the order type) and requires a written reason for the modification
- "Reject" requires the judge to specify an alternative decision and provide a written reason
- Decision is stored in the judge_decision field of the case record
- Reasoning is mandatory for modify and reject decisions
- Decision is timestamped and associated with the judge's authenticated user ID
- Once recorded, the decision cannot be changed without creating a new decision record (amendment trail)

**Happy Flow:**
1. Judge has reviewed the verdict recommendation (US-022) and fairness audit (US-023) and is ready to record their decision.
2. Judge clicks "Record Decision" from the Verdict tab.
3. System presents three options: Accept As Is, Modify, or Reject.
4. Judge selects "Modify" — having decided the damages should be lower than recommended.
5. System presents the recommendation details in an editable form. Judge changes the damages amount from $4,800 to $3,500.
6. System prompts for a reason. Judge enters: "Reduced damages to account for Claimant's contributory failure to mitigate — Claimant delayed reporting the defect by 2 weeks, during which continued use may have worsened the damage."
7. Judge reviews the decision summary: "Decision: Modify — Damages reduced from $4,800 to $3,500 — Reason recorded" and confirms.
8. System stores the decision with timestamp and judge ID, and the case status updates to "Decision Recorded".
9. Judge can now export the case report (US-027) reflecting the recorded decision.

---

## 1.7 Audit, Export & Session

### US-026: View Full Audit Trail

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to view a timestamped log of all agent actions, inputs, outputs, and tool calls, so that I can verify the provenance and transparency of the AI analysis.

**Acceptance Criteria:**
- Audit trail records every agent invocation with: timestamp, agent ID, action performed, inputs received, outputs produced, and tools called
- Solace message IDs are included for message-bus traceability
- Trail is filterable by agent (1-9), time range, and action type
- Each entry is expandable to show full input/output payloads
- Audit trail is immutable — entries cannot be edited or deleted
- Trail includes judge actions (overrides, decisions, annotations) alongside agent actions
- Export of audit trail is available as JSON for compliance purposes

**Happy Flow:**
1. Judge opens the case analysis view and navigates to the "Audit Trail" tab.
2. System displays a chronological log of all actions taken on the case, starting from upload.
3. Judge sees entries like: "27 Mar 2026 09:15:22 SGT — Agent 1 (Case Processing) — Action: document_ingestion — Input: 3 PDF files — Output: structured case data — Solace MsgID: MSG-2026-03-27-091522-001."
4. Judge filters the trail to show only Agent 6 (Legal Knowledge) actions to review the legal research process.
5. Filtered results show the vector store queries, search terms used, results returned, and relevance scores assigned.
6. Judge expands an entry to see the full output payload, verifying that the AI considered a specific statutory provision.
7. Judge also sees their own actions in the trail: "27 Mar 2026 14:30:00 SGT — Judge — Action: override_rejection — Justification: 'Offence date incorrectly extracted'."
8. Judge is satisfied with the transparency of the analysis process.

---

### US-027: Export Case Report

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to download a formatted case report in PDF or JSON format, so that I have a portable record of the AI analysis for filing, archival, or reference purposes.

**Acceptance Criteria:**
- Report is available in PDF (human-readable) and JSON (machine-readable) formats
- PDF report includes: case summary, evidence analysis, fact timeline, legal framework, arguments, deliberation, verdict recommendation, judicial decision (if recorded), and audit trail summary
- Report is marked on every page: "AI-Generated Decision Support — Not Official Judgment"
- JSON export includes the full structured data from all pipeline stages
- Report reflects the latest state of the case, including any re-processing or document additions
- Export includes a generation timestamp and case ID for traceability
- PDF is formatted for A4 printing with appropriate headers, page numbers, and table of contents

**Happy Flow:**
1. Judge opens the case analysis view and clicks "Export Report".
2. System presents format options: PDF or JSON.
3. Judge selects PDF and clicks "Generate Report".
4. System compiles the report from all pipeline outputs, incorporating the judge's recorded decision and annotations.
5. System generates the PDF with a cover page showing: case ID, domain, parties, generation date, and the disclaimer "AI-Generated Decision Support — Not Official Judgment".
6. The report includes a table of contents and sections corresponding to each analysis area, with the audit trail summary as an appendix.
7. Judge downloads the PDF and verifies the content is complete and correctly formatted.
8. Judge files the report alongside the official case file for reference.

---

### US-028: Search and Filter Cases

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to search and filter my cases by domain, status, date range, complexity, and outcome, so that I can efficiently manage my caseload and find specific cases.

**Acceptance Criteria:**
- Case list supports filtering by: domain (SCT, Traffic), status (processing, completed, escalated, closed, rejected), date range (filed date), complexity level, and outcome (if decided)
- Full-text search is available across case summaries, party names, and key facts
- Filters can be combined (e.g., "SCT + Completed + Last 30 days")
- Results display case ID, parties, domain, status, filing date, and summary snippet
- Results are sortable by date, status, or complexity
- Search and filter state is preserved during the session (navigating away and back retains filters)
- Pagination is provided for large result sets

**Happy Flow:**
1. Judge opens the case list view, which initially shows all their cases in reverse chronological order.
2. Judge applies a filter for "SCT" domain and "Completed" status to review recently concluded cases.
3. System updates the list to show only matching cases — e.g., 12 results.
4. Judge enters a search term "furniture" in the full-text search box to find a specific case about defective furniture.
5. Results narrow to 2 cases. Judge sees: "SCT-2026-0089 — Lim v FurniturePlus Pte Ltd — Completed — Filed: 10 Feb 2026 — Summary: Claim for defective dining table set..."
6. Judge clicks on the case to open the full analysis view.
7. Judge returns to the case list and finds their filters still applied, allowing them to continue browsing.

---

### US-029: View Dashboard Overview

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to see aggregate metrics on cases processed, processing times, confidence distribution, escalation rates, and costs, so that I can understand system performance and my caseload patterns.

**Acceptance Criteria:**
- Dashboard displays: total cases processed (by domain), average pipeline processing time, confidence score distribution (histogram or summary), escalation rate (percentage and count), and cost per case (API usage)
- Metrics are filterable by time period (last 7 days, 30 days, 90 days, custom range)
- Dashboard updates reflect the latest available data
- Key metrics are shown as summary cards with trend indicators (up/down vs previous period)
- Judge can drill down from any metric to the underlying case list
- Dashboard loads within 3 seconds

**Happy Flow:**
1. Judge opens the Dashboard view from the main navigation.
2. System displays summary cards: "Cases Processed: 47 (Last 30 days) — SCT: 28, Traffic: 19", "Avg Processing Time: 4m 32s", "Avg Confidence: 71/100", "Escalation Rate: 8.5% (4 cases)", "Avg Cost per Case: $0.42".
3. Judge notices the escalation rate has increased from 4% to 8.5% compared to the previous period (shown as a red up-arrow).
4. Judge clicks on the escalation rate card to drill down and sees the 4 escalated cases listed with their escalation reasons.
5. Judge reviews the confidence distribution: a histogram showing most cases cluster between 65-80 confidence, with 3 outliers below 50.
6. Judge changes the time filter to "Last 90 days" to see longer-term trends.
7. System updates all metrics and trend indicators to reflect the 90-day window.

---

### US-030: Manage Session and Authentication

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to securely log in, maintain my session, and log out with token invalidation, so that case data is protected and only accessible to authenticated judicial officers.

**Acceptance Criteria:**
- Authentication uses JWT tokens issued upon successful login
- Tokens are stored in HTTP-only cookies (not accessible to client-side JavaScript)
- Session has a configurable timeout with automatic logout on expiry
- Judge receives a warning before session expiry with an option to extend
- Logout invalidates the token server-side (token blacklist or equivalent mechanism)
- Failed login attempts are rate-limited and logged
- All authenticated routes require a valid, non-expired token
- Session state (current case, filters, preferences) is preserved for the duration of the session

**Happy Flow:**
1. Judge navigates to the VerdictCouncil login page.
2. Judge enters their credentials (username and password) and clicks "Login".
3. System validates credentials, generates a JWT token, and sets it as an HTTP-only secure cookie.
4. Judge is redirected to the Dashboard (US-029) as the default landing page.
5. During their session, the judge opens cases, reviews analysis, and records decisions — all API calls include the JWT cookie automatically.
6. After 25 minutes of a 30-minute session timeout, the judge sees a notification: "Your session will expire in 5 minutes. Click to extend."
7. Judge clicks "Extend Session" and the token is refreshed for another 30 minutes.
8. When finished, the judge clicks "Logout". The system invalidates the token server-side and clears the cookie.
9. Any subsequent attempt to access a protected route redirects to the login page.

