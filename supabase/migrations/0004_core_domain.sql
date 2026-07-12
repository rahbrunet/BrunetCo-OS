-- Migration 0004 — core domain: clients, contacts, families, matters, family ACLs (WP 0.8).
--
-- Family is the spine (spec §2): matters, references, the folder tree, and the future chain
-- record all hang off the Technology Family. Reference grammar per Appendix A:
--   {ClientCode}-{FamilySeq}[-{JurisdictionSegment}]
-- The reference string NEVER encodes relationship type (M1-R1) — that lives in
-- parent_matter_id + relationship_type, because docketing logic needs the real type
-- (national-phase deadlines compute from the PCT filing date; Madrid carries a 5-year
-- central-attack dependency).
--
-- RLS framework: firm-general read/write for active staff; families may be marked restricted,
-- in which case only family_access grantees (plus permissions admin) see them. The ACL table
-- also carries the future external-delegate model (applicant authority / delegate / per-country
-- read) for the FamilyRecordExport + Client Portal path — enforced now, extended later.

-- ---------------------------------------------------------------------------
-- Clients & contacts
-- ---------------------------------------------------------------------------

create table app.clients (
  id                uuid primary key default gen_random_uuid(),
  code              text not null unique,          -- e.g. 3DB; associates-as-clients included (D36)
  name              text not null,
  is_associate_firm boolean not null default false, -- dual-role orgs (client AND foreign associate)
  small_entity      boolean not null default false, -- CIPO s.44(2)/USPTO default; matter may override
  reference_scheme  text,                           -- per-client numbering template (Appendix A); null = standard 4-digit
  is_archived       boolean not null default false, -- "(archive)" folders observed in crawl
  created_at        timestamptz not null default now()
);

create table app.contacts (
  id         uuid primary key default gen_random_uuid(),
  client_id  uuid references app.clients (id) on delete set null,
  kind       text not null default 'person' check (kind in ('person', 'organization')),
  full_name  text not null,
  email      text,
  phone      text,
  notes      text,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Families
-- ---------------------------------------------------------------------------

create type app.family_type as enum ('patent', 'trademark', 'design', 'advisory');

create table app.families (
  id           uuid primary key default gen_random_uuid(),
  client_id    uuid not null references app.clients (id),
  family_seq   text not null,                     -- 4-digit standard; per-client schemes vary; 9999 = general
  reference    text not null unique,              -- {ClientCode}-{FamilySeq}; legacy imports verbatim
  title        text not null,                     -- Technology Title (Unicode allowed, e.g. μCollaFibR)
  family_type  app.family_type not null,
  tm_design    boolean not null default false,    -- trademark families: false = "(TM)" word mark, true = Design mark
  restricted   boolean not null default false,    -- true → visibility via family_access only
  created_at   timestamptz not null default now(),
  unique (client_id, family_seq)
);

-- ---------------------------------------------------------------------------
-- Matters
-- ---------------------------------------------------------------------------

create type app.relationship_type as enum (
  'continuation',
  'cip',
  'divisional',
  'pct_national_phase',   -- deadlines compute from PCT filing date
  'madrid_designation',   -- 5-year central-attack dependency monitoring
  'tm_extension',         -- trademark extension applications (sequel pattern, Appendix A)
  'external_priority',    -- typed link to an external priority application (D35, Italian example)
  'other'
);

create type app.matter_status as enum (
  'pending', 'filed', 'published', 'allowed', 'issued', 'registered',
  'abandoned', 'client_abandoned',                -- D35: "Cient Abandoned" → enum at migration
  'expired', 'closed'
);

create table app.matters (
  id                   uuid primary key default gen_random_uuid(),
  family_id            uuid not null references app.families (id),
  reference            text not null unique,      -- full string, legacy-safe verbatim (per-client schemes import as-is)
  jurisdiction_code    text not null,             -- internal, disambiguated (EP-EPO vs EP-EUIPO going forward; legacy strings preserved on reference)
  jurisdiction_segment text not null,             -- ordered per-family segment: USP, US, US2 … PCT, MP; '' for advisory
  parent_matter_id     uuid references app.matters (id),
  relationship_type    app.relationship_type,     -- required iff parent_matter_id is set (or external_priority)
  status               app.matter_status not null default 'pending',
  application_no       text,
  registration_no      text,
  filing_date          date,
  registration_date    date,
  small_entity         boolean,                   -- null = inherit client default (M3-R6)
  responsible_user_id  uuid references app.os_users (id),   -- responsible attorney (Family Record field)
  responsible_associate_id uuid references app.contacts (id), -- foreign associate where applicable
  created_at           timestamptz not null default now(),
  check (parent_matter_id is null or relationship_type is not null)
);

create index matters_family_idx on app.matters (family_id);
create index matters_parent_idx on app.matters (parent_matter_id) where parent_matter_id is not null;

-- ---------------------------------------------------------------------------
-- Family ACLs
-- ---------------------------------------------------------------------------

create type app.family_access_level as enum (
  'applicant_authority',  -- ultimate authority (future portal / chain model)
  'delegate',             -- initiating attorney
  'country_read'          -- per-country associate scoped read
);

create table app.family_access (
  family_id    uuid not null references app.families (id) on delete cascade,
  user_id      uuid references app.os_users (id) on delete cascade,
  contact_id   uuid references app.contacts (id) on delete cascade,  -- future external parties
  access_level app.family_access_level not null,
  country_code text,                              -- scope for country_read
  granted_by   uuid not null references app.os_users (id),
  granted_at   timestamptz not null default now(),
  check (user_id is not null or contact_id is not null)
);

create unique index family_access_user_uq on app.family_access (family_id, user_id, access_level)
  where user_id is not null;

create or replace function app.can_see_family(fid uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.families f
     where f.id = fid
       and (
         (not f.restricted and app.is_active_staff())
         or exists (select 1 from app.family_access a
                     where a.family_id = fid and a.user_id = app.jwt_uid())
         or app.is_permissions_admin()
       )
  );
$$;

revoke all on function app.can_see_family(uuid) from public;
grant execute on function app.can_see_family(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table app.clients        enable row level security;
alter table app.clients        force row level security;
alter table app.contacts       enable row level security;
alter table app.contacts       force row level security;
alter table app.families       enable row level security;
alter table app.families       force row level security;
alter table app.matters        enable row level security;
alter table app.matters        force row level security;
alter table app.family_access  enable row level security;
alter table app.family_access  force row level security;

-- Clients/contacts: firm-general (CRM basics are not a protected domain).
create policy clients_staff_all on app.clients
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy contacts_staff_all on app.contacts
  for all using (app.is_active_staff()) with check (app.is_active_staff());

-- Families: visible per ACL model; writes by any active staff on visible families.
create policy families_select on app.families
  for select using (app.can_see_family(id));
create policy families_write on app.families
  for insert with check (app.is_active_staff());
create policy families_update on app.families
  for update using (app.can_see_family(id) and app.is_active_staff())
  with check (app.is_active_staff());

-- Matters: visibility follows the family.
create policy matters_select on app.matters
  for select using (app.can_see_family(family_id));
create policy matters_write on app.matters
  for insert with check (app.can_see_family(family_id) and app.is_active_staff());
create policy matters_update on app.matters
  for update using (app.can_see_family(family_id) and app.is_active_staff())
  with check (app.can_see_family(family_id));

-- family_access: visible to those who can see the family; managed by the permissions admin.
create policy family_access_select on app.family_access
  for select using (app.can_see_family(family_id));
create policy family_access_admin_write on app.family_access
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

grant select, insert, update, delete on
  app.clients, app.contacts, app.families, app.matters, app.family_access
to authenticated;
