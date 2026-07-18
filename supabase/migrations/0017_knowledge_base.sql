-- Migration 0017 — Firm Knowledge Base service (WP 6.8, spec §12.1).
--
-- One retrieval service — ingestion, chunking, citation-aware retrieval, freshness tracking —
-- shared by A3 (rule-change monitor), A4 (G&S harmonizer), A9 (email drafting), A11 (OA
-- reporting) and the orchestrator. Built once here rather than four times badly.
--
-- Three requirements from §12.1 shape this schema, and none is cosmetic:
--
--   1. **Citation-aware.** A retrieved passage is useless to a professional unless it says where
--      it came from. Every chunk carries its own citation ("MOPOP §17.02"), so a drafted letter
--      can cite the section it relied on rather than asserting law from nowhere.
--
--   2. **Freshness / superseded editions.** Practice manuals are revised. A passage from the 2019
--      MOPOP may be flatly wrong today, so sources are edition-scoped and a superseded edition
--      stays queryable but is FLAGGED — agents must be able to report that they relied on an old
--      edition. Deleting superseded content would destroy the ability to explain past advice.
--
--   3. **Copyright hygiene.** Curation of third-party sources is deliberate (§12.1). Third-party
--      material is licensed here as GROUNDING ONLY: retrieval returns a short extract and a
--      citation so an agent can read and cite it, never a payload to reproduce. The license class
--      travels with every chunk so the guard cannot be lost between retrieval and use.
--
-- Retrieval is Postgres full-text search, per §13 ("Postgres FTS in v1, pgvector available for
-- semantic retrieval"). pgvector is deliberately NOT used yet: neither the local container nor
-- the CI image ships the extension, and adding an embedding column is a additive follow-up
-- migration once the Supabase-hosted database (which does have it) is the target.

create schema if not exists kb;

-- ---------------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------------

create type kb.authority_type as enum (
  'statute',          -- Patent Act, 35 U.S.C.
  'regulation',       -- Patent Rules
  'manual',           -- MOPOP, MPEP, TMEP, Trademarks Examination Manual
  'classification',   -- Goods & Services Manual, USPTO ID Manual
  'practice_notice',  -- CIPO/USPTO practice notices, Federal Register
  'office_website',
  'firm_content',     -- the firm's own site + FAQ
  'curated_blog'      -- allow-listed third-party commentary
);

-- The copyright posture, carried as data so retrieval can enforce it.
create type kb.license_class as enum (
  'open',             -- Crown copyright / US government works / public statutes: quotable
  'firm_owned',       -- the firm's own content: quotable
  'grounding_only'    -- third-party: cite and paraphrase, never reproduce at length
);

create table kb.sources (
  id                  uuid primary key default gen_random_uuid(),
  key                 text not null,              -- stable slug: 'mopop', 'mpep', 'cipo-notices'
  name                text not null,
  jurisdiction        text not null check (jurisdiction in ('CA', 'US', 'EP', 'WO', 'FIRM')),
  authority_type      kb.authority_type not null,
  license_class       kb.license_class not null,
  publisher           text,
  url                 text,
  -- Edition scoping. A source key may have many editions; exactly one is current
  -- (superseded_at is null), enforced by the partial unique index below.
  edition_label       text not null,              -- 'MOPOP 2024-06', '35 U.S.C. (2023 ed.)'
  edition_effective   date,
  superseded_at       date,                       -- null = current edition
  -- Freshness. refresh_interval_days null = the source does not go stale on a schedule
  -- (a statute edition is stable); a practice-notice feed very much does.
  refreshed_at        timestamptz,
  refresh_interval_days integer check (refresh_interval_days > 0),
  is_active           boolean not null default true,
  created_at          timestamptz not null default now()
);

-- One current edition per source key. Superseded editions are unconstrained, so history
-- accumulates without ever colliding with the live edition.
create unique index kb_sources_current_edition
  on kb.sources (key) where superseded_at is null;
create index kb_sources_jurisdiction_idx on kb.sources (jurisdiction, authority_type);

-- ---------------------------------------------------------------------------
-- Documents + chunks
-- ---------------------------------------------------------------------------

create table kb.documents (
  id           uuid primary key default gen_random_uuid(),
  source_id    uuid not null references kb.sources (id) on delete cascade,
  title        text not null,
  url          text,
  -- Content hash makes re-ingestion idempotent: an unchanged document is skipped rather than
  -- re-chunked, which would churn every chunk id and orphan any citation held elsewhere.
  content_hash text not null,
  retrieved_at timestamptz not null default now(),
  unique (source_id, content_hash)
);

create table kb.chunks (
  id           uuid primary key default gen_random_uuid(),
  document_id  uuid not null references kb.documents (id) on delete cascade,
  -- Denormalized from the document so retrieval can filter by jurisdiction/edition/licence
  -- without a three-table join on every query.
  source_id    uuid not null references kb.sources (id) on delete cascade,
  -- The citation a professional would actually write: 'MOPOP §17.02', '37 CFR 1.56'.
  citation     text not null,
  -- Breadcrumb of enclosing headings, so a passage can be located in context.
  heading_path text,
  ordinal      integer not null,        -- position within the document, for adjacent-chunk reads
  body         text not null,
  char_count   integer not null,
  -- Generated tsvector: the citation and heading are weighted above the body, so searching
  -- "17.02" or "double patenting" finds the section whose HEADING says so, not merely a
  -- paragraph that mentions it in passing.
  search_tsv   tsvector generated always as (
    setweight(to_tsvector('english', coalesce(citation, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(heading_path, '')), 'B') ||
    setweight(to_tsvector('english', body), 'C')
  ) stored
);

create index kb_chunks_search_idx on kb.chunks using gin (search_tsv);
create index kb_chunks_document_idx on kb.chunks (document_id, ordinal);
create index kb_chunks_source_idx on kb.chunks (source_id);

-- ---------------------------------------------------------------------------
-- Ingestion runs (ops visibility — "when did the KB last actually update?")
-- ---------------------------------------------------------------------------

create table kb.ingestion_runs (
  id             uuid primary key default gen_random_uuid(),
  source_id      uuid references kb.sources (id) on delete set null,
  started_at     timestamptz not null default now(),
  finished_at    timestamptz,
  status         text not null default 'running'
                   check (status in ('running', 'completed', 'failed')),
  documents_seen integer not null default 0,
  documents_new  integer not null default 0,
  chunks_written integer not null default 0,
  detail         text
);

create index kb_ingestion_runs_source_idx on kb.ingestion_runs (source_id, started_at desc);

-- ---------------------------------------------------------------------------
-- Retrieval helper
-- ---------------------------------------------------------------------------
--
-- Ranked, citation-carrying passages. Kept in SQL so every consumer (A9 drafting, A4, A11,
-- the orchestrator) ranks identically — four callers re-implementing ts_rank would drift.
-- `include_superseded` defaults false: an agent asking a plain question should get current law,
-- and must opt in to historical editions rather than receive them by accident.

create or replace function kb.search(
  query_text text,
  jurisdictions text[] default null,
  max_results integer default 8,
  include_superseded boolean default false
)
returns table (
  chunk_id      uuid,
  citation      text,
  heading_path  text,
  body          text,
  source_key    text,
  source_name   text,
  jurisdiction  text,
  license_class kb.license_class,
  edition_label text,
  is_superseded boolean,
  rank          real
)
language sql stable
as $$
  select
    c.id, c.citation, c.heading_path, c.body,
    s.key, s.name, s.jurisdiction, s.license_class, s.edition_label,
    (s.superseded_at is not null) as is_superseded,
    ts_rank(c.search_tsv, websearch_to_tsquery('english', query_text)) as rank
  from kb.chunks c
  join kb.sources s on s.id = c.source_id
  where s.is_active
    and (include_superseded or s.superseded_at is null)
    and (jurisdictions is null or s.jurisdiction = any(jurisdictions))
    and c.search_tsv @@ websearch_to_tsquery('english', query_text)
  order by rank desc, c.ordinal
  limit greatest(max_results, 0);
$$;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
--
-- The KB is firm-general reference material: every member of staff may read all of it, and it
-- carries no client data, so there is nothing here to scope per user. Writes are another matter
-- — a bad ingestion or an unvetted "authoritative" source silently corrupts the grounding under
-- every agent, so curation is admin-gated and ingestion runs as a system worker.

alter table kb.sources        enable row level security;
alter table kb.sources        force row level security;
alter table kb.documents      enable row level security;
alter table kb.documents      force row level security;
alter table kb.chunks         enable row level security;
alter table kb.chunks         force row level security;
alter table kb.ingestion_runs enable row level security;
alter table kb.ingestion_runs force row level security;

create policy kb_sources_read on kb.sources
  for select using (app.is_active_staff());
create policy kb_sources_write on kb.sources
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

create policy kb_documents_read on kb.documents
  for select using (app.is_active_staff());
create policy kb_documents_write on kb.documents
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

create policy kb_chunks_read on kb.chunks
  for select using (app.is_active_staff());
create policy kb_chunks_write on kb.chunks
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

create policy kb_ingestion_runs_read on kb.ingestion_runs
  for select using (app.is_active_staff());
create policy kb_ingestion_runs_write on kb.ingestion_runs
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

grant usage on schema kb to authenticated;
grant select on kb.sources, kb.documents, kb.chunks, kb.ingestion_runs to authenticated;
grant insert, update, delete on kb.sources, kb.documents, kb.chunks, kb.ingestion_runs
  to authenticated;
grant execute on function kb.search(text, text[], integer, boolean) to authenticated;

-- ---------------------------------------------------------------------------
-- Seed the corpus registry (§12.1) — sources only, no content
-- ---------------------------------------------------------------------------
--
-- Registering the corpus is not the same as ingesting it: these rows declare what the firm
-- intends to ground on, with its licence posture fixed up front. Ingestion fills them.
-- Note the licence split — Crown/US-government material is quotable; the curated blogs are
-- grounding_only from the moment they are registered, so no ingestion path can promote them.

insert into kb.sources
  (key, name, jurisdiction, authority_type, license_class, publisher, edition_label,
   refresh_interval_days)
values
  ('mopop', 'Manual of Patent Office Practice', 'CA', 'manual', 'open', 'CIPO',
   'unversioned (pending first ingestion)', 90),
  ('patent-act-ca', 'Patent Act (Canada)', 'CA', 'statute', 'open', 'Justice Canada',
   'unversioned (pending first ingestion)', 180),
  ('patent-rules-ca', 'Patent Rules (Canada)', 'CA', 'regulation', 'open', 'Justice Canada',
   'unversioned (pending first ingestion)', 180),
  ('tm-act-ca', 'Trademarks Act (Canada)', 'CA', 'statute', 'open', 'Justice Canada',
   'unversioned (pending first ingestion)', 180),
  ('tm-rules-ca', 'Trademarks Regulations (Canada)', 'CA', 'regulation', 'open',
   'Justice Canada', 'unversioned (pending first ingestion)', 180),
  ('tmep-ca', 'Trademarks Examination Manual', 'CA', 'manual', 'open', 'CIPO',
   'unversioned (pending first ingestion)', 90),
  ('gs-manual-ca', 'Goods and Services Manual', 'CA', 'classification', 'open', 'CIPO',
   'unversioned (pending first ingestion)', 30),
  ('cipo-notices', 'CIPO practice notices', 'CA', 'practice_notice', 'open', 'CIPO',
   'rolling', 7),
  ('mpep', 'Manual of Patent Examining Procedure', 'US', 'manual', 'open', 'USPTO',
   'unversioned (pending first ingestion)', 90),
  ('tmep-us', 'Trademark Manual of Examining Procedure', 'US', 'manual', 'open', 'USPTO',
   'unversioned (pending first ingestion)', 90),
  ('usc-35', '35 U.S.C. (Patents)', 'US', 'statute', 'open', 'US Congress',
   'unversioned (pending first ingestion)', 180),
  ('uspto-id-manual', 'USPTO ID Manual', 'US', 'classification', 'open', 'USPTO',
   'unversioned (pending first ingestion)', 30),
  ('federal-register-uspto', 'Federal Register (USPTO notices)', 'US', 'practice_notice',
   'open', 'US GPO', 'rolling', 7),
  ('firm-site', 'Brunet & Co. website and FAQ', 'FIRM', 'firm_content', 'firm_owned',
   'Brunet & Co.', 'live', 30)
on conflict do nothing;
