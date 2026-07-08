# Modules M1–M4: Docketing, Billing & Trust, Expenses, Fee Schedules

These four modules form the practice's operational core — the part of the system that has to be right on day one, because it's the part with legal and financial consequences if it's wrong.

---

## M1 — Families, Matters & Docketing

This is the foundation module: everything else in the system ultimately links back to a Family or a Matter.

- **M1-R1 (References & relationships):** References follow Appendix A exactly (`07-appendix-A-file-conventions.md`). Auto-generation treats the jurisdiction segment as an ordered, per-family sequence — US, then US2, then US3, and so on. A `parent_matter` field plus a `relationship_type` field capture the actual nature of the connection (continuation, CIP, divisional, or a PCT/Madrid national-designation link) — the reference string alone never encodes this, because the docketing logic genuinely needs to know the type: national-phase deadlines are computed from the PCT filing date, and Madrid registrations carry a 5-year central-attack dependency period that needs its own monitoring.
- **M1-R2 (Deadline engine):** Rules fire from trigger dates and can chain. Extensions carry their own fee logic. Jurisdiction-specific holiday calendars apply. CIPO specifics are modeled explicitly — maintenance fees run from the 2nd anniversary of filing, and examination-on-request has its own docketing logic.
- **M1-R3:** The AppColl rule library (552 rules, fully catalogued — see `08-discovery-findings-appcoll-sharepoint-data.md`) is the seed data for this engine, not a from-scratch build.
- **M1-R4 (Rules are data):** Rules live in an admin UI and can also be edited via natural language through Agent A2. All rule changes are versioned, effective-dated, and gated behind approval — no rule silently changes behavior.
- **M1-R5:** Docket views plus a daily docket email.
- **M1-R6:** Full audit trail on every docket change.
- **M1-R7:** AppColl migration and the parallel run, detailed in `10-testing-cutover-open-items-action-items.md` §14.
- **M1-R8 (Annuity docketing):** Maintenance-fee deadlines are generated in-house per jurisdiction and feed the annuity instruction workflow (§7.1 below).
- **M1-R9 — Smart Matter Opening (D32):** The matter-opening form asks for a jurisdiction and a number, then retrieves and populates bibliographic data from the authoritative office source — used both when taking over an existing matter and when opening a national entry of a PCT/Madrid/Hague filing. For child filings, the user selects the parent matter(s) instead, and the relationship plus all shared bibliographic fields are inherited automatically. Either way, retrieved or inherited data is shown as a **review-and-confirm diff before save** — this is a deliberate anti-silent-write design, since a wrong filing date corrupts every downstream deadline. The form only asks the user for what retrieval genuinely couldn't supply, per the D33 guided-Q&A UX principle.
- **M1-R10:** Task-level fee/expense caps — per-client cost-control ceilings, surfaced at time/expense entry and at billing in M2.
- **M1-R11 (from D35, amended by D37):** Every task carries dual dates — a `RespondBy` date and a `FinalDueDate` — plus a trigger `RefDate` and a `ClosedOn` date. Every task also has a **DeadlineType**, and the real taxonomy (confirmed by the CSV export analysis) has **six** values: Hard External, Extendable External, Internal, General Reminder, Event, and Transient Event. This taxonomy directly drives Agent A18's reminder-ladder behavior, sets escalation severity, and scopes the "silence never abandons" safety rule (which only applies to Hard and Extendable External deadlines — the ones with real legal consequences). Task statuses include Received, Not Needed, and Missed (deadline passed, extension now required), alongside the obvious Open/Completed.
- **M1-R12 (from D35):** **Awaiting-office tasks** — items like "Expect to receive first Examiner's Report by [date]" — are a first-class third category of waiting item, alongside awaiting-client (handled by A18) and blocked-on-review (handled by micro-requests). These surface distinctly in My Day, and when the expected event runs overdue, that's treated as a signal worth escalating — often it means someone should query the office directly or check whether the status watcher missed something.
- **M1-R13 (from D37, the CSV rule-library analysis):** The legacy rule library revealed several rule-engine capabilities the OS needs to replicate: owner resolution that supports not just fixed role queues (e.g., "Patents Admin") but *matter-relative* roles ("Attorney for matter," "Paralegal for matter"), a "Current User" resolution, and multi-assignee expansion ("All inventors for matter"); rules that define **alternate offset paths** — essentially conditional dual deadlines depending on some condition; and rules that carry **actions beyond simple task creation** — specifically, matter-field setters using template expressions evaluated against the triggering task or event (a real example found in the export: `Update Matter: AllowanceDate={TriggeringTask.RefDate}`). Legacy reminder-pair task types (a courtesy reminder task plus a separate deadline task) get migrated into A18 ladder definitions rather than surviving as two separate standalone tasks.

---

## M2 — Time, Billing, Invoicing & Trust

- **M2-R1:** Fast time entry — file-reference autocomplete, activity codes, timers. Scheduled as its own mini-phase, 2.5, in the build order.
- **M2-R2:** A flat-fee catalogue with per-client overrides.
- **M2-R3:** Re-billable expense items are drawn from the Fee Schedule Service (M4). Per D12, there is no markup, and re-billing always uses the actual settled FX rate — invoicing hard-warns if a linked expense is still unreconciled (i.e., its estimated amount hasn't yet been replaced by the final settled amount).
- **M2-R4:** Client discounts (flat percentage or a schedule) trace back to the quote that generated them, so a discount always has a documented origin.
- **M2-R5:** WIP review flows into draft invoices, with any relevant quotes surfaced alongside for reference.
- **M2-R6:** Invoices push to Xero. The tax engine reads a client's tax-residency flag and province, maps that to the correct HST rate and the corresponding Xero tax code, and zero-rates non-residents automatically (per D12).
- **M2-R7 (Virtual trust):** Retainer balances are tracked per client/matter against a Xero client-deposits liability account. Available trust is shown at invoicing time; a trust-to-revenue transfer entry is generated automatically when an invoice issues. The reconciliation bank account is selectable, and a future physical trust account is supported by the design without being required now. Dashboards surface trust that's been collected but not yet applied to a sent invoice.
- **M2-R8 — Receivables management (D31):** Overdue-invoice dunning ladders run through Agent A18 (statements generated from Xero balances, sent from accounting@, escalating in tone). Configurable stop-work thresholds — by outstanding amount and/or days overdue — flag a matter: the flag changes color/badge state across every task view in the system, raises a soft-block (with logged override) on new time or expense entry against that matter, and adds advance-payment warning language to the dunning emails. Rights-preserving tasks are exempt from stop-work unless the principal explicitly signs off — a fee dispute must never silently cost a client their legal rights. Flags clear automatically the moment Xero's payment webhook confirms payment.
- **M2-R9 — Client credit/prepaid programs (from D35):** Tracks client credit balances and funding-program entitlements — the concrete example found in AppColl's live billing data was "IPON banked time" (a government-funded IP support program some clients draw against). Billing items can be charged against a program balance, with the remaining balance visible both at entry time and on the client record. Zero-amount courtesy items render as "no charge" while still showing the standard rate, matching the pattern observed in production.
- **M2-R10 (from D37, the billing CSV analysis):** Billing items support signed adjustments and formal write-off records, both audit-logged and reason-coded. A per-item taxable flag drives the HST computation directly. An optional activity-code taxonomy (the legacy PA/TR codes found in the export, e.g. PA430, PA130, TR100) is imported and made available for analytics and for clients who require coded invoices. Critically, client payments, retainer receipts, and refunds are **not** modeled as OS billing items at all — they live entirely in Xero and the virtual trust module — and the migration logic maps the legacy AppColl payment-type items into that structure rather than replicating them as line items in the new system.

---

## M3 — Expense Capture & Reconciliation

The core loop: record an expense at the moment it's incurred (matter, type, currency, *expected* amount) → push it to Xero as either a Spend Money entry or an ACCPAY bill, plus a Xero **LinkedTransaction** marking it as a billable expense → match it against the bank feed (using Agent A7's heuristics plus a manual fallback UI) → write back the **actual** CAD amount and the FX rate that applied → surface anything that doesn't reconcile cleanly on an exceptions dashboard → and finally, guard invoicing so it hard-warns if any linked expense is still sitting unreconciled.

- **M3-R5:** The CIPO deposit account is modeled as a payment source in its own right — expected charges against it, statement reconciliation, and top-up transfers are all tracked.
- **M3-R6:** Fee amounts are entity-size-aware (CIPO small-entity status under s.44(2); USPTO small/micro entity status) and effective-dated. The evidence for why effective-dating matters came directly from the firm's own historical filings: a CIPO national-entry fee moved from $210.51 to $225.00 between 2023 and 2024 filings in the firm's own records, meaning a naive "current fee" lookup would misprice a retroactive calculation.
- **M3-R7:** Payment confirmation documents are auto-attached to the relevant expense record straight from email ingestion (M6), without anyone needing to manually file them.
- **M3-R8:** Clarivate's own invoices (for annuity payment services) are ingested as re-billable expenses, linked directly to the annuity matters they relate to.

---

## M4 — Official Fee Schedule Service

Effective-dated, entity-size-aware fee tables covering **CIPO, USPTO, EPO, and WIPO only in v1** (D20 — a deliberate scope limit, not an oversight). Fee data is kept current via a scheduled scrape with human-approved diffs before anything changes live — no fee table updates silently. This service feeds both the M2 billing dropdowns (so staff pick a real, current fee rather than typing a number) and M3's expected-amount fields (so expense reconciliation has something correct to compare against).

### 7.1 — Annuity Instruction Workflow

This ties together M1's annuity docketing, M4's fee data, and the D18 Clarivate decision into one closed-loop process: a deadline approaches → the client is reported to and an instruction is captured (pay or abandon) → the instruction is sent to Clarivate → the confirmation is reconciled → the resulting cost flows into M3 as an expense.

🔎 **Research finding to verify at build:** Clarivate's documented renewals integrations are with its own IPMS products (FoundationIP/IPfolio). No public third-party instruction API was found during research, though bespoke firm-specific integrations are known to exist (J A Kemp is a cited precedent), so a negotiated file/feed channel remains plausible.

Given that uncertainty, the design is explicitly **tiered** rather than betting everything on an answer that hasn't come back yet:
- **Tier 1** ⏳ (pending): the owner asks Clarivate's account manager whether an instruction-submission or status-feed channel exists; if one does, a connector gets built against it.
- **Tier 2** (ships regardless of the Tier 1 answer): the OS generates a per-cycle instruction batch — the files that need instructing, with deep links into Clarivate's portal where possible — a staff member executes the batch manually and checks items off, and Clarivate's confirmation emails are ingested via M6 and matched back to verify that every single instruction actually landed. This closes the loop even when the process stays manual.
- **Tier 3** (deferred, not v1): full browser-agent automation of the Clarivate portal itself. Deliberately not built yet — instructing the payment of a client's IP rights is judged to warrant a human click until a properly supported channel exists, not just a technically-possible-to-automate one.
