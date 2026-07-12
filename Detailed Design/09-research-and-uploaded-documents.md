# Research Findings & Uploaded Document Analysis

Two categories of external input that shaped the spec but aren't part of the interview or the live-data discovery: web research into how various third-party systems actually work, and analysis of documents the owner uploaded early in the project.

---

## Web Research Findings

These were verified during the project via web search, and several are flagged 🔎 in the spec itself as "confirmed by research but worth re-verifying at actual build time," since APIs and product offerings can change between spec-writing and implementation.

### USPTO Open Data Portal (ODP)

The modern API is at `api.uspto.gov`, requires an API key, and refreshes daily. It provides file-wrapper documents with direct download URLs, which is exactly what Agent A1's notification-driven USPTO watching design depends on. One dated finding worth flagging: USPTO's older "Developer Hub" was decommissioned in June 2026 — meaning any documentation or tooling referencing the old Developer Hub is stale, and ODP is the only current path.

### CIPO

Confirmed finding: **CIPO has no public API.** This is exactly why the existing CIPO watcher (D7) works by scraping rather than calling an API, and why that approach has to be ported forward rather than replaced with something cleaner. CIPO does publish **IP Horizons** bulk data — a weekly trademark XML dump and a weekly patent data dump — which is the structured backstop used by several agents (A14's Trademarks Journal ingestion, A15's self-represented-applicant detection) rather than depending entirely on live scraping for those functions.

### Xero

The `LinkedTransactions` endpoint is the mechanism for billable-expense linkage, supporting DRAFT and BILLED states — this is the specific API surface M3's expense-to-Xero push depends on. `BankTransactions` covers the Spend Money side of expense entry.

### Microsoft Graph

One clear constraint confirmed: **inline editable Office document embedding is not supported** by Graph — documents have to open via their `webUrl` in Office Online or the Office desktop apps, not embed directly inside the OS's own interface. This shaped M5's design (open-in-new-tab rather than embed). Delta queries and webhooks are the correct mechanism for keeping the OS's document index in sync with externally-added files without constant polling. The `Sites.Selected` permission scope is the appropriate, least-privilege way to grant the OS access to specific SharePoint sites rather than tenant-wide access.

### CIPO Goods & Services Manual

Confirmed to be a genuinely searchable database of pre-approved trademark terms (Nice Classification, 13th edition), and CIPO's 2024 Specificity Guidelines are a real, current practice requirement — both directly inform Agent A4's G&S Harmonizer design.

### Clarivate renewals

Research found no public third-party instruction API for Clarivate's renewals service — their documented integrations are with their own IPMS products (FoundationIP, IPfolio). However, bespoke firm-specific integrations are known to exist in the industry (J A Kemp was found as a cited precedent), meaning a negotiated file/feed channel remains plausible even without a documented public API. This finding directly produced the tiered Tier 1/Tier 2/Tier 3 design in D18 and M4 §7.1, rather than the spec simply assuming an API would exist.

### LinkedIn

Confirmed: LinkedIn's API terms explicitly **prohibit automated messaging**. This is why every LinkedIn-related agent capability in the spec (A14's outreach, general CRM use) is designed as manual-assist only — the OS can draft and track, but a human has to actually click send on LinkedIn itself. This isn't a design preference; it's a hard external constraint the research confirmed early enough to design around from the start rather than discovering it after building something non-compliant.

---

## Uploaded Document Analysis

Six documents plus one forwarded email were uploaded by the owner during the project and analyzed to validate the file-naming and reference conventions against real firm output, rather than relying solely on the owner's verbal description of how things work.

### CIPO correspondence documents

A **Notice of Establishment acknowledgment** for matter 3DB-0001-CA showed the bilingual (English/French) format CIPO correspondence actually takes, and confirmed how maintenance-fee and examination-on-request deadlines get docketed in practice from a real CIPO notice — directly informing M1's deadline-engine design.

**National-entry request letters** provided concrete evidence of **effective-dated fees** in action: the firm's own filings showed a small-entity fee moving from $210.51 to $225.00 across 2023–2024, which became the specific example cited in M3-R6 justifying why the Fee Schedule Service needs to be effective-dated rather than tracking only a single current amount. These letters also confirmed the CIPO deposit account is the firm's standard payment mechanism for these filings.

### US Office Action response

An office action response for US application 18/021,029 (matter 3DB-0001-US), authored by Hans Koenig (the firm's registered US patent attorney, USPTO reg. 46,474), served as a real sample of the firm's actual OA-response voice and structure — useful groundwork for Agent A11's eventual training corpus, even though the bulk of that training will come from the larger legacy `.msg` email corpus during migration.

### Assignment document

An assignment document for matter 3DB-0004-USP confirmed the firm's actual assignment-document format and content structure, informing the document-generation layer's assignment-template design.

### The Tech Everest email — the origin of Agent A15

A forwarded email (`FW__Canadian_trademark_applicants_with_recent_Examiner_s_Reports.msg`) turned out to be more consequential than a typical reference document: it was a genuine vendor pitch from a company called **Tech Everest Intelligence**, offering to sell the firm — as a commercial product — exactly the dataset that became Agent A15: a list of Canadian trademark applicants who filed without an agent of record and then recently received an Examiner's Report, meaning they're self-represented and just hit an objection they likely don't know how to answer. Rather than buying this feed on an ongoing basis, the decision (D26) was to **replicate the capability in-house** as a new agent. The vendor's own stated benchmark — roughly 200–300 qualified leads generated per two-week period — became the informal target for A15 to match or beat. This email also happened to reveal the owner's Calendly link, which was later confirmed as the firm's existing scheduling tool and incorporated into the Prospect Engagement Orchestration design (§12.2) as the incumbent scheduling option.
