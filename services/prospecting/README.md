# Service: prospecting

**Purpose.** Shared prospecting engine: register/journal ingestion, prospect ledger (CASL fields,
deadline expiry), enrichment, human-approved outreach + tracking. One pipeline, four hunters.

**Consumers.** A14 (TM journal prospecting), A15 (self-represented applicants), A16 (PCT national
phase), A17 (portfolio-audit delivery). The A10 quote agent shares the CRM quote-record surface.

**Interface sketch (implemented Phase 8, Track B).**
- `screen(source_feed, watchlist) -> candidates[]`
- `ledger.upsert(prospect)` — CASL consent + suppression state, deadline expiry.
- `outreach(prospect, template)` — routed through the universal outbound audit queue; rate limits,
  CASL suppression, sentiment pause (§12.2 safety rails).

**Reuse.** `email-assistant/quote-tool/Quote-Tool/backend/` (A10 quote tool, WP 6.10 gating code —
found on-machine) plugs into the CRM quote-record surface here.

**WP 0.7 status:** skeleton README only. No implementation.
