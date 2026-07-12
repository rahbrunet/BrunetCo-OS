-- Migration 0006 — D41 document identity + M6-R7/D39 email privacy skeleton (WP 0.8).
--
-- D41: the Postgres document record IS the document's canonical identity. SharePoint holds the
-- bytes; the driveItem ID is the storage pointer; the folder path is a human-convenience
-- projection, never a system fact. Resolve by driveItem ID, never by path — a file moved or
-- renamed outside the OS keeps its matter linkage, and delta-sync flags the path discrepancy.
--
-- M5-R2: association is canonical and many-to-many — a document may link to several matters
-- and optionally to families (Assignment/, PriorArt/ patterns). One physical file lives under
-- the primary matter's folder; optional physical copies for secondary matters are separate
-- driveItems bound back to the same record and marked derived (they are never independent
-- documents). Attachments are hash-deduplicated: filed once, linked from every carrying email.
--
-- D39/M6-R7: emails classified to a matter are firm-visible on that matter's timeline; unlinked
-- messages from an individual's mailbox are visible only to the mailbox owner (and the
-- classification pipeline, which runs as a system worker). Enforced HERE, in RLS — not in
-- application code.

-- ---------------------------------------------------------------------------
-- Documents (D41)
-- ---------------------------------------------------------------------------

create type app.document_source as enum (
  'email_attachment', 'generated', 'uploaded', 'office_correspondence', 'migrated'
);

create table app.documents (
  id            uuid primary key default gen_random_uuid(),
  drive_item_id text unique,                 -- SharePoint storage pointer; null only pre-upload
  filename      text not null,
  doc_type      text,                        -- shorthand vocab (OA, POA, IASR, …) — grows during migration; formalized in M5
  source        app.document_source not null,
  doc_date      date,
  content_hash  text,                        -- sha256; attachment dedup (D41)
  created_by    uuid references app.os_users (id),
  created_at    timestamptz not null default now()
);

create index documents_hash_idx on app.documents (content_hash) where content_hash is not null;

-- M5-R2 canonical associations: matter-level or family-level, many-to-many.
create table app.document_links (
  document_id uuid not null references app.documents (id) on delete cascade,
  matter_id   uuid references app.matters (id) on delete cascade,
  family_id   uuid references app.families (id) on delete cascade,
  is_primary  boolean not null default false,  -- primary matter = where the physical file lives
  linked_by   uuid references app.os_users (id),
  linked_at   timestamptz not null default now(),
  check ((matter_id is null) <> (family_id is null))  -- exactly one target
);

create unique index document_links_matter_uq on app.document_links (document_id, matter_id)
  where matter_id is not null;
create unique index document_links_family_uq on app.document_links (document_id, family_id)
  where family_id is not null;
-- At most one primary location per document.
create unique index document_links_primary_uq on app.document_links (document_id)
  where is_primary;

-- Derived physical copies (M5-R2 §3): separate driveItems, same document record, can drift —
-- tracked so delta-sync can police them. Never independent documents.
create table app.document_copies (
  document_id   uuid not null references app.documents (id) on delete cascade,
  drive_item_id text not null unique,
  matter_id     uuid not null references app.matters (id) on delete cascade,
  created_at    timestamptz not null default now(),
  primary key (document_id, matter_id)
);

-- ---------------------------------------------------------------------------
-- Emails (M6 skeleton; full ingestion is WP 4.3)
-- ---------------------------------------------------------------------------

create table app.emails (
  id               uuid primary key default gen_random_uuid(),
  graph_message_id text unique,
  -- Null owner = shared mailbox (patents@, trademarks@, info@, accounting@) → firm-visible.
  mailbox_owner_id uuid references app.os_users (id),
  subject          text,
  from_address     text,
  sent_at          timestamptz,
  body_text        text,                    -- stored locally (design review §3): Exchange is the legal archive
  created_at       timestamptz not null default now()
);

-- Email↔matter classification links (many-to-many; M5-R2 applies to email too).
create table app.email_matter_links (
  email_id  uuid not null references app.emails (id) on delete cascade,
  matter_id uuid not null references app.matters (id) on delete cascade,
  linked_by uuid references app.os_users (id),  -- null = classifier (pipeline suggests, human confirms)
  linked_at timestamptz not null default now(),
  primary key (email_id, matter_id)
);

-- Attachment carry: every email carrying a (deduped) attachment links to the same document.
create table app.email_documents (
  email_id    uuid not null references app.emails (id) on delete cascade,
  document_id uuid not null references app.documents (id) on delete cascade,
  primary key (email_id, document_id)
);

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table app.documents           enable row level security;
alter table app.documents           force row level security;
alter table app.document_links      enable row level security;
alter table app.document_links      force row level security;
alter table app.document_copies     enable row level security;
alter table app.document_copies     force row level security;
alter table app.emails              enable row level security;
alter table app.emails              force row level security;
alter table app.email_matter_links  enable row level security;
alter table app.email_matter_links  force row level security;
alter table app.email_documents     enable row level security;
alter table app.email_documents     force row level security;

-- A document is visible if any of its links points somewhere the caller can see
-- (or it is not yet linked and the caller is staff — e.g. mid-upload).
create or replace function app.can_see_document(did uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select
    (not exists (select 1 from app.document_links l where l.document_id = did)
      and app.is_active_staff())
    or exists (
      select 1 from app.document_links l
       where l.document_id = did
         and ((l.matter_id is not null and app.can_see_matter(l.matter_id))
           or (l.family_id is not null and app.can_see_family(l.family_id)))
    );
$$;

-- D39: matter-linked → firm-visible (subject to matter visibility);
-- unlinked → mailbox owner only; shared-mailbox (owner null) → staff.
create or replace function app.can_see_email(eid uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.emails e
     where e.id = eid
       and (
         exists (select 1 from app.email_matter_links l
                  where l.email_id = eid and app.can_see_matter(l.matter_id))
         or e.mailbox_owner_id = app.jwt_uid()
         or (e.mailbox_owner_id is null and app.is_active_staff())
       )
  );
$$;

revoke all on function app.can_see_document(uuid) from public;
revoke all on function app.can_see_email(uuid) from public;
grant execute on function app.can_see_document(uuid) to authenticated;
grant execute on function app.can_see_email(uuid) to authenticated;

create policy documents_select on app.documents
  for select using (app.can_see_document(id));
create policy documents_write on app.documents
  for insert with check (app.is_active_staff());
create policy documents_update on app.documents
  for update using (app.can_see_document(id) and app.is_active_staff())
  with check (app.is_active_staff());

create policy document_links_select on app.document_links
  for select using (app.can_see_document(document_id));
create policy document_links_write on app.document_links
  for all using (app.is_active_staff() and app.can_see_document(document_id))
  with check (
    app.is_active_staff()
    and ((matter_id is not null and app.can_see_matter(matter_id))
      or (family_id is not null and app.can_see_family(family_id)))
  );

create policy document_copies_select on app.document_copies
  for select using (app.can_see_document(document_id));
create policy document_copies_write on app.document_copies
  for all using (app.is_active_staff() and app.can_see_document(document_id))
  with check (app.is_active_staff() and app.can_see_matter(matter_id));

create policy emails_select on app.emails
  for select using (app.can_see_email(id));
-- Ingestion happens via system workers (service identity); user-path inserts are staff filing
-- their own mailbox items.
create policy emails_write on app.emails
  for insert with check (app.is_active_staff());
create policy emails_update on app.emails
  for update using (app.can_see_email(id) and app.is_active_staff())
  with check (app.is_active_staff());

create policy email_matter_links_select on app.email_matter_links
  for select using (app.can_see_email(email_id));
create policy email_matter_links_write on app.email_matter_links
  for all using (app.is_active_staff() and app.can_see_email(email_id))
  with check (app.is_active_staff() and app.can_see_matter(matter_id));

create policy email_documents_select on app.email_documents
  for select using (app.can_see_email(email_id));
create policy email_documents_write on app.email_documents
  for all using (app.is_active_staff() and app.can_see_email(email_id))
  with check (app.is_active_staff() and app.can_see_document(document_id));

grant select, insert, update, delete on
  app.documents, app.document_links, app.document_copies,
  app.emails, app.email_matter_links, app.email_documents
to authenticated;
