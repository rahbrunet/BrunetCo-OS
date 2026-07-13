-- Migration 0013 — prior-art reference database + citation states (WP 4A.1, M11).
--
-- The duty-of-disclosure (US 35 U.S.C. §1.56) tracking must have a live home at cutover, since
-- AppColl goes read-only (owner ruling 2026-07-08). This is the reference database + the
-- many-to-many cross-linking of references to matters/families with a per-link citation state.
-- Biblio auto-fill (fetching title/inventors/dates from an office) is an external adapter added
-- later; the schema stores whatever biblio is supplied now.
--
-- Cross-linking is many-to-many: one reference can be cited across a whole family's matters
-- (family-wide OAs), and each (reference, matter) carries its own citation state — the same
-- reference may be "disclosed" in one matter and "to_disclose" in a sibling. The cross-citation
-- matrix + duty dashboard read from here (WP 4A.2).

create type app.reference_kind as enum ('patent', 'npl');   -- npl = non-patent literature

create table app.prior_art_references (
  id           uuid primary key default gen_random_uuid(),
  kind         app.reference_kind not null default 'patent',
  citation     text not null,             -- patent no ('US1234567B2') or NPL citation string
  title        text,
  inventors    text,
  assignee     text,
  pub_date     date,
  biblio       jsonb not null default '{}'::jsonb,   -- raw/auto-filled bibliographic data
  created_by   uuid not null references app.os_users (id),
  created_at   timestamptz not null default now()
);

-- Normalized patent citation is unique so the same document is one reference row, cross-linked
-- from many matters (dedup at the DB — the migration seed from PriorArt/ + FH/PriorArt-IDS{n}
-- folders relies on this).
create unique index prior_art_citation_uq on app.prior_art_references (upper(citation));

-- §1.56 duty-of-disclosure lifecycle of a reference WITHIN a matter.
create type app.citation_state as enum (
  'to_disclose',   -- known material art; must be listed in an IDS
  'disclosed',     -- submitted to the office in an IDS
  'considered',    -- office returned it considered
  'not_relevant',  -- assessed, no disclosure duty
  'withdrawn'
);

create table app.reference_links (
  id             uuid primary key default gen_random_uuid(),
  reference_id   uuid not null references app.prior_art_references (id) on delete cascade,
  matter_id      uuid not null references app.matters (id),
  family_id      uuid not null references app.families (id),   -- denormalized for family-wide views
  citation_state app.citation_state not null default 'to_disclose',
  ids_bundle     text,            -- e.g. 'PriorArt-IDS3' (the FH bundle it belongs to)
  notes          text,
  linked_by      uuid not null references app.os_users (id),
  linked_at      timestamptz not null default now(),
  unique (reference_id, matter_id)
);

create index reference_links_matter_idx on app.reference_links (matter_id);
create index reference_links_family_idx on app.reference_links (family_id);
create index reference_links_ref_idx on app.reference_links (reference_id);

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table app.prior_art_references enable row level security;
alter table app.prior_art_references force row level security;
alter table app.reference_links      enable row level security;
alter table app.reference_links      force row level security;

-- References themselves are firm-general (a prior-art document is not matter-confidential; the
-- SENSITIVE part is which matter cites it, which lives in reference_links and follows the matter).
create policy prior_art_refs_staff on app.prior_art_references
  for all using (app.is_active_staff()) with check (app.is_active_staff());

-- Links follow matter/family visibility — a citation on a restricted family is only visible to
-- those who can see that family (the fact that we cite art in a matter is matter-confidential).
create policy reference_links_select on app.reference_links
  for select using (app.can_see_matter(matter_id));
create policy reference_links_write on app.reference_links
  for insert with check (app.can_see_matter(matter_id) and app.is_active_staff());
create policy reference_links_update on app.reference_links
  for update using (app.can_see_matter(matter_id) and app.is_active_staff())
  with check (app.can_see_matter(matter_id));

grant select, insert, update, delete on app.prior_art_references, app.reference_links
  to authenticated;
