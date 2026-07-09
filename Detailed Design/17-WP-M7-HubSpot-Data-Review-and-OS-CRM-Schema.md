# WP M7.x — HubSpot Data Review & OS-Native CRM Schema Recommendations

**Date:** 2026-07-09
**Purpose:** Live-data review of the firm's HubSpot portal (account 21576921) to ground M7 (CRM, Intake & Reciprocity) schema design, per D16/D22's decision to build the OS CRM as a HubSpot successor rather than an ongoing HubSpot integration. Companion to D34–D37 (AppColl parity findings) — same exercise, applied to the CRM side of the migration.

**Method:** Direct read access to the connected HubSpot portal (contacts, companies, deals, properties, pipelines). No write actions taken. 3,616 contacts / 2,233 companies / 1,878 deals reviewed in aggregate and by sample.

---

## 1. Two pipelines, two real business processes

HubSpot currently encodes two structurally different workflows as two Deal pipelines. Both should carry over as *processes*, but not as two disconnected pipelines with duplicate-looking stage names — see §3.

**Incoming Sales** (id `8014627`) — 1,564 of 1,878 deals (83%). This is the high-volume side: outside firms/applicants requesting BrunetCo file CA/US work, driven by the website Instant Quote tool (D22). Stages: Quotation Requested → Quotation Sent → Follow-up Sent → Second Follow-up Sent → **Instruction Received – Deal Won** (1,264) / Closed Lost (267). Deal names are sequential instruction numbers (`2026-114`, `2026-109`...). 1,368 of 1,878 deals firm-wide are `IMPORT`-sourced (the automated quote tool) vs. 510 `CRM_UI` (manual) — confirms the quote agent is the dominant deal-creation path today, exactly as D22 describes.

**Outgoing Sales** (`default`) — 208 deals (11%). This is the reciprocity side: BrunetCo's own clients need foreign filing, BrunetCo requests quotes from foreign associates, then instructs them on the client's behalf. Stages: RFQs Sent → Send Quotation to Client → Client Approval Received (34) → **Instruct Associate – Deal Won** (156) / Closed Lost (13). This is the operational substrate for D9 (reciprocity), D25–D27 (referral/prospecting agents), and M7's "reciprocity: inbound/outbound referral records."

**Unassigned pipeline** — 106 deals, legacy/import noise, not a third process.

**Sample record (deal 62121560249, "2026-120"):** `description`: *"Accept your quote #QT30582807 - PCT/CN2024/124840 Our Ref.: GWPCTP20260501050"*, `brunetco_file_reference`: `BGW-0004-CA`. This single record is the clearest evidence of the intended chain: quote number → PCT application number → foreign associate's own reference → BrunetCo file reference, captured only once a deal is won. It's the CRM-side proof of D32 (Smart Matter Opening) and the M7 intake chain operating together — but today the linkage lives in free text, not structured fields (see §4).

## 2. Custom fields already in production use

Beyond HubSpot's stock Deal fields, the firm has added: `applicant`, `application_number`, `app__no_` (short form), `brunetco_file_reference`, `client_name`, `client_type` (Law Firm / Corporate), `country`, `filing_status`, `filingdate`, `law_firm`, `matter_type` (Patent / Trademark / Design / Other), `priority_date`, `type`. On Contact: `associated_company` — *"Used when a corporate client is represented by a third-party firm that holds the relationship with Brunet & Co"* — a real, firm-specific relationship pattern with no native HubSpot equivalent. This is exactly the kind of "usual CA firm" / instructing-firm-vs-ultimate-client distinction D27's reciprocity guard needs, and it deserves to be a first-class typed relationship in the OS, not a free-text lookup field.

`matter_type` enum (Patent/Trademark/Design/Other) lines up cleanly with D20's four-jurisdiction fee-table scope and should carry forward unchanged.

## 3. Data quality findings (feed the migration plan, same spirit as D37)

- **74% of deals (1,392 of 1,878) have no `matter_type`/`client_type`/`applicant` populated.** This tracks almost exactly with the IMPORT vs. CRM_UI split — the automated quote-tool deals arrive with the data buried in `description` text (quote #, PCT/WO number, sometimes the associate's own reference) rather than in the structured fields that already exist for it. This is a process gap, not a HubSpot limitation: **the OS quote agent (A10/D22) should populate these structured fields at deal creation**, sourced from the same WIPO lookup it already performs, instead of leaving them for manual backfill.
- **`filing_status` and Deal-level `type` are dead fields** — `filing_status` has no defined options and no evident adoption; `type` is a free string with no distinct values in use. Do not carry either forward as-is. If a filing-status view is wanted on the CRM record, it should be a live read from the matter's actual M1 status (D35's status vocabulary), not a second, always-stale manual copy.
- **Contact-level and Company-level `type` are essentially unused** (3,606 of 3,616 contacts unassigned; 2,232 of 2,233 companies unassigned). HubSpot's generic classification never got operationalized here. Recommendation: don't replicate it — adopt the richer, already-in-use AppColl contact-role vocabulary from D35/D37 (Inventor, Company, Client, Employee, Foreign Associate, Client-Solo-Inventor, Assignee, Paralegal, Law Firm, Attorney) uniformly across the OS instead of inventing a third taxonomy.
- **`lifecyclestage` is the one classification field that is actually live** — contacts skew heavily to `lead` (fresh, day-to-day web/quote-tool intake), with `opportunity` appearing once a deal attaches. Worth preserving as the CRM-side funnel stage, separate from the Quote/Deal pipeline stage.
- Companies are overwhelmingly 1:1 with a single contact — a "one contact per instructing firm" pattern consistent with a high-volume associate network, distinct from AppColl's richer per-matter multi-contact model (Attorney/Paralegal/Client Contact/Law Firm, D35). Both patterns need to coexist in the OS: CRM-side (pre-matter) contacts are usually singular; matter-side contacts are plural.
- HubSpot's native `QUOTE` object (commerce quotes/line items) is read-available but **not used** — the firm's quote numbers (`QT30582807`) are minted by its own Instant Quote website tool and only appear as text. No HubSpot product/line-item catalog exists to migrate for M4's Fee Schedule Service; that build is starting from the firm's own tool, not from HubSpot data.
- `CAMPAIGN` and `PARTNER_CLIENT` object types require account-level changes to even read — not usable in this review and not a source of migration data.

## 4. Recommended OS CRM object model (M7)

Supersedes the two ad hoc HubSpot pipelines with a schema that matches how the firm actually operates, per D16/D22 ("OS CRM, HubSpot successor") and the M7 intake chain already specified:

**Contact** — name, email(s), phone, title, `role` (AppColl vocabulary, see §3), `represented_by_firm_id` (typed relationship, replaces `associated_company` free text), CASL consent (`basis`, `date`, `source` — required per D25), `lifecycle_stage` (lead → MQL → SQL → opportunity → customer, kept), owner, `source` (quote tool / opposition watch / PCT prospector / self-represented-applicant prospector / manual — ties directly into A14–A17).

**Company** — name, domain, country, `entity_type` (Law Firm / Corporate / Foreign Associate / Referral Partner), `reciprocity_partner` flag (feeds D27's reciprocity guard directly instead of requiring a lookup), rollup of associated matters (not just contacts/deals).

**Quote** *(new, first-class — not a repurposed generic Deal)* — `quote_number`, `matter_type` (Patent/Trademark/Design/Other, kept), `client_type` (Law Firm/Corporate/**Individual** — new value, needed once D26's self-represented-applicant prospecting brings in non-firm applicants), `applicant`, `application_number`, `pct_wo_number`, `priority_date`, `filing_date`, `country`, `brunetco_file_reference` (auto-populated the instant Smart Matter Opening runs — never manual), `flow_type` (**Incoming** / **Outgoing-Reciprocity**, replacing the two separate pipelines with one dimension), `wipo_lookup_snapshot` (raw data from the D22 lookup, for audit).

**Intake status** (single canonical enum instead of two pipelines' worth of near-duplicate stages): Requested → Sent → Follow-up (ladder handled by A18, not by hand-modeled "Follow-up Sent" / "Second Follow-up Sent" stages) → Accepted → Conflict Check → ToE Sent → ToE Signed → Retainer Invoiced → Retainer Paid → Matter Opened / Lost. This is the M7 intake chain already in the spec (§10) — the HubSpot review confirms it maps cleanly onto real data and that the follow-up-ladder stages are exactly the kind of thing A18 should own instead of the CRM pipeline.

**Reciprocity ledger** — derived, not hand-maintained: inbound/outbound counts computed from `Quote.flow_type` + `Company.reciprocity_partner`, by company/period, feeding D9's ratio reporting directly.

## 5. Migration note for WP 0.4-equivalent CRM cutover

Given finding §3, a straight field-mapped HubSpot→OS import will carry forward a large number of empty `matter_type`/`client_type`/`applicant` values. Recommend a **human-reviewed backfill pass** that regex-parses the `description` field of legacy IMPORT-sourced deals (reliably contains quote #, a matter-type keyword, and often a PCT/WO number and the associate's own reference) to populate the new structured fields — same "review-and-confirm diff, never silent" principle as D32's Smart Matter Opening, applied to CRM migration rather than matter opening.

---

*Contributes to M7 (§10) and Track A CRM/intake-chain work (D38). Recommend folding the object model in §4 into the next spec revision's M7 section once reviewed.*
