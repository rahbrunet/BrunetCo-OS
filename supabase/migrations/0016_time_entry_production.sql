-- Migration 0016 — time entry, flat-fee catalogue, timekeeper production ledger
-- (WPs 2.5.1–2.5.3, spec §M2, D42 M2-R10/R11/R13, D43).
--
-- Scope note: this migration builds the ENTERED -> BILLED half of the D42 chain, which needs no
-- Xero. The `collected` half needs payment webhooks (WP 3.3), so the columns exist and stay null
-- until then — the shape is settled now so Phase 3 is a wiring job, not a schema migration on
-- live compensation data.
--
-- Two representation choices that the rest of the chain depends on:
--
--   * Money is integer CENTS, never float. Bonus percentages are applied to these amounts and
--     the results are paid to people; a half-cent of binary floating-point drift compounding
--     across 33,756 billing lines is a payroll dispute nobody can reconstruct.
--   * Time is integer MINUTES, never decimal hours. 0.1h is not representable in binary either,
--     and "6 minutes" is what the timer actually recorded.

-- ---------------------------------------------------------------------------
-- Activity codes (reference data)
-- ---------------------------------------------------------------------------

create table app.activity_codes (
  code        text primary key,
  description text not null,
  is_active   boolean not null default true
);

-- ---------------------------------------------------------------------------
-- Time entries (WP 2.5.1)
-- ---------------------------------------------------------------------------

create type app.billing_status as enum (
  'draft',      -- being worked on; freely editable by its author
  'submitted',  -- released for WIP review
  'invoiced'    -- on an invoice; immutable (see the trigger below)
);

create table app.time_entries (
  id            uuid primary key default gen_random_uuid(),
  timekeeper_id uuid not null references app.os_users (id),
  -- M2-R11: the work-item link is NOT NULL by design. Matter-only time cannot be measured per
  -- piece of work, and back-filling the link across historic entries means re-keying by hand.
  work_item_id  uuid not null references app.work_items (id) on delete restrict,
  -- Denormalized from the work item for search and invoicing. Work items may be firm-general
  -- (null matter); time on those is non-billable overhead.
  matter_id     uuid references app.matters (id),
  activity_code text references app.activity_codes (code),
  narrative     text not null default '',
  entry_date    date not null,
  minutes       integer not null check (minutes > 0),
  is_billable   boolean not null default true,
  rate_cents    integer check (rate_cents >= 0),   -- hourly rate in force when entered
  status        app.billing_status not null default 'draft',
  invoice_line_id uuid,                            -- set at WP 3.2; no FK until that table exists
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index time_entries_timekeeper_date_idx
  on app.time_entries (timekeeper_id, entry_date desc);
create index time_entries_work_item_idx on app.time_entries (work_item_id);
create index time_entries_matter_idx on app.time_entries (matter_id);

-- ---------------------------------------------------------------------------
-- Flat-fee catalogue + client overrides (WP 2.5.2)
-- ---------------------------------------------------------------------------

create table app.flat_fee_services (
  id            uuid primary key default gen_random_uuid(),
  code          text not null unique,
  name          text not null,
  amount_cents  integer not null check (amount_cents >= 0),
  is_active     boolean not null default true
);

-- A client's negotiated price supersedes the standard one. Effective-dated for the same reason
-- fees are (WP 2.3): re-pricing history must not change what was already invoiced.
create table app.flat_fee_client_overrides (
  service_id     uuid not null references app.flat_fee_services (id) on delete cascade,
  client_id      uuid not null references app.clients (id) on delete cascade,
  amount_cents   integer not null check (amount_cents >= 0),
  effective_from date not null,
  primary key (service_id, client_id, effective_from)
);

create table app.flat_fee_items (
  id           uuid primary key default gen_random_uuid(),
  service_id   uuid not null references app.flat_fee_services (id),
  work_item_id uuid not null references app.work_items (id) on delete restrict,
  matter_id    uuid references app.matters (id),
  amount_cents integer not null check (amount_cents >= 0),
  entry_date   date not null,
  status       app.billing_status not null default 'draft',
  invoice_line_id uuid,
  created_by   uuid not null references app.os_users (id),
  created_at   timestamptz not null default now()
);

create index flat_fee_items_work_item_idx on app.flat_fee_items (work_item_id);

-- M2-R13: attribution in basis points, because percentages with decimals reintroduce the
-- rounding problem the cents representation exists to avoid. Default is 100% to the performer;
-- splits must total exactly 10000 bps — enforced as a constraint trigger below, not as a
-- validation message, because a split totalling 9000 bps silently underpays someone and the
-- discrepancy surfaces a quarter later at payroll.
create table app.flat_fee_attributions (
  flat_fee_item_id uuid not null references app.flat_fee_items (id) on delete cascade,
  timekeeper_id    uuid not null references app.os_users (id),
  share_bps        integer not null check (share_bps > 0 and share_bps <= 10000),
  primary key (flat_fee_item_id, timekeeper_id)
);

-- ---------------------------------------------------------------------------
-- Immutability + integrity triggers
-- ---------------------------------------------------------------------------

-- Editable until invoiced, immutable after. The transition point is the invoice, not a clock:
-- an entry that has been billed to a client is a statement the firm has already made.
create or replace function app.forbid_invoiced_mutation() returns trigger
  language plpgsql as $$
begin
  if old.status = 'invoiced' and tg_op = 'UPDATE' then
    -- The one permitted post-invoice write is the invoice linkage itself.
    if new.invoice_line_id is distinct from old.invoice_line_id
       and old.invoice_line_id is null then
      return new;
    end if;
    raise exception 'invoiced entries are immutable (adjust via a reason-coded credit, M2-R10)';
  end if;
  if tg_op = 'DELETE' and old.status = 'invoiced' then
    raise exception 'invoiced entries cannot be deleted (adjust via a reason-coded credit)';
  end if;
  return new;
end;
$$;

create trigger time_entries_immutable
  before update or delete on app.time_entries
  for each row execute function app.forbid_invoiced_mutation();

create trigger flat_fee_items_immutable
  before update or delete on app.flat_fee_items
  for each row execute function app.forbid_invoiced_mutation();

-- Attribution must total exactly 100%. Deferred to the end of the transaction so a legitimate
-- multi-row rewrite (delete both halves of a 60/40, insert a new 50/50) is not rejected midway.
create or replace function app.check_attribution_total() returns trigger
  language plpgsql as $$
declare
  item uuid := coalesce(new.flat_fee_item_id, old.flat_fee_item_id);
  total integer;
begin
  select coalesce(sum(share_bps), 0) into total
    from app.flat_fee_attributions where flat_fee_item_id = item;
  -- Zero rows is valid: the item defaults to 100% to its creator and carries no explicit split.
  if total <> 0 and total <> 10000 then
    raise exception 'flat-fee attribution must total 10000 bps (100%%), got %', total;
  end if;
  return null;
end;
$$;

create constraint trigger flat_fee_attributions_total
  after insert or update or delete on app.flat_fee_attributions
  deferrable initially deferred
  for each row execute function app.check_attribution_total();

-- ---------------------------------------------------------------------------
-- Production ledger (WP 2.5.3) — entered / billed / collected
-- ---------------------------------------------------------------------------
--
-- A view, not a table: entered and billed are derivable from the entries themselves, and a
-- materialized copy would be one more thing that can disagree with the source of truth about
-- what someone is owed. `collected_cents` reads 0 until WP 3.3 populates the allocation table.

create table app.collected_allocations (
  id              uuid primary key default gen_random_uuid(),
  -- Exactly one of these is set: the allocation targets a time entry or a flat-fee item.
  time_entry_id   uuid references app.time_entries (id) on delete cascade,
  flat_fee_item_id uuid references app.flat_fee_items (id) on delete cascade,
  amount_cents    integer not null,        -- signed: negative rows are write-downs/reversals
  payment_ref     text,                    -- Xero payment id (WP 3.3)
  allocated_at    timestamptz not null default now(),
  check (num_nonnulls(time_entry_id, flat_fee_item_id) = 1)
);

create index collected_allocations_time_entry_idx on app.collected_allocations (time_entry_id);
create index collected_allocations_flat_fee_idx on app.collected_allocations (flat_fee_item_id);

create or replace view app.timekeeper_production as
  select
    t.timekeeper_id,
    t.work_item_id,
    t.matter_id,
    t.entry_date,
    'time'::text as source,
    t.minutes,
    -- Entered: everything recorded. Billed: only what reached an invoice. Both retained
    -- (M2-R10) — overwriting entered with billed destroys the only evidence of the write-down.
    round(t.minutes * coalesce(t.rate_cents, 0) / 60.0)::integer as entered_cents,
    case when t.status = 'invoiced'
         then round(t.minutes * coalesce(t.rate_cents, 0) / 60.0)::integer
         else 0 end as billed_cents,
    coalesce((select sum(a.amount_cents) from app.collected_allocations a
               where a.time_entry_id = t.id), 0)::integer as collected_cents,
    t.invoice_line_id
  from app.time_entries t
  where t.is_billable
  union all
  select
    a.timekeeper_id,
    f.work_item_id,
    f.matter_id,
    f.entry_date,
    'flat_fee'::text as source,
    0 as minutes,
    round(f.amount_cents * a.share_bps / 10000.0)::integer as entered_cents,
    case when f.status = 'invoiced'
         then round(f.amount_cents * a.share_bps / 10000.0)::integer
         else 0 end as billed_cents,
    round(coalesce((select sum(c.amount_cents) from app.collected_allocations c
                     where c.flat_fee_item_id = f.id), 0) * a.share_bps / 10000.0)::integer
      as collected_cents,
    f.invoice_line_id
  from app.flat_fee_items f
  join app.flat_fee_attributions a on a.flat_fee_item_id = f.id;

-- ---------------------------------------------------------------------------
-- RLS (D43) — production data is compensation data
-- ---------------------------------------------------------------------------

alter table app.time_entries              enable row level security;
alter table app.time_entries              force row level security;
alter table app.flat_fee_items            enable row level security;
alter table app.flat_fee_items            force row level security;
alter table app.flat_fee_attributions     enable row level security;
alter table app.flat_fee_attributions     force row level security;
alter table app.collected_allocations     enable row level security;
alter table app.collected_allocations     force row level security;
alter table app.flat_fee_services         enable row level security;
alter table app.flat_fee_services         force row level security;
alter table app.flat_fee_client_overrides enable row level security;
alter table app.flat_fee_client_overrides force row level security;
alter table app.activity_codes            enable row level security;
alter table app.activity_codes            force row level security;

-- Own-record rule (D43): your own time always; everyone's only with accounting_reporting.
create policy time_entries_select on app.time_entries
  for select using (
    timekeeper_id = app.jwt_uid() or app.has_domain('accounting_reporting')
  );
-- Entry requires the time_entry grant, and you may only record time as yourself: attributing
-- work to another timekeeper moves money between people's bonus bases.
create policy time_entries_insert on app.time_entries
  for insert with check (
    timekeeper_id = app.jwt_uid() and app.has_domain('time_entry')
  );
create policy time_entries_update on app.time_entries
  for update using (timekeeper_id = app.jwt_uid() and app.has_domain('time_entry'))
  with check (timekeeper_id = app.jwt_uid());
create policy time_entries_delete on app.time_entries
  for delete using (timekeeper_id = app.jwt_uid() and app.has_domain('time_entry'));

create policy flat_fee_items_select on app.flat_fee_items
  for select using (
    app.has_domain('accounting_reporting')
    or exists (select 1 from app.flat_fee_attributions a
                where a.flat_fee_item_id = id and a.timekeeper_id = app.jwt_uid())
    or created_by = app.jwt_uid()
  );
create policy flat_fee_items_write on app.flat_fee_items
  for all using (app.has_domain('invoicing') or created_by = app.jwt_uid())
  with check (app.has_domain('invoicing') or created_by = app.jwt_uid());

create policy flat_fee_attributions_select on app.flat_fee_attributions
  for select using (
    timekeeper_id = app.jwt_uid() or app.has_domain('accounting_reporting')
  );
create policy flat_fee_attributions_write on app.flat_fee_attributions
  for all using (app.has_domain('invoicing')) with check (app.has_domain('invoicing'));

-- Collected allocations are written by the payment webhook (system worker) and by invoicing
-- staff correcting an allocation; everyone else reads only their own.
create policy collected_allocations_select on app.collected_allocations
  for select using (
    app.has_domain('accounting_reporting')
    or exists (select 1 from app.time_entries t
                where t.id = time_entry_id and t.timekeeper_id = app.jwt_uid())
    or exists (select 1 from app.flat_fee_attributions a
                where a.flat_fee_item_id = flat_fee_item_id
                  and a.timekeeper_id = app.jwt_uid())
  );
create policy collected_allocations_write on app.collected_allocations
  for all using (app.has_domain('invoicing')) with check (app.has_domain('invoicing'));

-- Catalogue and codes are firm-general reads; writes are invoicing-gated (a silent price change
-- mis-bills every subsequent matter).
create policy flat_fee_services_select on app.flat_fee_services
  for select using (app.is_active_staff());
create policy flat_fee_services_write on app.flat_fee_services
  for all using (app.has_domain('invoicing')) with check (app.has_domain('invoicing'));
create policy flat_fee_overrides_select on app.flat_fee_client_overrides
  for select using (app.is_active_staff());
create policy flat_fee_overrides_write on app.flat_fee_client_overrides
  for all using (app.has_domain('invoicing')) with check (app.has_domain('invoicing'));
create policy activity_codes_select on app.activity_codes
  for select using (app.is_active_staff());
create policy activity_codes_write on app.activity_codes
  for all using (app.has_domain('invoicing')) with check (app.has_domain('invoicing'));

grant select, insert, update, delete on
  app.time_entries, app.flat_fee_items, app.flat_fee_attributions, app.collected_allocations,
  app.flat_fee_services, app.flat_fee_client_overrides, app.activity_codes
  to authenticated;
grant select on app.timekeeper_production to authenticated;

-- Seed a minimal activity-code set; the real list arrives with the AppColl import (WP 1.6).
insert into app.activity_codes (code, description) values
  ('DRAFT', 'Drafting'),
  ('REVIEW', 'Review and analysis'),
  ('CORR', 'Correspondence'),
  ('RESEARCH', 'Research'),
  ('FILING', 'Filing and formalities'),
  ('ADMIN', 'Administrative')
on conflict (code) do nothing;
