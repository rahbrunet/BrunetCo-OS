# BrunetCo OS — Clean-Sheet Design Review

**Date:** 2026-07-08 | **Reviewer:** Claude (independent pass against Spec v0.15 / Tracker v15, full 14-file documentation set)
**Purpose:** Sanity-check the specification before coding starts; specifically reconsider email/file synchronization architecture and evaluate whether the current design is sound or should be restructured.

---

## 1. Verdict up front

The specification is fundamentally sound. The data model (family as spine, parent/relationship typing, the six-value DeadlineType taxonomy), the money boundary (Xero owns money movement, OS owns billable work), the human-in-the-loop policy, and the evidence-driven discovery process (D35–D37) are all better-grounded than most commercial products in this space. I recommend **keeping SharePoint as the document byte-store** — moving files into a database would be a step backwards for this firm — but with one architectural inversion described in §3 that resolves the "two sources of truth" tension the current design carries.

The two most consequential recommendations are **not** about storage:

1. **Re-sequence the build so AppColl cutover happens before the marketing/prospecting agents are built** (§4). The current phase order runs cutover (Phase 9) after all of Phases 7, 8, and 8.5. That's the single largest structural risk in the plan.
2. **Collapse the 19-agent roster into ~6 buildable platform services** at scaffold time (§5). The spec already hints at this; the scaffold should make it explicit so James isn't staring at 19 deliverables.

Everything else is refinement, not redesign.

---

## 2. What the clean-sheet exercise confirms as sound

I deliberately tried to argue against each of these and failed:

**Family-centric data model with `parent_matter` + `relationship_type`.** Correct, and the D35 finding of typed connections to *external* priority applications (the Italian filing example) is properly accommodated. No change.

**Emulate Monday.com/EOS rather than integrate (D30).** Correct call. Integrating Monday would fragment the matter-centric model and re-create the sync problem you're eliminating. The three-tier work model (docket / projects / micro-requests) matches how the work actually arrives.

**Xero as ledger, virtual trust, no OS-native money movement (D12, M2-R10).** The billing CSV analysis made this boundary obvious and it's right. The 8,722 "Payment from Client" rows in AppColl are exactly the noise you don't rebuild.

**Human-approves-everything-sent in v1, with dormant `auto_remind` (D31 revised).** Right, and the "silence never abandons" rule is the best single sentence in the spec.

**Fee schedules effective-dated and entity-size-aware (M3-R6/M4).** The $210.51→$225.00 evidence from your own filings settles it.

**Supabase + FastAPI + React/TS monorepo.** Appropriate for team size and skills. One caveat in §6.1.

**Rejecting the status-in-folder-names practice (D36).** Correct — and §3 below argues you should extend this same principle one step further.

---

## 3. The central question: where do emails and files live?

You asked whether emails and files (Word, PDF) could be stored in a database instead of the SharePoint structure, and whether other applications would make files easier to access per matter. I evaluated four options from a clean sheet.

### Option A — Database / object storage as master (files as blobs in Supabase Storage or S3)

**Rejected.** This looks cleaner on a whiteboard — one source of truth, transactional integrity, no folder taxonomy — but it destroys the properties a law firm actually depends on:

- **Native Office editing dies.** Word co-authoring, tracked changes, AutoSave, and desktop Office round-trips only work against SharePoint/OneDrive storage. You would need to build check-out/check-in or WebDAV plumbing — i.e., poorly rebuild what Microsoft already runs for you.
- **Every existing access path dies:** Explorer sync, Teams file tabs, SharePoint search, mobile access, sharing links. Staff currently reach files five different ways; all of them assume SharePoint.
- **Compliance burden moves to you:** retention, legal hold, backup/restore, and eDiscovery are currently M365's problem. A blob store makes them your problem.
- It contradicts your own stated principle: *integrate-first; don't replace utilities the firm already trusts.*

### Option B — SharePoint Embedded (Microsoft's app-owned container storage)

**Rejected, narrowly.** SharePoint Embedded has matured into Microsoft's preferred path for embedding M365 document editing inside custom apps — Office web/desktop editing works against it, and containers are app-exclusive. If BrunetCo OS were a SaaS product for other firms, this would be the right answer. But containers are **invisible to normal SharePoint UI, Explorer sync, and Teams** — your team would lose every current access path and become 100% dependent on the OS's own file UI from day one. It also bills on Azure consumption. Worth re-examining if you ever productize the OS for other agencies; wrong for the firm's own operations. ([Microsoft overview](https://learn.microsoft.com/en-us/sharepoint/dev/embedded/overview), [Office experiences in SPE](https://learn.microsoft.com/en-us/sharepoint/dev/embedded/development/content-experiences/office-experience))

### Option C — Buy a legal DMS (NetDocuments / iManage)

**Rejected as primary path; noted as the fallback.** These are purpose-built for exactly your problem — matter-centric filing with excellent Outlook email filing (iManage's Outlook integration in particular is the industry benchmark). But: roughly **$30–60/user/month plus implementation costs that commonly reach five to six figures** ([2026 comparison](https://www.bigmodeconsulting.com/compare/netdocuments-vs-imanage), [pricing guide](https://lexworkplace.com/netdocuments-pricing/)), a second migration of your 20,000 files, another integration surface for the OS, and it would gut the "single pane per matter" vision — the DMS would compete with the OS for the user's attention. The honest role for this option: **if the OS build stalls badly, NetDocuments + AppColl-successor is the commercial fallback.** Knowing that is worth something; buying it now is not.

### Option D — Keep SharePoint as byte-store, make the database canonical for identity — **Recommended**

This is your current design **plus one inversion**. Today's spec treats the SharePoint folder path as meaningful (path encodes client/family/jurisdiction; M6-R6 even uses folder location as migration ground truth — correctly, for *legacy* data). Going forward, invert it:

> **The Postgres document record is the document's identity. SharePoint holds the bytes. The folder path is a human-convenience projection, not a system fact.**

Concretely:

1. Every document gets a DB row: `document_id`, matter/family link, document type (the OA/POA/IASR taxonomy), dates, source (email attachment / generated / uploaded / office correspondence), and the SharePoint **driveItem ID** as the storage pointer. The OS always resolves by driveItem ID — never by path — so a renamed or moved file never breaks linkage. (Graph delta queries return ID-stable changes; this is what makes the M5 delta-sync design robust rather than fragile.)
2. The OS still *writes* files into the Appendix A folder structure so Explorer/Teams users see the familiar tree. But if a human drags a file to the wrong folder, the DB record — not the path — says which matter it belongs to, and the OS can flag or fix the discrepancy instead of inheriting it.
3. This is the same move as D36 (status out of folder names), applied to matter-linkage itself. D36 was the first step; this completes the thought. It also means the future Client Portal and any eventual DMS-grade features (ethical walls, per-document permissions beyond folder inheritance) hang off DB records, not folder ACLs.

**For email**, the spec's M6 design is already Option D in spirit — a full email database, threaded and searchable, linked to matters — and I confirm it. Three sharpening recommendations:

- **Retire the `.msg`-file-in-SharePoint practice at cutover.** The 10–15k legacy `.msg` files migrate into the email DB (per M6-R6) and the originals stay in SharePoint untouched as archive — but *new* correspondence should never be filed as `.msg` files again. The email DB + matter correspondence timeline replaces that practice entirely. The spec implies this; it should say it, because it's a staff-habit change worth announcing deliberately.
- **Store message bodies + extracted text in your own database; treat Exchange as the legal-record archive.** Graph pointers alone rot (retention policies, mailbox changes); local storage is what makes threading, full-text search, and A8/A9/A11 corpus work fast and offline from Graph throttling. Exchange retention/litigation-hold remains the compliance backstop. Deduplicate attachments by content hash — file the attachment **once** into the matter folder as a document (getting a DB row per point 1), and let every email that carried it link to the same document record.
- **Decide the privacy model for personal mailboxes now, not at build time.** D15 ingests every team member's mailbox. The spec is silent on who can then see whose mail. A firm-wide searchable database of ten people's complete mailboxes is a very different thing from a matter-correspondence archive. My recommended default: **matter-linked messages become firm-visible on the matter timeline; unlinked messages remain visible only to the mailbox owner (and the classification pipeline).** Enforce it in RLS, not application code. This needs your explicit sign-off either way — it's a D-series decision that's currently missing.

### The integration that actually answers "easier access per matter": an Outlook add-in

You asked whether other applications could make files easier to access per matter. The highest-leverage answer isn't a storage change — it's meeting staff where they already are. The reason iManage wins hearts is its Outlook sidebar. Build the equivalent as a thin **Outlook add-in (Office.js)** against the OS API:

- Reading pane shows: which matter this thread is classified to (or a one-click picker if the classifier wasn't confident), the matter's recent correspondence, and open tasks.
- One click: file this message + attachments to matter X (which is just confirming/correcting the M6 classification).
- Compose: insert template, link the outgoing message to a matter/task.

This is a modest work package (the API does the heavy lifting; the add-in is UI), it materially raises classification precision by making human correction frictionless, and it removes the biggest adoption risk in M6 — staff bypassing the OS because Outlook is where they live. I'd slot it as **WP 4.7** alongside the email module. A matching Teams tab and a Word add-in are natural later extensions but not v1.

Two smaller integration notes: (a) expose the OS API as an **MCP server** from day one — trivially cheap since you're generating typed contracts from OpenAPI anyway, and it makes every future Claude-based workflow (including the 19 agents themselves, and ad-hoc "ask Claude about matter X" queries) first-class rather than bespoke; (b) a **Microsoft Graph connector** feeding OS records into M365 search/Copilot is a nice-to-have, post-cutover.

---

## 4. The biggest structural issue: cutover comes too late

The tracker sequences cutover (Phase 9) after CRM/intake (7), all marketing and prospecting agents (8), and the filing assistants (8.5). Taken literally, you keep paying for AppColl, running the parallel-run harness, and reconciling two systems while building opposition-watch prospecting and PPH audit engines — none of which depend on cutover, and none of which cutover depends on.

Every month of parallel operation costs money, attention, and drift risk. And the team is effectively one primary developer plus part-time contributors. Recommendation — split into two explicit tracks:

**Track A — Replace AppColl (the critical path).** Phase 0 → 1 (docketing) → 2/2.5/3 (expenses, time, billing/trust) → 4 (documents & email core) → a *minimal* slice of 5 (My Day + docket views; not the full board framework or EOS layer) → 6.1–6.4 (orchestrator, watchers) → 7.3–7.4 only (intake chain + signatures, since new matters must be openable) → parallel run → **cutover**. Also pull **5B.4 (conflicts)** into Track A — you shouldn't open matters in the new system without conflicts checking.

**Track B — Grow the firm (post-cutover, iterative).** Full board framework and EOS layer, A4–A6, A8–A11 (these benefit from the migrated email corpus anyway — A11's training data only exists after WP 4.6), all of Phase 8 marketing/prospecting, 8.5 filing assistants, report builder, client portal.

This isn't a scope cut — everything survives — it's a promise about what "done enough to stop paying AppColl" means. It also honors the pattern memory records: this project has repeatedly reached "v1-ready" and then expanded. A two-track structure gives scope expansion a lane (Track B) that can absorb new ideas without moving the cutover date.

One related flag: **zero missed deadlines** is the first success criterion, and deadline-engine correctness (WP 1.2/1.3 golden tests, the opaque trigger-linkage reconstruction from D37) is the hardest, least-glamorous work in the plan. Track A sequencing protects the developer's attention for exactly that.

---

## 5. Nineteen agents should be built as ~six services

As a *specification taxonomy*, A0–A18 is excellent. As a *build plan*, it risks 19 bespoke deliverables. The spec already notes the sharing (A14/A15/A16 on one prospecting engine; A8/A9/A10 on one ingestion pipeline). Make the scaffold reflect the real architecture:

| Platform service | Serves |
|---|---|
| Email ingestion & classification pipeline | A8, A9, A10, M6 |
| Drafting service (style-trained, KB-grounded) | A9, A11, A18 templates, M8 content |
| Prospecting engine (register ingestion, ledger, enrichment, CASL gates, outreach tracking) | A14, A15, A16, A17 delivery |
| Ladder/scheduler engine (offsets, templates, halt conditions, escalation) | A18 deadlines, awaiting-client, dunning |
| Watcher framework (per-office adapters) | A1, A3 |
| Browser-automation framework | A12, A13, (Clarivate Tier 3 if ever) |

Plus the orchestrator (A0) and the Knowledge Base service, which the spec already treats as infrastructure. Each "agent" is then a configuration + prompt + detection rule on a shared service — which is also what makes the 20th agent cheap.

---

## 6. Smaller flags (verify or decide at scaffold time)

**6.1 RLS is only real if requests carry user identity to Postgres.** With FastAPI between the client and Supabase, the common failure mode is the API using the service-role key everywhere — which bypasses RLS entirely, silently demoting your "family-level permissions in the data layer" to app-code-only enforcement. Decide at WP 0.7: either pass the user's JWT through to Supabase per-request, or explicitly accept app-layer enforcement and treat RLS as defense-in-depth for direct-connection paths. Either is defensible; drifting into the second while believing you have the first is not.

**6.2 Graph operational realities.** Mail webhook subscriptions expire after ~3 days and must be renewed; build a subscription manager with a delta-query catch-up sweep as the safety net (missed webhooks are a *when*, not an *if*). Throttling budgets are per-mailbox and per-app — 14 mailboxes of history backfill should be rate-limit-aware batch jobs, not naive loops. Use `Sites.Selected` for SharePoint scope (already in the research notes — good). Verify all current limits at build; they shift.

**6.3 Unified search is implied but unspecced.** "A single pane per matter" plus a full email DB plus a document index implies cross-entity search (matters, emails, documents, tasks) as a first-class feature. Postgres FTS covers v1; pgvector is a cheap add if you want semantic retrieval later (and the KB service needs pgvector-style retrieval anyway — one pattern, two uses). Worth one line in the spec so it doesn't arrive as a surprise requirement mid-build.

**6.4 M365 backup.** Nightly Supabase backups are specced; SharePoint/Exchange rely on Microsoft's native protection, which is not a backup product (retention ≠ restore). A third-party M365 backup subscription is a cheap, boring risk reducer for the system that will hold your entire matter file. Optional, but decide consciously.

**6.5 Data residency.** D2 accepts Canada-or-US hosting. Fine under PIPEDA generally, but some client engagement letters or government-client contracts (you have a US Government agency/contract field in the data model, and IPON-funded clients) may impose residency or handling constraints. Worth a one-time scan of standard engagement terms before locking a US region.

**6.6 Already-flagged items I'll simply endorse:** verify at build that CIPO/USPTO still lack filing-submission APIs (the 🔎 flags); prove A8 precision on labelled data before it touches intake; the 8-week parallel run needs your explicit confirmation; the AppColl trigger-linkage reconstruction belongs in WP 1.3 golden testing exactly as planned.

---

## 7. Decisions requested from you

1. **Adopt the two-track sequencing (cutover before marketing agents)?** — recommended yes; this is the highest-impact change in this review.
2. **Personal-mailbox privacy model** — matter-linked emails firm-visible, unlinked private to mailbox owner (recommended), or full firm visibility? Needs to become a D-series decision.
3. **Add the Outlook add-in as WP 4.7?** — recommended yes.
4. **Confirm the identity inversion** (DB record canonical, driveItem ID pointers, folder path as projection) as a design ruling extending D36.
5. **MCP-server surface for the OS API** — recommended yes at scaffold time (near-zero marginal cost).

None of these reopen settled decisions D1–D37; items 2 and 4 are additions the register is currently missing.

---

*Sources for §3 option analysis: [SharePoint Embedded overview — Microsoft Learn](https://learn.microsoft.com/en-us/sharepoint/dev/embedded/overview) · [Office file experiences for SharePoint Embedded — Microsoft Learn](https://learn.microsoft.com/en-us/sharepoint/dev/embedded/development/content-experiences/office-experience) · [NetDocuments vs iManage (2026) — Big Mode Consulting](https://www.bigmodeconsulting.com/compare/netdocuments-vs-imanage) · [NetDocuments pricing — LexWorkplace](https://lexworkplace.com/netdocuments-pricing/) · [iManage vs NetDocuments vs SharePoint (2026)](https://www.comparethecloud.net/articles/imanage-vs-netdocuments-vs-sharepoint-document-management-uk-law-firm)*
