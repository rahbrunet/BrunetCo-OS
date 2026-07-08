# Appendix A — Reference & Directory Conventions

This is the "Rosetta Stone" of the whole project: the exact grammar for matter references, and the exact directory structure they map to in SharePoint. Get this wrong and every downstream module — docketing, documents, email filing, billing — inherits the error. It was built from the interview, then validated twice against reality: once against uploaded sample documents, and once by directly crawling the live SharePoint tenant (D36, detailed further in `08-discovery-findings-appcoll-sharepoint-data.md`).

---

## Client code

A short alphabetic code, three characters in the common case (e.g., `3DB` for 3DBioFibr).

## Family reference

`{ClientCode}-{FamilySeq}` — a 4-digit sequence number per client — mapping to a folder named `{FamilySeq} - {Technology Title}`.

Trademark families carry a tag after the sequence number: **`(TM)`** for a standard-character mark, **`Design`** for a design mark. Patent families are left untagged. No other matter type gets encoded directly into the reference string itself.

## Jurisdiction segment (the ordered sequence per family/country)

- **`USP`** (US provisional / priority filing) → **`US`** (first regular filing) → **`US2`, `US3`, …** (continuations, CIPs, divisionals). The *type* of relationship (continuation vs. CIP vs. divisional) is **not** encoded in the reference itself — it lives in the `relationship_type` field, alongside a `parent_matter` pointer. **Observed in live production data: sequences run as high as `US11`, `CA5`, `EP2`, `AU2`, and `MX3`.** The same sequel pattern also applies to trademark extension applications, not just patent continuations.
- Two-letter country codes for national filings: `CA`, `US`, `GB` (including auto-created Brexit-clone trademark registrations, confirmed in production data), `AU`, `JP`, `CN`, `IN`, `KR`, `MX`, `ZA`, `TW`, `NL`, and others — the docket genuinely covers a wide range of foreign-associate jurisdictions, not just the four offices with fee tables in v1 (D20).
- **`PCT`** (the WIPO/PCT patent vehicle) and **`MP`** (the Madrid Protocol trademark vehicle) exist as **separate sibling matters**, not as parents with nested children. National designations under either vehicle open at the *same level* as the vehicle matter itself; the data model links a designation back to its vehicle via `parent_matter`, exactly like any other parent-child relationship.
- **No-jurisdiction matters:** general or advisory work — IP strategy engagements, NDA review, freedom-to-operate searches — uses `{Client}-{Seq}` with no country segment at all. Client-level catch-alls use a `{Client}-9999`-style reference. The OS models both of these as Advisory/General matter types at the family level, confirmed by both the interview and later by live matter examples in the SharePoint crawl.
- **Per-client numbering schemes:** some clients maintain their own family numbering, which gets carried directly into the reference rather than following the standard 4-digit sequence — for example, `NRC-2019-016-CN` (a year-based scheme) or `ARL-004-1358-PCT` (a three-segment scheme). The reference generator is built to support per-client scheme templates rather than assuming one universal grammar; legacy references import verbatim regardless of which scheme they follow.
- **Legacy `EP` ambiguity:** historically, `EP` has been used to denote both the European Patent Office and the EU Intellectual Property Office (EUIPO), depending on the matter type. The OS resolves this by using **distinct internal jurisdiction codes** for the two offices going forward, while still preserving the original legacy reference strings exactly as they appear in historical records — so old references remain findable even though new ones disambiguate properly.

---

## Directory tree (SharePoint master / OneDrive mirror)

**Validated in place, 2026-07-07, via a full live crawl of the actual tenant (D36).** Physical location: tenant `brunetco365.sharepoint.com`, root site "Brunet & Co. Sharepoint" → document library **"Brunet & Co."** → top-level folder **"Brunet + Co"**. (Three other sites exist on the same tenant — BrunetCo Proven Process, Internal SharePoint, Due Diligence — but they're out of scope for matter data; they hold internal process documentation, not client files.)

```
Brunet + Co/
  {ClientCode} - {Client Name}/           associates-as-clients included; persons "SURNAME, First";
                                          "(archive)" suffix on retired folders; internal: ADM - Admin, BRU - Brunet Co
    {FamilySeq} - {Technology Title}/     TM families: "(TM)"/design descriptions; "(Approved)" status suffix observed;
                                          Unicode allowed (e.g., μCollaFibR)
      {Jurisdiction}[ (Status|Stage)]/    e.g., CA/, US (Issued)/, JP (Allowed)/, EP (EESR)/, JP2/, PCT/, MP/
        Correspondence/
        FH/
          Received/   Sent/
          PriorArt-IDS{n}/                numbered per-IDS submission bundles (US matters) → seeds M11
          YYYY-MM-DD-{Description}[-{Initials}].{ext}   loose working drafts + informal notes (.txt)
      Assignment/                          family-level
      PriorArt/                            family-level reference folder → seeds M11
    9999 - General/                        per-client general/advisory family
    Account/                               per-client accounting docs (sibling of families)
```

### Design rulings that came directly out of the crawl (D36)

**(1) Folder-name status annotations.** Folder names in the live tree currently duplicate docket state — `(Approved)` on trademark family folders, and `(Status|Stage)` annotations on jurisdiction folders like "US (Issued)" or "EP (EESR)." In the new OS, **status lives in the database, full stop.** The M5 document mapper is built to parse and tolerate these `(...)`-suffixed legacy folder names when binding existing folders to matters during migration, but **OS-created folders omit the status annotation entirely** — no rename churn ever again, and status is always visible correctly in-app rather than depending on someone remembering to rename a folder. **This ruling was explicitly confirmed by the owner** after being flagged as a design decision requiring sign-off.

**(2) Foreign-associate firms appear as clients.** The crawl confirmed that associate firms Brunet & Co. works with internationally show up as client-coded folders in exactly the same structure as actual clients (dual-role organizations). The CRM data model reflects this directly — one organization record can hold both roles simultaneously — and these same codes are exactly what feeds the reciprocity guard built into Agent A16 (D27), so this folder-structure finding and that agent design are directly connected.

**(3) Non-conforming legacy folders.** Legacy family and jurisdiction folders map to matters via tolerant pattern-matching, with anything that doesn't match cleanly routed to a manual-resolution queue during migration rather than silently mis-binding or failing.

---

## Documents

Standard pattern: `YYYY-MM-DD-{Description}[-{Initials}].{ext}`. The optional initials suffix (e.g., `2023-02-06-IASR-SJ.pdf`) was found in live data and is preserved as an accepted variant, not treated as an error.

**Shorthand vocabulary observed in production** (this list grows as migration proceeds and gets mapped to formal document types in M5): `NE` (Notice of Allowance / Filing Receipt context varies), `OA` (Office Action), `POA` (Power of Attorney), `IASR`, `PrelimAmend`, `St37`, `ResponseRR`, `ResponseOA{n}`, `AllowedClaims`, `CancelledClaims`.

**Legacy emails:** `YYYY-MM-DD__{From}to{To}__{Subject}.msg` — this convention is what makes the `.msg` migration (M6-R6) possible: the folder location the file sits in is treated as ground truth for which matter it belongs to.

---

## Practice constants

Correspondence with CIPO is conducted via patents@brunetco.com; trademarks@ handles the equivalent Canadian trademark correspondence. A CIPO deposit account is on file for fee payments. Small-entity status is tracked as a per-client, per-matter attribute (it affects fee amounts under CIPO s.44(2) and the equivalent USPTO small/micro entity provisions).
