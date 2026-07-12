# Testing, Cutover, Open Items & Action Items

This file consolidates everything about how the project verifies itself before going live, plus every remaining loose end — the honest "what's not yet nailed down" list, kept separate from the confident spec content in the other files so nobody mistakes an open question for a settled decision.

---

## Cross-Cutting Technical Commitments (§13)

A few platform-wide commitments that don't belong to any single module: Entra ID single sign-on across the whole system; a role-based permission model layered on top of family-level access control lists, enforced via Supabase's row-level security; an immutable audit log; nightly backups with point-in-time recovery; English as the primary interface language while remaining French-data-aware (since CIPO correspondence and underlying data may genuinely be in French); all secrets fetched from Bitwarden at runtime; and LLM egress encrypted, with Zero Data Retention terms used wherever the provider offers them.

---

## Testing & Cutover Plan (§14, per D4)

The plan has several layers, deliberately, because a system that computes legal deadlines wrong is not a system that fails gracefully:

- **Module-level tests**, plus specifically **deadline-engine golden tests** — every single docketing rule ships with paired trigger-date-to-due-date test cases, explicitly including holiday-roll and extension scenarios, not just the happy path.
- **Migration reconciliation reports**, checking row counts and field counts against the source AppColl data, backed by a **human audit of 5% of all active matters** plus **100% of matters with a deadline inside the next 90 days** — the highest-stakes matters get full manual verification, not just statistical sampling.
- **A parallel run** — both systems operating simultaneously — currently proposed at roughly 8 weeks (two docket cycles), though this duration is flagged as ⏳ **still needing owner confirmation**, not yet locked in. During the parallel run, a **daily automated diff** between the OS and AppColl on deadlines and tasks gets triaged to zero before cutover proceeds.
- **A reconciliation drill**: one full credit-card statement cycle and one full CIPO deposit-account cycle both need to tie out completely between the two systems.
- **An email-classification precision check** on a labelled sample, required *before* Agent A8 (email instruction detection) is allowed to go live — given what A8 is capable of proposing, its classification accuracy needs to be proven on real data first, not assumed.
- A formal **cutover checklist and rollback plan**, with AppColl retained as a **read-only archive** after cutover rather than being decommissioned entirely — historical data stays accessible even after the firm stops actively using it.

---

## Open / Pending Items ⏳

These are explicitly *not* blocking the spec itself — the spec is complete regardless of how these resolve — but they gate specific downstream work packages:

1. **Clarivate integration channel** — the owner needs to ask Clarivate's account manager whether a formal instruction-submission or status-feed channel exists. This only gates the Tier 1 connector (WP 2.9); Tier 2 ships regardless of the answer.
2. **VPS watcher repo/access** — needed from James to actually audit and port the existing CIPO watcher code. Gates WP 0.6, and transitively WP 6.2.
3. **The existing email-drafting agent's code** — needed from James to use as the literal template for Agent A9. Gates WP 6.9.
4. **The existing quote-detector and Instant Quote tool code** — needed from James to integrate as Agent A10. Gates WP 6.10.
5. **Claude Team subscription** for James, Omin, and Grey — raised as needing to happen at spec close; current pricing/seat structure should be checked live when this is actually arranged, since it may have changed since this spec was written.
6. **Parallel-run duration** — the 8-week proposal in §14 needs explicit owner confirmation, not just tacit acceptance.
7. 🔎 **Verify at build time:** the assumption that neither CIPO's online filing system nor USPTO's Patent Center offers a public submission API, meaning Agents A12 and A13 are built on browser automation by necessity rather than choice. This should be re-checked when those agents are actually built, in case either office has since introduced a submission API.

*(Two items originally listed here — the WP 0.3 SharePoint crawl and the AppColl CSV export requests — have since been completed and are recorded as such in the tracker rather than repeated here as still-open.)*

---

## Action Items (Owner) — Current Status

| Item | Gates | Status |
|---|---|---|
| Ask Clarivate account manager about an instruction API/feed | Enables Tier 1 of WP 2.9 | ⬜ Open |
| Obtain watcher repo/VPS access via James | Enables WP 0.6 → WP 6.2 | ⬜ Open |
| Obtain email-drafting agent code via James | Enables WP 6.9 (Agent A9 template) | ⬜ Open |
| Obtain quote-detector + Instant Quote tool code via James | Enables WP 6.10 (Agent A10) | ⬜ Open |
| Arrange Claude Team subscription (James, Omin, Grey seats) | Raised at spec close | ⬜ Raised, not yet actioned |
| Confirm the 8-week parallel-run duration | §14 testing plan | ⬜ Open |
| Grant SharePoint read access for the WP 0.3 crawl | WP 0.3 | ✅ Done (via browser route) |
| Confirm the D36 folder-annotation design ruling | M5 design | ✅ Confirmed by owner, 2026-07-07 |
| Rotate the AppColl password shared in plaintext in chat | Security hygiene | ✅ Done, confirmed by owner |
| Provide AppColl CSV exports (Task Types, Contacts, Billing Items) | Completes WP 0.4; seeds WP 1.3 | ✅ Received, 2026-07-07 |
| Optional: deeper in-app AppColl walkthrough (Prior Art, Files, Reports, Contacts, Settings modules — not yet reviewed) | Supplements WP 0.4 | ⬜ Open, optional |

---

## Blockers / Risks (carried forward, worth re-reading before build starts)

- **Clarivate Tier 1 channel is genuinely unknown** — mitigated by the fact that Tier 2 ships regardless of how that question resolves, so this risk doesn't block the annuity-payment workflow overall, only its most-automated tier.
- **A8's precision must be proven, not assumed**, on labelled real samples before it's allowed anywhere near intake or client-facing proposals — mitigated by the approval-gate design (nothing A8 proposes executes without human sign-off) plus the shared-mailbox triage queue acting as a safety net for anything A8 doesn't confidently classify.
- **The doc-type shorthand vocabulary** (NE, OA, POA, IASR, etc.) is understood to be incomplete — it will keep growing as the actual migration process encounters more file naming variants than any single review could catalogue in advance. This is treated as expected, ongoing work during migration rather than a gap to close before build starts.
