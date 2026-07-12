# BrunetCo OS — Project Documentation Set

**Compiled:** 2026-07-07 | **Status:** Specification complete (v1.0-ready), Phase 0 nearly done, build not yet started
**Firm:** Brunet & Co. Ltd. — Canadian & US patent agency, Canadian trademark agency, industrial designs; international foreign-associate network.
**Principal contact:** Rob Brunet (rob@brunetco.com)

---

## What this is

This is a full compilation of an extended specification and discovery project for **BrunetCo OS** — a custom-built IP agency operating system to replace AppColl (the firm's current practice-management system). It was developed entirely through conversation with Claude, including live, credentialed review of the firm's actual AppColl instance, SharePoint tenant, and exported operational data (task rules, contacts, billing history).

The two "official" deliverables the firm has been pinning to Project knowledge — `IP-OS-SPEC-v0.15.md` and `IP-OS-TRACKER.md` — are dense, cumulative documents that compress a lot of reasoning into decision-register table cells. **This documentation set unpacks that same content into logically organized, readable files**, so anyone picking up this project — a new Claude conversation, a developer (James/Omin/Grey), or Rob himself after time away — can reconstruct full context without re-reading the entire chat history.

## How to use this set

- **If you're resuming this project cold:** read this file, then `01-decision-register.md` (the spine — every decision in order, with rationale), then skim the module files (`03`–`06`) for the area you're working on.
- **If you're about to write code:** read `11-build-tracker.md` for what's done/next, then the relevant module file, then `07-agent-roster.md` if agents are involved.
- **If you're validating data/migration assumptions:** `08-appendix-A-file-conventions.md` and `09-discovery-findings-appcoll-sharepoint-data.md` are the ground truth — they're derived from actually looking at the firm's live systems, not from the interview alone.
- **If you want "what's left to decide or obtain":** `12-testing-cutover-open-items-action-items.md`.

## File index

| # | File | Contents |
|---|---|---|
| 00 | `00-README-START-HERE.md` | This file — navigation and project status |
| 01 | `01-decision-register.md` | All 37 decisions (D1–D37), full text, in order, with the reasoning behind each |
| 02 | `02-vision-principles-architecture.md` | Vision/objectives, design principles, technical architecture, the family-centric data model, blockchain-fork strategy |
| 03 | `03-modules-M1-M4-docketing-billing-expenses-fees.md` | M1 Docketing, M2 Billing/Trust, M3 Expenses, M4 Fee Schedules |
| 04 | `04-modules-M5-M8-documents-email-crm-marketing.md` | M5 Documents, M6 Email, M7 CRM/Intake/Reciprocity, M8 Marketing |
| 05 | `05-modules-M9-M11-workflow-eos-prior-art-utilities.md` | M9 Work Management, EOS layer, M11 Prior Art/IDS, cross-module utilities (Report Builder, Conflicts, Client Portal) |
| 06 | `06-agent-roster-A0-A18.md` | Full description of all 19 agents (A0–A18), the Knowledge Base service, and Prospect Engagement Orchestration |
| 07 | `07-appendix-A-file-conventions.md` | Complete reference-number grammar and SharePoint directory conventions (the "Rosetta Stone" for matter references and file structure) |
| 08 | `08-discovery-findings-appcoll-sharepoint-data.md` | Raw findings from the live AppColl in-app review, the SharePoint tenant crawl, and the three CSV export analyses — the evidence base behind many D3x decisions |
| 09 | `09-research-and-uploaded-documents.md` | Web research findings (USPTO/CIPO/Xero/Graph/Clarivate/LinkedIn APIs) and analysis of the 6 documents + 1 email the owner uploaded early in the project |
| 10 | `10-testing-cutover-open-items-action-items.md` | Testing & cutover plan, open/pending items, owner action items, blockers/risks |
| 11 | `11-build-tracker.md` | Phase-by-phase work package tracker (Phase 0 through 9 + future fork), decisions log, prompt log |
| 12 | `12-canonical-spec-v0.15-FULL.md` | The original, dense, cumulative canonical spec document (v0.15) exactly as pinned to Project knowledge — included verbatim for cross-reference |
| 13 | `13-canonical-tracker-v15-FULL.md` | The original, dense, cumulative canonical tracker document (v15) exactly as pinned to Project knowledge — included verbatim for cross-reference |

## Project status snapshot (as of this compilation)

- **Specification:** 37 decisions resolved, zero open design questions. Owner has declared readiness to freeze as v1.0 baseline pending final go-ahead.
- **Phase 0 (Foundations):** 5 of 8 work packages complete. Remaining: WP 0.6 (CIPO watcher code access via James — blocked), WP 0.7 (repo scaffold — **next action**), WP 0.8 (domain layer).
- **Data discovery:** Complete. Live AppColl review done (Tasks/Matters/Billing modules), full SharePoint tenant crawl done, three CSV exports analyzed (552 task-type rules, 4,596 contacts, 33,760 billing items).
- **Not yet started:** Any actual code/build work. This entire project to date is specification and discovery.
- **Immediate next step:** Generate the WP 0.7 Claude Code scaffold prompt (monorepo, FastAPI, React/TS, Supabase, CI, Entra app registration, Bitwarden pattern, typed contracts) — repeatedly offered, owner has kept expanding scope first. This compilation exists in part so that whenever the owner is ready, this step can proceed without losing anything above.

## The one-paragraph pitch (for anyone who needs the 30-second version)

BrunetCo OS is a technology-family-centric practice management system (patents, trademarks, industrial designs) that replaces AppColl for a 10-person Canadian/US IP boutique. It automates docketing and deadline management, official-fee-schedule-driven expense tracking with virtual trust accounting via Xero, email-derived work-item creation, a from-scratch guided-UX matter-opening flow with auto-population from WIPO/CIPO/USPTO/EPO, a full EOS (Entrepreneurial Operating System) workflow layer emulating Monday.com-style boards plus Ninety.io-style scorecards, and a roster of 19 specialized agents ranging from status watchers and office-filing assistants to marketing/prospecting agents (opposition watching, self-represented-applicant outreach, PCT national-phase prospecting, and free IP-portfolio audits) — all under a human-approves-everything-sent policy, with a deliberate architecture (`FamilyRecordStore` abstraction) that leaves room for a future blockchain-backed family record without requiring it now.
