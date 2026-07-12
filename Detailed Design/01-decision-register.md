# Decision Register — D1 through D37

This is the complete, ordered record of every design decision made during the BrunetCo OS specification project. Decisions are numbered in the order they were made (chronological = numerical), but grouped below by theme for readability. **The numbering is authoritative** — refer to decisions as "D12" etc. in all other project documents.

Legend: 🔎 = research finding to verify at build time · ⏳ = pending external input, not blocking the spec.

---

## Foundational decisions (interview round 1 — D1–D11)

**D1 — Accounting system.** Xero integration; no custom ledger built in v1. The OS is the practice-management layer; Xero remains the system of record for money.

**D2 — Hosting & database.** Canada or US cloud acceptable. Firm has an existing AWS VPS (US) already running the CIPO watcher. **Supabase (managed Postgres)** approved as the database platform.

**D3 — Blockchain strategy.** Treated as a **future fork**, not a v1 requirement. v1 is architected to make that fork painless via a `FamilyRecordStore` repository-pattern abstraction and a canonical, versioned Family Record JSON schema (see `02-vision-principles-architecture.md` §3.1 for the full design). The chain itself is tracked as a separate future project (Tracker item F.1).

**D4 — AppColl exit strategy.** Export is available (matters, tasks, rules, contacts, and — confirmed later — billing items). A **parallel run plus formal testing plan is required** before cutover; see `10-testing-cutover-open-items-action-items.md` §14.

**D5 — File conventions.** Codified in Appendix A (`07-appendix-A-file-conventions.md`); later validated and enriched by the WP 0.3 SharePoint crawl (D36).

**D6 — Build order.** Confirmed as proposed, with one adjustment: time entry became its own mini-phase, Phase 2.5, rather than being folded into billing.

**D7 — Status watcher reality.** The firm already has a Python/JSON nightly watcher running on the AWS VPS, built by James (the owner's son) in Claude Code — but it **covers CIPO only**. Decision: port and extend it. For **USPTO, there is no scraper** — the design is notification-driven: Patent Center's nightly notification emails trigger an ODP (Open Data Portal) API fetch.

**D8 — AI confidentiality.** Client data is sandboxed. External LLM APIs are permitted, with encrypted transport and Zero Data Retention (ZDR) terms used where available.

**D9 — Scale target.** 10 users today, design for 30. Approximately 3,000 active matters at decision time (roughly 1,000 active patent, 600 active trademark) — later confirmed against the full AppColl matter count of 3,661.

**D10 — Secrets management.** Bitwarden, with an agent-accessible API, for all integration credentials. No plaintext secrets anywhere in the system; agents fetch credentials at runtime.

**D11 — Connector access model.** The owner grants connector access (SharePoint, AppColl, HubSpot, Xero, M365) on request as each work package needs it, rather than provisioning everything up front.

---

## Billing, tax, and reference-grammar decisions (interview rounds 2–3 — D12–D20)

**D12 — Billing policy.** No markup on official fees — re-bill clients at the **actual settled FX rate**, not an estimate. HST charged by province for Canadian tax-resident clients; **zero tax for non-residents**. **Virtual trust model:** retainers are recorded as held-in-trust via a Xero liability account (no separate physical trust bank account today); the balance transfers to revenue at invoicing time. The OS supports selecting a reconciliation bank account and a future physical trust account, but bank mechanics stay in Xero.

**D13 — Reference grammar.** The matter-reference and jurisdiction-sequence grammar is fully specified in Appendix A: `USP` → `US` → `US2/US3…` for continuations; `PCT` and `MP` (Madrid Protocol) exist as sibling international-vehicle matters, not nested under a parent; `(TM)`/`Design` tags distinguish trademark family types.

**D14 — Maintainers and technical stack.** The build team is the owner plus James (james@), Omin (omin@), and Grey (grey@), working via Claude Code, with a Claude Team subscription to be arranged (flagged as a pending action item, see D14 in the register and the action-items file). **Stack:** Python/FastAPI backend with event-driven workers; React/TypeScript frontend; Supabase Postgres. Monorepo structure, typed API contracts generated from OpenAPI, Supabase Row-Level Security enforcing family-level permissions, event-driven workers for cross-system reactions. The build team retains the right to veto stack specifics at the WP 0.7 scaffolding stage.

**D15 — Email scope.** Four shared mailboxes (patents@, trademarks@, accounting@, info@) **plus every individual team member's mailbox** get ingested. This decision also spawned **Agent A8 (Email Instruction Detector)** — emails are monitored to detect actual work instructions from clients, which then propose intake or work-item creation (never auto-executed). Sent items, especially replies, are auto-filed for thread continuity. Legacy migration scope: an estimated 10,000–15,000 `.msg` files out of roughly 20,000 total files in SharePoint.

**D16 — CRM and marketing tooling.** LinkedIn integration is manual-assist only (the OS drafts and tracks; a human clicks send — consistent with LinkedIn's API terms prohibiting automated messaging, confirmed by research). **MailChimp is retained** for newsletter sending, with the OS mastering the actual lists and content and syncing both ways; CASL consent fields (basis, date, source) live on every contact. The OS is designed to own all content and prospect lists going forward, not treat marketing as an external system.

**D17 — Website.** The firm's WordPress site (custom template, hosted on Kinsta) gets a REST API connector. HubSpot's website-form embeds get swapped for OS-served forms as part of the HubSpot exit — a template-edit task on the custom theme.

**D18 — Annuity (maintenance fee) payments.** Docketed in-house by the OS (not outsourced). Paid via Clarivate's self-serve portal, instructed per-file. A **tiered connector design** results: Tier 1 is a formal integration channel, pending the owner asking Clarivate's account manager whether one exists (⏳ open item); Tier 2 — which **ships regardless of the Tier 1 answer** — is an instruction batch generated by the OS, executed manually in Clarivate's portal by staff, with confirmation emails ingested and matched to verify every instruction actually landed.

**D19 — Signatures and retainer collection.** DocuSign is used for Terms of Engagement and USPTO forms (declaration, oath, POA). For assignments, DocuSign is **optional** — some European offices require wet-ink signatures, so a parallel tracked wet-signature path (send for execution → receive executed copy → file) exists alongside the DocuSign path, with the system defaulting to asking which is needed. Retainer requests are issued as a Xero invoice carrying an online card-payment link, coded to the client-deposits liability account (i.e., to trust, not directly to revenue).

**D20 — Fee-table scope for v1.** Only CIPO, USPTO, EPO, and WIPO official fee tables are built in v1 — no broader jurisdiction coverage yet.

---

## Agent roster expansion — practice agents (D21–D24)

**D21 — Per-user email drafting agents (A9).** The owner already runs a personal email-monitoring/drafting agent; this becomes the **template** for a per-user agent duplicated across the team, with style learned from each individual's own sent mail and grounded on a shared firm Knowledge Base (see `06-agent-roster-A0-A18.md` §12.1). Code access via James is a pending gate.

**D22 — Quote agent (A10).** Similarly, the owner's existing quote-request detector plus website Instant Quote tool (already built: detects "quotation/estimate/cost" language plus a PCT/WO application number → runs a Python WIPO lookup → generates a fee-table-based quote → emails a link and PDF) gets integrated into the OS, rolled out to every inbox, extended to trademarks, and every quote gets logged into the OS's CRM.

**D23 — Office-action reporting drafter (A11).** An AI agent drafts the substantive section of OA-reporting emails to clients (objection summary + proposed responses), trained in the firm's own voice by learning from the historical corpus of legacy `.msg` OA-reporting emails. Always human-reviewed before sending.

**D24 — Office filing assistants (A12 CIPO, A13 USPTO Patent Center).** These agents **populate** filing forms and stage all required uploads from OS data, then stop and create a review task — **the human always clicks submit**. This is a hard rule, not a configurable option. Mechanism is browser automation, since no public filing-submission APIs are known to exist for either office (flagged 🔎 to verify at build time). Portal credentials are fetched from Bitwarden at runtime, never stored elsewhere.

---

## Agent roster expansion — marketing & prospecting agents (D25–D28)

**D25 — Opposition Watch & Outreach (A14 + new M7 module).** Weekly ingestion of the CIPO Trademarks Journal (newly advertised marks open a 2-month opposition window). Two modes: **(a) client watch** — screen advertised marks against an OS-maintained watchlist of client marks, and docket the opposition deadline automatically if there's a hit; **(b) prospecting** — find earlier, confusingly-similar marks owned by *non*-clients whose opposition window is about to open on someone else's newly advertised mark, enrich the owner's contact details (hunter.io API; LinkedIn manual-assist per D16), and run CASL-compliant, always-human-approved outreach before the window closes.

**D26 — Self-Represented Applicant Prospector (A15).** This decision has an origin story: a vendor (Tech Everest Intelligence) emailed the firm on 2026-07-07 offering, as a paid product, exactly this dataset — Canadian trademark applications filed *without* an agent of record that recently received an Examiner's Report (a rising segment, as more applicants self-file with AI assistance and then hit an objection they don't know how to answer). Rather than buy the vendor's feed, the decision was to **replicate it in-house**: detect these cases from the IP Horizons weekly TM XML data, resolve contacts from the public application record first (before paid enrichment), and route through the same CASL-compliant outreach pipeline as A14. The vendor's own stated benchmark — roughly 200–300 qualified leads per two weeks — is the target to match or beat.

**D27 — PCT National-Phase Prospector (A16).** A PatentScope-driven pipeline: find PCT applications whose 30-month Canadian national-entry deadline falls inside a configurable window, filter to applicants who historically do enter Canada, and for each one, identify their usual Canadian firm(s), their filing volume, and that firm's size (large firms → price-arbitrage targets; small firms → service-quality targets). Critically, this includes a **reciprocity guard**: applicants whose incumbent Canadian firm is one of Brunet & Co.'s own referral partners get flagged or excluded, so the marketing engine never accidentally cannibalizes the associate network that sends the firm reciprocal work.

**D28 — IP Audit Agent (A17).** A free portfolio audit offered as a lead magnet. For a given applicant, the agent looks across their whole portfolio for cases where they've already obtained a **granted US or EP equivalent**, or a **positive WO-ISA/IPRP opinion** on novelty and inventive step, and cross-references that against Canadian applications still within the 4-year examination-request window (or pending PCTs that could still enter Canada) — because those cases qualify for the **Patent Prosecution Highway (PPH)**, meaning faster, cheaper Canadian prosecution riding on work already done elsewhere. The audit report quantifies the savings and acceleration. A US-facing variant runs the same logic in reverse (Global PPH / PCT-PPH into USPTO). Always human-reviewed before delivery; carries a general-information, not-legal-advice disclaimer.

---

## Cross-cutting orchestration and workflow decisions (D29–D31)

**D29 — Prospect Engagement Orchestration + Agent Activity Dashboard.** The marketing agents (A14–A17) don't operate in isolation — they compose with A9 (drafting), A10 (quoting), calendar scheduling, the intake chain, and the filing assistants into a semi-autonomous **prospect-to-filing journey**. Every outbound message in that journey passes a human audit queue before sending, full stop, in v1 — though autonomy is designed to be configurable per message type later. A CRM-resident Agent Activity Dashboard tracks every prospect's stage, every agent action, and conversion funnels per campaign type.

**D30 — Work-management model.** This is the EOS/Monday.com decision — see `05-modules-M9-M11-workflow-eos-prior-art-utilities.md` for the full design. In short: Monday.com's board/column/view/automation interaction model is **emulated natively**, not integrated (integrating would fragment the matter-centric data model), as is EOS-specific software's L10/Scorecard/Rocks/To-Do mechanics. A three-tier work model — docket tasks, projects (scripted templates or orchestrator-planned unscripted projects), and micro-requests — covers everything from a rule-generated deadline to a same-day "please re-review this before I file it" ping, with the explicit goal of getting the firm off email and Word as workflow tools.

**D31 — Reminder & Follow-up Agent (A18) + stop-work mechanism.** Escalating reminder ladders for client-facing deadlines — the canonical example being maintenance fees: T−2 months (courtesy), T−1 month (action requested), T−2 weeks ("FINAL REMINDER," with jurisdiction-correct consequence language, e.g., CIPO deemed-abandonment). Awaiting-client tags on any work item can start a similar preset schedule. Client replies halt the ladder automatically. **This decision was revised** — see below. The same engine powers accounting's overdue-invoice dunning, which can escalate into a **stop-work** flag on a matter: a visible warning across every task view, a soft-block (with logged override) on new time/expense entry, and consequence language in the dunning emails — but rights-preserving tasks (deadline responses, maintenance fees) are explicitly exempt from stop-work unless the principal signs off, because a billing dispute must never silently cost a client their rights. The mirror-image safety rule — **"silence never abandons"** — means a reminder ladder that exhausts on a rights-preserving deadline always escalates to a professional for an explicit pay-or-abandon decision rather than letting the system do nothing.

> **Revision to D31 (recorded separately, same decision number):** the owner initially approved a carve-out where deterministic, pre-approved templates could auto-send without human review. He **later reversed this** — see D31's full text in the module file for the final policy: in v1, *all* reminders pass the human audit queue, exactly like every other outbound message. The auto-send logic was preserved (not deleted) behind a per-client, per-task-type `auto_remind` flag defaulting to off, so automation is a configuration change to make later, not a rebuild. This is a good example of how this register captures not just decisions but revisions to them — nothing here is search-and-replaced away.

---

## Matter-opening and UX decisions (D32–D34)

**D32 — Smart Matter Opening.** Directly replaces AppColl's single worst piece of manual work: copy-pasting bibliographic data field-by-field from WIPO/CIPO/USPTO/EPO into a new matter form. The new design: enter a jurisdiction and an application/registration number (or a PCT/Madrid/Hague number), and the OS retrieves and populates the matter automatically — used both for taking over existing matters and for opening national entries of international filings. For child filings (continuations, CIPs, divisionals, or a national filing descending from a provisional), the user instead selects the parent matter, and shared bibliographic fields inherit automatically, with only the child-specific deltas needing entry. In both cases, retrieved or inherited data is shown as a **review-and-confirm diff before save** — never silently written, because a wrong filing date propagates into every downstream deadline calculation.

**D33 — UX design principle.** A direct instruction from the owner: **do not copy AppColl's UX.** AppColl serves purely as a functional-parity checklist — the goal is a re-imagined interface built around guided question-and-answer flows at the moments that matter (matter opening, intake, project creation), not a redesign of AppColl's screens.

**D34 — AppColl parity gaps (closed via public-documentation research).** Before any credentialed access to the live system, a public-docs pass against AppColl's marketed feature set found four things the spec had missed entirely: **(1)** a full **Prior Art & IDS module** (critical for US duty-of-disclosure compliance) — this became module M11; **(2)** formal **Conflicts checking** across contacts/matters/adverse parties; **(3)** a **Report Builder** — user-defined, saved, and schedulable reports, distinct from in-app dashboards; **(4)** a **Client Portal** (AppColl calls theirs "Tandem") — deliberately phased in *after* cutover, and elegantly cheap to build later since it's just a permissioned skin over the `FamilyRecordExport` API already designed for the blockchain fork. Smaller items absorbed into existing modules: task-level fee/expense caps, CSV import/export everywhere, and a document-generation layer for pre-populated PTO forms.

---

## Live data-discovery decisions (D35–D37)

These three decisions differ from everything above in kind: they weren't design choices made through discussion, but **findings recorded as decisions** after directly inspecting the firm's live systems and data — first AppColl in-app, then SharePoint, then three CSV exports. Full raw detail on all three lives in `08-discovery-findings-appcoll-sharepoint-data.md`; what follows is the short version.

**D35 — AppColl in-app review.** Using the owner's own authenticated browser session (never his typed credentials — Claude does not accept passwords, see the security note in that file), a live walkthrough of the Tasks, Matters, and Billing modules validated and substantially enriched the matter data model: the real status vocabulary (including a deliberate distinction between "Abandoned" and "Client Abandoned"), a three-way reference-mapping field (firm ref / client ref / **foreign associate ref** — a field the interview alone never surfaced), US trademark Basis and Register fields, a Complexity rating that's designed to feed the new assignment engine, and more. It also surfaced production realities in the reference grammar — sequences running to `US11`, per-client custom numbering schemes, `GB` marks auto-created from Brexit clones — that the interview-derived Appendix A hadn't anticipated.

**D36 — SharePoint structure validated in place.** A full crawl of the actual SharePoint tenant (`brunetco365.sharepoint.com`), navigating client folder → family folder → jurisdiction folder → file-history subfolder, confirmed the Appendix A directory convention essentially as specced, while adding real detail: per-client `9999 - General` and `Account` folders, prior-art bundles already living in the file history (a ready-made migration seed for the new M11 module), and — importantly — the discovery that **folder names currently duplicate docket status** ("US (Issued)", "EP (EESR)"), which produced a design ruling (owner-confirmed) that the OS stores status only in the database and creates new folders without status annotations, ending the practice of staff manually renaming folders to reflect status changes.

**D37 — AppColl CSV exports analyzed.** Three files — 552 task-type rules, 4,596 contacts, and 33,760 billing items — gave the project its first look at the *actual configured behavior* of the current system, not just its screens. Most consequentially, the real deadline-type taxonomy turned out to have **six** categories, not the four visible in the UI, and legacy rules can embed field-setter actions on the triggering matter, not just create follow-on tasks — both of which became requirements for the OS's own rule engine. On the billing side, the full type vocabulary (client payments, retainer transactions, adjustments, write-offs) confirmed that all money-movement types belong in Xero and the virtual trust module rather than being replicated as OS billing items — a clean architectural boundary the raw export made obvious in a way the AppColl screens hadn't.

---

*For the full text of any decision as it appears in the canonical spec table, see `IP-OS-SPEC-v0.15.md` §0 (or later versions). This file is a companion, expanded read — the spec's decision register remains the single source of truth for exact wording.*
