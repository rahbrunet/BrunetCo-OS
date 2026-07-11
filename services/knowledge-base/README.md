# Service: knowledge-base

**Purpose.** Citation-aware retrieval over authoritative IP sources with freshness metadata.
Grounds every drafting and analysis agent so outputs cite real, current authority (§12.1).

**Consumers.** drafting (A9/A11), A4–A6 analysis agents, ad-hoc "ask about matter X" MCP queries.

**Interface sketch (implemented WP 6.8, Track B).**
- `ingest(source)` — MOPOP / MPEP / TMEP / G&S manuals / statutes / office sites / firm site +
  curated blogs; freshness metadata per document.
- `retrieve(query, k) -> passages[]` with citations — pgvector semantic retrieval (design review
  §6.3: one pgvector pattern serves both the KB and unified search).

**WP 0.7 status:** skeleton README only. No implementation.
