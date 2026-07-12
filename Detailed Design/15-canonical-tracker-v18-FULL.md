# IP Agency OS — Build Tracker

**Last updated:** 2026-07-09 (v18, aligned to Spec v0.17) | **Current phase:** 0 (build starting — WP 0.7 prompt issued)
**v18 changes:** **D44 ruled (RLS = user-JWT pass-through)**; WP 0.7 scaffold prompt issued to James (`WP0.7-scaffold-prompt.md`); first Prompt Log entry; v16 RLS blocker resolved.
**v17 changes:** D42–D43 folded in — new WPs 2.5.3, 3.6, 3.7; amendments to 0.7, 0.8, 2.2, 2.5.1, 2.5.2, 3.2, 3.3, 5.5, 9.1; three new owner action items. Amended items marked **[v17]**.
Statuses: ⬜ pending · 🔵 prompt issued · 🟡 in progress · ✅ done · ⛔ blocked/gated

---

## Track structure (D38, 2026-07-08)

Per D38, the build is organized into two explicit tracks. **Track A — Replace AppColl** is the critical path: everything required to run a validated parallel run and cut over. **Track B — Grow the firm** proceeds post-cutover, iteratively. Nothing is descoped; scope expansion lands in Track B and cannot move the cutover date. Phase numbering is preserved from v15 for continuity — the track column is the sequencing authority.

**Track A order:** Phase 0 → 1 → 2 / 2.5 / 3 → 4 (incl. new WPs 4.7–4.9) → **4A.1–4A.2 (prior-art database + duty-of-disclosure dashboard — owner ruling 2026-07-08)** → 5-lite (WP 5.5 My Day on a minimal work-item substrate) → 6-core (WPs 6.1–6.4) → 7-core (WPs 7.3–7.4) → **5B.4 conflicts (pulled forward)** → Phase 9 parallel run → cutover.
**Track B:** everything else — full board framework + EOS, 4A.3, 5B.1–5B.3 + 5B.5, WPs 6.5–6.13, WPs 7.1/7.2/7.5/7.6, Phase 8, Phase 8.5, F.1.

**[v17] Per D42/D43, Track A also carries:** the permission framework (built at WP 0.8, enforced module-by-module) and the full timekeeper production/bonus chain (WPs 2.5.1–2.5.3, 3.2, 3.3, 3.6, 3.7). Launch-required firm-operations functionality, not AppColl parity.

---

## Phase 0 — Specification & Foundations (Track A)
| WP | Description | Status | Notes |
|---|---|---|---|
| 0.1 | Spec v0.1 | ✅ | |
| 0.2 | Interview round 1 → v0.2 | ✅ | D1–D11 |
| 0.3 | SharePoint crawl: validate Appendix A; catalogue doc-type shorthand + suffix vocab | ✅ | **Done 2026-07-07 (D36)** via owner-authenticated browser (M365 connector tools not loading in this chat — retry when enabled per-conversation). Validated to file level; enrichments: FH/PriorArt-IDS{n} bundles, family PriorArt/, status-annotated folders, 9999 - General + Account folders, associates-as-clients, initials-suffixed filenames, shorthand vocab (IASR, PrelimAmend, St37, ResponseRR, …) |
| 0.4 | AppColl export audit (matters, tasks, rules, contacts, billing items) | ✅ | **Complete 2026-07-07 (D35 + D37).** TaskTypes (552), Contacts (4,596), Billing (33,760) analyzed; CSVs retained for WP 1.3/1.6. Note: trigger-event linkage uses opaque IDs absent from export — reconstruct during WP 1.3 golden tests; Matters CSV optional (in-app review + migration export will cover) |
| 0.5 | Interview rounds 2–3 → **Spec v0.3** | ✅ | D12–D20; A8 added |
| 0.6 | Existing CIPO watcher code audit (with James) | ⛔ | Gated: repo/VPS access via James |
| 0.9 | Clean-sheet design review → **Spec v0.16** | ✅ | **Done 2026-07-08.** D38–D41; M1-R14, M5-R/M5-R2, M6-R7/R8; browser-native commitment; two-track sequencing; storage architecture confirmed (SharePoint byte-store + DB-canonical identity). Rationale: `BrunetCo-OS-Design-Review-2026-07-08.md` |
| 0.7 | Repo scaffold: monorepo, FastAPI, React/TS, Supabase, CI, Entra app, Bitwarden pattern, typed contracts | 🔵 | **[v18] Prompt issued 2026-07-09** (`WP0.7-scaffold-prompt.md`) to James; Omin/Grey reviewing. **RLS decided (D44): user-JWT pass-through** — service-role key banned on user paths; direct-Postgres RLS proof test required in scaffold CI (pattern reused by WP 9.1). Scaffold reflects ~6 platform services + orchestrator + KB; MCP server surface from day one; tooling specifics team-vetoable via repo `DECISIONS.md` |
| 0.8 | Domain layer: Client, Family, Matter (+parent/relationship), Contact, Task, User; `FamilyRecordStore`; canonical Family Record schema; RLS family ACLs | ⬜ | Include **document identity model (D41):** DB-canonical document records, driveItem-ID pointers, many-to-many matter/family links (M5-R2); M6-R7 mailbox-privacy RLS; **[v17 — D43]** permission-domain model (firm-general + five separately grantable protected domains: time entry / expense entry / invoicing / accounting reporting / compensation admin), grant tables, role templates (Agent/Paralegal/Bookkeeper/Principal), permissions-admin screen, own-record rule, RLS policy framework |

## Phase 1 — Docketing Core (M1) — Track A
| WP | Description | Track | Status |
|---|---|---|---|
| 1.1 | Family/Matter CRUD + reference generation (ordered jurisdiction sequences, TM/Design tags, PCT/MP siblings) | A | ⬜ |
| 1.2 | Deadline engine + holiday calendars + golden tests. **Every generated task writes an M1-R14 provenance record** (trigger item, matter, rule ID + version, input dates, calculated dates with holiday-roll trace) | A | ⬜ |
| 1.3 | AppColl rule import. Rules stored in the **declarative form (M1-R4 sharpened)**; dual-mode viewer/editor + **dry-run simulator** | A | ⬜ |
| 1.4 | Docket views + daily docket email | A | ⬜ |
| 1.5 | Audit trail | A | ⬜ |
| 1.6 | AppColl data migration + reconciliation reports (**D35 quality rules:** 15-yr pre-2019 CA TM renewal conversion; "Cient Abandoned" → Client-Abandoned enum; comment-log → structured activity records; retainer billing lines → trust module; 33,756 billing items; 3,661 matters) | A | ⬜ |
| 1.7 | Parallel-run harness (daily OS↔AppColl diff) — **reads from the M1-R14 provenance log** | A | ⬜ |
| 1.8 | Annuity deadline docketing (M1-R8) | A | ⬜ |
| 1.9 | **Smart Matter Opening (D32):** bootstrap-by-number retrieval adapters (WIPO/CIPO/USPTO/EPO), parent-child inheritance, review-and-confirm diff, guided opening flow | A | ⬜ |

## Phase 2 — Expenses, Fees & Xero (M3, M4) — Track A
| WP | Description | Track | Status |
|---|---|---|---|
| 2.1 | Xero OAuth + contact sync (+ tax-residency/province → tax codes) | A | ⬜ |
| 2.2 | Expense entry UI. **[v17 — D43]** gated by the expense-entry grant | A | ⬜ |
| 2.3 | Fee Schedule Service (effective-dated, entity-size incl. CIPO small entity) | A | ⬜ |
| 2.4 | Push to Xero + LinkedTransactions | A | ⬜ |
| 2.5 | Bank-feed matching + actual/FX write-back (A7) | A | ⬜ |
| 2.6 | CIPO deposit-account reconciliation + top-ups | A | ⬜ |
| 2.7 | Exceptions dashboard + invoice guards | A | ⬜ |
| 2.8 | Fee-page scrapers + approved diffs | A | ⬜ |
| 2.9 | Clarivate: Tier 2 instruction batch + confirmation-email verification; Tier 1 connector if channel exists | A | ⛔ Tier 1 gated on account-manager answer |

## Phase 2.5 — Time Entry — Track A
| WP | Description | Track | Status |
|---|---|---|---|
| 2.5.1 | Time entry UI + activity codes + timers. **[v17 — D42/M2-R11]** explicit work-item link on every time entry; searchable by timekeeper/client/matter/work item/**date range** (needs the WP 5.5 minimal work-item substrate — sequence accordingly) | A | ⬜ |
| 2.5.2 | Flat-fee catalogue + client overrides. **[v17 — D42/M2-R13]** timekeeper attribution on flat-fee items: default 100% to performing timekeeper, optional splits totalling 100%, editable until invoiced | A | ⬜ |
| 2.5.3 | **[v17 — new, D42/M2-R11]** Timekeeper production ledger: entered vs. billed vs. collected per work item; invoice # + live Xero payment status per entry; date-range search; CSV export; feeds Report Builder (5B.1) post-cutover | A | ⬜ |

## Phase 3 — Billing & Trust (M2) — Track A
| WP | Description | Track | Status |
|---|---|---|---|
| 3.1 | Discounts/quote linkage | A | ⬜ |
| 3.2 | WIP review → draft invoices (HST engine; non-resident zero-rating). **[v17 — D42]** persist invoice-line ↔ time-entry/flat-fee linkage; retain **both** hours entered and hours billed (WIP adjustments reason-coded per M2-R10) | A | ⬜ |
| 3.3 | Invoice push + webhooks (send from accounting@). **[v17 — D42]** payment webhooks drive line-level collected allocation (pro-rata default, manual override); the same event already feeds A18 dunning auto-clear (6.13) — one consumer, two subscribers | A | ⬜ |
| 3.4 | Virtual trust: retainer balances, liability coding, trust→revenue on invoice, unapplied-trust dashboard | A | ⬜ |
| 3.5 | Reconciliation drill (card + deposit-account cycles tie out) | A | ⬜ |
| 3.6 | **[v17 — new, D42/M2-R11]** Post-invoice write-down/credit/void propagation: reason-coded adjustments (extends M2-R10) flowing to collected attribution and pending-bonus reversal | A | ⬜ |
| 3.7 | **[v17 — new, D42/M2-R12 + D43]** Bonus engine: timekeeper × client rate matrix (effective-dated, versioned, audit-logged); accrue-on-billing / finalize-on-collection ledger; running totals (pending + earned; month/quarter/YTD/custom) with drill-down; per-timekeeper payroll statements; compensation-admin gate + own-record RLS; golden tests incl. partial payment, write-down, void, mid-period rate change | A | ⬜ |

## Phase 4 — Documents & Email (M5, M6) — Track A
| WP | Description | Track | Status |
|---|---|---|---|
| 4.1 | Graph/SharePoint adapter + open-in-Office. **D41 identity model:** DB-canonical document records; resolve by driveItem ID, never path | A | ⬜ |
| 4.2 | Auto-filing + delta sync. **M5-R filing provenance log** (suggested vs. selected, confidence, channel; acceptance-rate metric); **M5-R2 multi-matter filing** (many-to-many links; primary-folder placement; optional derived copies via hash-dedup); path-discrepancy flagging | A | ⬜ |
| 4.3 | Email ingestion: 4 shared + all user mailboxes; sent-item auto-filing; **M6-R7 mailbox privacy in RLS**; subscription manager (webhook renewal + delta catch-up sweep); throttle-aware backfill | A | ⬜ |
| 4.4 | AI matter classification + triage queue (precision check before go-live). Suggested-vs-selected pairs logged to M5-R for classifier training | A | ⬜ |
| 4.5 | Template library + approved outbound. **M6-R8:** no new `.msg` filing post-cutover; attachments hash-deduped, filed once, linked from every carrying email | A | ⬜ |
| 4.6 | Legacy .msg migration (~10–15k files; filename parser + folder-path matter inference + dedup; originals remain in SharePoint as archive) | A | ⬜ |
| 4.7 | **Outlook add-in (D40):** reading-pane matter sidebar; classification confirm/correct; one-click multi-matter filing of message + attachments; compose-side templates + matter/task linkage. Office.js (web + desktop identical) | A | ⬜ |
| 4.8 | **Word add-in (D40, thin):** save/profile open document to matter(s). Must stay thin pre-cutover | A | ⬜ |
| 4.9 | **Teams matter tab (D40, thin):** matter view over the OS API. Must stay thin pre-cutover | A | ⬜ |

## Phase 5 — Workflow & EOS (M9) — split
| WP | Description | Track | Status |
|---|---|---|---|
| 5.5 | **My Day** unified queue + daily digest + quick actions — **Track A ships this on a minimal work-item substrate** (docket tasks + basic manual tasks); full engine features arrive with 5.1. **[v17 — D43]** dashboard widgets permission-tagged from the first widget; the minimal work-item substrate is also what WP 2.5.1 time entries link to | A (lite) | ⬜ |
| 5.1 | Work-item engine + scripted project templates (chained tasks, role routing, versioned templates) | B | ⬜ |
| 5.2 | Assignment engine + capacity board (replaces Word project lists; 30-user scale) | B | ⬜ |
| 5.3 | **Board framework:** typed columns, table/kanban/timeline/calendar/workload views, groups, saved filters, no-code automations | B | ⬜ |
| 5.4 | **Micro-requests:** @request on items/documents, SLA timers, parent-blocking, Teams notifications, turnaround logging | B | ⬜ |
| 5.6 | **Unscripted projects:** manual builder + orchestrator NL plan drafting → edit → launch | B | ⬜ |
| 5.7 | EOS core: Scorecard (auto-populated), Rocks, Accountability Chart, Issues/IDS capture-from-anywhere, To-Dos (90% tracking), L10 meeting mode | B | ⬜ |
| 5.8 | **L10 pack + 1-on-1 reports**; productivity measurables (on-time %, cycle time, request turnaround, aging WIP); role dashboards | B | ⬜ |

## Phase 4A — Prior Art & IDS (M11) — split (owner ruling 2026-07-08)
| WP | Description | Track | Status |
|---|---|---|---|
| 4A.1 | Reference database + biblio auto-fill + matter/family cross-linking with citation states | A | ⬜ |
| 4A.2 | Cross-citation matrix view + bulk cross-cite + duty-of-disclosure dashboard | A | ⬜ |
| 4A.3 | IDS (SB08) one-click generation → A13 staging; auto-ingest of OA/search-report references | B | ⬜ (depends on A13/doc-gen, Track B) |

> **Owner ruling (2026-07-08, amends D38):** 4A.1–4A.2 pulled into Track A — US §1.56 duty-of-disclosure tracking must have a live home at cutover (AppColl goes read-only). 4A.3 remains Track B because it depends on the A13 filing assistant and document-generation layer. Migration seed: the family `PriorArt/` and `FH/PriorArt-IDS{n}/` folders found in the D36 crawl.

## Phase 5B — Cross-Module Utilities (D34) — split
| WP | Description | Track | Status |
|---|---|---|---|
| 5B.4 | Conflicts database + intake/on-demand checks with logged results — **pulled into Track A per D38** (no matter opens without a conflicts check) | A | ⬜ |
| 5B.1 | Report Builder: saved/shareable reports, scheduled runs, PDF/spreadsheet delivery | B | ⬜ |
| 5B.2 | CSV import/export framework across modules | B | ⬜ |
| 5B.3 | Document-generation layer (PTO forms + firm docs, pre-populated; feeds A12/A13 + DocuSign) | B | ⬜ |
| 5B.5 | Client Portal on FamilyRecordExport (**post-cutover phase**) | B | ⬜ deferred |

## Phase 6 — Agents — split
| WP | Description | Track | Status |
|---|---|---|---|
| 6.1 | Orchestrator + approval queue + egress gate + Bitwarden creds | A | ⬜ |
| 6.2 | Port CIPO watcher into workers (after 0.6) | A | ⛔ gated on 0.6 |
| 6.3 | USPTO: Patent Center notification recognition (M6) → ODP fetch → tasks/docs/draft reports; TSDR | A | ⬜ |
| 6.4 | EPO OPS + WIPO watchers | A | ⬜ |
| 6.5 | **A8 Email Instruction Detector:** detection → proposed intake/work items → approval gates; sender trust tiers; precision check on labelled sample before go-live | B | ⬜ |
| 6.6 | Task-Rule Builder (A2) — NL ↔ declarative-form round-trip + test-case generation + dry-run (M1-R4); the *manual* rule editor + simulator ship in Track A with 1.3 | B | ⬜ |
| 6.7 | Rule-Change Monitor (A3) | B | ⬜ |
| 6.8 | **Knowledge Base service** (§12.1: MOPOP/MPEP/TMEP/G&S manuals/statutes/office sites/firm site + curated blogs; citation-aware retrieval; freshness metadata) | B | ⬜ |
| 6.9 | **A9 per-user email drafters:** audit owner's existing agent (code via James) → generalize → per-user style training → KB grounding → review-queue delivery → rollout | B | ⛔ gated on code access |
| 6.10 | **A10 Quote agent:** port existing detector + Instant Quote pipeline (code via James) → OS CRM quote records → all-inbox rollout → trademark extension | B | ⛔ gated on code access |
| 6.11 | **A11 OA Reporting drafter:** style/substance corpus from legacy OA reports (after 4.6) → draft-section generation → held-out evaluation → professional review flow | B | ⬜ |
| 6.12 | **A18 Reminder & Follow-up:** ladder engine (offsets/templates/counts per task type + jurisdiction), awaiting-client tags, A8 reply-halting, A9 human-reviewed answers, **review-first sending via audit queue + dormant `auto_remind` flag (per client/task type, default off)**, escalation-on-exhaustion (silence never abandons), send/delivery logging | B | ⬜ |
| 6.13 | **A18 dunning + stop-work:** Xero-driven statement ladders from accounting@, threshold engine, matter flag propagation, soft-block + logged override, rights-preserving exemption, auto-clear on payment webhook | B | ⬜ |

## Phase 7 — CRM, Intake & Reciprocity (M7) — split
| WP | Description | Track | Status |
|---|---|---|---|
| 7.3 | Intake chain: conflicts → DocuSign ToE → Xero retainer (trust liability) → payment webhook → matter open | A | ⬜ |
| 7.4 | Signature workflows: DocuSign envelopes (ToE, USPTO forms); assignments per-envelope DocuSign/wet-ink tracked path | A | ⬜ |
| 7.1 | Pipeline/quotes/discounts; LinkedIn manual-assist | B | ⬜ |
| 7.2 | HubSpot import + website form repoint (WordPress template edit; copy-to-info@ preserved) | B | ⬜ |
| 7.5 | Reciprocity + ratios (Madrid-inbound aware) | B | ⬜ |
| 7.6 | MailChimp sync (lists/tags/campaigns/metrics; CASL fields) | B | ⬜ |

## Phase 8 — Marketing & Specialist Agents (M8; A4–A6, A14–A17) — Track B
| WP | Description | Status |
|---|---|---|
| 8.1 | Content calendar + generation agents + approvals | ⬜ |
| 8.2 | WordPress REST connector (Kinsta staging test) + LinkedIn/YouTube publishing + metrics | ⬜ |
| 8.3 | A4 G&S Harmonizer + eval set | ⬜ |
| 8.4 | A5 Search Strategist | ⬜ |
| 8.5 | A6 Prior-Art Analyzer + reporting letters | ⬜ |
| 8.6 | **Opposition Watch module:** watchlist CRUD, screening criteria, prospect ledger (CASL fields, deadline expiry), client alert/report templates, docketed opposition deadlines | ⬜ |
| 8.7 | **A14 Journal pipeline:** weekly Trademarks Journal ingestion (+ IP Horizons XML backstop), similarity screening/scoring vs. watchlist | ⬜ |
| 8.8 | **A14 prospecting:** register search for earlier conflicting marks, owner resolution, hunter.io enrichment, LinkedIn manual-assist tasks, human-approved CASL outreach + tracking | ⬜ |
| 8.9 | **A15 self-represented prospector:** IP Horizons diff for no-agent + recent Examiner's Report; response-deadline expiry; public-record contact resolution; reuses TM Prospecting Engine outreach pipeline | ⬜ |
| 8.10 | **A16 national-phase prospector:** PatentScope 30-month-window query (verify API access tier), Canada-propensity + incumbent-firm analysis, volume/firm-size rankings, reciprocity guard (allow/block list vs. M7 referral partners), outreach via shared engine | ⬜ |
| 8.11 | **A17 audit engine:** family enumeration (PatentScope/OPS/ODP/CIPO), grant/positive-opinion detection, 4-year exam-window + enterable-PCT identification, PPH recommendations + claim summaries with citations, savings quantification from fee schedules | ⬜ |
| 8.12 | **A17 delivery:** website audit-request form → CRM → queued audit → human review → report send; manual OS trigger; US variant | ⬜ |
| 8.13 | **Engagement orchestration (§12.2):** prospect conversation threads, journey state machine, A9/A10 in-thread composition, universal outbound audit queue, safety rails (rate limits, CASL suppression, escalation, sentiment pause) | ⬜ |
| 8.14 | **Scheduling connector:** Calendly (incumbent) + MS Bookings/Graph free-busy; booked meetings → CRM activities + calendar events | ⬜ |
| 8.15 | **Agent Activity Dashboard:** per-prospect journey view, campaign funnels + conversion rates, audit-queue metrics, per-agent performance, stalled/expiring alerts; EOS scorecard feed | ⬜ |

## Phase 8.5 — Office Filing Assistants (A12, A13) — Track B
| WP | Description | Status |
|---|---|---|
| 8.5.1 | Browser-automation framework for portal agents (session mgmt, Bitwarden creds, field-level audit log, verify no filing APIs exist) | ⬜ |
| 8.5.2 | **A12 CIPO:** patent filing populate + stage → admin review task → human submit; acknowledgment capture via M6 | ⬜ |
| 8.5.3 | A12 extensions: trademark filing (G&S statement from OS/A4) + industrial design filing (drawings PDF + description) | ⬜ |
| 8.5.4 | **A13 USPTO Patent Center:** form population/uploads (regenerate AppColl fillable PDFs in OS doc layer or use PC web forms) → admin review → human submit | ⬜ |
| 8.5.5 | Orchestrator invocation flows ("file X") + workflow-task triggers | ⬜ |

## Phase 9 — Cutover (concludes Track A)
| WP | Description | Track | Status |
|---|---|---|---|
| 9.1 | Parallel-run exit criteria (zero diffs over agreed window; diff driven by the M1-R14 provenance log). **[v17 — D43]** permission acceptance tests pass: API-level and direct-Postgres RLS checks — accounting-reporting gating, cross-timekeeper bonus isolation, rate-matrix write protection | A | ⬜ |
| 9.2 | Cutover + rollback plan; AppColl read-only archive; **M6-R8 takes effect** (no new `.msg` filing) | A | ⬜ |

## Future Fork
| WP | Description | Status |
|---|---|---|
| F.1 | Permissioned blockchain Family Record store + browser reader | ⬜ future |

---

## Action Items (owner)
| Item | Gate | Status |
|---|---|---|
| Ask Clarivate account manager about instruction API/feed | Enables Tier 1 (WP 2.9) | ⬜ |
| Watcher repo/VPS access via James | Enables WP 0.6 → 6.2 | ⬜ |
| Email-drafting agent code via James | Enables WP 6.9 (A9 template) | ⬜ |
| Quote detector + Instant Quote tool code via James | Enables WP 6.10 (A10) | ⬜ |
| Claude Team subscription (James, Omin, Grey seats) | Spec close (now) | ⬜ raised |
| Confirm 8-week parallel-run duration | §14 | ⬜ |
| **[v17]** Populate initial bonus rate matrix (per-timekeeper default % + per-client overrides, effective dates) | Enables WP 3.7 configuration | ⬜ |
| **[v17]** Define initial permission grants per team member (role templates: Agent / Paralegal / Bookkeeper / Principal) | Enables WP 0.8 configuration | ⬜ |
| **[v17]** Decide bonus statement period for payroll (monthly vs. quarterly) | WP 3.7 statements | ⬜ |
| Decide Phase 4A (Prior Art & IDS) placement | Track A scope freeze at WP 0.7 | ✅ **resolved 2026-07-08: 4A.1–4A.2 → Track A; 4A.3 stays Track B** |
| Grant SharePoint read access for WP 0.3 crawl | WP 0.3 | ✅ done (browser route) |
| Confirm D36 ruling: OS-created folders omit status annotations (status lives in app) | M5 design | ✅ confirmed 2026-07-07 |
| **Rotate AppColl password** (was shared in chat plaintext) | Security hygiene — do now | ✅ done per owner |
| **AppColl CSV exports:** Task Types, Contacts, Billing Items | Completes WP 0.4; seeds WP 1.3 rule import | ✅ received 2026-07-07 |
| Optional: in-app AppColl walkthrough via Claude in Chrome (owner logs in; deeper parity check — Prior Art, Files, Reports, Contacts, Settings modules not yet reviewed) | Supplements WP 0.4 | ⬜ |

## Prompt Log
| # | Date | WP | Prompt summary | Outcome |
|---|---|---|---|---|
| 1 | 2026-07-09 | 0.7 | Repo scaffold: monorepo layout, D44 JWT-pass-through auth bridge + RLS proof test, typed contracts + drift check, MCP surface, 8 service skeletons, worker/event demo, Bitwarden secrets, CI. Acceptance checklist + explicit out-of-scope (0.8 domain layer). File: `WP0.7-scaffold-prompt.md` | 🔵 issued to James |

## Decisions Log
Cumulative register lives in Spec §0 (D1–D43). Log post-spec decisions here.
| Date | Decision | Rationale |
|---|---|---|
| 2026-07-07 | D36 folder-annotation ruling confirmed by owner (status in DB; legacy annotations parsed; new folders unannotated) | Filesystem stops duplicating docket state |
| 2026-07-07 | Reminder auto-send withdrawn for v1; `auto_remind` flag (per client/task type, default off) preserves the path to automation | Owner decision — earn automation after review period |
| 2026-07-08 | **D38** two-track sequencing: cutover concludes Track A; marketing/prospecting agents move post-cutover; 5B.4 conflicts pulled into Track A | Shortest path off AppColl; parallel-run burden and drift risk minimized; scope expansion gets a lane that can't move the cutover date |
| 2026-07-08 | **D39** personal-mailbox privacy: matter-linked emails firm-visible; unlinked visible to mailbox owner only; RLS-enforced | Whole-firm mailbox ingestion (D15) needed an explicit visibility model |
| 2026-07-08 | **D40** Outlook/Word/Teams add-ins in Track A (WPs 4.7–4.9); Outlook is priority, Word/Teams stay thin pre-cutover | Meet staff where they work; classification correction becomes one click; feeds M5-R training data |
| 2026-07-08 | **D41** document identity inversion: DB record canonical, driveItem ID pointer, path = projection; no new `.msg` filing post-cutover; hash-deduped attachments; multi-matter filing (M5-R2) | Extends D36; matter linkage survives human file moves; email DB replaces `.msg` practice |
| 2026-07-08 | **D38 amended:** 4A.1–4A.2 (prior-art database, cross-citation matrix, duty-of-disclosure dashboard) pulled into Track A; 4A.3 (SB08 → A13 staging) stays Track B | §1.56 duty-of-disclosure tracking must have a live home at cutover; AppColl goes read-only |
| 2026-07-09 | **D42** timekeeper production & bonus tracking: timekeeper × client rate matrix (effective-dated); accrue on billing, finalize on collection; flat-fee attribution; entered → billed → collected chain with invoice # + Xero payment status; Track A | Agent compensation runs on billed-and-collected percentages varying by client; write-downs make collected ≠ billed; billed originates in time entries, collected in Xero |
| 2026-07-09 | **D44** RLS enforcement mode: **user-JWT pass-through to Postgres per-request**; service-role key restricted to migrations/system workers/admin scripts (each usage enumerated); scaffold ships a direct-Postgres RLS proof test | Owner ruling at WP 0.7. Effectively forced by D43 acceptance tests (WP 9.1 must prove RLS — not app code — is the control, tested directly against Postgres); also carries D39 mailbox privacy and family ACLs |
| 2026-07-09 | **D43** role-based permission framework: firm-general vs. five separately grantable protected domains (accounting reporting separate from time/expense/invoicing entry); permission-tagged dashboards; own-bonus-only visibility + compensation admin; RLS-enforced; Track A | Bonus and financial data must be invisible to unauthorized staff; permissions must precede the modules they protect or RLS gets retrofitted |

## Blockers / Risks
- Clarivate Tier 1 channel unknown (Tier 2 ships regardless — mitigated).
- A8 instruction detection: precision must be proven on labelled samples before it touches intake (mitigation: approval gates + triage safety net). Now Track B — post-cutover.
- Doc-type shorthand vocabulary understood to be incomplete; grows during migration (expected, not blocking).
- ~~RLS enforcement mode must be settled at WP 0.7~~ — **resolved v18 (D44): user-JWT pass-through.** Residual risk: drift back to service-role usage on user paths; mitigated by the enumerated-usage rule + CI proof test.
- **New (v16):** WP 5.5 (My Day, Track A-lite) needs a minimal work-item substrate ahead of the full 5.1 engine — scope it deliberately to avoid dragging the full board framework into Track A.
- **New (v17):** Xero payment webhooks are invoice-level; M2-R11 line-level collected allocation is OS-side arithmetic (pro-rata default) — confirm partial-payment/overpayment behavior against Xero API docs at WP 3.3.
- **New (v17):** WP 2.5.1 time entries link to work items — the WP 5.5 minimal work-item substrate must exist by Phase 2.5, earlier than its Track A slot after Phase 4; carve the substrate (record + CRUD, no board features) into WP 0.8/2.5.1 scaffolding.
