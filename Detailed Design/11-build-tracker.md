# Build Tracker — Phase-by-Phase Work Package Status

*This is the canonical work-package tracker, included here as part of the full documentation compilation. It is kept in sync with the standalone `IP-OS-TRACKER.md` file the firm pins to Project knowledge — if the two ever diverge, treat the most recently dated one as current.*

**Last updated:** 2026-07-07 (v15, aligned to Spec v0.15) | **Current phase:** 0 (spec complete; foundations starting)
Statuses: ⬜ pending · 🔵 prompt issued · 🟡 in progress · ✅ done · ⛔ blocked/gated

---

## Phase 0 — Specification & Foundations
| WP | Description | Status | Notes |
|---|---|---|---|
| 0.1 | Spec v0.1 | ✅ | |
| 0.2 | Interview round 1 → v0.2 | ✅ | D1–D11 |
| 0.3 | SharePoint crawl: validate Appendix A; catalogue doc-type shorthand + suffix vocab | ✅ | **Done 2026-07-07 (D36)** via owner-authenticated browser (M365 connector tools not loading in this chat — retry when enabled per-conversation). Validated to file level; enrichments: FH/PriorArt-IDS{n} bundles, family PriorArt/, status-annotated folders, 9999 - General + Account folders, associates-as-clients, initials-suffixed filenames, shorthand vocab (IASR, PrelimAmend, St37, ResponseRR, …) |
| 0.4 | AppColl export audit (matters, tasks, rules, contacts, billing items) | ✅ | **Complete 2026-07-07 (D35 + D37).** TaskTypes (552), Contacts (4,596), Billing (33,760) analyzed; CSVs retained for WP 1.3/1.6. Note: trigger-event linkage uses opaque IDs absent from export — reconstruct during WP 1.3 golden tests; Matters CSV optional (in-app review + migration export will cover) |
| 0.5 | Interview rounds 2–3 → **Spec v0.3** | ✅ | D12–D20; A8 added |
| 0.6 | Existing CIPO watcher code audit (with James) | ⛔ | Gated: repo/VPS access via James |
| 0.7 | Repo scaffold: monorepo, FastAPI, React/TS, Supabase, CI, Entra app, Bitwarden pattern, typed contracts | ⬜ | **Next up.** Team (James/Omin/Grey) may veto specifics |
| 0.8 | Domain layer: Client, Family, Matter (+parent/relationship), Contact, Task, User; `FamilyRecordStore`; canonical Family Record schema; RLS family ACLs | ⬜ | |

## Phase 1 — Docketing Core (M1)
| WP | Description | Status |
|---|---|---|
| 1.1 | Family/Matter CRUD + reference generation (ordered jurisdiction sequences, TM/Design tags, PCT/MP siblings) | ⬜ |
| 1.2 | Deadline engine + holiday calendars + golden tests | ⬜ |
| 1.3 | AppColl rule import | ⬜ |
| 1.4 | Docket views + daily docket email | ⬜ |
| 1.5 | Audit trail | ⬜ |
| 1.6 | AppColl data migration + reconciliation reports (**D35 quality rules:** 15-yr pre-2019 CA TM renewal conversion; "Cient Abandoned" → Client-Abandoned enum; comment-log → structured activity records; retainer billing lines → trust module; 33,756 billing items; 3,661 matters) | ⬜ |
| 1.7 | Parallel-run harness (daily OS↔AppColl diff) | ⬜ |
| 1.8 | Annuity deadline docketing (M1-R8) | ⬜ |
| 1.9 | **Smart Matter Opening (D32):** bootstrap-by-number retrieval adapters (WIPO/CIPO/USPTO/EPO), parent-child inheritance, review-and-confirm diff, guided opening flow | ⬜ |

## Phase 4A — Prior Art & IDS (M11)
| WP | Description | Status |
|---|---|---|
| 4A.1 | Reference database + biblio auto-fill + matter/family cross-linking with citation states | ⬜ |
| 4A.2 | Cross-citation matrix view + bulk cross-cite + duty-of-disclosure dashboard | ⬜ |
| 4A.3 | IDS (SB08) one-click generation → A13 staging; auto-ingest of OA/search-report references | ⬜ |

## Phase 5B — Cross-Module Utilities (D34)
| WP | Description | Status |
|---|---|---|
| 5B.1 | Report Builder: saved/shareable reports, scheduled runs, PDF/spreadsheet delivery | ⬜ |
| 5B.2 | CSV import/export framework across modules | ⬜ |
| 5B.3 | Document-generation layer (PTO forms + firm docs, pre-populated; feeds A12/A13 + DocuSign) | ⬜ |
| 5B.4 | Conflicts database + intake/on-demand checks with logged results | ⬜ |
| 5B.5 | Client Portal on FamilyRecordExport (**post-cutover phase**) | ⬜ deferred |

## Phase 2 — Expenses, Fees & Xero (M3, M4)
| WP | Description | Status |
|---|---|---|
| 2.1 | Xero OAuth + contact sync (+ tax-residency/province → tax codes) | ⬜ |
| 2.2 | Expense entry UI | ⬜ |
| 2.3 | Fee Schedule Service (effective-dated, entity-size incl. CIPO small entity) | ⬜ |
| 2.4 | Push to Xero + LinkedTransactions | ⬜ |
| 2.5 | Bank-feed matching + actual/FX write-back (A7) | ⬜ |
| 2.6 | CIPO deposit-account reconciliation + top-ups | ⬜ |
| 2.7 | Exceptions dashboard + invoice guards | ⬜ |
| 2.8 | Fee-page scrapers + approved diffs | ⬜ |
| 2.9 | Clarivate: Tier 2 instruction batch + confirmation-email verification; Tier 1 connector if channel exists | ⛔ Tier 1 gated on account-manager answer |

## Phase 2.5 — Time Entry
| WP | Description | Status |
|---|---|---|
| 2.5.1 | Time entry UI + activity codes + timers | ⬜ |
| 2.5.2 | Flat-fee catalogue + client overrides | ⬜ |

## Phase 3 — Billing & Trust (M2)
| WP | Description | Status |
|---|---|---|
| 3.1 | Discounts/quote linkage | ⬜ |
| 3.2 | WIP review → draft invoices (HST engine; non-resident zero-rating) | ⬜ |
| 3.3 | Invoice push + webhooks (send from accounting@) | ⬜ |
| 3.4 | Virtual trust: retainer balances, liability coding, trust→revenue on invoice, unapplied-trust dashboard | ⬜ |
| 3.5 | Reconciliation drill (card + deposit-account cycles tie out) | ⬜ |

## Phase 4 — Documents & Email (M5, M6)
| WP | Description | Status |
|---|---|---|
| 4.1 | Graph/SharePoint adapter + open-in-Office | ⬜ |
| 4.2 | Auto-filing + delta sync | ⬜ |
| 4.3 | Email ingestion: 4 shared + all user mailboxes; sent-item auto-filing | ⬜ |
| 4.4 | AI matter classification + triage queue (precision check before go-live) | ⬜ |
| 4.5 | Template library + approved outbound | ⬜ |
| 4.6 | Legacy .msg migration (~10–15k files; filename parser + folder-path matter inference + dedup) | ⬜ |

## Phase 5 — Workflow & EOS (M9)
| WP | Description | Status |
|---|---|---|
| 5.1 | Work-item engine + scripted project templates (chained tasks, role routing, versioned templates) | ⬜ |
| 5.2 | Assignment engine + capacity board (replaces Word project lists; 30-user scale) | ⬜ |
| 5.3 | **Board framework:** typed columns, table/kanban/timeline/calendar/workload views, groups, saved filters, no-code automations | ⬜ |
| 5.4 | **Micro-requests:** @request on items/documents, SLA timers, parent-blocking, Teams notifications, turnaround logging | ⬜ |
| 5.5 | **My Day** unified queue + daily digest + quick actions | ⬜ |
| 5.6 | **Unscripted projects:** manual builder + orchestrator NL plan drafting → edit → launch | ⬜ |
| 5.7 | EOS core: Scorecard (auto-populated), Rocks, Accountability Chart, Issues/IDS capture-from-anywhere, To-Dos (90% tracking), L10 meeting mode | ⬜ |
| 5.8 | **L10 pack + 1-on-1 reports**; productivity measurables (on-time %, cycle time, request turnaround, aging WIP); role dashboards | ⬜ |

## Phase 6 — Agents (A0–A3, A7, A8)
| WP | Description | Status |
|---|---|---|
| 6.1 | Orchestrator + approval queue + egress gate + Bitwarden creds | ⬜ |
| 6.2 | Port CIPO watcher into workers (after 0.6) | ⛔ gated on 0.6 |
| 6.3 | USPTO: Patent Center notification recognition (M6) → ODP fetch → tasks/docs/draft reports; TSDR | ⬜ |
| 6.4 | EPO OPS + WIPO watchers | ⬜ |
| 6.5 | **A8 Email Instruction Detector:** detection → proposed intake/work items → approval gates; sender trust tiers | ⬜ |
| 6.6 | Task-Rule Builder (A2) | ⬜ |
| 6.7 | Rule-Change Monitor (A3) | ⬜ |
| 6.12 | **A18 Reminder & Follow-up:** ladder engine (offsets/templates/counts per task type + jurisdiction), awaiting-client tags, A8 reply-halting, A9 human-reviewed answers, **review-first sending via audit queue + dormant `auto_remind` flag (per client/task type, default off)**, escalation-on-exhaustion (silence never abandons), send/delivery logging | ⬜ |
| 6.13 | **A18 dunning + stop-work:** Xero-driven statement ladders from accounting@, threshold engine, matter flag propagation (boards/My Day/capacity), soft-block + logged override, rights-preserving exemption, auto-clear on payment webhook | ⬜ |
| 6.8 | **Knowledge Base service** (§12.1: ingestion of MOPOP/MPEP/TMEP/G&S manuals/statutes/office sites/firm site + curated blogs; citation-aware retrieval; freshness metadata) | ⬜ |
| 6.9 | **A9 per-user email drafters:** audit owner's existing agent (code via James) → generalize as template → per-user style training from sent mail → KB grounding → review-queue delivery → rollout to all 10 users | ⛔ gated on code access |
| 6.10 | **A10 Quote agent:** port existing detector + Instant Quote pipeline (code via James) → OS CRM quote records + pipeline placement → all-inbox rollout → trademark quoting extension | ⛔ gated on code access |
| 6.11 | **A11 OA Reporting drafter:** style/substance corpus from legacy OA reports (after 4.6) → draft-section generation into merge templates → held-out evaluation → professional review flow | ⬜ |

## Phase 7 — CRM, Intake & Reciprocity (M7)
| WP | Description | Status |
|---|---|---|
| 7.1 | Pipeline/quotes/discounts; LinkedIn manual-assist | ⬜ |
| 7.2 | HubSpot import + website form repoint (WordPress template edit; copy-to-info@ preserved) | ⬜ |
| 7.3 | Intake chain: conflicts → DocuSign ToE → Xero retainer (trust liability) → payment webhook → matter open | ⬜ |
| 7.4 | Signature workflows: DocuSign envelopes (ToE, USPTO forms); assignments per-envelope DocuSign/wet-ink tracked path | ⬜ |
| 7.5 | Reciprocity + ratios (Madrid-inbound aware) | ⬜ |
| 7.6 | MailChimp sync (lists/tags/campaigns/metrics; CASL fields) | ⬜ |

## Phase 8 — Marketing & Specialist Agents (M8; A4–A6)
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

## Phase 8.5 — Office Filing Assistants (A12, A13)
| WP | Description | Status |
|---|---|---|
| 8.5.1 | Browser-automation framework for portal agents (session mgmt, Bitwarden creds, field-level audit log, verify no filing APIs exist) | ⬜ |
| 8.5.2 | **A12 CIPO:** patent filing populate + stage → admin review task → human submit; acknowledgment capture via M6 | ⬜ |
| 8.5.3 | A12 extensions: trademark filing (G&S statement from OS/A4) + industrial design filing (drawings PDF + description) | ⬜ |
| 8.5.4 | **A13 USPTO Patent Center:** form population/uploads (regenerate AppColl fillable PDFs in OS doc layer or use PC web forms) → admin review → human submit | ⬜ |
| 8.5.5 | Orchestrator invocation flows ("file X") + workflow-task triggers | ⬜ |

## Phase 9 — Cutover
| WP | Description | Status |
|---|---|---|
| 9.1 | Parallel-run exit criteria (zero diffs over agreed window) | ⬜ |
| 9.2 | Cutover + rollback plan; AppColl read-only archive | ⬜ |

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
| Grant SharePoint read access for WP 0.3 crawl | WP 0.3 | ✅ done (browser route) |
| Confirm D36 ruling: OS-created folders omit status annotations (status lives in app) | M5 design | ✅ confirmed 2026-07-07 |
| **Rotate AppColl password** (was shared in chat plaintext) | Security hygiene — do now | ✅ done per owner |
| **AppColl CSV exports:** Task Types, Contacts, Billing Items | Completes WP 0.4; seeds WP 1.3 rule import | ✅ received 2026-07-07 |
| Optional: in-app AppColl walkthrough via Claude in Chrome (owner logs in; deeper parity check of custom task rules/settings) | Supplements WP 0.4 | ⬜ |

## Prompt Log
| # | Date | WP | Prompt summary | Outcome |
|---|---|---|---|---|
| — | | | | |

## Decisions Log
Cumulative register lives in Spec §0 (D1–D31). Log post-spec decisions here.
| Date | Decision | Rationale |
|---|---|---|
| 2026-07-07 | D36 folder-annotation ruling confirmed by owner (status in DB; legacy annotations parsed; new folders unannotated) | Filesystem stops duplicating docket state |
| 2026-07-07 | Reminder auto-send withdrawn for v1; `auto_remind` flag (per client/task type, default off) preserves the path to automation | Owner decision — earn automation after review period |

## Blockers / Risks
- Clarivate Tier 1 channel unknown (Tier 2 ships regardless — mitigated).
- AppColl billing-item export "possibly" available — confirm at 0.4.
- A8 instruction detection: precision must be proven on labelled samples before it touches intake (mitigation: approval gates + triage safety net).
- Doc-type shorthand vocabulary unconfirmed until 0.3 crawl.
