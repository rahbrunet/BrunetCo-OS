# Modules M9–M11: Work Management, EOS, Prior Art & IDS, Cross-Module Utilities

This is the largest single design conversation in the project, and it deserves its own file. It started as a question — "is Monday.com a useful framework to employ?" — and turned into the module (D30) that arguably matters most for whether the OS actually changes daily behavior at the firm, as opposed to becoming another tab nobody opens.

---

## M9 — Work Management (D30)

### The Monday.com question, answered directly

**Emulate it, don't integrate it.** Monday.com's core interaction model — boards of items with typed columns (status, owner, date, priority), multiple views over the same underlying data (table, kanban, timeline, workload, calendar), groups, subitems, and user-configurable automations ("when status changes to Done, create item X and assign it to Y") — is genuinely good, proven UX, and it's a large part of why non-technical teams adopt Monday so readily. But *integrating* Monday itself would fragment the firm's carefully-designed matter-centric data model: Monday's items don't natively know what a matter, a docket deadline, or a trust balance is, and the firm would end up re-syncing data it just spent this whole project designing properly. The right move, and the one adopted, is to **emulate the board/column/view/automation interaction model natively inside the OS**, so every work item is born matter-linked rather than needing to be connected to one after the fact.

The same logic applies on the EOS side: purpose-built EOS software (Ninety.io, Bloom Growth) has already refined the L10/Scorecard/Rocks/To-Do mechanics well — worth studying and emulating closely, not bolting on as a separate tool, because the scorecard's measurables should auto-populate from the firm's own docket, billing, and pipeline data, which no external EOS product can do on its own.

### The three-tier work model

The core design insight is that the firm's work genuinely comes in three different shapes, and trying to force them into one shape (as email and Word currently do, badly) is exactly the problem being solved:

1. **Docket tasks** — deadline-driven, rule-generated, coming out of M1. These carry non-negotiable dates and the full DeadlineType taxonomy described in that module.
2. **Projects** — which split into two sub-types:
   - **Scripted projects:** template-driven work with predefined stages, chained tasks, role routing, and standard cycle times — patent drafting, OA responses, that kind of repeatable practice work.
   - **Unscripted projects:** the genuinely ad-hoc stuff — the two examples used throughout this project are assignment recordal across multiple jurisdictions, and obtaining signatures from a deceased inventor's estate. For these, the orchestrator can do real work: describe the project in natural language, and it drafts a plan — stages, tasks, owners, dependencies, target dates — that the user edits and then launches. This is the mechanism by which one-off projects stop living in Word documents: creating a tracked project has to be *easier* than opening Word, or nobody will bother.
3. **Micro-requests** — the intra-day layer, and the piece specifically designed to kill email-as-workflow. "Review this response before I file it" becomes an `@request` attached directly to the work item or document in question: it spawns a micro-task in the reviewer's queue with an SLA timer, notifies them via Teams (not email), **blocks the parent task until it's resolved**, can bounce back and forth as many times as needed within the same day, and every round-trip's turnaround time gets logged automatically.

### My Day

Each person gets one single unified queue — docket tasks, project tasks, micro-requests, and EOS To-Dos, all together, priority-ordered by deadline proximity, SLA pressure, and blocked-status — with quick actions built in (complete a task and its chained follow-on spawns automatically; request a review; defer with a logged reason). The intended effect is that My Day replaces the inbox as the place someone starts their morning.

### Board framework

Configurable boards over work items, with typed columns (status, owner, due date, priority, matter link, stage, and custom fields), five view types (table, kanban, timeline/Gantt, calendar, workload), groups, saved filters, and subitems — every item matter- or family-linked at the moment of creation, never as a retrofit. User-configurable automations extend this into genuine no-code territory: "when status moves to Filed, create an invoice-review task for accounting" or "when a review is approved, unblock the parent task and notify its owner."

### Assignment & capacity

Task assignment can be suggested or fully automated from role, current live workload, and a person's historical cycle time on that specific task type. An interactive capacity board — per-person, per-matter, drag-to-reassign — is the direct replacement for the firm's existing Word-based Patent and Trademark Project Lists. SLAs and escalation paths route to the responsible professional, with visibility on a firm-wide dashboard.

### Productivity visibility — a deliberate framing choice

Metrics are framed as **seat-owned EOS measurables**, not activity surveillance, and this distinction was a deliberate design decision, not an afterthought. Per person: on-time completion percentage, throughput by task type, cycle time against a task-type baseline, micro-request turnaround time, aging WIP, and To-Do completion rate (with EOS's standard 90% target tracked explicitly). Per firm: a funnel of open/at-risk/overdue work, docket compliance, and review-bottleneck detection. The metrics surface on a person's *own* dashboard first, and only aggregate up from there — the reasoning being that EOS accountability works when people own their own numbers, and gets gamed (or resented) the moment it feels like being watched rather than measured.

### Weekly accountability reporting

An auto-generated **L10 pack** — the firm scorecard with red/yellow/green status, Rocks status, To-Do completion, overdue and at-risk items broken out *by owner*, aging WIP, the next 7 days of critical deadlines, and headline metrics — drives the weekly L10 meeting directly. Alongside it, per-person **1-on-1 reports** carry the same metric set scoped down to just that seat and their own open items, supporting individual supervision conversations. Both are designed to be exportable and printable, not locked inside the app.

---

## M9.0.1 — The EOS Layer Itself

The mechanics, spelled out:

- **Scorecard** — weekly measurables, auto-populated from docket data, billing data, CRM data, and content-engagement data wherever possible, supplemented by manual entries where no automatic source exists; each measurable has an owner and is scored red/yellow/green.
- **Rocks** — quarterly goals, tracked per person and per company, with milestones.
- **Accountability Chart** — the org structure as EOS defines it (seats and their measurables, not a traditional org chart).
- **Issues list** — capturable from *any* task, thread, or meeting, feeding directly into the IDS (Identify, Discuss, Solve) process rather than requiring someone to remember to write issues down separately.
- **To-Dos** — the 7-day commitments that come out of an L10 meeting, with completion rate tracked against the 90% EOS benchmark.
- **L10 meeting mode** — a guided in-app flow through the standard EOS agenda: segue → scorecard → Rocks → headlines → To-Dos → IDS → conclude, with notes and new To-Do capture built directly into the flow.
- **Quarterly/annual review support.**
- **Role-based dashboards:** a firm-level EOS view, a professional's My Day/docket/WIP view, and an admin's billing/reconciliation/audit-queue view — three different lenses on the same underlying data.

---

## M11 — Prior Art & IDS (D34)

This module didn't exist in the spec until a gap-analysis pass against AppColl's public feature documentation turned it up — and for a firm with a US patent practice, it closes a real liability gap (US duty-of-disclosure obligations under §1.56), not just a nice-to-have.

The design: a reference database (patents, publications, non-patent literature) with bibliographic auto-fill by document number via USPTO/EPO/WIPO retrieval, where every reference is **cross-linked to matters and families** carrying a per-link state — cited, to-be-cited, or considered-by-examiner. A **family cross-citation matrix** view shows, at a glance, which references have been cited against which family members and what still needs citing across the rest of the family — genuinely useful for large families where the same prior art is relevant to multiple related applications. Bulk cross-citing lets hundreds of references get attached to a new application in one action rather than one at a time. Office-action references and search-report references get ingested automatically, fed by Agents A1 (status watcher) and A6 (prior-art analyzer). A **one-click IDS generation** feature produces a pre-populated USPTO Form SB08, staged directly into Agent A13's filing flow. A **duty-of-disclosure dashboard** flags any matter carrying known-but-uncited references — turning a compliance obligation that currently depends on someone remembering into something the system actively surfaces.

The module also stores Canadian protest and prior-art submissions where relevant, so it isn't purely a US-practice tool even though US duty-of-disclosure is the primary driver.

One direct benefit worth calling out: the SharePoint crawl (D36) found that the firm's US matters already have physical `FH/PriorArt-IDS{n}/` bundle folders in their file history, and family-level `PriorArt/` folders exist too — meaning this module's migration has a ready-made seed rather than starting from an empty database.

---

## Cross-Module Utilities (D34)

Four smaller items, all closing gaps found in the same AppColl parity pass that produced M11:

- **Report Builder:** user-defined reports over any module's data — filters, column selection, grouping — saved and shareable, with scheduled runs and delivery as PDF or spreadsheet by email (SFTP delivery available as an option). This is explicitly distinct from in-app dashboards: dashboards are for glancing at, reports are deliverables — including client-facing report templates.
- **CSV import/export on every module**, for bulk loading, ad-hoc analysis, and migration work.
- **Document-generation layer:** one-click generation of PTO forms and other firm documents, pre-populated from matter and contact data. This replaces AppColl's fillable-PDF-form approach and feeds directly into Agent A12/A13's filing-staging flow and into DocuSign envelope creation.
- **Conflicts checking:** a proper conflicts database spanning contacts, matters, applicants, and adverse parties, checked automatically at intake (M7-R4, as part of the intake chain) and runnable on demand at any time, with every check logging a result.
- **Client Portal** (deliberately phased in *after* cutover, not part of v1): a controlled, client-facing view of the client's own portfolio — matters, statuses, upcoming deadlines, reports — permissioned per client. Built directly on the `FamilyRecordExport` API described in `02-vision-principles-architecture.md` §3.1, meaning the same infrastructure serves both this portal and the future blockchain fork — one build, two eventual uses.
