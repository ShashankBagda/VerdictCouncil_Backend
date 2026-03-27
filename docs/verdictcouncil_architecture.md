# VerdictCouncil

## Multi-Agent AI Judicial Decision-Support System

**Consolidated Architecture, User Stories, Agent Configurations, Diagrams & CI/CD**

NUS Master of Software Engineering | Agentic AI Architecture Module | March 2026 | v7.0

---

**Table of Contents**

- [Part 1: User Stories](#part-1-user-stories)
- [Part 2: System Architecture](#part-2-system-architecture)
- [Part 3: Agent Configurations (SAM YAML)](#part-3-agent-configurations-sam-yaml)
- [Part 4: Tech Stack](#part-4-tech-stack)
- [Part 5: Diagrams](#part-5-diagrams)
- [Part 6: CI/CD Pipeline](#part-6-cicd-pipeline)
- [Appendices](#appendices)

---



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

As a judicial officer, I want to trigger a live search of judiciary.gov.sg and search.pair.gov.sg, so that I can access the most recent and comprehensive case law beyond the curated vector store.

**Acceptance Criteria:**
- Judge can initiate a live precedent search from the case analysis view
- Search queries judiciary.gov.sg and search.pair.gov.sg using the search_precedents tool on Agent 6
- Results are tagged "live_search" to distinguish them from "curated" vector store results
- Live results include: case citation, court, date, summary, and a relevance/similarity score
- Results are integrated into the precedents list alongside curated results (US-015)
- Search accepts custom keywords or uses AI-generated search terms based on case facts
- System indicates when the live search was last performed and its result count

**Happy Flow:**
1. Judge is reviewing precedents (US-015) and wants to check for more recent case law not in the curated store.
2. Judge clicks "Search Live Database" from the Precedents tab.
3. System presents a search panel with AI-suggested search terms based on the case facts (e.g., "defective goods sale satisfactory quality SCT") and an option to enter custom keywords.
4. Judge accepts the suggested terms and adds a custom keyword: "latent defect".
5. System executes the search_precedents tool on Agent 6, querying judiciary.gov.sg and search.pair.gov.sg.
6. Results are returned and displayed, each tagged "live_search" — e.g., "Ong Siew Kee v FurnitureMart Pte Ltd [2025] SGSCT 61 — live_search — Relevance: 74%".
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



# Part 2: System Architecture

---

## 2.1 Consolidation Rationale

The original design specified 18 specialized agents — one per logical task. While this maximized separation of concerns, it introduced unacceptable orchestration complexity: 17 inter-agent transitions, compounding latency, and token overhead from serializing/deserializing CaseState at every hop.

We consolidated to 9 agents using four guiding principles:

1. **Preserve the explainable decision pipeline.** The core reasoning chain — Evidence, Facts, Law, Arguments, Deliberation, Fairness, Verdict — must remain traceable. Each link in this chain stays as an independent agent so the Judge can audit exactly where a conclusion originated.

2. **Bundle operational/administrative agents that perform logically sequential tasks.** Agents that always run in fixed order with no branching logic (e.g., intake then structuring then classification then jurisdiction) collapse into a single agent with sequential internal steps.

3. **Keep reasoning-heavy agents independent.** Agents that perform substantive legal reasoning (Evidence Analysis, Deliberation) remain standalone. Their outputs are auditable decision points that the Judge reviews independently.

4. **Reduce orchestration complexity and token cost.** Fewer agents means fewer Solace topic transitions, fewer payload serializations, and lower aggregate token consumption from repeated CaseState parsing.

### Consolidation Map

| # | Consolidated Agent | Original Agents Merged | Reduction |
|---|---|---|---|
| 1 | Case Processing | Case Intake + Case Structuring + Domain Classification + Jurisdiction Validation | 4 → 1 |
| 2 | Complexity & Routing | Complexity Assessment & Routing | 1 → 1 |
| 3 | Evidence Analysis | Evidence Analysis | 1 → 1 |
| 4 | Fact Reconstruction | Fact Extraction + Timeline Construction | 2 → 1 |
| 5 | Witness Analysis | Witness Identification + Testimony Anticipation + Credibility Assessment | 3 → 1 |
| 6 | Legal Knowledge | Legal Rule Retrieval + Precedent Retrieval | 2 → 1 |
| 7 | Argument Construction | Claim/Prosecution Advocate + Defense/Respondent Advocate + Balanced Assessment | 3 → 1 |
| 8 | Deliberation | Deliberation Engine | 1 → 1 |
| 9 | Governance & Verdict | Fairness/Bias Audit + Verdict Recommendation | 2 → 1 |
| | **Total** | | **18 → 9** |

**Net reduction:** 9 fewer inter-agent transitions, approximately 50% reduction in orchestration overhead.

---

## 2.2 Orchestration Platform: Solace Agent Mesh

VerdictCouncil runs on **Solace Agent Mesh (SAM)**, an event-driven multi-agent framework built on the Solace PubSub+ Event Broker. SAM was selected over alternatives (LangGraph, CrewAI, AutoGen) for the following reasons:

**Event-Driven Async A2A Communication.** Agents communicate via the Solace Event Broker using a publish/subscribe model. Each agent subscribes to its input topic and publishes to the next agent's input topic. This decouples agents temporally — a slow Evidence Analysis does not block Case Processing from handling the next case.

**YAML-Driven Agent Configuration.** Each agent is defined in a standalone YAML file specifying its model, system prompt, tools, and broker connection. No Python orchestration code is needed for agent wiring — the topic structure IS the orchestration.

**SAM A2A Protocol.** Agents communicate using SAM's Agent-to-Agent protocol over Solace topics. The topic pattern is:

```
{namespace}/a2a/v1/agent/request/{target_agent_name}
```

For example, when Case Processing completes and needs to invoke Complexity & Routing:

```
verdictcouncil/a2a/v1/agent/request/complexity-routing
```

**No Built-in Shared State.** SAM does not provide a shared state store. This is a deliberate architectural constraint — it forces all inter-agent communication to flow through the event broker, creating a complete audit trail. The CaseState object is passed as the event payload through the pipeline (see Section 2.4).

**LiteLLM Wrapper for Model Abstraction.** SAM integrates with LLM providers through LiteLLM, allowing model specifications like `o3`, `o4-mini`, `gpt-4.1`, and `gpt-4.1-mini` to route to OpenAI's API without provider-specific code.

**Built-in Web Gateway.** SAM provides an HTTP/SSE gateway module that exposes the agent mesh to external clients. This serves as both the production API endpoint and a debugging/tracing interface during development.

**Enterprise-Grade Observability.** Every message that flows through the Solace Event Broker is auditable. Combined with SAM's built-in tracing, this provides a complete, immutable record of every agent invocation, every payload, and every response — critical for a judicial system.

**Fault-Tolerant Message Delivery.** The Solace Event Broker provides guaranteed message delivery with persistence. If an agent pod crashes mid-processing, the message remains in the broker queue and is redelivered when the agent recovers.

---

## 2.3 Architecture Layers

The 9 agents are organized into 4 logical layers reflecting the judicial reasoning process:

```
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 1: CASE PREPARATION                    │
│  ┌─────────────────────┐    ┌──────────────────────────────┐   │
│  │  Case Processing     │───▶│  Complexity & Routing         │   │
│  │  (gpt-4.1-mini)      │    │  (o4-mini)                    │   │
│  └─────────────────────┘    └──────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│                 LAYER 2: EVIDENCE RECONSTRUCTION                │
│  ┌──────────────────┐ ┌──────────────────┐ ┌────────────────┐  │
│  │ Evidence Analysis │ │Fact Reconstruction│ │Witness Analysis│  │
│  │ (gpt-4.1)        │ │(gpt-4.1)         │ │(o4-mini)       │  │
│  └──────────────────┘ └──────────────────┘ └────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    LAYER 3: LEGAL REASONING                     │
│  ┌──────────────────────┐    ┌──────────────────────────────┐  │
│  │  Legal Knowledge      │───▶│  Argument Construction        │  │
│  │  (gpt-4.1)            │    │  (o3)                         │  │
│  └──────────────────────┘    └──────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                   LAYER 4: JUDICIAL DECISION                    │
│  ┌──────────────────────┐    ┌──────────────────────────────┐  │
│  │  Deliberation         │───▶│  Governance & Verdict         │  │
│  │  (o3)                 │    │  (o3)                         │  │
│  └──────────────────────┘    └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

| Layer | Agents | Purpose | Model Tier |
|-------|--------|---------|------------|
| **Layer 1: Case Preparation** | Case Processing, Complexity & Routing | Intake, structuring, jurisdiction validation, complexity assessment, routing | gpt-4.1-mini, o4-mini |
| **Layer 2: Evidence Reconstruction** | Evidence Analysis, Fact Reconstruction, Witness Analysis | Analyze evidence, extract facts, build timeline, assess witnesses | gpt-4.1, gpt-4.1, o4-mini |
| **Layer 3: Legal Reasoning** | Legal Knowledge, Argument Construction | Retrieve applicable law and precedents, construct both sides' arguments | gpt-4.1, o3 |
| **Layer 4: Judicial Decision** | Deliberation, Governance & Verdict | Reason from evidence to conclusion, audit for fairness, produce recommendation | o3, o3 |

**Model assignment rationale:**
- **gpt-4.1-mini** for administrative tasks (parsing, structuring) — fast and cost-efficient.
- **o4-mini** for classification and assessment tasks requiring moderate reasoning — good balance of reasoning capability and speed.
- **gpt-4.1** for evidence analysis and legal retrieval — strong instruction-following for structured extraction with large context windows.
- **o3** for deep reasoning tasks (argument construction, deliberation, governance) — maximum reasoning capability for high-stakes judicial analysis.

---

## 2.4 CaseState as Event Payload

Since SAM has no built-in shared state mechanism, the entire case context travels as the event payload through the pipeline. Each agent receives the CaseState JSON, reads the fields relevant to its task, writes its output to its designated fields, and publishes the updated payload to the next agent's topic.

This pattern has three important properties:

1. **Self-contained messages.** Each event contains the complete case context. No database lookups are needed during agent processing — the payload IS the state.
2. **Immutable audit trail.** Each published event is a snapshot of the CaseState at that pipeline stage. The Solace broker retains these, creating a full history of how the case evolved.
3. **Stateless agents.** Agents hold no state between invocations. Any agent pod can process any case — enabling horizontal scaling and fault tolerance.

### CaseState Schema

```python
class CaseState:
    """
    Central state object passed as JSON payload through the SAM pipeline.
    Each agent reads relevant fields and writes to its designated section.
    """

    # --- Identity & Status (written by Case Processing) ---
    case_id: str                    # UUID, assigned at intake
    run_id: str                     # UUID, generated per pipeline execution.
                                    # Uniquely identifies this run of the pipeline.
                                    # What-if scenario runs get a new run_id.
    parent_run_id: str | None       # UUID, nullable. References the run_id this
                                    # execution was derived from (for what-if
                                    # scenarios). None for initial pipeline runs.
                                    # Enables tracing the lineage of what-if analyses.
    domain: str                     # "small_claims" | "traffic_violation"
    status: str                     # "PROCESSING" | "REJECTED" | "ESCALATED" | "COMPLETED" | "FAILED"
    parties: list[dict]             # [{name, role, contact, representation_status}]
    case_metadata: dict             # {
                                    #   filed_date, category, subcategory,
                                    #   monetary_value (SCT), offence_code (traffic),
                                    #   jurisdiction_valid: bool,
                                    #   jurisdiction_issues: list[str]
                                    # }

    # --- Documents (written by Case Processing) ---
    raw_documents: list[dict]       # [{doc_id, filename, file_id (OpenAI), type,
                                    #   submitted_by, description}]

    # --- Evidence (written by Evidence Analysis) ---
    evidence_analysis: dict         # {
                                    #   items: [{doc_id, classification, strength,
                                    #            admissibility_risk, linked_claims,
                                    #            reasoning}],
                                    #   contradictions: [{doc_a, doc_b, description}],
                                    #   gaps: [str],
                                    #   corroborations: [{doc_ids, description}]
                                    # }

    # --- Facts (written by Fact Reconstruction) ---
    extracted_facts: dict           # {
                                    #   facts: [{fact_id, date, description, parties,
                                    #            location, source_refs, confidence,
                                    #            status: "agreed"|"disputed",
                                    #            conflicting_versions: [dict]}],
                                    #   timeline: [{timestamp, event, fact_id}]
                                    # }

    # --- Witnesses (written by Witness Analysis) ---
    witnesses: dict                 # {
                                    #   identified: [{name, role, relationship,
                                    #                 party_alignment, has_statement,
                                    #                 bias_indicators}],
                                    #   testimony_anticipation: [{witness_name,
                                    #                             summary, strong_points,
                                    #                             vulnerabilities,
                                    #                             conflicts_with_evidence}],
                                    #   credibility: [{witness_name, score: 0-100,
                                    #                  internal_consistency,
                                    #                  external_consistency,
                                    #                  bias_assessment,
                                    #                  specificity, corroboration}]
                                    # }

    # --- Law (written by Legal Knowledge) ---
    legal_rules: list[dict]         # [{statute, section, text, relevance_score,
                                    #   application_to_facts}]
    precedents: list[dict]          # [{citation, outcome, reasoning_summary,
                                    #   similarity_score, distinguishing_factors,
                                    #   source: "curated"|"live_search"}]

    # --- Arguments (written by Argument Construction) ---
    arguments: dict                 # Traffic: {prosecution, defense, contested_issues}
                                    # SCT: {claimant, respondent, agreed_facts,
                                    #        disputed_facts, evidence_gaps,
                                    #        strength_comparison}
                                    # Both include: {judicial_questions: [dict]}

    # --- Deliberation (written by Deliberation) ---
    deliberation: dict              # {
                                    #   established_facts, applicable_law,
                                    #   application: [{element, evidence, satisfied}],
                                    #   argument_evaluation,
                                    #   witness_impact, precedent_alignment,
                                    #   preliminary_conclusion,
                                    #   uncertainty_flags: [str]
                                    # }

    # --- Governance (written by Governance & Verdict) ---
    fairness_check: dict            # {
                                    #   balance_assessment, unsupported_claims,
                                    #   logical_fallacies, demographic_bias_check,
                                    #   evidence_completeness,
                                    #   precedent_cherry_picking,
                                    #   critical_issues_found: bool,
                                    #   audit_passed: bool
                                    # }
    verdict_recommendation: dict    # SCT: {recommended_order, amount, legal_basis,
                                    #        reasoning_summary}
                                    # Traffic: {recommended_verdict, sentence,
                                    #           sentencing_range}
                                    # Both: {confidence_score, uncertainty_factors,
                                    #         alternative_outcomes, fairness_report}

    # --- Judge (written externally after Judge review) ---
    judge_decision: dict            # {accepted, modified, rejected, notes, final_order}

    # --- Audit (appended by every agent) ---
    audit_log: list[dict]           # [{agent, timestamp, action, input_hash,
                                    #   output_hash, model, token_usage}]
```

**Field ownership rules:**
- Each agent writes ONLY to its designated fields.
- Each agent MAY read any field written by a preceding agent.
- No agent may overwrite another agent's fields.
- The `audit_log` is append-only — every agent adds its entry.

---

## 2.5 Pipeline Flow

The complete pipeline flow with SAM topic routing:

```
                         SAM Topic Routing
                         ─────────────────

    ┌──────────────┐  verdictcouncil/a2a/v1/agent/request/complexity-routing
    │    Case       │────────────────────────────────────────────────────────▶
    │  Processing   │
    └──────────────┘
                         ┌──────────────────┐
                         │  Complexity &     │
                    ◀────│  Routing          │
                    │    └──────────────────┘
                    │
              ┌─────┴─────┐
              │            │
         escalate     proceed
         _human       _automated / _with_review
              │            │
           [HALT]          │
                           │  .../request/evidence-analysis
                           │  .../request/fact-reconstruction
                           │  .../request/witness-analysis
                           ▼
                    ┌──────────────────────────────────────────┐
                    │     PARALLEL EXECUTION (fan-out)         │
                    │                                          │
                    │  ┌────────────┐ ┌────────────┐ ┌──────┐ │
                    │  │ Evidence   │ │   Fact     │ │Witnes│ │
                    │  │ Analysis   │ │Reconstrctn│ │Analys│ │
                    │  │ (Agent 3)  │ │ (Agent 4)  │ │(Ag 5)│ │
                    │  └────────────┘ └────────────┘ └──────┘ │
                    │                                          │
                    │  All three read same upstream data and   │
                    │  execute concurrently via topic fan-out. │
                    └──────────────────────────────────────────┘
                           │  .../response/evidence-analysis
                           │  .../response/fact-reconstruction
                           │  .../response/witness-analysis
                           ▼
                    ┌──────────────────────────────────────────┐
                    │      Layer2Aggregator (fan-in barrier)   │
                    │                                          │
                    │  Subscribes to all 3 agent output topics │
                    │  Tracks completion per case_id           │
                    │  Merges outputs into unified CaseState   │
                    │  Publishes to Agent 6 only when all 3    │
                    │  have completed for the same case_id     │
                    └──────────────────────────────────────────┘
                           │  .../request/legal-knowledge
                           ▼
                    ┌──────────────────┐
                    │ Legal Knowledge   │
                    └──────────────────┘
                           │  .../request/argument-construction
                           ▼
                    ┌──────────────────────┐
                    │ Argument Construction │
                    │                      │
                    │  ┌────────┐ ┌──────┐ │  (traffic cases: internal
                    │  │Prosecn.│ │Defense│ │   parallel analysis)
                    │  └────────┘ └──────┘ │
                    └──────────────────────┘
                           │  .../request/deliberation
                           ▼
                    ┌──────────────────┐
                    │   Deliberation    │
                    └──────────────────┘
                           │  .../request/governance-verdict
                           ▼
                    ┌──────────────────────┐
                    │ Governance & Verdict  │
                    │                      │
                    │  Phase 1: Audit      │
                    │     │                │
                    │  ┌──┴──┐             │
                    │  │     │             │
                    │ FAIL  PASS           │
                    │  │     │             │
                    │[HALT]  Phase 2:      │
                    │        Verdict       │
                    └──────────────────────┘
                           │  .../request/web-gateway
                           ▼
                    ┌──────────────────┐
                    │   Web Gateway     │
                    │   (HTTP/SSE)      │
                    └──────────────────┘
                           │
                           ▼
                      Judge's UI
```

**Full topic sequence:**

| Step | Source Agent | Target Topic | Target Agent |
|------|-------------|-------------|--------------|
| 1 | Web Gateway | `verdictcouncil/a2a/v1/agent/request/case-processing` | Case Processing |
| 2 | Case Processing | `verdictcouncil/a2a/v1/agent/request/complexity-routing` | Complexity & Routing |
| 3 | Complexity & Routing | `verdictcouncil/a2a/v1/agent/request/evidence-analysis` | Evidence Analysis (fan-out) |
| 3 | Complexity & Routing | `verdictcouncil/a2a/v1/agent/request/fact-reconstruction` | Fact Reconstruction (fan-out) |
| 3 | Complexity & Routing | `verdictcouncil/a2a/v1/agent/request/witness-analysis` | Witness Analysis (fan-out) |
| 4 | Evidence Analysis | `verdictcouncil/a2a/v1/agent/response/evidence-analysis` | Layer2Aggregator |
| 5 | Fact Reconstruction | `verdictcouncil/a2a/v1/agent/response/fact-reconstruction` | Layer2Aggregator |
| 6 | Witness Analysis | `verdictcouncil/a2a/v1/agent/response/witness-analysis` | Layer2Aggregator |
| 7 | Layer2Aggregator | `verdictcouncil/a2a/v1/agent/request/legal-knowledge` | Legal Knowledge |
| 7 | Legal Knowledge | `verdictcouncil/a2a/v1/agent/request/argument-construction` | Argument Construction |
| 8 | Argument Construction | `verdictcouncil/a2a/v1/agent/request/deliberation` | Deliberation |
| 9 | Deliberation | `verdictcouncil/a2a/v1/agent/request/governance-verdict` | Governance & Verdict |
| 10 | Governance & Verdict | `verdictcouncil/a2a/v1/agent/request/web-gateway` | Web Gateway |

**Parallel execution note:** For traffic cases, Argument Construction internally runs prosecution and defense analysis in parallel within the single agent invocation. This is handled at the prompt level (the model produces both analyses in one response) rather than at the orchestration level — no additional SAM topics are needed.

---

## 2.5.1 Layer 2 Aggregator — Fan-In Barrier Service

SAM's event broker provides native topic fan-out (one publisher, multiple subscribers), but does not provide a built-in fan-in barrier (wait for multiple publishers to complete before triggering a downstream consumer). The **Layer2Aggregator** is a lightweight stateful service that implements this barrier for Agents 3, 4, and 5.

### Purpose

After Complexity & Routing (Agent 2) publishes the CaseState, three agents execute concurrently:
- Agent 3 (Evidence Analysis) — writes `evidence_analysis`
- Agent 4 (Fact Reconstruction) — writes `extracted_facts`
- Agent 5 (Witness Analysis) — writes `witnesses`

Agent 6 (Legal Knowledge) requires outputs from all three before it can proceed. The Layer2Aggregator collects the three outputs, merges them into a single CaseState, and publishes to Agent 6's input topic only when all three have completed.

### Architecture

```
Agent 2 ─── fan-out ──┬── Agent 3 ──┐
                      ├── Agent 4 ──┤── Layer2Aggregator ── Agent 6
                      └── Agent 5 ──┘
```

### Behaviour

1. **Subscribe** to three response topics:
   - `verdictcouncil/a2a/v1/agent/response/evidence-analysis`
   - `verdictcouncil/a2a/v1/agent/response/fact-reconstruction`
   - `verdictcouncil/a2a/v1/agent/response/witness-analysis`

2. **Track completion** per `case_id:run_id` using an in-memory map (backed by Redis for crash recovery). The `run_id` (UUID) is generated per pipeline execution (or equals the `scenario_id` for what-if runs), ensuring concurrent executions for the same case are isolated:
   ```python
   pending = {
       f"{case_id}:{run_id}": {
           "evidence_analysis": None,   # or CaseState fragment
           "extracted_facts": None,
           "witnesses": None,
           "_original_case_state": None, # full CaseState from first receipt
       }
   }
   ```

3. **On each message received:**
   - Parse the `case_id`, `run_id`, and agent identifier from the topic/payload.
   - On first receipt for a given `case_id:run_id`, store the original full CaseState for later merge.
   - Store the agent's output in the corresponding slot.
   - Atomically check if all three slots are populated (via Redis Lua script to prevent duplicate publishes).
   - If yes: deep-copy the original CaseState, merge the three agent outputs into their designated fields (`evidence_analysis`, `extracted_facts`, `witnesses`), publish the full merged CaseState to `verdictcouncil/a2a/v1/agent/request/legal-knowledge`, and remove the `case_id:run_id` from the pending map.

4. **Timeout handling:** If fewer than 3 agents complete within 120 seconds, the aggregator halts the pipeline and sets the case status to FAILED. It logs which agents did not complete. Partial results are never forwarded to Agent 6 — incomplete analysis is worse than no analysis in a judicial context.

5. **Duplicate handling:** If the same agent publishes twice for the same `case_id` (e.g., due to broker retry), the aggregator overwrites the existing slot with the newer payload (idempotent).

### SAM YAML Configuration

```yaml
# configs/services/layer2-aggregator.yaml
apps:
  - name: layer2-aggregator
    app_module: solace_agent_mesh.services.aggregator.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      service_name: "Layer2Aggregator"
      display_name: "Layer 2 Fan-In Aggregator"

      subscriptions:
        - topic: "verdictcouncil/a2a/v1/agent/response/evidence-analysis"
          agent_key: "evidence_analysis"        # maps to CaseState.evidence_analysis
        - topic: "verdictcouncil/a2a/v1/agent/response/fact-reconstruction"
          agent_key: "extracted_facts"           # maps to CaseState.extracted_facts
        - topic: "verdictcouncil/a2a/v1/agent/response/witness-analysis"
          agent_key: "witnesses"                 # maps to CaseState.witnesses

      output_topic: "verdictcouncil/a2a/v1/agent/request/legal-knowledge"

      barrier:
        required_agents: ["evidence_analysis", "extracted_facts", "witnesses"]
        timeout_seconds: 120
        on_timeout: "halt_and_flag"
        # Redis key includes run_id to isolate concurrent/what-if executions:
        # key format: {key_prefix}{case_id}:{run_id}

      state_backend: "redis"
      redis:
        host: ${REDIS_HOST}
        port: ${REDIS_PORT}
        password: ${REDIS_PASSWORD}
        db: 1
        key_prefix: "vc:aggregator:"
        ttl_seconds: 300

      session_service: *default_session_service
      artifact_service: *default_artifact_service
```

### Implementation (Python)

```python
# services/layer2_aggregator/aggregator.py
"""Layer 2 Fan-In Aggregator for VerdictCouncil.

Collects outputs from Agents 3, 4, and 5. Merges them into a unified
CaseState and publishes to Agent 6 only when all three have completed.
"""

import json
import time
from typing import Any

import redis


class Layer2Aggregator:
    """Stateful barrier that waits for 3 agent outputs before forwarding.

    The Redis key includes both case_id and run_id to isolate concurrent
    pipeline executions (e.g., what-if scenario runs) from each other.
    """

    REQUIRED_AGENTS = frozenset([
        "evidence_analysis",
        "extracted_facts",
        "witnesses",
    ])
    TIMEOUT_SECONDS = 120

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "vc:aggregator:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _key(self, case_id: str, run_id: str) -> str:
        """Redis key scoped to both case and run to isolate concurrent executions."""
        return f"{self.prefix}{case_id}:{run_id}"

    def receive_output(
        self, case_id: str, run_id: str, agent_key: str, payload: dict
    ) -> dict | None:
        """Store an agent's output. Returns merged CaseState if barrier is met.

        Uses a Redis Lua script for atomic check-and-publish to prevent
        duplicate publishes when multiple agents complete near-simultaneously.

        Args:
            case_id: The case identifier.
            run_id: UUID for this pipeline execution (or scenario_id for what-if runs).
            agent_key: One of 'evidence_analysis', 'extracted_facts', 'witnesses'.
            payload: The agent's output CaseState fragment.

        Returns:
            Merged CaseState dict if all 3 agents have completed, else None.
        """
        if agent_key not in self.REQUIRED_AGENTS:
            raise ValueError(f"Unknown agent_key: {agent_key}")

        key = self._key(case_id, run_id)

        # Atomic check-and-publish via Lua script to prevent race conditions.
        # The script stores the agent output, checks completeness, and sets a
        # "published" flag atomically — ensuring exactly-once publish semantics.
        lua_script = """
        redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
        redis.call('HSET', KEYS[1], ARGV[1] .. '_ts', ARGV[3])
        if redis.call('EXISTS', KEYS[2]) == 0 then
            redis.call('SET', KEYS[2], ARGV[3])
            redis.call('EXPIRE', KEYS[2], 300)
        end
        -- Store original CaseState on first receipt
        if redis.call('HEXISTS', KEYS[1], '_original_case_state') == 0 then
            redis.call('HSET', KEYS[1], '_original_case_state', ARGV[4])
        end
        -- Check if all required agents have reported
        local fields = redis.call('HKEYS', KEYS[1])
        local agent_count = 0
        for _, f in ipairs(fields) do
            if not string.find(f, '_ts$') and f ~= '_original_case_state' and f ~= '_published' then
                agent_count = agent_count + 1
            end
        end
        if agent_count >= 3 and redis.call('HEXISTS', KEYS[1], '_published') == 0 then
            redis.call('HSET', KEYS[1], '_published', '1')
            return 1
        end
        return 0
        """
        ready = self.redis.eval(
            lua_script,
            2,
            key,
            key + ":created",
            agent_key,
            json.dumps(payload),
            str(time.time()),
            json.dumps(payload),  # original CaseState from first agent's full payload
        )

        if ready == 1:
            return self._merge_and_cleanup(case_id, run_id)

        return None

    def check_timeout(self, case_id: str, run_id: str) -> None:
        """Check if the barrier has timed out. Halts pipeline on timeout.

        Unlike the previous design which published partial results, timeout
        now sets the case status to FAILED and logs which agents did not
        complete. Partial CaseState is never forwarded to downstream agents.
        """
        key = self._key(case_id, run_id)
        created_raw = self.redis.get(key + ":created")
        if not created_raw:
            return None

        created = float(created_raw)
        if time.time() - created > self.TIMEOUT_SECONDS:
            # Determine which agents are missing
            all_data = self.redis.hgetall(key)
            stored_agents = {
                k.decode() if isinstance(k, bytes) else k
                for k in all_data.keys()
                if not (k.decode() if isinstance(k, bytes) else k).endswith("_ts")
                and (k.decode() if isinstance(k, bytes) else k) not in (
                    "_original_case_state", "_published"
                )
            }
            missing = self.REQUIRED_AGENTS - stored_agents

            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                "Layer2Aggregator TIMEOUT for case_id=%s run_id=%s. "
                "Missing agents: %s. Setting case status to FAILED.",
                case_id, run_id, list(missing),
            )

            # Cleanup Redis state — do NOT publish partial results
            self.redis.delete(key)
            self.redis.delete(key + ":created")

            # Caller is responsible for updating case status to FAILED
            # in the database and notifying the gateway.
            return None

        return None

    def _merge_and_cleanup(
        self, case_id: str, run_id: str, partial: bool = False
    ) -> dict:
        """Merge agent outputs into the original CaseState (not a bare dict).

        Deep-copies the original CaseState received at pipeline entry, then
        updates only the three designated fields (evidence_analysis,
        extracted_facts, witnesses) from the agent outputs. All other
        CaseState fields (case_id, domain, parties, raw_documents, etc.)
        are preserved.
        """
        import copy

        key = self._key(case_id, run_id)
        all_data = self.redis.hgetall(key)

        # Recover the original CaseState stored on first receipt
        original_raw = all_data.get(
            b"_original_case_state" if isinstance(
                list(all_data.keys())[0], bytes
            ) else "_original_case_state"
        )
        if original_raw:
            original_case_state = json.loads(original_raw)
        else:
            original_case_state = {}

        # Deep-copy to avoid mutating cached data
        merged = copy.deepcopy(original_case_state)

        # Merge only the designated agent output fields into the full CaseState
        for agent_key in self.REQUIRED_AGENTS:
            raw = all_data.get(
                agent_key.encode() if isinstance(
                    list(all_data.keys())[0], bytes
                ) else agent_key
            )
            if raw:
                fragment = json.loads(raw)
                # Update the designated CaseState field with the agent's output
                merged[agent_key] = fragment.get(agent_key, fragment)

        # Cleanup
        self.redis.delete(key)
        self.redis.delete(key + ":created")

        return merged
```

### Class Diagram Addition

```mermaid
classDiagram
    class Layer2Aggregator {
        -redis: RedisClient
        -prefix: str
        +receive_output(case_id, run_id, agent_key, payload) dict|None
        +check_timeout(case_id, run_id) None
        -_merge_and_cleanup(case_id, run_id, partial) dict
        -_key(case_id, run_id) str
    }

    BaseAgent <.. Layer2Aggregator : consumes outputs from
    Layer2Aggregator --> LegalKnowledgeAgent : publishes merged CaseState to
```

### Deployment

The Layer2Aggregator runs as an additional pod in the K8s cluster (or container in docker-compose). It is stateless except for the Redis-backed pending map, so it can be restarted without data loss.

| Component | Container Name | Port | Image |
|---|---|---|---|
| Layer2Aggregator | vc-layer2-aggregator | 8090 (health) | ghcr.io/verdictcouncil/layer2-aggregator |

This brings the total deployment to: **9 agent pods + 1 gateway + 1 What-If Controller + 1 Layer2Aggregator + 1 Solace broker + PostgreSQL + Redis = 14 containers**.

---

## 2.6 Conditional Edges and Error Handling

### Halt Conditions

The pipeline has two explicit halt points where processing stops and the case is escalated to a human judicial officer:

**Halt Point 1: Complexity & Routing**

```
IF route == "escalate_human":
    status = "ESCALATED"
    Pipeline HALTS
    Case queued for manual assignment
    Reason logged to audit_log
```

Triggers: high complexity, potential precedent-setting impact, vulnerable parties without adequate safeguards, cross-jurisdictional issues requiring judicial discretion.

**Halt Point 2: Governance & Verdict (Phase 1 Audit)**

```
IF critical_issues_found == true:
    status = "ESCALATED"
    Pipeline HALTS BEFORE verdict generation
    Fairness audit report sent to Judge
    No verdict recommendation is produced
```

Triggers: systematic bias detected, reasoning relies on facts not in evidence, critical logical fallacies, demographic bias indicators, evidence from one party systematically overlooked.

The separation between audit and verdict within the Governance agent is critical: the fairness check MUST pass before any verdict recommendation is generated. A partial verdict (one that failed governance) must never reach the Judge.

### Error Handling

Any agent failure triggers the following sequence:

1. **Pipeline halts immediately.** No subsequent agents are invoked.
2. **Error is logged** to the `audit_log` with the failing agent name, error type, timestamp, and the CaseState snapshot at failure.
3. **Case status is set to `ERROR`** with a reference to the failing agent.
4. **Case is flagged for manual processing.** The Judge receives notification that automated analysis could not be completed, along with whatever partial analysis was produced by preceding agents.
5. **The Solace broker retains the failed message** for replay after the issue is resolved.

```
Agent failure scenarios:
├── LLM API timeout/error     → Retry 2x with exponential backoff, then HALT
├── Tool execution failure     → Log tool error, HALT (do not skip the tool)
├── JSON schema validation     → Agent output rejected, HALT
├── Payload size exceeded      → HALT, flag for manual processing
└── Broker delivery failure    → Solace handles retry with guaranteed delivery
```

---

## 2.7 Security and Prompt Injection Defenses

A judicial decision-support system processes adversarial input by definition — parties in a legal dispute have strong incentives to manipulate outcomes. The following defenses protect the pipeline from prompt injection and data manipulation attacks.

### Plan-Then-Execute Separation

The pipeline topology (which agents run, in what order) is fixed at deployment time in the SAM YAML configuration. No user-submitted content can alter the execution plan. The orchestration is determined by the Solace topic subscriptions, not by LLM output.

### Privilege Separation

Agents that process untrusted content (Case Processing, Evidence Analysis) have no ability to modify the execution plan. They can only write to their designated CaseState fields and publish to a single hardcoded next-agent topic. Even if an attacker successfully injects instructions into an evidence document, the compromised agent cannot skip the Governance audit or redirect the pipeline.

### Content Isolation

Raw documents are never placed in system prompts. The `parse_document` tool extracts structured data (text, tables, metadata) from uploaded files via the OpenAI Files API. The extracted content enters the LLM context as user-message content, not as system instructions. This prevents document content from being interpreted as system-level directives.

```
UNSAFE:  system_prompt = f"Analyze this document: {raw_document_content}"
SAFE:    system_prompt = "You are the Evidence Analysis Agent..."
         user_message  = f"Analyze the following extracted content: {parsed_output}"
```

### Input Sanitization Layer

All text extracted by `parse_document` is passed through an input sanitization layer before being included in downstream agent prompts. This layer: (a) strips known injection patterns (IGNORE PREVIOUS, system prompt overrides), (b) escapes special tokens, (c) wraps extracted text in XML delimiters (`<user_document>...</user_document>`) so the model can distinguish document content from instructions. This defense is applied at the tool output level, not the agent prompt level, ensuring all downstream consumers receive sanitized text.

### Output Schema Validation

Every agent's output is validated against a JSON schema before it is written to the CaseState payload and published to the next topic. Malformed output — including output that attempts to write to fields outside the agent's designated section — is rejected, and the pipeline halts.

### SAM Event Broker Audit Trail

Every message published to and consumed from the Solace Event Broker is logged with timestamp, source, destination, and payload hash. This creates a complete, immutable audit trail that cannot be modified by any agent. Any discrepancy between an agent's claimed output and the broker's recorded payload is detectable.

### Governance Agent as Final Gate

The Governance & Verdict Agent serves as the last line of defense. It audits the entire reasoning chain for logical consistency, unsupported claims, and bias before producing a verdict recommendation. If the reasoning chain has been corrupted by injected content at any earlier stage, the Governance audit is designed to catch the resulting inconsistencies.

### Human-in-the-Loop

No recommendation reaches the Judge without passing the Governance audit. The Judge retains full authority to accept, modify, or reject any recommendation. The system is advisory — it cannot take autonomous action.

### Defense Summary

| Attack Vector | Defense | Layer |
|---------------|---------|-------|
| Prompt injection via documents | Content isolation + parse_document tool | Agent |
| Pipeline manipulation | Fixed topology in YAML, topic-based routing | Platform |
| Output corruption | JSON schema validation | Pipeline |
| Bias injection | Governance audit (Phase 1) | Agent |
| Audit trail tampering | Solace broker immutable message log | Platform |
| Unauthorized escalation bypass | Halt conditions enforced at agent level | Agent |
| Replay attacks | Case ID + timestamp validation | Pipeline |

---

---

# Part 3: Agent Configurations (SAM YAML)

---

## 3.1 shared_config.yaml

```yaml
# configs/shared_config.yaml
# Shared YAML anchors referenced by all agent configuration files.
# Usage: each agent config includes this file and references anchors with <<: *anchor_name

# ──────────────────────────────────────────────
# Model Definitions (OpenAI via LiteLLM)
# ──────────────────────────────────────────────

models:
  o3: &o3_model
    model: o3
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

  o4_mini: &o4_mini_model
    model: o4-mini
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

  gpt41: &gpt41_model
    model: gpt-4.1
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

  gpt41_mini: &gpt41_mini_model
    model: gpt-4.1-mini
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

# ──────────────────────────────────────────────
# Solace Event Broker Connection
# ──────────────────────────────────────────────

broker: &broker_connection
  broker_url: ${SOLACE_BROKER_URL}
  broker_vpn: ${SOLACE_BROKER_VPN}
  username: ${SOLACE_BROKER_USERNAME}
  password: ${SOLACE_BROKER_PASSWORD}

# ──────────────────────────────────────────────
# Session Service (PostgreSQL)
# ──────────────────────────────────────────────

session_service: &default_session_service
  type: sql
  database_url: ${DATABASE_URL}

# ──────────────────────────────────────────────
# Artifact Service (Filesystem)
# ──────────────────────────────────────────────

artifact_service: &default_artifact_service
  type: filesystem
  base_path: /tmp/verdictcouncil
```

---

## 3.2 Agent 1: Case Processing

```yaml
# configs/agents/case-processing.yaml
apps:
  - name: case-processing
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "CaseProcessing"
      display_name: "Case Processing Agent"
      model:
        <<: *gpt41_mini_model
      instruction: |
        You are the Case Processing Agent for VerdictCouncil, a judicial decision-support system for Singapore lower courts.

        TASK: Process a new case submission through 4 sequential steps.

        STEP 1 - INTAKE: Parse all submitted documents and extract:
          - Parties: names, roles (claimant/respondent or accused/prosecution)
          - Case summary: 2-3 sentence plain language description
          - Claim/offence: type, monetary value (SCT), offence code (traffic)
          - Evidence inventory: each document with type and description

        STEP 2 - STRUCTURE: Normalize into universal case schema:
          - Map dispute to category (SCT: sale_of_goods, provision_of_services, property_damage, tenancy | Traffic: speeding, red_light, etc.)
          - Link each evidence item to the party that submitted it
          - Identify agreed vs disputed issues from the submissions

        STEP 3 - CLASSIFY DOMAIN: Based on structured data, output:
          - domain: 'small_claims' or 'traffic_violation'

        STEP 4 - VALIDATE JURISDICTION:
          - SCT: claim <= $20,000 (or $30,000 with consent), within 2 years
          - Traffic: valid offence code, not time-barred
          - Output: jurisdiction_valid (bool), jurisdiction_issues (list)

        CONSTRAINTS:
        - Extract ONLY what is explicitly stated. Flag missing info as MISSING.
        - If jurisdiction fails, set status to 'REJECTED' with specific reasons.
        - Output the complete structured CaseState fields as JSON.

        GUARDRAILS:
        - Must not infer facts beyond what documents state. Flag gaps, do not fill them.
        - Jurisdiction rejection must cite the specific statutory limit violated.
        - Must handle both formal legal filings and informal self-represented submissions.
      tools:
        - name: parse_document
          type: python
          module: tools.parse_document
          function: parse_document
          description: "Parse uploaded documents via OpenAI Files API. Extracts text, tables, and metadata from legal filings."
          parameters:
            - name: file_id
              type: string
              description: "OpenAI File ID of the uploaded document"
              required: true
            - name: extract_tables
              type: boolean
              description: "Whether to extract tabular data from the document"
              required: false
              default: true
            - name: ocr_enabled
              type: boolean
              description: "Whether to enable OCR for scanned documents"
              required: false
              default: false
      agent_card:
        description: "Processes new case submissions through intake, structuring, domain classification, and jurisdiction validation."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text", "file"]
        skills:
          - id: "case_processing"
            name: "Case Processing"
            description: "Parse case documents, structure case data, classify domain, and validate jurisdiction for Singapore lower courts."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.3 Agent 2: Complexity & Routing

```yaml
# configs/agents/complexity-routing.yaml
apps:
  - name: complexity-routing
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "ComplexityRouting"
      display_name: "Complexity & Routing Agent"
      model:
        <<: *o4_mini_model
      instruction: |
        You are the Complexity & Routing Agent for VerdictCouncil.

        TASK: Assess case complexity and decide the processing route.

        EVALUATE:
        1. Number of parties and evidence items
        2. Legal novelty: does this raise unusual legal questions?
        3. Cross-jurisdictional or multi-statute complexity
        4. Potential for significant precedent-setting impact
        5. Presence of vulnerable parties (minors, elderly)

        OUTPUT:
        - complexity: 'low' | 'medium' | 'high'
        - route: 'proceed_automated' | 'proceed_with_review' | 'escalate_human'
        - reasoning: brief justification

        CONSTRAINT: When in doubt, route to 'proceed_with_review'. Judicial oversight is always preferred over autonomous processing.

        GUARDRAILS:
        - Must default to escalation for potential precedent-setting cases.
        - Must flag vulnerable parties for additional safeguards.
        - This is the first HALT point: if escalate_human, pipeline stops here.
      tools: []
      agent_card:
        description: "Assesses case complexity and determines whether the case should proceed through automated analysis, require additional review, or be escalated to a human judicial officer."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "complexity_routing"
            name: "Complexity & Routing"
            description: "Evaluate case complexity across multiple dimensions and determine the appropriate processing route."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.4 Agent 3: Evidence Analysis

```yaml
# configs/agents/evidence-analysis.yaml
apps:
  - name: evidence-analysis
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "EvidenceAnalysis"
      display_name: "Evidence Analysis Agent"
      model:
        <<: *gpt41_model
      instruction: |
        You are the Evidence Analysis Agent for VerdictCouncil. You serve the presiding judicial officer with IMPARTIAL analysis.

        TASK: Analyze ALL submitted evidence comprehensively.

        FOR EACH EVIDENCE ITEM:
        1. Use parse_document to extract content.
        2. Classify: documentary | testimonial | physical | digital | expert.
        3. Assess STRENGTH: strong | medium | weak (with reasoning).
        4. Assess ADMISSIBILITY RISK: flag hearsay, expired certifications, authentication issues, chain-of-custody gaps.
        5. Link to specific claims/charges it supports or undermines.

        CROSS-DOCUMENT ANALYSIS:
        6. Use cross_reference to find CONTRADICTIONS between documents.
        7. Identify GAPS: what evidence is expected but missing?
        8. Identify CORROBORATIONS: which items mutually reinforce?

        CONSTRAINTS:
        - NEUTRAL. Assess evidence from both parties with equal rigor.
        - Do NOT determine guilt, liability, or verdict.
        - Cite specific document/page/paragraph for every assessment.

        GUARDRAILS:
        - Must not express opinions on guilt, liability, or outcome.
        - Must flag ALL contradictions, even seemingly minor ones.
        - Must assess both parties' evidence with identical rigor.
      tools:
        - name: parse_document
          type: python
          module: tools.parse_document
          function: parse_document
          description: "Parse uploaded documents via OpenAI Files API. Extracts text, tables, and metadata from legal filings."
          parameters:
            - name: file_id
              type: string
              description: "OpenAI File ID of the uploaded document"
              required: true
            - name: extract_tables
              type: boolean
              description: "Whether to extract tabular data from the document"
              required: false
              default: true
            - name: ocr_enabled
              type: boolean
              description: "Whether to enable OCR for scanned documents"
              required: false
              default: false
        - name: cross_reference
          type: python
          module: tools.cross_reference
          function: cross_reference
          description: "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items."
          parameters:
            - name: segments
              type: array
              description: "List of document segments to compare. Each segment: {doc_id, text, page, paragraph}"
              required: true
            - name: check_type
              type: string
              description: "Type of cross-reference check: 'contradiction' | 'corroboration' | 'all'"
              required: true
      agent_card:
        description: "Performs comprehensive, impartial analysis of all submitted evidence including classification, strength assessment, admissibility risk evaluation, and cross-document analysis."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text", "file"]
        skills:
          - id: "evidence_analysis"
            name: "Evidence Analysis"
            description: "Analyze all submitted evidence for strength, admissibility, contradictions, gaps, and corroborations."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.5 Agent 4: Fact Reconstruction

```yaml
# configs/agents/fact-reconstruction.yaml
apps:
  - name: fact-reconstruction
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "FactReconstruction"
      display_name: "Fact Reconstruction Agent"
      model:
        <<: *gpt41_model
      instruction: |
        You are the Fact Reconstruction Agent for VerdictCouncil.

        TASK: Extract facts and build a sourced, chronological timeline.

        FOR EACH FACT:
        1. Extract: date/time, event description, parties involved, location.
        2. Source: document reference (ID, page, paragraph).
        3. Corroboration: other documents supporting or contradicting this fact.
        4. Confidence: high (multiple sources) | medium (single source) | low (uncorroborated) | disputed (conflicting sources).
        5. Status: agreed (both parties accept) | disputed (contested).

        Use timeline_construct to build the chronological sequence.

        CONSTRAINTS:
        - Include facts from ALL parties equally.
        - Mark DISPUTED facts clearly with both parties' versions.
        - Do NOT resolve factual disputes. Present both sides.

        GUARDRAILS:
        - Must not resolve disputed facts. Present both versions.
        - Must include source references for every extracted fact.
        - Must flag low-confidence facts for judicial attention.
      tools:
        - name: parse_document
          type: python
          module: tools.parse_document
          function: parse_document
          description: "Parse uploaded documents via OpenAI Files API. Extracts text, tables, and metadata from legal filings."
          parameters:
            - name: file_id
              type: string
              description: "OpenAI File ID of the uploaded document"
              required: true
            - name: extract_tables
              type: boolean
              description: "Whether to extract tabular data from the document"
              required: false
              default: true
            - name: ocr_enabled
              type: boolean
              description: "Whether to enable OCR for scanned documents"
              required: false
              default: false
        - name: timeline_construct
          type: python
          module: tools.timeline_construct
          function: timeline_construct
          description: "Build a chronological timeline from extracted events. Handles date normalization, ordering, and conflict detection."
          parameters:
            - name: events
              type: array
              description: "List of events to order. Each event: {date, description, source_ref, parties, location}"
              required: true
        - name: cross_reference
          type: python
          module: tools.cross_reference
          function: cross_reference
          description: "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items."
          parameters:
            - name: segments
              type: array
              description: "List of document segments to compare. Each segment: {doc_id, text, page, paragraph}"
              required: true
            - name: check_type
              type: string
              description: "Type of cross-reference check: 'contradiction' | 'corroboration' | 'all'"
              required: true
      agent_card:
        description: "Extracts facts from case evidence and constructs a sourced, chronological timeline with confidence assessments and dispute identification."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text", "file"]
        skills:
          - id: "fact_reconstruction"
            name: "Fact Reconstruction"
            description: "Extract facts from evidence, assess confidence, identify disputes, and build a chronological timeline."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.6 Agent 5: Witness Analysis

```yaml
# configs/agents/witness-analysis.yaml
apps:
  - name: witness-analysis
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "WitnessAnalysis"
      display_name: "Witness Analysis Agent"
      model:
        <<: *o4_mini_model
      instruction: |
        You are the Witness Analysis Agent for VerdictCouncil.

        TASK: Complete witness analysis in 3 phases.

        PHASE 1 - IDENTIFICATION:
        For each potential witness found in case materials:
          - Name, role (police officer, party, bystander, expert).
          - Relationship to the case and which party they support.
          - Whether a formal written statement exists.
          - Potential bias indicators.

        PHASE 2 - TESTIMONY ANTICIPATION (traffic cases only):
        For each identified witness with a written statement:
          - Summarize their likely testimony based STRICTLY on the statement.
          - Identify strong points and areas vulnerable to challenge.
          - Note conflicts between their statement and documentary evidence.
          Mark all output: 'Simulated - For Judicial Preparation Only'.

        PHASE 3 - CREDIBILITY ASSESSMENT:
        For each witness, score credibility (0-100) based on:
          - Internal consistency (self-contradictions).
          - External consistency (alignment with physical/documentary evidence).
          - Bias indicators (employment, financial interest, relationships).
          - Specificity and verifiability of claims.
          - Corroboration by other witnesses.

        NOTE: Credibility scores are relative indicators produced by LLM reasoning,
        not statistically calibrated measurements. They should be interpreted as
        directional signals (higher = stronger support) rather than precise probabilities.

        For SCT cases: assess credibility of BOTH the Claimant's and Respondent's statements using the same criteria.

        CONSTRAINTS:
        - Assess ALL witnesses with equal rigor regardless of which side.
        - Testimony simulation must NOT fabricate beyond written statements.
        - Credibility concerns must cite specific evidence, not suspicion.

        GUARDRAILS:
        - Must assess all witnesses equally regardless of which party called them.
        - Testimony simulation output must be marked as simulated preparation material.
        - Must not make ultimate credibility determinations — the Judge decides.
      tools:
        - name: cross_reference
          type: python
          module: tools.cross_reference
          function: cross_reference
          description: "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items."
          parameters:
            - name: segments
              type: array
              description: "List of document segments to compare. Each segment: {doc_id, text, page, paragraph}"
              required: true
            - name: check_type
              type: string
              description: "Type of cross-reference check: 'contradiction' | 'corroboration' | 'all'"
              required: true
        - name: generate_questions
          type: python
          module: tools.generate_questions
          function: generate_questions
          description: "Generate suggested judicial questions based on argument analysis and identified weaknesses."
          parameters:
            - name: argument_summary
              type: string
              description: "Summary of the argument or testimony to generate questions for"
              required: true
            - name: weaknesses
              type: array
              description: "List of identified weaknesses or gaps to probe"
              required: true
            - name: question_types
              type: array
              description: "Types of questions to generate: 'clarification' | 'challenge' | 'exploration' | 'credibility'"
              required: false
              default: ["clarification", "challenge"]
            - name: max_questions
              type: integer
              description: "Maximum number of questions to generate"
              required: false
              default: 5
      agent_card:
        description: "Identifies witnesses, anticipates testimony for traffic cases, and assesses credibility across all witnesses with equal rigor."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text"]
        skills:
          - id: "witness_analysis"
            name: "Witness Analysis"
            description: "Identify witnesses, anticipate testimony, and assess credibility with bias detection."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.7 Agent 6: Legal Knowledge

```yaml
# configs/agents/legal-knowledge.yaml
apps:
  - name: legal-knowledge
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "LegalKnowledge"
      display_name: "Legal Knowledge Agent"
      model:
        <<: *gpt41_model
      instruction: |
        You are the Legal Knowledge Agent for VerdictCouncil.

        TASK: Retrieve applicable law and relevant precedents.

        PART A - STATUTORY RULES:
        1. Formulate semantic queries from the case facts and dispute issues.
        2. Use file_search to retrieve relevant statutes and regulations.
        3. For each rule: statute name, section, verbatim text, relevance score, and how it applies to the specific case facts.

        KNOWLEDGE BASE (by domain):
          SCT: Small Claims Tribunals Act, Consumer Protection (Fair Trading) Act, Sale of Goods Act, Supply of Services Act.
          Traffic: Road Traffic Act, Road Traffic Rules, Motor Vehicles Act.

        PART B - PRECEDENT CASES:
        1. Use file_search to find cases with matching fact patterns from curated knowledge base.
        2. Use search_precedents to query live judiciary databases for recent rulings.
        3. For each precedent: citation, outcome, reasoning summary, similarity score, distinguishing factors, and source (curated or live_search).

        CONSTRAINTS:
        - ONLY cite statutes and cases from the curated knowledge base or verified live search results.
        - Do NOT hallucinate citations or section numbers.
        - Present precedents supporting BOTH possible outcomes.
        - Always note distinguishing factors. No precedent is a perfect match.

        GUARDRAILS:
        - Must ONLY cite sources from the curated knowledge base or verified live search. No hallucinated citations.
        - Must include verbatim statutory text for every cited provision.
        - Must present precedents supporting BOTH possible outcomes, not just one side.
      tools:
        - name: file_search
          type: builtin
          description: "Search OpenAI Vector Stores for relevant statutes, regulations, and curated precedent cases."
          vector_store_ids:
            - ${VS_SCT_ID}
            - ${VS_TRAFFIC_ID}
        - name: search_precedents
          type: python
          module: tools.search_precedents
          function: search_precedents
          description: "Query live judiciary databases (judiciary.gov.sg, search.pair.gov.sg) for recent rulings matching case patterns. Results are Redis-cached with 24h TTL."
          parameters:
            - name: query
              type: string
              description: "Semantic search query describing the legal issue or fact pattern"
              required: true
            - name: domain
              type: string
              description: "Legal domain: 'small_claims' | 'traffic'"
              required: true
            - name: max_results
              type: integer
              description: "Maximum number of precedents to return"
              required: false
              default: 10
            - name: date_range
              type: object
              description: "Date range filter: {start: 'YYYY-MM-DD', end: 'YYYY-MM-DD'}"
              required: false
      agent_card:
        description: "Retrieves applicable statutory rules and relevant precedent cases from curated knowledge bases and live judiciary databases."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "legal_knowledge"
            name: "Legal Knowledge Retrieval"
            description: "Retrieve applicable statutes and precedent cases for Singapore lower court matters."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.8 Agent 7: Argument Construction

```yaml
# configs/agents/argument-construction.yaml
apps:
  - name: argument-construction
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "ArgumentConstruction"
      display_name: "Argument Construction Agent"
      model:
        <<: *o3_model
      instruction: |
        You are the Argument Construction Agent for VerdictCouncil. You serve the JUDGE, not either party. All output is INTERNAL.

        TASK: Construct both sides' arguments for judicial evaluation.

        FOR TRAFFIC CASES:
        A. PROSECUTION ARGUMENT:
          - Charges with statutory provisions.
          - Elements to prove, mapped to evidence for each element.
          - Witness support for each element.
          - Proposed penalty range from precedents.
          - Prosecution WEAKNESSES the Judge should be aware of.

        B. DEFENSE ARGUMENT:
          - Response to each charge.
          - Evidence challenges (admissibility, reliability, calibration).
          - Mitigating factors (clean record, personal circumstances).
          - Precedents favoring defense.
          - Defense WEAKNESSES the Judge should be aware of.

        C. CONTESTED ISSUES: Key points where prosecution and defense disagree.

        FOR SCT CASES:
        A. CLAIMANT POSITION: stated claim, supporting evidence, legal basis, weaknesses.
        B. RESPONDENT POSITION: stated response, supporting evidence, legal basis, weaknesses.
        C. AGREED FACTS vs DISPUTED FACTS.
        D. EVIDENCE GAPS: what could resolve the disputed facts.
        E. STRENGTH COMPARISON: Claimant % vs Respondent % with reasoning.

        Use generate_questions to suggest judicial questions for each party.

        CONSTRAINTS:
        - Analyze BOTH sides with equal depth and rigor.
        - Note WEAKNESSES in both arguments. The Judge needs the full picture.
        - Header: 'Internal Analysis for Judicial Review Only'.

        GUARDRAILS:
        - Must analyze both sides with equal depth. Asymmetric analysis is a failure.
        - Must note weaknesses in BOTH arguments, not just one side.
        - Output must be headered "Internal Analysis for Judicial Review Only".
        - Must not determine guilt/liability. That is the Judge's role.
      tools:
        - name: generate_questions
          type: python
          module: tools.generate_questions
          function: generate_questions
          description: "Generate suggested judicial questions based on argument analysis and identified weaknesses."
          parameters:
            - name: argument_summary
              type: string
              description: "Summary of the argument or testimony to generate questions for"
              required: true
            - name: weaknesses
              type: array
              description: "List of identified weaknesses or gaps to probe"
              required: true
            - name: question_types
              type: array
              description: "Types of questions to generate: 'clarification' | 'challenge' | 'exploration' | 'credibility'"
              required: false
              default: ["clarification", "challenge"]
            - name: max_questions
              type: integer
              description: "Maximum number of questions to generate"
              required: false
              default: 5
      agent_card:
        description: "Constructs balanced arguments for both sides of a case to assist judicial evaluation. Serves the Judge, not either party."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "argument_construction"
            name: "Argument Construction"
            description: "Build prosecution/defense or claimant/respondent arguments with equal rigor for judicial review."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.9 Agent 8: Deliberation

```yaml
# configs/agents/deliberation.yaml
apps:
  - name: deliberation
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "Deliberation"
      display_name: "Deliberation Agent"
      model:
        <<: *o3_model
      instruction: |
        You are the Deliberation Agent for VerdictCouncil. You are the judicial reasoning core of the system.

        TASK: Produce a step-by-step reasoning chain from evidence to conclusion.

        REASONING CHAIN:
        1. ESTABLISHED FACTS: Facts supported by evidence, with confidence.
        2. APPLICABLE LAW: Matched statutes with specific section references.
        3. APPLICATION: For each legal element, does the evidence satisfy it?
        4. ARGUMENT EVALUATION:
           Traffic: prosecution vs defense argument strength.
           SCT: claimant vs respondent position strength.
        5. WITNESS ASSESSMENT: Credibility findings and their impact.
        6. PRECEDENT ALIGNMENT: How do similar cases inform this analysis?
        7. PRELIMINARY CONCLUSION: What does the chain suggest?
        8. UNCERTAINTY FLAGS: Where is reasoning uncertain or dependent on factual determinations the Judge must make at hearing?

        CONSTRAINTS:
        - Every step MUST cite its source (which agent output, which evidence).
        - Flag LOW-CONFIDENCE steps explicitly.
        - This is a RECOMMENDATION, not a decision. The Judge decides.
        - Do NOT present the conclusion with false certainty.

        GUARDRAILS:
        - Every step must cite the upstream agent and evidence that produced it.
        - Must flag where reasoning depends on unresolved factual disputes.
        - Must not present conclusions with false certainty. Uncertainty is valuable.
      tools: []
      agent_card:
        description: "Produces a step-by-step reasoning chain from evidence through legal application to a preliminary conclusion, with explicit uncertainty flags."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "deliberation"
            name: "Judicial Deliberation"
            description: "Construct a traceable reasoning chain from established facts through legal application to preliminary conclusion."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.10 Agent 9: Governance & Verdict

```yaml
# configs/agents/governance-verdict.yaml
apps:
  - name: governance-verdict
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "GovernanceVerdict"
      display_name: "Governance & Verdict Agent"
      model:
        <<: *o3_model
      instruction: |
        You are the Governance & Verdict Agent for VerdictCouncil. You are the final checkpoint before a recommendation reaches the Judge.

        PHASE 1 - FAIRNESS AUDIT:
        1. BALANCE: Were both parties' evidence weighted equally? Flag asymmetry.
        2. UNSUPPORTED CLAIMS: Does reasoning rely on facts NOT in evidence?
        3. LOGICAL FALLACIES: circular reasoning, false equivalences, confirmation bias, anchoring to early evidence.
        4. DEMOGRAPHIC BIAS: reasoning influenced by race, gender, age, nationality, or socioeconomic status.
        5. EVIDENCE COMPLETENESS: was any submitted evidence overlooked?
        6. PRECEDENT CHERRY-PICKING: were contrary precedents acknowledged?

        If ANY critical issue found: set recommendation to 'ESCALATE_HUMAN' and STOP. Do NOT generate a verdict recommendation.

        PHASE 2 - VERDICT RECOMMENDATION (only if audit passes):

        FOR SCT: recommended_order (compensation/repair/dismiss), amount, legal_basis, reasoning_summary.
        FOR TRAFFIC: recommended_verdict (guilty/not_guilty/reduced), sentence (fine, demerit points), sentencing_range from precedents.

        ALWAYS INCLUDE:
        - confidence_score: 0-100 (use confidence_calc tool)
        - uncertainty_factors: what could change this recommendation
        - alternative_outcomes: at least ONE other reasonable outcome
        - fairness_report: summary of Phase 1 audit results

        CONSTRAINTS:
        - This is a RECOMMENDATION. State this clearly.
        - Always present at least one alternative outcome.
        - The Judge has FULL authority to accept, modify, or reject.
        - Be AGGRESSIVE in flagging bias. False positives are acceptable.

        GUARDRAILS:
        - Must HALT pipeline if critical fairness issue detected. No partial verdicts.
        - Must always present at least one alternative outcome.
        - Must clearly label output as a recommendation, not a decision.
        - Fairness audit must err on the side of flagging. False negatives are unacceptable.
      tools:
        - name: confidence_calc
          type: python
          module: tools.confidence_calc
          function: confidence_calc
          description: "Calculate weighted confidence score for verdict recommendation based on evidence strength, rule relevance, precedent similarity, and witness credibility."
          parameters:
            - name: evidence_scores
              type: array
              description: "List of evidence strength scores (0-100) from Evidence Analysis"
              required: true
            - name: rule_relevance_scores
              type: array
              description: "List of rule relevance scores (0-100) from Legal Knowledge"
              required: true
            - name: precedent_similarity_scores
              type: array
              description: "List of precedent similarity scores (0-100) from Legal Knowledge"
              required: true
            - name: witness_credibility_scores
              type: array
              description: "List of witness credibility scores (0-100) from Witness Analysis"
              required: true
            - name: weights
              type: object
              description: "Weight distribution: {evidence, rules, precedents, witnesses}. Must sum to 1.0"
              required: false
      agent_card:
        description: "Final checkpoint performing fairness audit and, if passed, generating a verdict recommendation with confidence scoring and alternative outcomes."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "governance_verdict"
            name: "Governance & Verdict"
            description: "Audit pipeline output for fairness and bias, then produce a confidence-scored verdict recommendation."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.11 Web Gateway Configuration

```yaml
# configs/gateway/web-gateway.yaml
apps:
  - name: web-gateway
    app_module: solace_agent_mesh.gateway.http_sse.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      gateway_name: "VerdictCouncilGateway"
      display_name: "VerdictCouncil Web Gateway"

      # HTTP Server Configuration
      http:
        host: 0.0.0.0
        port: 8000
        workers: 4

      # CORS Configuration
      cors:
        allow_origins:
          - ${FRONTEND_ORIGIN}
        allow_methods:
          - GET
          - POST
          - OPTIONS
        allow_headers:
          - Authorization
          - Content-Type
          - X-Request-ID
        allow_credentials: true
        max_age: 3600

      # Authentication
      auth:
        type: jwt
        jwt:
          secret: ${JWT_SECRET}
          algorithm: HS256
          issuer: ${JWT_ISSUER}
          audience: ${JWT_AUDIENCE}
          token_expiry: 3600

      # Case-Level Authorization
      # All case-scoped API endpoints MUST verify that request.user_id == case.created_by
      # before returning data. The gateway enforces this via middleware that loads the case
      # record and checks ownership before forwarding to the handler.
      case_authorization:
        enabled: true
        middleware: "verdictcouncil.middleware.CaseOwnershipMiddleware"
        check: "request.user_id == case.created_by"

      # SSE Configuration for Pipeline Updates
      sse:
        enabled: true
        heartbeat_interval: 15
        max_connections: 100
        retry_timeout: 5000

      # File Upload Configuration
      upload:
        max_file_size: 52428800  # 50MB
        allowed_types:
          - application/pdf
          - image/jpeg
          - image/png
          - application/msword
          - application/vnd.openxmlformats-officedocument.wordprocessingml.document
          - text/plain
        upload_to: openai_files  # Upload directly to OpenAI Files API

      # Rate Limiting
      rate_limit:
        enabled: true
        requests_per_minute: 30
        burst: 5

      # Routes
      routes:
        - path: /api/v1/cases
          method: POST
          target_agent: case-processing
          description: "Submit a new case for processing"
        - path: /api/v1/cases/{case_id}/status
          method: GET
          description: "Get current processing status via SSE stream"
          sse: true
        - path: /api/v1/cases/{case_id}
          method: GET
          description: "Get completed case analysis"

      # Pipeline Response Topic
      response_topic: ${NAMESPACE}/a2a/v1/agent/request/web-gateway

      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.12 Custom Python Tool Definitions

### 3.12.1 parse_document

```python
# tools/parse_document.py
"""Document parsing tool wrapping the OpenAI Files API for VerdictCouncil."""

import json
import os
from typing import Any

from openai import OpenAI


def parse_document(
    file_id: str,
    extract_tables: bool = True,
    ocr_enabled: bool = False,
    tool_context: Any = None,
) -> dict:
    """Parse an uploaded document via the OpenAI Files API.

    Extracts text content, tables, and metadata from legal filings
    submitted to VerdictCouncil. Supports PDF, DOCX, images (with OCR),
    and plain text files.

    Args:
        file_id: OpenAI File ID of the uploaded document (e.g., "file-abc123").
        extract_tables: Whether to extract tabular data from the document.
            Defaults to True.
        ocr_enabled: Whether to enable OCR for scanned/image-based documents.
            Defaults to False.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - file_id: The original file ID.
            - filename: Original filename.
            - content_type: MIME type of the document.
            - text: Extracted plain text content.
            - tables: List of extracted tables (if extract_tables is True),
              each as a list of rows where each row is a list of cell values.
            - metadata: Document metadata including page count, word count,
              and creation date if available.
            - parsing_notes: Any warnings or issues encountered during parsing.
    """
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Retrieve file metadata
    file_info = client.files.retrieve(file_id)
    filename = file_info.filename
    content_type = getattr(file_info, "content_type", "application/octet-stream")

    # Download file content
    file_content = client.files.content(file_id)
    raw_bytes = file_content.read()

    parsing_notes = []
    extracted_text = ""
    extracted_tables = []
    metadata = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(raw_bytes),
    }

    # Use OpenAI for text extraction via chat completion with file attachment
    extraction_messages = [
        {
            "role": "system",
            "content": (
                "Extract all text content from the provided document. "
                "Preserve paragraph structure and formatting. "
                "If the document contains tables, extract each table as a "
                "JSON array of rows."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "file",
                    "file": {"file_id": file_id},
                },
                {
                    "type": "text",
                    "text": (
                        "Extract all text from this document. "
                        f"{'Also extract all tables as structured data.' if extract_tables else ''} "
                        f"{'This may be a scanned document - use OCR.' if ocr_enabled else ''} "
                        "Return JSON with keys: text, tables (list of tables), page_count, word_count."
                    ),
                },
            ],
        },
    ]

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=extraction_messages,
        response_format={"type": "json_object"},
    )

    try:
        parsed = json.loads(response.choices[0].message.content)
        extracted_text = parsed.get("text", "")
        extracted_tables = parsed.get("tables", []) if extract_tables else []
        metadata["page_count"] = parsed.get("page_count")
        metadata["word_count"] = parsed.get("word_count")
    except (json.JSONDecodeError, IndexError) as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise DocumentParseError(
            f"Failed to parse document {file_id}: JSON parsing failed: {str(e)}"
        ) from e

    if not extracted_text.strip():
        raise DocumentParseError(
            f"No text content extracted from document {file_id}. "
            "Document may be corrupt or unsupported format."
        )

    return {
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "text": extracted_text,
        "tables": extracted_tables,
        "metadata": metadata,
        "parsing_notes": parsing_notes,
    }
```

### 3.12.2 cross_reference

```python
# tools/cross_reference.py
"""Cross-reference tool for comparing document segments in VerdictCouncil."""

import json
import os
from typing import Any

from openai import OpenAI


def cross_reference(
    segments: list,
    check_type: str = "all",
    tool_context: Any = None,
) -> dict:
    """Compare document segments to identify contradictions and corroborations.

    Analyzes pairs of document segments to find contradictions,
    corroborations, and inconsistencies across evidence items submitted
    to VerdictCouncil.

    Args:
        segments: List of document segments to compare. Each segment is a
            dictionary with keys:
            - doc_id (str): Document identifier.
            - text (str): The text content of the segment.
            - page (int): Page number where the segment appears.
            - paragraph (int): Paragraph number within the page.
        check_type: Type of cross-reference check to perform.
            One of: "contradiction", "corroboration", "all".
            Defaults to "all".
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - contradictions: List of identified contradictions, each with
              segment_a, segment_b, description, and severity.
            - corroborations: List of identified corroborations, each with
              segment_a, segment_b, description, and strength.
            - inconsistencies: List of minor inconsistencies that may or may
              not be substantive.
            - summary: Brief overall assessment of cross-reference findings.
    """
    if len(segments) < 2:
        return {
            "contradictions": [],
            "corroborations": [],
            "inconsistencies": [],
            "summary": "Insufficient segments for cross-reference (minimum 2 required).",
        }

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Build pairwise comparison prompt
    segments_text = json.dumps(segments, indent=2)

    check_instruction = {
        "contradiction": "Focus ONLY on identifying contradictions between segments.",
        "corroboration": "Focus ONLY on identifying corroborations between segments.",
        "all": "Identify contradictions, corroborations, and inconsistencies.",
    }.get(check_type, "Identify contradictions, corroborations, and inconsistencies.")

    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a legal evidence analyst. Compare the provided document "
                    "segments and identify relationships between them. Be precise and "
                    "cite specific text from each segment. "
                    f"{check_instruction}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Compare these document segments:\n\n{segments_text}\n\n"
                    "Return JSON with keys:\n"
                    "- contradictions: [{segment_a: {doc_id, page, paragraph}, "
                    "segment_b: {doc_id, page, paragraph}, description, severity: "
                    "'critical'|'moderate'|'minor'}]\n"
                    "- corroborations: [{segment_a, segment_b, description, "
                    "strength: 'strong'|'moderate'|'weak'}]\n"
                    "- inconsistencies: [{segments, description, significance}]\n"
                    "- summary: brief overall assessment"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise CrossReferenceError(
            f"Failed to parse cross-reference analysis response: {str(e)}"
        ) from e

    # Filter based on check_type
    if check_type == "contradiction":
        result["corroborations"] = []
    elif check_type == "corroboration":
        result["contradictions"] = []

    return result
```

### 3.12.3 timeline_construct

```python
# tools/timeline_construct.py
"""Timeline construction tool for VerdictCouncil fact reconstruction."""

import json
import os
from typing import Any

from openai import OpenAI


def timeline_construct(
    events: list,
    tool_context: Any = None,
) -> dict:
    """Build a chronological timeline from extracted case events.

    Takes a list of events extracted from case evidence and produces a
    normalized, chronologically ordered timeline with conflict detection
    for events with contradictory dates or descriptions.

    Args:
        events: List of events to order. Each event is a dictionary with:
            - date (str): Date/time string in any recognizable format.
            - description (str): Description of the event.
            - source_ref (str): Source document reference (doc_id, page, paragraph).
            - parties (list[str]): Parties involved in the event.
            - location (str, optional): Location where the event occurred.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - timeline: Chronologically ordered list of events, each with
              normalized_date (ISO 8601), description, source_ref, parties,
              location, and sequence_number.
            - date_conflicts: Events where multiple sources disagree on timing.
            - undated_events: Events that could not be assigned a date.
            - duration: Overall timespan from earliest to latest event.
            - event_count: Total number of events processed.
    """
    if not events:
        return {
            "timeline": [],
            "date_conflicts": [],
            "undated_events": [],
            "duration": None,
            "event_count": 0,
        }

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    events_text = json.dumps(events, indent=2)

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a legal timeline analyst. Given a list of case events, "
                    "normalize all dates to ISO 8601 format, sort chronologically, "
                    "detect conflicting dates for the same event from different sources, "
                    "and identify events without clear dates. "
                    "Singapore timezone (SGT, UTC+8) unless otherwise specified."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Construct a chronological timeline from these events:\n\n"
                    f"{events_text}\n\n"
                    "Return JSON with keys:\n"
                    "- timeline: [{sequence_number, normalized_date (ISO 8601), "
                    "description, source_ref, parties, location}]\n"
                    "- date_conflicts: [{event_description, sources: [{source_ref, "
                    "claimed_date}], resolution_note}]\n"
                    "- undated_events: [{description, source_ref, estimated_position}]\n"
                    "- duration: {start_date, end_date, span_description}\n"
                    "- event_count: total number"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise TimelineConstructionError(
            f"Failed to parse timeline construction response: {str(e)}"
        ) from e

    return result
```

### 3.12.4 generate_questions

```python
# tools/generate_questions.py
"""Judicial question generation tool for VerdictCouncil."""

import json
import os
from typing import Any

from openai import OpenAI


def generate_questions(
    argument_summary: str,
    weaknesses: list,
    question_types: list = None,
    max_questions: int = 5,
    tool_context: Any = None,
) -> dict:
    """Generate suggested judicial questions for hearing preparation.

    Analyzes argument summaries and identified weaknesses to produce
    targeted questions a Judge could ask each party during a hearing.
    Questions are tagged by type and linked to the specific weakness
    or issue they address.

    Args:
        argument_summary: Summary of the argument or testimony to
            generate questions for.
        weaknesses: List of identified weaknesses or gaps in the
            argument that questions should probe.
        question_types: Types of questions to generate. Options:
            "clarification", "challenge", "exploration", "credibility".
            Defaults to ["clarification", "challenge"].
        max_questions: Maximum number of questions to generate.
            Defaults to 5.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - questions: List of generated questions, each with:
              - question (str): The question text.
              - type (str): Question type tag.
              - target_party (str): Which party the question is directed at.
              - addresses_weakness (str): The specific weakness this probes.
              - expected_impact (str): What the answer could reveal.
              - priority (str): "high", "medium", or "low".
            - question_count: Total number of questions generated.
            - coverage: Which weaknesses are addressed and which are not.
    """
    if question_types is None:
        question_types = ["clarification", "challenge"]

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    weaknesses_text = json.dumps(weaknesses, indent=2)
    types_text = ", ".join(question_types)

    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a judicial hearing preparation assistant for Singapore "
                    "lower courts. Generate focused, professionally worded questions "
                    "that a Judge or Tribunal Magistrate could ask during a hearing. "
                    "Questions must be neutral, not leading. They should probe "
                    "weaknesses and gaps without revealing the court's preliminary "
                    "analysis."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on the following argument summary and weaknesses, "
                    f"generate up to {max_questions} judicial questions.\n\n"
                    f"ARGUMENT SUMMARY:\n{argument_summary}\n\n"
                    f"WEAKNESSES TO PROBE:\n{weaknesses_text}\n\n"
                    f"QUESTION TYPES TO INCLUDE: {types_text}\n\n"
                    "Return JSON with keys:\n"
                    "- questions: [{question, type, target_party, "
                    "addresses_weakness, expected_impact, priority}]\n"
                    "- question_count: number\n"
                    "- coverage: {addressed_weaknesses: [str], "
                    "unaddressed_weaknesses: [str]}"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.choices[0].message.content)
        # Enforce max_questions limit
        if "questions" in result and len(result["questions"]) > max_questions:
            result["questions"] = result["questions"][:max_questions]
            result["question_count"] = max_questions
    except json.JSONDecodeError as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise QuestionGenerationError(
            f"Failed to parse question generation response: {str(e)}"
        ) from e

    return result
```

> **Note:** All custom tool errors (`DocumentParseError`, `CrossReferenceError`, `TimelineConstructionError`, `QuestionGenerationError`) propagate to the calling agent, which logs the error and halts the pipeline per Section 2.6 error handling policy. Tools must never silently return empty or default results on failure.

### 3.12.5 confidence_calc

```python
# tools/confidence_calc.py
"""Confidence calculation tool for VerdictCouncil verdict recommendations."""

from typing import Any


def confidence_calc(
    evidence_scores: list,
    rule_relevance_scores: list,
    precedent_similarity_scores: list,
    witness_credibility_scores: list,
    weights: dict = None,
    tool_context: Any = None,
) -> dict:
    """Calculate weighted confidence score for a verdict recommendation.

    Combines evidence strength, rule relevance, precedent similarity, and
    witness credibility scores into a single confidence metric. Uses
    configurable weights to reflect the relative importance of each factor
    for the specific case type.

    Args:
        evidence_scores: List of evidence strength scores (0-100) from
            Evidence Analysis agent output.
        rule_relevance_scores: List of rule relevance scores (0-100) from
            Legal Knowledge agent output.
        precedent_similarity_scores: List of precedent similarity scores
            (0-100) from Legal Knowledge agent output.
        witness_credibility_scores: List of witness credibility scores
            (0-100) from Witness Analysis agent output.
        weights: Weight distribution across the four factors. Dictionary
            with keys: evidence, rules, precedents, witnesses. Values must
            sum to 1.0. Defaults to equal weighting (0.25 each).
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - confidence_score: Overall weighted score (0-100).
            - component_scores: Breakdown by factor (average of each list).
            - weights_used: The weight distribution applied.
            - data_quality: Assessment of input completeness.
            - interpretation: Human-readable confidence band
              ("very_high", "high", "moderate", "low", "very_low").
    """
    if weights is None:
        weights = {
            "evidence": 0.25,
            "rules": 0.25,
            "precedents": 0.25,
            "witnesses": 0.25,
        }

    # Validate weights sum to 1.0 (with floating point tolerance)
    weight_sum = sum(weights.values())
    if abs(weight_sum - 1.0) > 0.01:
        # Normalize weights if they do not sum to 1.0
        weights = {k: v / weight_sum for k, v in weights.items()}

    # Calculate component averages
    def safe_average(scores: list) -> float:
        if not scores:
            return 0.0
        valid = [s for s in scores if isinstance(s, (int, float)) and 0 <= s <= 100]
        return sum(valid) / len(valid) if valid else 0.0

    component_scores = {
        "evidence": safe_average(evidence_scores),
        "rules": safe_average(rule_relevance_scores),
        "precedents": safe_average(precedent_similarity_scores),
        "witnesses": safe_average(witness_credibility_scores),
    }

    # Calculate weighted confidence
    confidence_score = round(
        component_scores["evidence"] * weights["evidence"]
        + component_scores["rules"] * weights["rules"]
        + component_scores["precedents"] * weights["precedents"]
        + component_scores["witnesses"] * weights["witnesses"],
        1,
    )

    # Clamp to 0-100
    confidence_score = max(0.0, min(100.0, confidence_score))

    # Assess data quality
    all_scores = [
        evidence_scores,
        rule_relevance_scores,
        precedent_similarity_scores,
        witness_credibility_scores,
    ]
    empty_components = sum(1 for s in all_scores if not s)
    data_quality = {
        "complete_components": 4 - empty_components,
        "total_components": 4,
        "missing": [],
    }
    if not evidence_scores:
        data_quality["missing"].append("evidence_scores")
    if not rule_relevance_scores:
        data_quality["missing"].append("rule_relevance_scores")
    if not precedent_similarity_scores:
        data_quality["missing"].append("precedent_similarity_scores")
    if not witness_credibility_scores:
        data_quality["missing"].append("witness_credibility_scores")

    if empty_components > 0:
        data_quality["warning"] = (
            f"{empty_components} component(s) had no scores. "
            "Confidence may be unreliable."
        )

    # Determine interpretation band
    if confidence_score >= 85:
        interpretation = "very_high"
    elif confidence_score >= 70:
        interpretation = "high"
    elif confidence_score >= 50:
        interpretation = "moderate"
    elif confidence_score >= 30:
        interpretation = "low"
    else:
        interpretation = "very_low"

    return {
        "confidence_score": confidence_score,
        "component_scores": {k: round(v, 1) for k, v in component_scores.items()},
        "weights_used": weights,
        "data_quality": data_quality,
        "interpretation": interpretation,
    }
```

> **Score Calibration Disclaimer:** The confidence score is a relative indicator produced by weighted LLM-derived component scores, not a statistically calibrated probability. It should be interpreted as a directional signal (higher = stronger support for the verdict) rather than a precise likelihood. The system does not claim statistical validity for this metric.

### 3.12.6 search_precedents

```python
# tools/search_precedents.py
"""Live judiciary precedent search tool for VerdictCouncil."""

import hashlib
import json
import os
import time
from typing import Any

import redis
import requests


# Distributed rate limiter: 2 requests per second across all pods.
# Uses Redis INCR with TTL instead of a process-local variable, which
# would not work correctly when multiple replicas are running.


def _rate_limit(redis_client: redis.Redis, key: str = "vc:ratelimit:judiciary",
                max_per_second: int = 2):
    """Enforce distributed rate limit using Redis INCR with TTL."""
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, 1)
    if count > max_per_second:
        time.sleep(1.0)


def _get_redis_client() -> redis.Redis:
    """Get Redis client for caching."""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        db=int(os.environ.get("REDIS_DB", 0)),
        password=os.environ.get("REDIS_PASSWORD"),
        decode_responses=True,
    )


def _cache_key(query: str, domain: str, max_results: int, date_range: dict) -> str:
    """Generate deterministic cache key."""
    key_data = json.dumps(
        {"query": query, "domain": domain, "max_results": max_results,
         "date_range": date_range},
        sort_keys=True,
    )
    return f"vc:precedents:{hashlib.sha256(key_data.encode()).hexdigest()}"


def _search_judiciary_sg(query: str, domain: str, max_results: int,
                         date_range: dict) -> list:
    """Query judiciary.gov.sg for judgments."""
    _rate_limit(_get_redis_client())

    base_url = "https://www.judiciary.gov.sg/api/search"
    params = {
        "q": query,
        "court": "sct" if domain == "small_claims" else "traffic",
        "limit": max_results,
    }
    if date_range:
        if "start" in date_range:
            params["from"] = date_range["start"]
        if "end" in date_range:
            params["to"] = date_range["end"]

    try:
        resp = requests.get(
            base_url,
            params=params,
            timeout=30,
            headers={"User-Agent": "VerdictCouncil/1.0 (judicial-research)"},
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "citation": item.get("citation", ""),
                "title": item.get("title", ""),
                "outcome": item.get("outcome", ""),
                "reasoning_summary": item.get("summary", ""),
                "date": item.get("date", ""),
                "court": item.get("court", ""),
                "source": "judiciary_sg",
            })
        return results
    except requests.RequestException as e:
        # Flag failed lookups explicitly — never silently return empty results
        return [{"error": f"judiciary.gov.sg search failed: {str(e)}",
                 "source": "judiciary_sg",
                 "source_status": "search_failed"}]


def _search_pair_sg(query: str, domain: str, max_results: int,
                    date_range: dict) -> list:
    """Query search.pair.gov.sg for legal precedents."""
    _rate_limit(_get_redis_client())

    base_url = "https://search.pair.gov.sg/api/v1/search"
    payload = {
        "query": query,
        "filters": {
            "domain": domain,
        },
        "limit": max_results,
    }
    if date_range:
        payload["filters"]["date_range"] = date_range

    try:
        resp = requests.post(
            base_url,
            json=payload,
            timeout=30,
            headers={
                "User-Agent": "VerdictCouncil/1.0 (judicial-research)",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "citation": item.get("citation", ""),
                "title": item.get("title", ""),
                "outcome": item.get("outcome", ""),
                "reasoning_summary": item.get("summary", ""),
                "similarity_score": item.get("score", 0),
                "date": item.get("date", ""),
                "court": item.get("court", ""),
                "source": "pair_sg",
            })
        return results
    except requests.RequestException as e:
        # Flag failed lookups explicitly — never silently return empty results
        return [{"error": f"search.pair.gov.sg search failed: {str(e)}",
                 "source": "pair_sg",
                 "source_status": "search_failed"}]


def search_precedents(
    query: str,
    domain: str = "small_claims",
    max_results: int = 10,
    date_range: dict = None,
    tool_context: Any = None,
) -> dict:
    """Query live judiciary databases for recent rulings matching case patterns.

    Searches judiciary.gov.sg and search.pair.gov.sg for precedent cases
    with matching fact patterns. Results are cached in Redis with a 24-hour
    TTL to reduce external API load. Rate-limited to 2 requests per second
    per source.

    Args:
        query: Semantic search query describing the legal issue or fact
            pattern to search for.
        domain: Legal domain to search within. One of "small_claims" or
            "traffic". Defaults to "small_claims".
        max_results: Maximum number of precedents to return (total across
            both sources). Defaults to 10.
        date_range: Optional date range filter. Dictionary with keys:
            - start (str): Start date in "YYYY-MM-DD" format.
            - end (str): End date in "YYYY-MM-DD" format.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - precedents: List of matching precedent cases, each with
              citation, title, outcome, reasoning_summary,
              similarity_score, date, court, and source.
            - sources_queried: List of databases that were searched.
            - total_results: Total number of results found.
            - cached: Whether results were served from cache.
            - cache_ttl_remaining: Seconds remaining on cache entry (if cached).
    """
    if date_range is None:
        date_range = {}

    cache_k = _cache_key(query, domain, max_results, date_range)

    # Check Redis cache
    try:
        r = _get_redis_client()
        cached = r.get(cache_k)
        if cached:
            result = json.loads(cached)
            result["cached"] = True
            result["cache_ttl_remaining"] = r.ttl(cache_k)
            return result
    except (redis.RedisError, json.JSONDecodeError):
        pass  # Cache miss or connection failure; proceed with live search

    # Query both sources
    per_source_limit = max(1, max_results // 2)
    judiciary_results = _search_judiciary_sg(query, domain, per_source_limit,
                                            date_range)
    pair_results = _search_pair_sg(query, domain, per_source_limit, date_range)

    # Merge and deduplicate by citation
    all_results = []
    seen_citations = set()
    for item in judiciary_results + pair_results:
        if "error" in item:
            continue
        citation = item.get("citation", "")
        if citation and citation not in seen_citations:
            seen_citations.add(citation)
            all_results.append(item)
        elif not citation:
            all_results.append(item)

    # Sort by similarity_score descending (if available)
    all_results.sort(
        key=lambda x: x.get("similarity_score", 0), reverse=True
    )
    all_results = all_results[:max_results]

    result = {
        "precedents": all_results,
        "sources_queried": ["judiciary_sg", "pair_sg"],
        "total_results": len(all_results),
        "cached": False,
        "cache_ttl_remaining": None,
    }

    # Cache result in Redis with 24h TTL
    try:
        r = _get_redis_client()
        r.setex(cache_k, 86400, json.dumps(result))  # 24h = 86400s
    except redis.RedisError:
        pass  # Cache write failure is non-fatal

    return result
```

---

## 3.13 OpenAI Function-Calling JSON Schemas

### 3.13.1 parse_document

```json
{
  "type": "function",
  "function": {
    "name": "parse_document",
    "description": "Parse an uploaded document via the OpenAI Files API. Extracts text content, tables, and metadata from legal filings submitted to VerdictCouncil. Supports PDF, DOCX, images (with OCR), and plain text files.",
    "parameters": {
      "type": "object",
      "properties": {
        "file_id": {
          "type": "string",
          "description": "OpenAI File ID of the uploaded document (e.g., 'file-abc123')"
        },
        "extract_tables": {
          "type": "boolean",
          "description": "Whether to extract tabular data from the document",
          "default": true
        },
        "ocr_enabled": {
          "type": "boolean",
          "description": "Whether to enable OCR for scanned or image-based documents",
          "default": false
        }
      },
      "required": ["file_id"],
      "additionalProperties": false
    }
  }
}
```

### 3.13.2 cross_reference

```json
{
  "type": "function",
  "function": {
    "name": "cross_reference",
    "description": "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items. Analyzes pairs of segments from different documents submitted by different parties.",
    "parameters": {
      "type": "object",
      "properties": {
        "segments": {
          "type": "array",
          "description": "List of document segments to compare",
          "items": {
            "type": "object",
            "properties": {
              "doc_id": {
                "type": "string",
                "description": "Document identifier"
              },
              "text": {
                "type": "string",
                "description": "Text content of the segment"
              },
              "page": {
                "type": "integer",
                "description": "Page number where the segment appears"
              },
              "paragraph": {
                "type": "integer",
                "description": "Paragraph number within the page"
              }
            },
            "required": ["doc_id", "text", "page", "paragraph"],
            "additionalProperties": false
          }
        },
        "check_type": {
          "type": "string",
          "enum": ["contradiction", "corroboration", "all"],
          "description": "Type of cross-reference check to perform"
        }
      },
      "required": ["segments", "check_type"],
      "additionalProperties": false
    }
  }
}
```

### 3.13.3 timeline_construct

```json
{
  "type": "function",
  "function": {
    "name": "timeline_construct",
    "description": "Build a chronological timeline from extracted case events. Handles date normalization to ISO 8601 (Singapore timezone), chronological ordering, and conflict detection for events with contradictory dates or descriptions.",
    "parameters": {
      "type": "object",
      "properties": {
        "events": {
          "type": "array",
          "description": "List of events to order chronologically",
          "items": {
            "type": "object",
            "properties": {
              "date": {
                "type": "string",
                "description": "Date/time string in any recognizable format"
              },
              "description": {
                "type": "string",
                "description": "Description of the event"
              },
              "source_ref": {
                "type": "string",
                "description": "Source document reference (doc_id, page, paragraph)"
              },
              "parties": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Parties involved in the event"
              },
              "location": {
                "type": "string",
                "description": "Location where the event occurred"
              }
            },
            "required": ["date", "description", "source_ref", "parties"],
            "additionalProperties": false
          }
        }
      },
      "required": ["events"],
      "additionalProperties": false
    }
  }
}
```

### 3.13.4 generate_questions

```json
{
  "type": "function",
  "function": {
    "name": "generate_questions",
    "description": "Generate suggested judicial questions for hearing preparation. Analyzes argument summaries and identified weaknesses to produce targeted, neutral questions a Judge could ask each party during a hearing.",
    "parameters": {
      "type": "object",
      "properties": {
        "argument_summary": {
          "type": "string",
          "description": "Summary of the argument or testimony to generate questions for"
        },
        "weaknesses": {
          "type": "array",
          "items": { "type": "string" },
          "description": "List of identified weaknesses or gaps in the argument that questions should probe"
        },
        "question_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": ["clarification", "challenge", "exploration", "credibility"]
          },
          "description": "Types of questions to generate",
          "default": ["clarification", "challenge"]
        },
        "max_questions": {
          "type": "integer",
          "description": "Maximum number of questions to generate",
          "default": 5,
          "minimum": 1,
          "maximum": 20
        }
      },
      "required": ["argument_summary", "weaknesses"],
      "additionalProperties": false
    }
  }
}
```

### 3.13.5 confidence_calc

```json
{
  "type": "function",
  "function": {
    "name": "confidence_calc",
    "description": "Calculate a weighted confidence score for a verdict recommendation. Combines evidence strength, rule relevance, precedent similarity, and witness credibility into a single metric with interpretation bands.",
    "parameters": {
      "type": "object",
      "properties": {
        "evidence_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of evidence strength scores (0-100) from Evidence Analysis"
        },
        "rule_relevance_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of rule relevance scores (0-100) from Legal Knowledge"
        },
        "precedent_similarity_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of precedent similarity scores (0-100) from Legal Knowledge"
        },
        "witness_credibility_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of witness credibility scores (0-100) from Witness Analysis"
        },
        "weights": {
          "type": "object",
          "properties": {
            "evidence": { "type": "number", "minimum": 0, "maximum": 1 },
            "rules": { "type": "number", "minimum": 0, "maximum": 1 },
            "precedents": { "type": "number", "minimum": 0, "maximum": 1 },
            "witnesses": { "type": "number", "minimum": 0, "maximum": 1 }
          },
          "description": "Weight distribution across the four factors. Values must sum to 1.0. Defaults to equal weighting (0.25 each).",
          "additionalProperties": false
        }
      },
      "required": [
        "evidence_scores",
        "rule_relevance_scores",
        "precedent_similarity_scores",
        "witness_credibility_scores"
      ],
      "additionalProperties": false
    }
  }
}
```

### 3.13.6 file_search (Built-in)

```json
{
  "type": "function",
  "function": {
    "name": "file_search",
    "description": "Search OpenAI Vector Stores for relevant statutes, regulations, and curated precedent cases. Performs semantic similarity search across the VerdictCouncil legal knowledge base (vs_sct for Small Claims Tribunal, vs_traffic for Traffic Court).",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Semantic search query to find relevant legal documents"
        },
        "vector_store_ids": {
          "type": "array",
          "items": { "type": "string" },
          "description": "IDs of the OpenAI Vector Stores to search (e.g., ['vs_sct', 'vs_traffic'])"
        },
        "max_results": {
          "type": "integer",
          "description": "Maximum number of results to return",
          "default": 10,
          "minimum": 1,
          "maximum": 50
        }
      },
      "required": ["query"],
      "additionalProperties": false
    }
  }
}
```

### 3.13.7 search_precedents

```json
{
  "type": "function",
  "function": {
    "name": "search_precedents",
    "description": "Query live Singapore judiciary databases (judiciary.gov.sg, search.pair.gov.sg) for recent rulings matching case fact patterns. Results are Redis-cached with 24-hour TTL. Rate-limited to 2 requests per second per source.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Semantic search query describing the legal issue or fact pattern to search for"
        },
        "domain": {
          "type": "string",
          "enum": ["small_claims", "traffic"],
          "description": "Legal domain to search within"
        },
        "max_results": {
          "type": "integer",
          "description": "Maximum number of precedents to return (total across both sources)",
          "default": 10,
          "minimum": 1,
          "maximum": 50
        },
        "date_range": {
          "type": "object",
          "properties": {
            "start": {
              "type": "string",
              "description": "Start date in YYYY-MM-DD format"
            },
            "end": {
              "type": "string",
              "description": "End date in YYYY-MM-DD format"
            }
          },
          "description": "Optional date range filter for precedent search",
          "additionalProperties": false
        }
      },
      "required": ["query", "domain"],
      "additionalProperties": false
    }
  }
}
```



# Part 4: Tech Stack

## 4.1 Technology Matrix

| Category | Technology | Version | Justification |
|---|---|---|---|
| **Runtime** | Python | 3.12 | SAM is Python-native; 3.12 offers improved performance and better type hints |
| **Agent Framework** | Solace Agent Mesh (SAM) | latest | Event-driven multi-agent orchestration with YAML-based agent configuration, built-in broker integration |
| **LLM Provider** | OpenAI | API v1 | Multi-model strategy: o3 (deep reasoning), o4-mini (fast reasoning), gpt-4.1 (balanced), gpt-4.1-mini (lightweight tasks) |
| **LLM Abstraction** | LiteLLM (via SAM) | — | SAM uses LiteLLM internally for unified model routing and fallback handling |
| **Agent Protocol** | A2A via Solace topics | — | Asynchronous, decoupled inter-agent communication over publish/subscribe topics |
| **Message Broker** | Solace PubSub+ Event Broker | latest | Enterprise-grade event mesh with guaranteed delivery, topic hierarchy, message replay, and built-in audit trail |
| **RAG Pipeline** | OpenAI Vector Stores | — | Fully managed RAG: automatic parsing, chunking, embedding (text-embedding-3-large), and hybrid retrieval (semantic + keyword) |
| **Document Storage** | OpenAI Files API | — | Native PDF, image, and text parsing with no custom extraction libraries required |
| **Database** | PostgreSQL | 16 | Case records, audit logs, session persistence; JSONB support for flexible nested data |
| **Cache** | Redis | 7 | search_precedents caching (24h TTL), session token storage, rate limiting counters |
| **Authentication** | PyJWT | — | Lightweight JWT generation and validation; tokens transported via HTTP-only cookies |
| **HTTP Client** | httpx | — | Async HTTP client for search_precedents tool calls to judiciary.gov.sg and PAIR |
| **Containerization** | Docker | — | Multi-stage builds; per-agent images for independent scaling and deployment |
| **Container Registry** | GitHub Container Registry (GHCR) | — | Integrated with GitHub Actions CI/CD; organisation-scoped image access |
| **Orchestration** | Kubernetes | 1.28+ | 11+ pods: 9 agents + 1 gateway + 1 broker + infrastructure (PostgreSQL, Redis) |
| **CI/CD** | GitHub Actions | — | Build, test, deploy with environment promotion aligned to gitflow branching |
| **Monitoring** | Prometheus + Grafana | — | Agent health metrics, pipeline latency histograms, LLM token usage and cost tracking |
| **Logging** | Python stdout + Solace audit trail | — | Kubernetes collects stdout via log drivers; Solace provides message-level audit for every agent hop |
| **Code Quality** | ruff + mypy | — | Fast linting (ruff) and static type checking (mypy) enforced in CI |
| **Security Scanning** | pip-audit + bandit | — | Dependency vulnerability scanning (pip-audit) and Python code security analysis (bandit) |
| **Testing** | pytest | — | Unit and integration tests with mocked OpenAI calls; coverage target 80%+ |

## 4.2 Model Selection Strategy

Each agent is assigned a model based on reasoning depth requirements:

| Tier | Model | Use Case | Agents |
|---|---|---|---|
| **Lightweight** | gpt-4.1-mini | Parsing, classification, low-complexity extraction | CaseProcessing |
| **Fast Reasoning** | o4-mini | Quick analytical decisions with reasoning traces | ComplexityRouting, WitnessAnalysis |
| **Balanced** | gpt-4.1 | Detailed analysis requiring broad context | EvidenceAnalysis, FactReconstruction, LegalKnowledge |
| **Deep Reasoning** | o3 | Complex legal reasoning, fairness auditing, final verdicts | ArgumentConstruction, Deliberation, GovernanceVerdict |

## 4.3 Key Design Decisions

| Decision | Choice | Alternatives Considered | Rationale |
|---|---|---|---|
| RAG approach | OpenAI Vector Stores | Self-hosted (Chroma, Weaviate, Pinecone) | Zero infrastructure overhead; automatic chunking and embedding; managed hybrid retrieval eliminates tuning |
| Broker | Solace PubSub+ | RabbitMQ, Kafka, NATS | SAM-native integration; topic hierarchy maps to agent pipeline; enterprise audit trail for judicial compliance |
| Agent framework | SAM | LangGraph, CrewAI, AutoGen | Purpose-built for Solace broker; YAML-driven agent config; built-in tool registration and message routing |
| Database | PostgreSQL | MongoDB, DynamoDB | Relational integrity for case-party-evidence relationships; JSONB for flexible nested fields; mature ecosystem |
| Auth transport | HTTP-only cookies | Bearer tokens in headers | XSS protection; automatic inclusion in browser requests; no client-side token storage |

---

# Part 5: Diagrams

## 5.1 Entity-Relationship Diagram

```mermaid
erDiagram
    users {
        uuid id PK
        varchar name
        varchar email UK
        enum role "judge | admin"
        varchar password_hash
        timestamp created_at
        timestamp updated_at
    }

    sessions {
        uuid id PK
        uuid user_id FK
        varchar jwt_token_hash
        timestamp expires_at
        timestamp created_at
    }

    cases {
        uuid id PK
        enum domain "small_claims | traffic_violation"
        enum status "pending | processing | ready_for_review | decided | rejected | escalated"
        boolean jurisdiction_valid
        enum complexity "low | medium | high"
        enum route "proceed_automated | proceed_with_review | escalate_human"
        uuid created_by FK
        timestamp created_at
        timestamp updated_at
    }

    parties {
        uuid id PK
        uuid case_id FK
        varchar name
        enum role "claimant | respondent | accused | prosecution"
        jsonb contact_info
    }

    documents {
        uuid id PK
        uuid case_id FK
        varchar openai_file_id
        varchar filename
        varchar file_type
        uuid uploaded_by FK
        timestamp uploaded_at
    }

    evidence {
        uuid id PK
        uuid case_id FK
        uuid document_id FK
        enum evidence_type "documentary | testimonial | physical | digital | expert"
        enum strength "strong | medium | weak"
        jsonb admissibility_flags
        jsonb linked_claims
    }

    facts {
        uuid id PK
        uuid case_id FK
        date event_date
        time event_time
        text description
        uuid source_document_id FK
        enum confidence "high | medium | low | disputed"
        enum status "agreed | disputed"
        jsonb corroboration
    }

    witnesses {
        uuid id PK
        uuid case_id FK
        varchar name
        varchar role
        uuid party_id FK
        int credibility_score
        jsonb bias_indicators
        text simulated_testimony
    }

    legal_rules {
        uuid id PK
        uuid case_id FK
        varchar statute_name
        varchar section
        text verbatim_text
        float relevance_score
        text application
    }

    precedents {
        uuid id PK
        uuid case_id FK
        varchar citation
        varchar court
        varchar outcome
        text reasoning_summary
        float similarity_score
        text distinguishing_factors
        enum source "curated | live_search"
        varchar url
    }

    arguments {
        uuid id PK
        uuid case_id FK
        enum side "prosecution | defense | claimant | respondent"
        text legal_basis
        jsonb supporting_evidence
        text weaknesses
        jsonb suggested_questions
    }

    deliberations {
        uuid id PK
        uuid case_id FK
        jsonb reasoning_chain
        text preliminary_conclusion
        jsonb uncertainty_flags
        int confidence_score
    }

    verdicts {
        uuid id PK
        uuid case_id FK
        enum recommendation_type "compensation | repair | dismiss | guilty | not_guilty | reduced"
        text recommended_outcome
        jsonb sentence
        int confidence_score
        jsonb alternative_outcomes
        jsonb fairness_report
    }

    audit_logs {
        uuid id PK
        uuid case_id FK
        varchar agent_name
        varchar action
        varchar input_hash
        varchar output_hash
        jsonb tool_calls
        varchar solace_message_id
        timestamp created_at
    }

    users ||--o{ sessions : "has"
    users ||--o{ cases : "creates"
    cases ||--o{ parties : "involves"
    cases ||--o{ documents : "contains"
    cases ||--o{ evidence : "has"
    documents ||--o{ evidence : "supports"
    cases ||--o{ facts : "establishes"
    documents ||--o{ facts : "sources"
    cases ||--o{ witnesses : "involves"
    parties ||--o{ witnesses : "associated_with"
    cases ||--o{ legal_rules : "applies"
    cases ||--o{ precedents : "references"
    cases ||--o{ arguments : "presents"
    cases ||--|| deliberations : "produces"
    cases ||--|| verdicts : "concludes_with"
    cases ||--o{ audit_logs : "tracks"
```

## 5.2 Sequence Diagram — Full Pipeline Flow

```mermaid
sequenceDiagram
    actor Judge
    participant WG as WebGateway
    participant SB as SolaceBroker
    participant PG as PostgreSQL
    participant OAI as OpenAI API
    participant CP as CaseProcessing
    participant CR as ComplexityRouting
    participant EA as EvidenceAnalysis
    participant FR as FactReconstruction
    participant WA as WitnessAnalysis
    participant LK as LegalKnowledge
    participant JAPI as JudiciaryAPI
    participant RD as Redis
    participant AC as ArgumentConstruction
    participant DL as Deliberation
    participant GV as GovernanceVerdict

    Note over Judge, GV: Phase 1 — Case Intake

    Judge ->>+ WG: POST /cases (upload documents)
    WG ->> OAI: Upload files (Files API)
    OAI -->> WG: file_ids[]
    WG ->> PG: INSERT case (status: PROCESSING)
    PG -->> WG: case_id
    WG -->> Judge: 202 Accepted (case_id)
    WG ->>- SB: Publish verdictcouncil/case_processing/{case_id}

    Note over SB, CP: Phase 2 — Case Processing

    SB ->>+ CP: Deliver message
    CP ->> OAI: parse_document (Files API per file)
    OAI -->> CP: Parsed document content
    CP ->> OAI: gpt-4.1-mini — classify domain, validate jurisdiction
    OAI -->> CP: domain, jurisdiction_valid, parties[]
    CP ->> PG: UPDATE case (domain, jurisdiction, parties)
    CP ->>- SB: Publish verdictcouncil/complexity_routing/{case_id}

    Note over SB, CR: Phase 3 — Complexity Routing

    SB ->>+ CR: Deliver message
    CR ->> OAI: o4-mini (reasoning_effort: low) — assess complexity
    OAI -->> CR: complexity, route

    alt route = escalate_human
        CR ->> PG: UPDATE case (status: ESCALATED)
        CR ->> SB: Publish verdictcouncil/gateway/escalation/{case_id}
        SB ->> WG: Deliver escalation
        WG ->> Judge: Display escalation alert
    else route = proceed_automated OR proceed_with_review
        CR ->> PG: UPDATE case (complexity, route)
        CR ->>- SB: Publish verdictcouncil/evidence_analysis/{case_id}
    end

    Note over SB, WA: Phase 4-6 — Parallel Analysis (Layer 2)

    par Evidence Analysis
        SB ->>+ EA: Deliver message
        EA ->> OAI: parse_document (extract tables, OCR)
        OAI -->> EA: Structured content
        EA ->> OAI: gpt-4.1 — analyze evidence strength, admissibility
        OAI -->> EA: evidence[], cross_references[]
        EA ->> PG: INSERT evidence records
        EA ->>- SB: Publish to aggregator
    and Fact Reconstruction
        SB ->>+ FR: Deliver message
        FR ->> OAI: gpt-4.1 — extract facts, build timeline
        OAI -->> FR: facts[], timeline
        FR ->> OAI: cross_reference (consistency check)
        OAI -->> FR: contradictions[], corroborations[]
        FR ->> PG: INSERT facts records
        FR ->>- SB: Publish to aggregator
    and Witness Analysis
        SB ->>+ WA: Deliver message
        WA ->> OAI: o4-mini — identify witnesses, assess credibility
        OAI -->> WA: witnesses[], credibility_scores[]
        WA ->> OAI: o4-mini — simulate testimony, generate questions
        OAI -->> WA: simulated_testimony[], questions[]
        WA ->> PG: INSERT witness records
        WA ->>- SB: Publish to aggregator
    end

    Note over SB, LK: Layer2Aggregator — Fan-In Barrier

    SB ->> SB: Layer2Aggregator collects all 3 outputs
    SB ->> SB: Merge into unified CaseState (deep-copy original, update 3 fields)
    SB ->> SB: Publish to legal-knowledge topic

    Note over SB, LK: Phase 7 — Legal Knowledge

    SB ->>+ LK: Deliver message
    LK ->> OAI: file_search (vector store — statutes)
    OAI -->> LK: matching statutes[]
    LK ->> RD: Check precedent cache
    RD -->> LK: Cache miss
    LK ->> JAPI: search_precedents (judiciary.gov.sg)
    JAPI -->> LK: live_results[]
    LK ->> JAPI: search_precedents (search.pair.gov.sg)
    JAPI -->> LK: pair_results[]
    LK ->> RD: Cache results (TTL: 24h)
    LK ->> OAI: gpt-4.1 — rank relevance, extract reasoning
    OAI -->> LK: legal_rules[], precedents[]
    LK ->> PG: INSERT legal_rules, precedents
    LK ->>- SB: Publish verdictcouncil/argument_construction/{case_id}

    Note over SB, AC: Phase 8 — Argument Construction

    SB ->>+ AC: Deliver message
    AC ->> OAI: o3 — build prosecution/claimant arguments
    OAI -->> AC: prosecution_args
    AC ->> OAI: o3 — build defense/respondent arguments
    OAI -->> AC: defense_args
    AC ->> OAI: o3 — balanced assessment, generate questions
    OAI -->> AC: balanced_assessment, questions[]
    AC ->> PG: INSERT arguments (2 per case)
    AC ->>- SB: Publish verdictcouncil/deliberation/{case_id}

    Note over SB, DL: Phase 9 — Deliberation

    SB ->>+ DL: Deliver message
    DL ->> OAI: o3 — synthesize reasoning chain
    OAI -->> DL: reasoning_chain[], preliminary_conclusion, uncertainty_flags[]
    DL ->> PG: INSERT deliberation
    DL ->>- SB: Publish verdictcouncil/governance_verdict/{case_id}

    Note over SB, GV: Phase 10 — Governance & Verdict

    SB ->>+ GV: Deliver message
    GV ->> OAI: o3 — fairness audit (bias detection)
    OAI -->> GV: fairness_report

    alt critical_bias_detected
        GV ->> PG: UPDATE case (status: ESCALATED)
        GV ->> SB: Publish verdictcouncil/gateway/halt/{case_id}
        SB ->> WG: Deliver halt notification
        WG ->> Judge: Display bias alert — manual review required
    else audit_passes
        GV ->> OAI: confidence_calc (weighted scoring)
        OAI -->> GV: confidence_score
        GV ->> OAI: o3 — generate final verdict recommendation
        OAI -->> GV: verdict_recommendation
        GV ->> SB: Publish verdictcouncil/gateway/verdict/{case_id}
    end

    SB ->> WG: Deliver verdict
    WG ->> PG: INSERT verdict + audit_logs
    WG ->> PG: UPDATE case (status: READY_FOR_REVIEW)
    WG ->>- Judge: Display verdict recommendation

    Note over Judge, GV: Phase 11 — Judicial Decision

    Judge ->>+ WG: POST /cases/{id}/decision (accept/modify/reject)
    WG ->> PG: UPDATE case (status: DECIDED, judge_decision)
    WG -->>- Judge: 200 OK — decision recorded
```

## 5.3 Physical Architecture Diagram

```mermaid
flowchart TB
    subgraph External["External Services"]
        OAI["OpenAI API<br/>api.openai.com"]
        JUD["judiciary.gov.sg"]
        PAIR["search.pair.gov.sg"]
    end

    subgraph K8s["Kubernetes Cluster — namespace: verdictcouncil"]
        ING["Ingress Controller<br/>NGINX"]

        subgraph Gateway["Gateway Layer"]
            WG["web-gateway pod<br/>SAM + FastAPI<br/>:8000"]
        end

        subgraph Broker["Message Broker"]
            SOL["solace-broker pod<br/>Solace PubSub+<br/>:55555 / :8080 / :1883 / :8008"]
        end

        subgraph Agents["Agent Pods (9)"]
            CP["case-processing"]
            CR["complexity-routing"]
            EA["evidence-analysis"]
            FR["fact-reconstruction"]
            WA["witness-analysis"]
            LK["legal-knowledge"]
            AC["argument-construction"]
            DL["deliberation"]
            GV["governance-verdict"]
        end

        subgraph Data["Data Layer (StatefulSets)"]
            PG["postgresql pod<br/>:5432<br/>PVC: 50Gi"]
            RD["redis pod<br/>:6379"]
        end

        subgraph Services["ClusterIP Services"]
            WGS["web-gateway-svc"]
            SOLS["solace-broker-svc"]
            PGS["postgresql-svc"]
            RDS["redis-svc"]
        end
    end

    %% Ingress
    ING -->|"HTTPS :443"| WGS
    WGS --> WG

    %% Gateway connections
    WG --> SOLS
    WG --> PGS

    %% Agent connections to broker
    CP --> SOLS
    CR --> SOLS
    EA --> SOLS
    FR --> SOLS
    WA --> SOLS
    LK --> SOLS
    AC --> SOLS
    DL --> SOLS
    GV --> SOLS

    %% Broker service
    SOLS --> SOL

    %% Agent connections to database
    CP --> PGS
    CR --> PGS
    EA --> PGS
    FR --> PGS
    WA --> PGS
    LK --> PGS
    AC --> PGS
    DL --> PGS
    GV --> PGS

    %% Database service
    PGS --> PG

    %% Redis
    LK --> RDS
    RDS --> RD

    %% External API connections
    CP -.->|"HTTPS"| OAI
    CR -.->|"HTTPS"| OAI
    EA -.->|"HTTPS"| OAI
    FR -.->|"HTTPS"| OAI
    WA -.->|"HTTPS"| OAI
    LK -.->|"HTTPS"| OAI
    AC -.->|"HTTPS"| OAI
    DL -.->|"HTTPS"| OAI
    GV -.->|"HTTPS"| OAI

    LK -.->|"HTTPS"| JUD
    LK -.->|"HTTPS"| PAIR
```

## 5.4 Class Diagram

```mermaid
classDiagram
    class BaseAgent {
        <<abstract>>
        #config: dict
        #broker_client: SolaceBrokerClient
        #db_session: Session
        +handle_message(payload: dict) dict
        +publish(topic: str, payload: dict) void
        +validate_output(result: dict, schema: dict) bool
        +log_audit(action: str, input_hash: str, output_hash: str) void
    }

    class CaseProcessingAgent {
        +parse_and_structure() DocumentContent
        +classify_domain() str
        +validate_jurisdiction() bool
    }

    class ComplexityRoutingAgent {
        +assess_complexity() str
        +determine_route() str
    }

    class EvidenceAnalysisAgent {
        +analyze_evidence() list~EvidenceItem~
        +cross_reference_documents() list~Finding~
    }

    class FactReconstructionAgent {
        +extract_facts() list~Fact~
        +build_timeline() Timeline
    }

    class WitnessAnalysisAgent {
        +identify_witnesses() list~Witness~
        +assess_credibility() list~int~
        +simulate_testimony() list~str~
    }

    class LegalKnowledgeAgent {
        +retrieve_statutes() list~LegalRule~
        +retrieve_precedents() list~Precedent~
        +search_live_precedents() list~Precedent~
    }

    class ArgumentConstructionAgent {
        +build_prosecution_args() Argument
        +build_defense_args() Argument
        +build_balanced_assessment() dict
    }

    class DeliberationAgent {
        +synthesize_reasoning_chain() list~ReasoningStep~
    }

    class GovernanceVerdictAgent {
        +run_fairness_audit() dict
        +generate_verdict() VerdictRecommendation
    }

    class ParseDocumentTool {
        +execute(file_id: str, extract_tables: bool, ocr_enabled: bool) DocumentContent
    }

    class CrossReferenceTool {
        +execute(segments: list, check_type: str) list~Finding~
    }

    class TimelineConstructTool {
        +execute(events: list~Fact~) Timeline
    }

    class GenerateQuestionsTool {
        +execute(argument_summary: str, weaknesses: str, question_types: list, max_questions: int) list~Question~
    }

    class ConfidenceCalcTool {
        +execute(evidence_scores: list, rule_relevance_scores: list, precedent_similarity_scores: list, witness_credibility_scores: list, weights: dict) float
    }

    class SearchPrecedentsTool {
        +execute(query: str, domain: str, max_results: int, date_range: dict) list~Precedent~
    }

    class CaseState {
        +case_id: str
        +domain: str
        +status: str
        +parties: list~Party~
        +case_metadata: dict
        +raw_documents: list~Document~
        +evidence_analysis: list~EvidenceItem~
        +extracted_facts: list~Fact~
        +witnesses: list~Witness~
        +legal_rules: list~LegalRule~
        +precedents: list~Precedent~
        +arguments: list~Argument~
        +deliberation: list~ReasoningStep~
        +fairness_check: dict
        +verdict_recommendation: VerdictRecommendation
        +judge_decision: dict
        +audit_log: list~AuditEntry~
    }

    class Party {
        +id: str
        +name: str
        +role: str
        +contact_info: dict
    }

    class Document {
        +id: str
        +openai_file_id: str
        +filename: str
        +file_type: str
    }

    class EvidenceItem {
        +id: str
        +document_id: str
        +evidence_type: str
        +strength: str
        +admissibility_flags: dict
        +linked_claims: list
    }

    class Fact {
        +id: str
        +date: str
        +time: str
        +description: str
        +source_doc_id: str
        +confidence: str
        +status: str
        +corroboration: dict
    }

    class Witness {
        +id: str
        +name: str
        +role: str
        +party_id: str
        +credibility_score: int
        +bias_indicators: dict
    }

    class LegalRule {
        +id: str
        +statute_name: str
        +section: str
        +verbatim_text: str
        +relevance_score: float
        +application: str
    }

    class Precedent {
        +id: str
        +citation: str
        +court: str
        +outcome: str
        +reasoning_summary: str
        +similarity_score: float
        +source: str
    }

    class Argument {
        +id: str
        +side: str
        +legal_basis: str
        +supporting_evidence: list
        +weaknesses: str
        +questions: list
    }

    class ReasoningStep {
        +step_number: int
        +category: str
        +content: str
        +source_agent: str
        +source_evidence: list
        +confidence: float
    }

    class VerdictRecommendation {
        +type: str
        +outcome: str
        +sentence: dict
        +confidence_score: int
        +alternatives: list
        +fairness_report: dict
    }

    class AuditEntry {
        +id: str
        +agent_name: str
        +action: str
        +input_hash: str
        +output_hash: str
        +tool_calls: list
        +timestamp: str
    }

    class CaseService {
        +create_case(data: dict) CaseState
        +get_case(case_id: str) CaseState
        +update_status(case_id: str, status: str) void
        +search_cases(filters: dict) list~CaseState~
        +add_documents(case_id: str, files: list) list~Document~
    }

    class AuditService {
        +log_action(entry: AuditEntry) void
        +get_audit_trail(case_id: str) list~AuditEntry~
        +get_case_audit(case_id: str, agent: str) list~AuditEntry~
    }

    class AuthService {
        +authenticate(email: str, password: str) dict
        +issue_token(user_id: str) str
        +validate_token(token: str) dict
        +revoke_token(token: str) void
    }

    class ExportService {
        +generate_pdf(case_id: str) bytes
        +generate_json(case_id: str) dict
    }

    %% Inheritance
    BaseAgent <|-- CaseProcessingAgent
    BaseAgent <|-- ComplexityRoutingAgent
    BaseAgent <|-- EvidenceAnalysisAgent
    BaseAgent <|-- FactReconstructionAgent
    BaseAgent <|-- WitnessAnalysisAgent
    BaseAgent <|-- LegalKnowledgeAgent
    BaseAgent <|-- ArgumentConstructionAgent
    BaseAgent <|-- DeliberationAgent
    BaseAgent <|-- GovernanceVerdictAgent

    %% Tool usage
    CaseProcessingAgent --> ParseDocumentTool : uses
    EvidenceAnalysisAgent --> ParseDocumentTool : uses
    EvidenceAnalysisAgent --> CrossReferenceTool : uses
    FactReconstructionAgent --> ParseDocumentTool : uses
    FactReconstructionAgent --> TimelineConstructTool : uses
    FactReconstructionAgent --> CrossReferenceTool : uses
    WitnessAnalysisAgent --> CrossReferenceTool : uses
    WitnessAnalysisAgent --> GenerateQuestionsTool : uses
    LegalKnowledgeAgent --> SearchPrecedentsTool : uses
    ArgumentConstructionAgent --> GenerateQuestionsTool : uses
    GovernanceVerdictAgent --> ConfidenceCalcTool : uses

    %% State usage
    CaseProcessingAgent ..> CaseState : reads/writes
    ComplexityRoutingAgent ..> CaseState : reads/writes
    EvidenceAnalysisAgent ..> CaseState : reads/writes
    FactReconstructionAgent ..> CaseState : reads/writes
    WitnessAnalysisAgent ..> CaseState : reads/writes
    LegalKnowledgeAgent ..> CaseState : reads/writes
    ArgumentConstructionAgent ..> CaseState : reads/writes
    DeliberationAgent ..> CaseState : reads/writes
    GovernanceVerdictAgent ..> CaseState : reads/writes

    %% Composition
    CaseState *-- Party
    CaseState *-- Document
    CaseState *-- EvidenceItem
    CaseState *-- Fact
    CaseState *-- Witness
    CaseState *-- LegalRule
    CaseState *-- Precedent
    CaseState *-- Argument
    CaseState *-- ReasoningStep
    CaseState *-- VerdictRecommendation
    CaseState *-- AuditEntry
```

---

# Part 6: CI/CD Pipeline

## 6.1 Workflow Overview

Three GitHub Actions workflows aligned with the gitflow branching strategy:

| Workflow | Trigger | Purpose | Target |
|---|---|---|---|
| `ci.yml` | Push to `feat/*`, PR to `development` | Lint, test, security scan, build verification | — |
| `staging-deploy.yml` | Push to `release/*` | Build images, deploy to staging, smoke test | Staging cluster |
| `production-deploy.yml` | Push to `main` | Build release images, deploy to production, create GitHub Release | Production cluster |

## 6.2 CI Workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches:
      - 'feat/**'
  pull_request:
    branches:
      - development

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  PYTHON_VERSION: '3.12'

jobs:
  lint-and-typecheck:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install ruff mypy
          pip install -r requirements.txt

      - name: Run ruff linter
        run: ruff check . --output-format=github

      - name: Run ruff formatter check
        run: ruff format --check .

      - name: Run mypy type checking
        run: mypy src/ --ignore-missing-imports

  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    needs: lint-and-typecheck
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run unit tests with coverage
        run: |
          pytest tests/unit/ \
            --cov=src \
            --cov-report=xml \
            --cov-report=term-missing \
            --cov-fail-under=80 \
            -v
        env:
          OPENAI_API_KEY: "sk-test-mock-key"

      - name: Upload coverage report
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage.xml

  integration-tests:
    name: Integration Tests
    runs-on: ubuntu-latest
    needs: unit-tests
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: verdictcouncil_test
          POSTGRES_USER: vc_test
          POSTGRES_PASSWORD: test_password
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U vc_test"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run database migrations
        run: python -m alembic upgrade head
        env:
          DATABASE_URL: "postgresql://vc_test:test_password@localhost:5432/verdictcouncil_test"

      - name: Run integration tests
        run: |
          pytest tests/integration/ \
            -v \
            --timeout=120
        env:
          DATABASE_URL: "postgresql://vc_test:test_password@localhost:5432/verdictcouncil_test"
          REDIS_URL: "redis://localhost:6379/0"
          OPENAI_API_KEY: "sk-test-mock-key"
          SOLACE_BROKER_URL: "tcp://localhost:55555"

  security-scan:
    name: Security Scan
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pip-audit bandit
          pip install -r requirements.txt

      - name: Run pip-audit (dependency vulnerabilities)
        run: pip-audit --strict --desc

      - name: Run bandit (code security analysis)
        run: bandit -r src/ -f json -o bandit-report.json || true

      - name: Check bandit results
        run: |
          bandit -r src/ -ll -ii
        continue-on-error: false

      - name: Upload security report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: security-reports
          path: bandit-report.json

  docker-build-test:
    name: Docker Build Verification
    runs-on: ubuntu-latest
    needs: [unit-tests, security-scan]
    strategy:
      matrix:
        agent:
          - web-gateway
          - case-processing
          - complexity-routing
          - evidence-analysis
          - fact-reconstruction
          - witness-analysis
          - legal-knowledge
          - argument-construction
          - deliberation
          - governance-verdict
          - layer2-aggregator
          - whatif-controller
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build image (no push)
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/${{ matrix.agent }}/Dockerfile
          push: false
          tags: verdictcouncil/${{ matrix.agent }}:test
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

## 6.3 Docker Strategy

### Base Dockerfile

```dockerfile
# docker/base/Dockerfile
# Stage 1: Builder — install all dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime — minimal image
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy shared source code
COPY src/shared/ /app/src/shared/
COPY src/models/ /app/src/models/
COPY src/services/ /app/src/services/
COPY src/tools/ /app/src/tools/

# Non-root user for security
RUN groupadd -r vcagent && useradd -r -g vcagent vcagent
USER vcagent

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app
```

### Agent Dockerfile (example: case-processing)

```dockerfile
# docker/case-processing/Dockerfile
# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /install /usr/local

# Shared source
COPY src/shared/ /app/src/shared/
COPY src/models/ /app/src/models/
COPY src/services/ /app/src/services/
COPY src/tools/ /app/src/tools/

# Agent-specific code and config
COPY src/agents/case_processing/ /app/src/agents/case_processing/
COPY configs/agents/case_processing.yaml /app/configs/agent.yaml

RUN groupadd -r vcagent && useradd -r -g vcagent vcagent
USER vcagent

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "-m", "solace_agent_mesh.main", "--config", "/app/configs/agent.yaml"]
```

### Image Naming Convention

```
ghcr.io/verdictcouncil/web-gateway:{tag}
ghcr.io/verdictcouncil/case-processing:{tag}
ghcr.io/verdictcouncil/complexity-routing:{tag}
ghcr.io/verdictcouncil/evidence-analysis:{tag}
ghcr.io/verdictcouncil/fact-reconstruction:{tag}
ghcr.io/verdictcouncil/witness-analysis:{tag}
ghcr.io/verdictcouncil/legal-knowledge:{tag}
ghcr.io/verdictcouncil/argument-construction:{tag}
ghcr.io/verdictcouncil/deliberation:{tag}
ghcr.io/verdictcouncil/governance-verdict:{tag}
```

Tag formats:
- Feature builds: `feat-{branch}-{sha}` (never pushed)
- Staging: `rc-{sha}`
- Production: `v1.2.0` (semver from git tag)

## 6.4 Staging Deploy Workflow

```yaml
# .github/workflows/staging-deploy.yml
name: Staging Deploy

on:
  push:
    branches:
      - 'release/**'

env:
  REGISTRY: ghcr.io
  IMAGE_PREFIX: ghcr.io/${{ github.repository_owner }}

jobs:
  build-and-push:
    name: Build & Push Images
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    strategy:
      matrix:
        agent:
          - web-gateway
          - case-processing
          - complexity-routing
          - evidence-analysis
          - fact-reconstruction
          - witness-analysis
          - legal-knowledge
          - argument-construction
          - deliberation
          - governance-verdict
          - layer2-aggregator
          - whatif-controller
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract short SHA
        id: sha
        run: echo "short=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/${{ matrix.agent }}/Dockerfile
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:rc-${{ steps.sha.outputs.short }}
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:staging-latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy-staging:
    name: Deploy to Staging
    runs-on: ubuntu-latest
    needs: build-and-push
    environment: staging
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Configure kubectl
        uses: azure/setup-kubectl@v3
        with:
          version: 'v1.28.0'

      - name: Set kubeconfig
        run: |
          mkdir -p $HOME/.kube
          echo "${{ secrets.STAGING_KUBECONFIG }}" | base64 -d > $HOME/.kube/config

      - name: Extract short SHA
        id: sha
        run: echo "short=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Update image tags in manifests
        run: |
          AGENTS=(
            web-gateway
            case-processing
            complexity-routing
            evidence-analysis
            fact-reconstruction
            witness-analysis
            legal-knowledge
            argument-construction
            deliberation
            governance-verdict
          )
          for agent in "${AGENTS[@]}"; do
            sed -i "s|image:.*${agent}:.*|image: ${{ env.IMAGE_PREFIX }}/${agent}:rc-${{ steps.sha.outputs.short }}|" \
              k8s/staging/${agent}-deployment.yaml
          done

      - name: Apply Kubernetes manifests
        run: |
          kubectl apply -f k8s/staging/ --namespace verdictcouncil-staging

      - name: Wait for rollout
        run: |
          DEPLOYMENTS=$(kubectl get deployments -n verdictcouncil-staging -o name)
          for deploy in $DEPLOYMENTS; do
            kubectl rollout status "$deploy" -n verdictcouncil-staging --timeout=300s
          done

  smoke-test:
    name: Smoke Test
    runs-on: ubuntu-latest
    needs: deploy-staging
    environment: staging
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Wait for services to stabilise
        run: sleep 15

      - name: Health check all agents
        run: |
          STAGING_URL="${{ secrets.STAGING_URL }}"
          response=$(curl -s -o /dev/null -w "%{http_code}" "${STAGING_URL}/health")
          if [ "$response" != "200" ]; then
            echo "Gateway health check failed with status $response"
            exit 1
          fi
          echo "Gateway health check passed"

      - name: Submit test case
        run: |
          STAGING_URL="${{ secrets.STAGING_URL }}"

          # Authenticate
          TOKEN=$(curl -s -X POST "${STAGING_URL}/auth/login" \
            -H "Content-Type: application/json" \
            -d '{"email":"test-judge@verdictcouncil.sg","password":"${{ secrets.STAGING_TEST_PASSWORD }}"}' \
            | jq -r '.token')

          # Submit test case
          CASE_ID=$(curl -s -X POST "${STAGING_URL}/api/v1/cases" \
            -H "Authorization: Bearer ${TOKEN}" \
            -F "documents=@tests/fixtures/test_case.pdf" \
            -F "domain=small_claims" \
            | jq -r '.case_id')

          echo "Test case submitted: ${CASE_ID}"
          echo "case_id=${CASE_ID}" >> "$GITHUB_ENV"

      - name: Wait for pipeline completion
        run: |
          STAGING_URL="${{ secrets.STAGING_URL }}"
          MAX_WAIT=300
          ELAPSED=0
          while [ $ELAPSED -lt $MAX_WAIT ]; do
            STATUS=$(curl -s "${STAGING_URL}/api/v1/cases/${case_id}" \
              -H "Authorization: Bearer ${TOKEN}" \
              | jq -r '.status')
            if [ "$STATUS" = "ready_for_review" ] || [ "$STATUS" = "escalated" ]; then
              echo "Pipeline completed with status: ${STATUS}"
              exit 0
            fi
            sleep 10
            ELAPSED=$((ELAPSED + 10))
            echo "Waiting... (${ELAPSED}s / ${MAX_WAIT}s) — current status: ${STATUS}"
          done
          echo "Pipeline did not complete within ${MAX_WAIT}s"
          exit 1

  notify:
    name: Notify
    runs-on: ubuntu-latest
    needs: [deploy-staging, smoke-test]
    if: always()
    steps:
      - name: Post deployment status
        run: |
          if [ "${{ needs.smoke-test.result }}" = "success" ]; then
            echo "Staging deployment successful"
          else
            echo "Staging deployment failed — check workflow logs"
            exit 1
          fi
```

## 6.5 Production Deploy Workflow

```yaml
# .github/workflows/production-deploy.yml
name: Production Deploy

on:
  push:
    branches:
      - main

env:
  REGISTRY: ghcr.io
  IMAGE_PREFIX: ghcr.io/${{ github.repository_owner }}

jobs:
  build-and-push:
    name: Build & Push Release Images
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    outputs:
      version: ${{ steps.version.outputs.tag }}
    strategy:
      matrix:
        agent:
          - web-gateway
          - case-processing
          - complexity-routing
          - evidence-analysis
          - fact-reconstruction
          - witness-analysis
          - legal-knowledge
          - argument-construction
          - deliberation
          - governance-verdict
          - layer2-aggregator
          - whatif-controller
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Get version from git tag
        id: version
        run: |
          TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"
          echo "Building version: ${TAG}"

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/${{ matrix.agent }}/Dockerfile
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:${{ steps.version.outputs.tag }}
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy-production:
    name: Deploy to Production
    runs-on: ubuntu-latest
    needs: build-and-push
    environment: production
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Configure kubectl
        uses: azure/setup-kubectl@v3
        with:
          version: 'v1.28.0'

      - name: Set kubeconfig
        run: |
          mkdir -p $HOME/.kube
          echo "${{ secrets.PRODUCTION_KUBECONFIG }}" | base64 -d > $HOME/.kube/config

      - name: Get version
        id: version
        run: |
          TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"

      - name: Update image tags in manifests
        run: |
          AGENTS=(
            web-gateway
            case-processing
            complexity-routing
            evidence-analysis
            fact-reconstruction
            witness-analysis
            legal-knowledge
            argument-construction
            deliberation
            governance-verdict
          )
          for agent in "${AGENTS[@]}"; do
            sed -i "s|image:.*${agent}:.*|image: ${{ env.IMAGE_PREFIX }}/${agent}:${{ steps.version.outputs.tag }}|" \
              k8s/production/${agent}-deployment.yaml
          done

      - name: Apply Kubernetes manifests (rolling update)
        run: |
          kubectl apply -f k8s/production/ --namespace verdictcouncil

      - name: Wait for rollout
        run: |
          DEPLOYMENTS=$(kubectl get deployments -n verdictcouncil -o name)
          for deploy in $DEPLOYMENTS; do
            kubectl rollout status "$deploy" -n verdictcouncil --timeout=600s
          done

  verify:
    name: Production Verification
    runs-on: ubuntu-latest
    needs: deploy-production
    environment: production
    steps:
      - name: Health check all pods
        run: |
          PROD_URL="${{ secrets.PRODUCTION_URL }}"
          response=$(curl -s -o /dev/null -w "%{http_code}" "${PROD_URL}/health")
          if [ "$response" != "200" ]; then
            echo "Production health check failed with status $response"
            exit 1
          fi
          echo "Production health check passed"

      - name: Canary test case
        run: |
          PROD_URL="${{ secrets.PRODUCTION_URL }}"

          TOKEN=$(curl -s -X POST "${PROD_URL}/auth/login" \
            -H "Content-Type: application/json" \
            -d '{"email":"canary@verdictcouncil.sg","password":"${{ secrets.CANARY_TEST_PASSWORD }}"}' \
            | jq -r '.token')

          CASE_ID=$(curl -s -X POST "${PROD_URL}/api/v1/cases" \
            -H "Authorization: Bearer ${TOKEN}" \
            -F "documents=@tests/fixtures/canary_case.pdf" \
            -F "domain=small_claims" \
            | jq -r '.case_id')

          echo "Canary case submitted: ${CASE_ID}"

          MAX_WAIT=300
          ELAPSED=0
          while [ $ELAPSED -lt $MAX_WAIT ]; do
            STATUS=$(curl -s "${PROD_URL}/api/v1/cases/${CASE_ID}" \
              -H "Authorization: Bearer ${TOKEN}" \
              | jq -r '.status')
            if [ "$STATUS" = "ready_for_review" ] || [ "$STATUS" = "escalated" ]; then
              echo "Canary passed with status: ${STATUS}"
              exit 0
            fi
            sleep 10
            ELAPSED=$((ELAPSED + 10))
          done
          echo "Canary test did not complete within ${MAX_WAIT}s"
          exit 1

  create-release:
    name: Create GitHub Release
    runs-on: ubuntu-latest
    needs: verify
    permissions:
      contents: write
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Get version
        id: version
        run: |
          TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"

      - name: Create GitHub Release
        run: |
          gh release create "${{ steps.version.outputs.tag }}" \
            --title "${{ steps.version.outputs.tag }}" \
            --generate-notes \
            --target main
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## 6.6 Kubernetes Manifest Examples

### Agent Deployment + Service (Template)

```yaml
# k8s/base/agent-deployment.yaml (example: case-processing)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: case-processing
  namespace: verdictcouncil
  labels:
    app: case-processing
    component: agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: case-processing
  template:
    metadata:
      labels:
        app: case-processing
        component: agent
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "9090"
    spec:
      serviceAccountName: verdictcouncil-agent
      containers:
        - name: case-processing
          image: ghcr.io/verdictcouncil/case-processing:latest
          ports:
            - containerPort: 9090
              name: metrics
          envFrom:
            - configMapRef:
                name: agent-common-config
            - secretRef:
                name: verdictcouncil-secrets
          env:
            - name: AGENT_NAME
              value: "case-processing"
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: /health
              port: 9090
            initialDelaySeconds: 10
            periodSeconds: 15
          livenessProbe:
            httpGet:
              path: /health
              port: 9090
            initialDelaySeconds: 30
            periodSeconds: 30
      restartPolicy: Always
---
apiVersion: v1
kind: Service
metadata:
  name: case-processing-svc
  namespace: verdictcouncil
  labels:
    app: case-processing
spec:
  type: ClusterIP
  selector:
    app: case-processing
  ports:
    - port: 9090
      targetPort: 9090
      name: metrics
```

### PostgreSQL StatefulSet

```yaml
# k8s/base/postgresql-statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgresql
  namespace: verdictcouncil
  labels:
    app: postgresql
    component: database
spec:
  serviceName: postgresql-svc
  replicas: 1
  selector:
    matchLabels:
      app: postgresql
  template:
    metadata:
      labels:
        app: postgresql
        component: database
    spec:
      containers:
        - name: postgresql
          image: postgres:16
          ports:
            - containerPort: 5432
              name: postgres
          env:
            - name: POSTGRES_DB
              value: verdictcouncil
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: verdictcouncil-secrets
                  key: POSTGRES_USER
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: verdictcouncil-secrets
                  key: POSTGRES_PASSWORD
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
          volumeMounts:
            - name: postgresql-data
              mountPath: /var/lib/postgresql/data
          readinessProbe:
            exec:
              command:
                - pg_isready
                - -U
                - $(POSTGRES_USER)
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            exec:
              command:
                - pg_isready
                - -U
                - $(POSTGRES_USER)
            initialDelaySeconds: 30
            periodSeconds: 30
  volumeClaimTemplates:
    - metadata:
        name: postgresql-data
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 50Gi
---
apiVersion: v1
kind: Service
metadata:
  name: postgresql-svc
  namespace: verdictcouncil
  labels:
    app: postgresql
spec:
  type: ClusterIP
  selector:
    app: postgresql
  ports:
    - port: 5432
      targetPort: 5432
      name: postgres
```

### Redis StatefulSet

```yaml
# k8s/base/redis-statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
  namespace: verdictcouncil
  labels:
    app: redis
    component: cache
spec:
  serviceName: redis-svc
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
        component: cache
    spec:
      containers:
        - name: redis
          image: redis:7
          ports:
            - containerPort: 6379
              name: redis
          command:
            - redis-server
            - --maxmemory
            - 256mb
            - --maxmemory-policy
            - allkeys-lru
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 300Mi
          readinessProbe:
            exec:
              command:
                - redis-cli
                - ping
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            exec:
              command:
                - redis-cli
                - ping
            initialDelaySeconds: 15
            periodSeconds: 20
          volumeMounts:
            - name: redis-data
              mountPath: /data
  volumeClaimTemplates:
    - metadata:
        name: redis-data
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 5Gi
---
apiVersion: v1
kind: Service
metadata:
  name: redis-svc
  namespace: verdictcouncil
  labels:
    app: redis
spec:
  type: ClusterIP
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379
      name: redis
```

### ConfigMap for Agent Configuration

```yaml
# k8s/base/agent-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-common-config
  namespace: verdictcouncil
  labels:
    component: config
data:
  SOLACE_BROKER_URL: "tcp://solace-broker-svc:55555"
  SOLACE_BROKER_VPN: "verdictcouncil"
  # NOTE: DATABASE_URL is NOT set here. Kubernetes ConfigMaps do not perform
  # $(VAR) interpolation — those are literal strings, not resolved references.
  # DATABASE_URL is constructed at container startup via an entrypoint script
  # that reads POSTGRES_USER and POSTGRES_PASSWORD from the Secret and builds
  # the connection string dynamically. See the entrypoint.sh in each agent's
  # Docker image.
  POSTGRES_HOST: "postgresql-svc"
  POSTGRES_PORT: "5432"
  POSTGRES_DB: "verdictcouncil"
  REDIS_URL: "redis://redis-svc:6379/0"
  FASTAPI_HOST: "0.0.0.0"
  FASTAPI_PORT: "8000"
  LOG_LEVEL: "INFO"
  NAMESPACE: "verdictcouncil"
  PRECEDENT_CACHE_TTL_SECONDS: "86400"
  JUDICIARY_BASE_URL: "https://www.judiciary.gov.sg"
  PAIR_BASE_URL: "https://search.pair.gov.sg"
```

### Secret for Credentials

```yaml
# k8s/base/secrets.yaml (values are base64-encoded placeholders)
apiVersion: v1
kind: Secret
metadata:
  name: verdictcouncil-secrets
  namespace: verdictcouncil
  labels:
    component: secrets
type: Opaque
data:
  OPENAI_API_KEY: "BASE64_ENCODED_VALUE"
  SOLACE_BROKER_USERNAME: "BASE64_ENCODED_VALUE"
  SOLACE_BROKER_PASSWORD: "BASE64_ENCODED_VALUE"
  POSTGRES_USER: "BASE64_ENCODED_VALUE"
  POSTGRES_PASSWORD: "BASE64_ENCODED_VALUE"
  JWT_SECRET: "BASE64_ENCODED_VALUE"
```

### HorizontalPodAutoscaler for Web Gateway

```yaml
# k8s/base/web-gateway-hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-gateway-hpa
  namespace: verdictcouncil
  labels:
    app: web-gateway
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web-gateway
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 1
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
```

### Ingress

```yaml
# k8s/base/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: verdictcouncil-ingress
  namespace: verdictcouncil
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - api.verdictcouncil.sg
      secretName: verdictcouncil-tls
  rules:
    - host: api.verdictcouncil.sg
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: web-gateway-svc
                port:
                  number: 8000
```

## 6.7 Environment Promotion Diagram

```mermaid
flowchart LR
    subgraph Feature["Feature Development"]
        FEAT["feat/* branch"]
    end

    subgraph CI["CI Pipeline"]
        LINT["Lint & Type Check"]
        UNIT["Unit Tests"]
        INTEG["Integration Tests"]
        SEC["Security Scan"]
        BUILD["Docker Build Test"]
    end

    subgraph Integration["Integration"]
        DEV["development branch"]
    end

    subgraph Staging["Staging Pipeline"]
        REL["release/* branch"]
        SBUILD["Build & Push<br/>rc-{sha} tags"]
        SDEPLOY["Deploy Staging<br/>K8s namespace:<br/>verdictcouncil-staging"]
        SMOKE["Smoke Test"]
    end

    subgraph Production["Production Pipeline"]
        MAIN["main branch"]
        PBUILD["Build & Push<br/>v{semver} tags"]
        PDEPLOY["Deploy Production<br/>K8s namespace:<br/>verdictcouncil"]
        VERIFY["Verify & Canary"]
        RELEASE["GitHub Release<br/>+ Auto Notes"]
    end

    FEAT -->|"push / PR"| LINT
    LINT --> UNIT
    UNIT --> INTEG
    LINT --> SEC
    UNIT --> BUILD
    SEC --> BUILD

    BUILD -->|"PR merge"| DEV

    DEV -->|"merge when stable"| REL
    REL --> SBUILD
    SBUILD --> SDEPLOY
    SDEPLOY --> SMOKE

    SMOKE -->|"QA approved<br/>merge"| MAIN
    MAIN --> PBUILD
    PBUILD --> PDEPLOY
    PDEPLOY --> VERIFY
    VERIFY --> RELEASE

    style FEAT fill:#4a9eff,color:#fff
    style DEV fill:#ffa500,color:#fff
    style REL fill:#ff6b6b,color:#fff
    style MAIN fill:#2ecc71,color:#fff
    style RELEASE fill:#2ecc71,color:#fff
```

---

# Appendices

## Appendix A: Cost Model

### Per-Case Cost Breakdown

| # | Agent | Model | Est. Input Tokens | Est. Output Tokens | Est. Cost (USD) |
|---|---|---|---|---|---|
| 1 | CaseProcessing | gpt-4.1-mini | ~5,000 | ~2,000 | $0.010 |
| 2 | ComplexityRouting | o4-mini | ~2,000 | ~500 | $0.010 |
| 3 | EvidenceAnalysis | gpt-4.1 | ~15,000 | ~5,000 | $0.030 |
| 4 | FactReconstruction | gpt-4.1 | ~10,000 | ~3,000 | $0.020 |
| 5 | WitnessAnalysis | o4-mini | ~8,000 | ~3,000 | $0.020 |
| 6 | LegalKnowledge | gpt-4.1 | ~10,000 | ~5,000 | $0.030 |
| 7 | ArgumentConstruction | o3 | ~12,000 | ~5,000 | $0.050 |
| 8 | Deliberation | o3 | ~15,000 | ~5,000 | $0.050 |
| 9 | GovernanceVerdict | o3 | ~10,000 | ~3,000 | $0.040 |
| | **Total LLM per case** | | **~87,000** | **~31,500** | **$2.10 - $2.60** |

### Infrastructure Costs

| Item | Cost | Notes |
|---|---|---|
| OpenAI Vector Store | ~$0.005/day | 50MB statute corpus at $0.10/GB/day |
| search_precedents (live) | Negligible | Results cached in Redis for 24h; judiciary.gov.sg and PAIR are free public APIs |
| Solace PubSub+ | $0/month | Community edition for development; enterprise licensing for production |
| Kubernetes cluster | Variable | Cloud-provider dependent; estimated 3-5 nodes for production |

### Monthly Projections

| Volume | Cases/Month | Est. LLM Cost | Vector Store | Total |
|---|---|---|---|---|
| Low | 50 | $105 - $130 | $0.15 | ~$130 |
| Medium | 200 | $420 - $520 | $0.15 | ~$520 |
| High | 500 | $1,050 - $1,300 | $0.15 | ~$1,300 |

## Appendix B: Environment Variables Reference

### Required Variables

| Variable | Description | Example | Used By |
|---|---|---|---|
| `OPENAI_API_KEY` | OpenAI API authentication key | `sk-proj-...` | All agents |
| `SOLACE_BROKER_URL` | Solace broker connection URL | `tcp://solace-broker-svc:55555` | All agents, web-gateway |
| `SOLACE_BROKER_VPN` | Solace message VPN name | `verdictcouncil` | All agents, web-gateway |
| `SOLACE_BROKER_USERNAME` | Solace broker username | `vc-agent` | All agents, web-gateway |
| `SOLACE_BROKER_PASSWORD` | Solace broker password | `(secret)` | All agents, web-gateway |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@postgresql-svc:5432/verdictcouncil` | All agents, web-gateway |
| `REDIS_URL` | Redis connection string | `redis://redis-svc:6379/0` | legal-knowledge, web-gateway |
| `JWT_SECRET` | Secret key for JWT signing | `(secret, 256-bit)` | web-gateway |

### Application Configuration

| Variable | Description | Default | Used By |
|---|---|---|---|
| `NAMESPACE` | Kubernetes namespace | `verdictcouncil` | All pods |
| `FASTAPI_HOST` | Web gateway bind address | `0.0.0.0` | web-gateway |
| `FASTAPI_PORT` | Web gateway bind port | `8000` | web-gateway |
| `LOG_LEVEL` | Python logging level | `INFO` | All agents |
| `AGENT_NAME` | Agent identifier for logging/audit | `(per-agent)` | Each agent pod |
| `PRECEDENT_CACHE_TTL_SECONDS` | Redis cache TTL for precedent searches | `86400` (24h) | legal-knowledge |
| `JUDICIARY_BASE_URL` | Singapore judiciary website base URL | `https://www.judiciary.gov.sg` | legal-knowledge |
| `PAIR_BASE_URL` | PAIR search API base URL | `https://search.pair.gov.sg` | legal-knowledge |

### OpenAI Configuration

| Variable | Description | Default | Used By |
|---|---|---|---|
| `OPENAI_VECTOR_STORE_ID` | Vector store ID for statute corpus | `vs_...` | legal-knowledge |
| `OPENAI_MODEL_LIGHTWEIGHT` | Model for lightweight tasks | `gpt-4.1-mini` | case-processing |
| `OPENAI_MODEL_FAST_REASONING` | Model for fast reasoning | `o4-mini` | complexity-routing, witness-analysis |
| `OPENAI_MODEL_BALANCED` | Model for balanced analysis | `gpt-4.1` | evidence-analysis, fact-reconstruction, legal-knowledge |
| `OPENAI_MODEL_DEEP_REASONING` | Model for deep reasoning | `o3` | argument-construction, deliberation, governance-verdict |

### Database Configuration (PostgreSQL pod)

| Variable | Description | Example |
|---|---|---|
| `POSTGRES_DB` | Database name | `verdictcouncil` |
| `POSTGRES_USER` | Database superuser | `vc_admin` |
| `POSTGRES_PASSWORD` | Database password | `(secret)` |
| `PGDATA` | Data directory path | `/var/lib/postgresql/data/pgdata` |

## Appendix C: Glossary

| Abbreviation | Full Name | Description |
|---|---|---|
| **SAM** | Solace Agent Mesh | Event-driven multi-agent orchestration framework built on Solace PubSub+ |
| **A2A** | Agent-to-Agent | Communication protocol where agents exchange messages via broker topics |
| **SCT** | Small Claims Tribunals | Singapore tribunal handling civil claims up to $20,000 (or $30,000 by consent) |
| **SCTA** | Small Claims Tribunals Act | Cap. 308, governing statute for SCT proceedings |
| **RTA** | Road Traffic Act | Cap. 276, governing statute for traffic offences in Singapore |
| **CPFTA** | Consumer Protection (Fair Trading) Act | Cap. 52A, consumer protection statute relevant to small claims |
| **SOGA** | Sale of Goods Act | Cap. 393, governing statute for sale of goods disputes |
| **RAG** | Retrieval-Augmented Generation | Technique that retrieves relevant documents before generating LLM responses |
| **LiteLLM** | LiteLLM | Unified API abstraction layer for multiple LLM providers; used internally by SAM |
| **PubSub+** | Solace PubSub+ Event Broker | Enterprise message broker supporting publish/subscribe, queueing, and event mesh patterns |
| **VPN** | Message VPN | Solace virtual partition for isolating message traffic (not a network VPN) |
| **HPA** | Horizontal Pod Autoscaler | Kubernetes resource that automatically scales pod replicas based on metrics |
| **PVC** | Persistent Volume Claim | Kubernetes abstraction for requesting persistent storage |
| **GHCR** | GitHub Container Registry | Container image registry integrated with GitHub |
| **SemVer** | Semantic Versioning | Versioning scheme: MAJOR.MINOR.PATCH |
| **PAIR** | Platform for AI-assisted Research | Singapore government legal research platform |
| **JWT** | JSON Web Token | Compact, URL-safe token format for authentication claims |
| **OCR** | Optical Character Recognition | Technology to extract text from images and scanned documents |
| **TTL** | Time to Live | Duration for which cached data remains valid before expiry |

---

# Part 7: Contestable Judgment Mode

---

## 7.1 Overview

The Contestable Judgment Mode transforms VerdictCouncil from a static recommendation engine into an **interactive judicial simulator**. After receiving an initial verdict recommendation, the presiding judge can manipulate the underlying inputs and observe how the outcome changes in near-real-time.

| Modification | Description | Example |
|---|---|---|
| **Fact toggle** | Flip a fact from "agreed" to "disputed" or vice versa | Toggle whether the defendant was present at the scene |
| **Evidence exclusion/inclusion** | Remove or restore a specific exhibit from consideration | Exclude a contested CCTV recording |
| **Witness credibility adjustment** | Up-rank or down-rank a witness's credibility score | Reduce credibility of a witness with prior perjury |
| **Legal interpretation change** | Substitute an alternative interpretation of a statutory provision | Apply a narrow reading of a section |

The system re-runs **only the downstream agents** affected by the change, producing an updated verdict alongside a structured diff view. The original verdict is always preserved; what-if variants are stored as separate, linked records.

This serves two judicial needs:
1. **Pre-judgment stress testing** — the judge explores whether the verdict is robust before delivering it.
2. **Appellate reasoning support** — the judge can document that even under alternative assumptions, the outcome holds (or identify precisely which facts are dispositive).

---

## 7.2 What-If Re-Execution Engine

### 7.2.1 Architecture

A dedicated **What-If Controller** service sits between the web gateway and the Solace PubSub+ broker. It intercepts the judge's modification request, mutates the CaseState payload, determines the correct re-entry point in the agent pipeline, and publishes directly to the appropriate mid-pipeline topic.

```
Judge → WebGateway → WhatIfController → SolaceBroker → Agent(N) → ... → Agent(9) → WebGateway → Judge
```

**Primary path: mid-pipeline topic publishing.** SAM's topic-based routing makes this possible without custom orchestration. The What-If Controller publishes a modified payload to whichever agent's input topic corresponds to the re-execution start point.

### 7.2.2 Change Impact Mapping

| Modification Type | Re-execution Start | Agents Re-run | Rationale |
|---|---|---|---|
| Fact toggled (agreed ↔ disputed) | Agent 7 (Argument Construction) | 7 → 8 → 9 | Facts feed into argument framing; upstream evidence analysis is unaffected |
| Evidence excluded/included | Agent 3 (Evidence Analysis) | 3 → 4 → 5 → 6 → 7 → 8 → 9 | Evidence exclusion changes the evidentiary foundation |
| Witness credibility changed | Agent 7 (Argument Construction) | 7 → 8 → 9 | Credibility scores are consumed during argument weighting |
| Legal interpretation changed | Agent 7 (Argument Construction) | 7 → 8 → 9 | Interpretive shifts affect argument construction |

### 7.2.3 CaseState Mutation

1. Deep-clone the original CaseState from the completed pipeline run (stored in PostgreSQL).
2. Apply the judge's modification to the cloned state.
3. Tag the payload with metadata: `what_if_scenario_id`, `original_verdict_id`, `modification_type`, `modification_description`.
4. Publish the mutated payload to the appropriate agent's input topic.

Downstream agents process the payload identically to a normal run — they are unaware they are operating in what-if mode.

### 7.2.4 Fallback: Full Pipeline with Short-Circuit Caching

If partial re-execution proves unreliable, the system falls back to a full pipeline re-run with short-circuit caching:

1. All upstream agent outputs cached in Redis (TTL 24h), keyed by `case_id:agent_id:input_hash`.
2. Full pipeline triggered from Agent 1.
3. Each agent checks Redis before executing — cache hit returns immediately (< 50ms).
4. First cache miss triggers normal execution for that agent and all downstream.

```
Agent 1 → [cache hit, skip] →
Agent 2 → [cache hit, skip] →
Agent 3 → [cache MISS, execute] →     ← modification affects this agent
Agent 4 → [execute] → ... → Agent 9 → [execute] → result
```

---

## 7.3 Verdict Stability Score

### 7.3.1 Method

Systematically perturb the case inputs and observe whether the verdict holds:

| Mode | Description | When Used |
|---|---|---|
| **Sampled (default)** | Top N=5 perturbations ranked by citation frequency in the Deliberation Agent's reasoning chain | Default for all cases |
| **Exhaustive** | Toggle every binary fact and exclude every exhibit, one at a time | On explicit judge request; cases with ≤ 15 toggleable inputs |

### 7.3.2 Formula

```
stability_score = (perturbations_where_verdict_holds / N) × 100
```

| Score Range | Classification | Judicial Guidance |
|---|---|---|
| 85–100 | **Stable** | Verdict is robust. Strong basis for judgment. |
| 60–84 | **Moderately sensitive** | Verdict holds in most scenarios but is sensitive to specific inputs. |
| 0–59 | **Highly sensitive** | Verdict changes under multiple perturbations. Heightened scrutiny warranted. |

> **Score Calibration Disclaimer:** All numerical scores in VerdictCouncil (credibility scores from Agent 5, confidence scores from Agent 9, and stability scores from what-if analysis) are relative indicators produced by LLM reasoning, not statistically calibrated measurements. They should be interpreted as directional signals (higher = stronger support) rather than precise probabilities. The system does not claim statistical validity for these scores. Judges should treat them as one input among many, not as determinative.

### 7.3.3 Execution Model

The stability score is computed **asynchronously**:
1. Initial verdict recommendation returned to the judge immediately.
2. Judge clicks "Assess Stability" (or auto-triggers per configuration).
3. What-If Controller spawns N perturbation runs **in parallel**.
4. Progress indicator updates as each perturbation completes.
5. Score computed and persisted once all N finish.
6. Results populate within 2-3 minutes.

### 7.3.4 Cost

| Component | Cost per Run | N=5 Total |
|---|---|---|
| Agent 7 (Argument Construction, o3) | ~$0.50 | $2.50 |
| Agent 8 (Deliberation, o3) | ~$0.50 | $2.50 |
| Agent 9 (Governance & Verdict, o3) | ~$0.40 | $2.00 |
| **Total** | **~$1.40** | **~$7.00** |

---

## 7.4 Diff View

Structured before/after comparison:

| Component | Display |
|---|---|
| **Fact status** | Changed facts highlighted with old → new status |
| **Evidence items** | Excluded items with strikethrough; included with addition marker |
| **Argument strengths** | Side-by-side percentage bars with delta (Δ%) |
| **Deliberation reasoning** | Inline diff (additions green, removals red) |
| **Verdict outcome** | Bold indicator if outcome changed |
| **Confidence score** | Score with delta (Δ%) |

---

## 7.5 Latency Budget

| Scenario | Agents Re-run | Target Latency |
|---|---|---|
| Fact / witness / legal change | Agents 7, 8, 9 | **≤ 45s** |
| Evidence exclusion (with parallel 3-5) | Agents 3–9 | **≤ 50s** |
| Stability score (N=5, parallel) | 5× Agents 7–9 | **≤ 180s (async)** |

---

## 7.6 User Stories

### US-031: Toggle Facts in What-If Mode

**Actor:** Tribunal Magistrate / Judge

As a judicial officer reviewing a completed case analysis, I want to toggle a fact between "agreed" and "disputed" and see how the verdict changes, so that I can assess whether the verdict is sensitive to that specific factual finding.

**Acceptance Criteria:**
- A "What-If Mode" toggle is visible on completed case views
- Judge can click any binary fact to toggle its status with a visual indicator
- Clicking "Re-analyse" re-runs Agents 7–9 with modified CaseState and shows agent-level progress
- Diff view shows argument strength deltas, reasoning chain diffs, verdict outcome comparison
- Original verdict is preserved alongside the what-if variant
- What-if scenario is persisted with full audit trail (modification type, values, timestamps, user ID)
- Judge can cancel an in-progress re-analysis

**Happy Flow:**
1. Judge opens a completed case from the case list.
2. Judge clicks "What-If Mode" in the case toolbar.
3. Facts, evidence, and witness credibility scores become interactive/editable.
4. Judge clicks on Fact F-003 ("Defendant was present at the scene") — status toggles from "agreed" to "disputed."
5. Judge clicks "Re-analyse."
6. Progress indicator: "Re-running Argument Construction (Agent 7)..."
7. After ~10s: "Re-running Deliberation (Agent 8)..."
8. After ~20s: "Synthesizing Verdict (Agent 9)..."
9. After ~30s, diff view appears: Prosecution 72% → 65% (Δ-7%), Defence 58% → 64% (Δ+6%), Verdict: Convict → Acquit, Confidence: 78% → 54% (Δ-24%).
10. Judge reviews the diff, saves the what-if scenario for the case record.

**Domain Notes:**
- SCT: Facts classified as "agreed" (undisputed) or "disputed" (contested). Toggling simulates how the case resolves under alternative factual assumptions.
- Traffic: Toggling facts related to specific charges may affect multiple charge outcomes.

---

### US-032: View Verdict Stability Score

**Actor:** Tribunal Magistrate / Judge

As a judicial officer who has received a verdict recommendation, I want to see a stability score indicating how robust the verdict is across perturbations, so that I can gauge whether the recommendation is reliable or finely balanced.

**Acceptance Criteria:**
- "Assess Stability" button visible on completed verdict summaries
- Progress indicator shows perturbation completion (e.g., "2/5 complete")
- Score displayed as 0–100 with classification label and colour coding (green/amber/red)
- Breakdown view shows each perturbation: what changed, whether verdict held, confidence score
- Judge can drill into any perturbation to see its full diff view
- Stability computation does not block the judge from reviewing the original verdict
- Computed score is persisted and does not require recomputation

**Happy Flow:**
1. Judge reviews initial verdict for Case C-2025-0042.
2. Judge clicks "Assess Stability."
3. System acknowledges: "Computing stability score... This will take 2–3 minutes."
4. Progress indicator: "Perturbation 1/5: toggling Fact F-003..."
5. After ~30s: "1/5 complete. Verdict held."
6. Judge continues reading detailed reasoning while perturbations run.
7. After ~150s, all 5 perturbations complete.
8. Stability score appears: **80/100 — Moderately Sensitive** (amber).
9. Judge clicks score for breakdown: P1 held (71%), P2 **flipped** (52%), P3 held (74%), P4 held (68%), P5 held (76%).
10. Judge clicks P2 to see full diff view — Exhibit E-007 (CCTV footage) is dispositive.
11. Judge uses this insight to focus oral submissions on admissibility of E-007.

---

### US-033: Pipeline Replay (View Agent Reasoning)

**Actor:** Tribunal Magistrate / Judge

As a judicial officer reviewing a verdict recommendation, I want to inspect the reasoning of each individual agent in the pipeline, so that I can understand why the system reached its conclusion and verify the logic at each step.

**Acceptance Criteria:**
- Pipeline view shows all 9 agents in sequence with status indicators and execution times
- Clicking any agent's node opens a detail panel showing: input payload, system prompt, tool calls, LLM response, structured output
- All data sourced from SAM's audit trail (Solace message IDs, timestamps)
- For what-if scenarios, only re-run agents show updated data; skipped agents display "Output unchanged"
- Judge can copy full input/output payload for external reference
- Agent execution times displayed for bottleneck identification

**Happy Flow:**
1. Judge opens Case C-2025-0042 and navigates to "Pipeline" tab.
2. Horizontal pipeline visualisation shows all 9 agents with green checkmarks and execution times.
3. Judge clicks Agent 8 (Deliberation).
4. Detail panel opens with tabs: Input, Prompt, Reasoning, Output.
5. Judge reads the deliberation reasoning, noting heavy weighting of CCTV evidence.
6. Judge clicks "Copy Output" for written grounds of decision.
7. Judge navigates to Agent 3 (Evidence Analysis) to verify initial CCTV assessment.

---

## 7.7 Data Model Extensions

### New ERD Entities

```mermaid
erDiagram
    cases ||--o{ what_if_scenarios : "has"
    verdicts ||--o{ what_if_scenarios : "original verdict"
    what_if_scenarios ||--|| what_if_verdicts : "produces"
    cases ||--o{ stability_scores : "has"
    verdicts ||--|| stability_scores : "assessed by"
    users ||--o{ what_if_scenarios : "created by"

    what_if_scenarios {
        uuid id PK
        uuid case_id FK
        uuid original_verdict_id FK
        varchar modified_field
        enum modification_type "toggle_fact | exclude_evidence | include_evidence | change_credibility | change_interpretation"
        jsonb original_value
        jsonb modified_value
        text description
        timestamp created_at
        uuid created_by FK
    }

    what_if_verdicts {
        uuid id PK
        uuid scenario_id FK
        varchar recommendation_type
        text recommended_outcome
        int confidence_score
        jsonb reasoning_diff
        jsonb full_reasoning
        int execution_time_ms
        timestamp created_at
    }

    stability_scores {
        uuid id PK
        uuid case_id FK
        uuid verdict_id FK
        int score
        int perturbation_count
        jsonb perturbations
        enum status "pending | computing | complete | failed"
        timestamp started_at
        timestamp computed_at
    }
```

---

## 7.8 Sequence Diagram: What-If Mode

```mermaid
sequenceDiagram
    actor Judge
    participant WG as WebGateway
    participant WIC as WhatIfController
    participant DB as PostgreSQL
    participant SB as SolaceBroker
    participant A7 as Agent 7 (Arguments)
    participant A8 as Agent 8 (Deliberation)
    participant A9 as Agent 9 (Verdict)

    Judge->>WG: Toggle Fact F-003 (agreed → disputed)
    WG->>WIC: POST /what-if {case_id, modification}
    WIC->>DB: Fetch original CaseState & verdict
    DB-->>WIC: CaseState + verdict payload
    WIC->>WIC: Clone CaseState, apply modification
    WIC->>WIC: Determine start agent (fact toggle → Agent 7)
    WIC->>DB: INSERT what_if_scenarios
    WIC->>SB: Publish modified CaseState to agent7/input
    WIC-->>WG: 202 Accepted {scenario_id}
    WG-->>Judge: "Re-analysing..." (progress indicator)

    SB->>A7: Deliver modified CaseState
    A7->>A7: Construct arguments with modified facts
    A7->>SB: Publish to agent8/input
    Note over WG,A8: WebSocket: "Agent 7 complete"

    SB->>A8: Deliver Agent 7 output
    A8->>A8: Deliberate with modified arguments
    A8->>SB: Publish to agent9/input
    Note over WG,A9: WebSocket: "Agent 8 complete"

    SB->>A9: Deliver Agent 8 output
    A9->>A9: Synthesize verdict
    A9->>SB: Publish to whatif/result

    SB->>WIC: Deliver what-if result
    WIC->>WIC: Generate diff (original vs what-if)
    WIC->>DB: INSERT what_if_verdicts
    WIC->>WG: WebSocket: result + diff
    WG->>Judge: Display diff view
```

---

## 7.9 What-If Controller Class

```mermaid
classDiagram
    class WhatIfController {
        -caseStateRepo: CaseStateRepository
        -verdictRepo: VerdictRepository
        -scenarioRepo: ScenarioRepository
        -stabilityRepo: StabilityRepository
        -solacePublisher: SolacePublisher
        -redisCache: RedisCache
        -diffEngine: DiffEngine
        +receive_modification(case_id, modification) ScenarioResponse
        +determine_start_agent(modification_type) AgentId
        +build_modified_case_state(original, modification) CaseState
        +publish_to_mid_pipeline(case_state, start_agent) void
        +compute_stability_score(case_id, verdict_id, n) StabilityScore
        +generate_diff_view(original, whatif) DiffPayload
        +cancel_scenario(scenario_id) void
    }

    class Modification {
        +type: ModificationType
        +field: String
        +from_value: Any
        +to_value: Any
        +description: String
    }

    class DiffPayload {
        +scenario_id: UUID
        +facts_changed: List~FactDiff~
        +argument_strengths: ArgumentStrengthDiff
        +deliberation_diff: TextDiff
        +verdict_outcome: OutcomeDiff
        +confidence_delta: int
    }

    class StabilityScore {
        +score: int
        +classification: String
        +perturbations: List~PerturbationResult~
    }

    WhatIfController --> Modification
    WhatIfController --> DiffPayload
    WhatIfController --> StabilityScore
```

---

## 7.10 Docker Compose — Demo Mode

One-command startup for the complete VerdictCouncil system:

```bash
docker compose up -d      # Start all services
docker compose ps          # Verify health
docker compose down -v     # Tear down
```

The `docker-compose.yml` includes:
- **Solace PubSub+** broker (solace/solace-pubsub-standard)
- **PostgreSQL 16** with init scripts and seed data
- **Redis 7** for caching (search_precedents, short-circuit)
- **Web Gateway** (FastAPI, port 8000)
- **What-If Controller** (port 8001)
- **9 Agent containers** (each a lightweight SAM process with single agent config)
- Health checks on all containers
- Named network (`verdictcouncil-network`)
- Persistent volumes for broker, database, and cache data
- `.env` file for all credentials and configuration

Pre-loaded test data includes:
- 3 sample traffic cases (speeding, red light, careless driving)
- 2 sample SCT cases (sale of goods, provision of services)
- RTA and SCTA statutes in OpenAI vector stores (pre-uploaded)
- 50 pre-cached precedent results in Redis

---

## Appendix D: Evaluation Framework

### D.1 Purpose

The evaluation framework defines curated test cases with known expected outcomes, enabling the team to verify that VerdictCouncil produces correct, grounded, and well-reasoned recommendations. Each test case specifies the input documents, expected agent outputs at each pipeline stage, and the expected verdict recommendation. This serves as a gold set for regression testing and demo validation.

### D.2 Evaluation Dimensions

| Dimension | What It Measures | Target | How Measured |
|---|---|---|---|
| **Extraction accuracy** | Are facts, parties, and evidence correctly extracted from documents? | 95%+ of facts extracted with correct source citations | Manual comparison against ground truth per test case |
| **Retrieval precision** | Do the retrieved statutes and precedents match the expected legal provisions? | 80%+ of expected statutes retrieved in top-10 results | Expected statutes list vs Agent 6 output |
| **Citation grounding** | Are all cited statutes and precedents real (no hallucinations)? | 100% verifiable citations | Every citation cross-checked against vector store or live judiciary source |
| **Reasoning soundness** | Does the deliberation chain follow logically from evidence to conclusion? | No unsupported logical leaps in the reasoning chain | Manual review of Agent 8 reasoning chain |
| **Verdict alignment** | Does the recommended verdict match the expected outcome for the test case? | Correct verdict in all 3 test cases | Direct comparison: expected vs actual |
| **Fairness audit** | Does Agent 9 correctly flag planted biases and pass clean cases? | 100% detection of planted biases, 0% false positives on clean cases | Binary check per test case |
| **What-if sensitivity** | Does toggling the dispositive fact produce the expected verdict flip? | Correct flip in all cases where one is expected | Toggle dispositive fact, check verdict change |

### D.3 Test Case 1: Traffic Speeding (Straightforward Conviction)

**Case ID:** EVAL-TRAFFIC-001
**Domain:** Traffic Violation
**Complexity:** Low
**Expected Verdict:** Guilty — fine $800, 6 demerit points

#### Input Documents

| Document | Type | Party | Description |
|---|---|---|---|
| `charge-sheet-001.pdf` | Charge sheet | Prosecution | Speeding charge: 92 km/h in 70 km/h zone on PIE, 14 March 2025, 2:35 AM |
| `speed-camera-report.pdf` | Evidence | Prosecution | Calibrated speed camera report: device ID SC-4421, last calibrated 2025-01-10, speed recorded 92 km/h, photo of vehicle with licence plate SGX1234A |
| `accused-statement.pdf` | Statement | Defence | Accused admits driving the vehicle, disputes speed reading, claims camera may be inaccurate, no supporting evidence for dispute |
| `driving-record.pdf` | Evidence | Prosecution | Clean driving record, no prior offences in 10 years |

#### Expected Pipeline Outputs

| Agent | Key Expected Outputs |
|---|---|
| 1. Case Processing | Domain: traffic_violation. Offence: speeding (s 63(1) RTA). Jurisdiction: valid. |
| 2. Complexity & Routing | Complexity: low. Route: proceed_automated. |
| 3. Evidence Analysis | Speed camera report: strong (calibrated, recent certification). Accused statement: weak (disputes without evidence). |
| 4. Fact Reconstruction | Timeline: single event, 14 Mar 2025 02:35. Disputed: speed reading accuracy. Agreed: accused was driving. |
| 5. Witness Analysis | No witnesses. Credibility of accused statement: 35/100 (disputes calibrated equipment without evidence). |
| 6. Legal Knowledge | s 63(1) RTA: exceeding speed limit. s 131(2) RTA: penalties. Precedents: PP v Lim [2024] (similar speed, $700 fine). |
| 7. Argument Construction | Prosecution strength: 85%. Defence strength: 25%. Weakness: accused's dispute is unsupported. |
| 8. Deliberation | Established: accused drove at 92 km/h in 70 km/h zone. Camera calibrated. Dispute without evidence. Conclusion: guilty. |
| 9. Governance & Verdict | Fairness: PASS. Verdict: guilty. Sentence: $800 fine, 6 demerit points. Confidence: 88%. Alternative: $600-1000 fine range. |

#### What-If Test

| Toggle | Expected Result |
|---|---|
| Exclude speed camera report | Verdict flips to NOT GUILTY (no evidence of speed). Stability drops to 0%. |
| Toggle "accused was driving" to disputed | Verdict flips to NOT GUILTY (identity not established). |
| Change accused credibility to 80/100 | Verdict remains GUILTY but confidence drops to 65%. Speed camera evidence still dispositive. |

---

### D.4 Test Case 2: Traffic Red Light (Contested, Multiple Witnesses)

**Case ID:** EVAL-TRAFFIC-002
**Domain:** Traffic Violation
**Complexity:** Medium
**Expected Verdict:** Guilty — fine $400, 12 demerit points

#### Input Documents

| Document | Type | Party | Description |
|---|---|---|---|
| `charge-sheet-002.pdf` | Charge sheet | Prosecution | Red light violation: junction of Orchard Rd and Scotts Rd, 28 Feb 2025, 6:15 PM |
| `red-light-camera.pdf` | Evidence | Prosecution | Red light camera report: signal was red for 1.8 seconds when vehicle crossed stop line. Photo shows vehicle SGY5678B mid-junction. |
| `witness-statement-officer.pdf` | Statement | Prosecution | Traffic Police officer Sgt Tan: was stationed at junction, observed vehicle crossing after signal turned red, noted approximately 2 seconds into red phase. |
| `witness-statement-passenger.pdf` | Statement | Defence | Passenger Mr Lee: "The light was amber when we entered the junction. It turned red while we were already crossing." |
| `accused-statement-002.pdf` | Statement | Defence | Accused: "The light turned amber as I approached. I was too close to stop safely. I entered on amber but it changed to red during my crossing." |
| `junction-diagram.pdf` | Evidence | Prosecution | Traffic Police junction diagram showing camera position, stop line, and vehicle trajectory. |

#### Expected Pipeline Outputs

| Agent | Key Expected Outputs |
|---|---|
| 1. Case Processing | Domain: traffic_violation. Offence: red light violation (s 120(1)(a) RTA r/w Rule 8 RTR). Jurisdiction: valid. |
| 2. Complexity & Routing | Complexity: medium. Route: proceed_with_review. (Multiple witnesses, factual dispute.) |
| 3. Evidence Analysis | Camera report: strong (1.8s into red is clear violation). Officer statement: medium (corroborates camera but estimated timing). Passenger statement: weak (interested party, contradicts camera data). |
| 4. Fact Reconstruction | Disputed: whether vehicle entered junction on amber or red. Camera data says 1.8s red. Defence says amber entry. |
| 5. Witness Analysis | Sgt Tan: credibility 75/100 (corroborates camera, slight timing variance). Mr Lee: credibility 40/100 (interested party, contradicts objective evidence). |
| 6. Legal Knowledge | s 120(1)(a) RTA: failing to conform to traffic signals. Rule 8 RTR: red signal means stop. Precedent: PP v Wong [2023] (1.5s red, $350 fine, 12 demerit). |
| 7. Argument Construction | Prosecution: 78%. Defence: 35%. Key weakness for defence: camera data is objective, passenger testimony is subjective and contradicted by mechanical evidence. |
| 8. Deliberation | Camera data is dispositive. 1.8s is well past the amber-to-red transition. Defence argument of "entered on amber" contradicted by camera timestamp. |
| 9. Governance & Verdict | Fairness: PASS. Verdict: guilty. Sentence: $400 fine, 12 demerit points. Confidence: 82%. |

#### What-If Test

| Toggle | Expected Result |
|---|---|
| Exclude red light camera report | Verdict becomes uncertain. Officer vs passenger testimony. Confidence drops to 55%. Verdict may flip to NOT GUILTY. |
| Increase passenger credibility to 80/100 | Verdict remains GUILTY (camera data still dispositive) but confidence drops to 70%. |
| Toggle camera timing from 1.8s to 0.3s | Verdict likely flips — 0.3s is within amber-to-red transition. Defence argument of "entered on amber" becomes plausible. |

---

### D.5 Test Case 3: SCT Sale of Goods (Balanced Dispute)

**Case ID:** EVAL-SCT-001
**Domain:** Small Claims Tribunal
**Complexity:** Medium
**Expected Verdict:** Partial compensation — $3,500 of $5,000 claimed

#### Input Documents

| Document | Type | Party | Description |
|---|---|---|---|
| `claim-form.pdf` | Filing | Claimant | Sarah Lim claims $5,000 for defective custom furniture. Ordered custom dining table and 6 chairs on 15 Jan 2025 for $5,000. Delivered 20 Feb 2025. Table has visible warping and 2 chairs have uneven legs. |
| `contract-invoice.pdf` | Evidence | Claimant | Invoice from Woodcraft Pte Ltd: "Custom dining set — solid teak, 8-seater table + 6 chairs. $5,000. Delivery: 4-6 weeks." |
| `photos-defects.zip` | Evidence | Claimant | 12 photos showing: table surface warping (3 photos), chair leg unevenness (4 photos), overall set (5 photos). |
| `response-form.pdf` | Filing | Respondent | Woodcraft Pte Ltd (Mr David Tan, director) responds: admits warping on table but claims it is within normal range for solid teak (wood expands/contracts with humidity). Denies chair defects — says chairs were level when delivered and customer may have damaged them. |
| `woodcraft-warranty.pdf` | Evidence | Respondent | Warranty card: "12-month warranty against manufacturing defects. Normal wood movement (expansion/contraction up to 3mm) is not a defect." |
| `independent-assessment.pdf` | Evidence | Claimant | Assessment by furniture inspector Mr Ahmad: "Table warping exceeds 8mm in centre — beyond normal wood movement. 2 of 6 chairs have measurable leg length discrepancy of 4-5mm, consistent with manufacturing defect, not user damage." |

#### Expected Pipeline Outputs

| Agent | Key Expected Outputs |
|---|---|
| 1. Case Processing | Domain: small_claims. Category: sale_of_goods. Claim: $5,000. Jurisdiction: valid (under $20,000, within 2 years). |
| 2. Complexity & Routing | Complexity: medium. Route: proceed_with_review. (Expert evidence, competing factual claims.) |
| 3. Evidence Analysis | Photos: medium (show defects but severity debatable). Independent assessment: strong (objective, expert, quantified). Warranty: medium (relevant but narrow scope). |
| 4. Fact Reconstruction | Agreed: contract existed, $5,000 paid, furniture delivered 20 Feb 2025. Disputed: whether warping exceeds normal range, whether chairs were defective at delivery. |
| 5. Witness Analysis | No formal witnesses. Claimant credibility: 70/100 (consistent, supported by independent assessment). Respondent credibility: 55/100 (admits table warping but disputes severity, denies chair defects against expert finding). |
| 6. Legal Knowledge | s 13 SOGA: implied condition of satisfactory quality. s 14 SOGA: fitness for purpose. s 35 SCTA: tribunal may order repair, replacement, or refund. Precedent: Tan v Furniture World [2024] (similar defect, partial refund awarded). |
| 7. Argument Construction | Claimant: 65%. Respondent: 45%. Table warping (8mm) clearly exceeds warranty's 3mm threshold. Chair defects supported by independent expert against respondent's denial. Respondent's weakness: warranty threshold contradicts their own position. |
| 8. Deliberation | Table: defective (8mm > 3mm warranty threshold). Chairs: 2 of 6 defective per expert. 4 chairs acceptable. Partial compensation appropriate — not full refund since 4 chairs and overall set are usable. |
| 9. Governance & Verdict | Fairness: PASS. Verdict: partial compensation $3,500 (table replacement cost $2,500 + 2 defective chairs at $500 each). Confidence: 72%. Alternative: full refund $5,000 if tribunal finds set is not fit for purpose as a whole. |

#### What-If Test

| Toggle | Expected Result |
|---|---|
| Exclude independent assessment | Claimant strength drops. Verdict may reduce to $2,000 (table only, based on photos). Chair claim becomes "he said / she said." |
| Toggle "warranty 3mm threshold" fact to disputed | Respondent's position strengthens. Verdict may reduce — warping is contested. |
| Toggle chair defects to "agreed" | Verdict increases to full $5,000 — both table and all disputed chairs are now conceded as defective. |

---

### D.6 Evaluation Execution Protocol

1. **Before demo:** Run all 3 test cases through the full pipeline. Verify each agent's output against the expected outputs table. Flag any discrepancies.
2. **During demo:** Use EVAL-TRAFFIC-001 (simplest case) for the live pipeline demo. Use EVAL-TRAFFIC-002 for the what-if toggle demo (exclude camera report to show verdict flip).
3. **After implementation changes:** Re-run all 3 test cases as regression tests. Any deviation from expected outputs must be investigated and either accepted (with updated expectations) or fixed.

### D.7 Hallucination Detection Checklist

For each test case run, verify:
- [ ] All cited statute sections exist in the vector store (cross-reference against vs_sct or vs_traffic)
- [ ] All cited precedent cases exist in the vector store or live judiciary search results
- [ ] No statute section numbers are fabricated (compare against actual Act text)
- [ ] No precedent citations reference non-existent cases
- [ ] Confidence scores are consistent with evidence strength (strong evidence should not produce low confidence)
- [ ] The reasoning chain does not reference facts not present in the input documents