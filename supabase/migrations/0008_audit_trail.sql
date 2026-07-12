-- Migration 0008 — audit trail (M1-R6, WP 1.5).
--
-- Every docket change is recorded: a single append-only app.audit_log fed by row-level
-- triggers on the audited tables. Complements (does not replace) the M1-R14 provenance log:
-- provenance explains WHY a task was generated; the audit trail records every change to a row
-- after that — status flips, date edits, reassignments, rule edits, ACL grants.
--
-- Immutability: same posture as task_provenance — `authenticated` gets SELECT only, and no
-- insert/update/delete policy exists. Writes happen exclusively inside the trigger function,
-- which runs as the table owner. audit_log has RLS ENABLED but deliberately NOT FORCED: forcing
-- it would subject the owner-run trigger inserts to policies that must not exist for users.
-- (D43 satisfied: the policy ships here, in the same migration.)
--
-- Visibility: an audit row inherits the visibility of the record it describes. The trigger
-- resolves the owning family where one exists (families/matters/tasks); rows with no family
-- context (docket_rules, permission_grants) are permissions-admin reading.

create type app.audit_action as enum ('insert', 'update', 'delete');

create table app.audit_log (
  id             uuid primary key default gen_random_uuid(),
  table_name     text not null,
  row_id         uuid not null,
  family_id      uuid,                      -- owning family where derivable; drives visibility
  action         app.audit_action not null,
  changed_by     uuid,                      -- app.jwt_uid(); null = system/migration path
  changed_at     timestamptz not null default now(),
  old_row        jsonb,                     -- null on insert
  new_row        jsonb,                     -- null on delete
  changed_fields text[]                     -- updated column names; null unless action=update
);

create index audit_log_row_idx on app.audit_log (table_name, row_id);
create index audit_log_family_idx on app.audit_log (family_id) where family_id is not null;
create index audit_log_changed_at_idx on app.audit_log (changed_at);

-- ---------------------------------------------------------------------------
-- Trigger function
-- ---------------------------------------------------------------------------

create or replace function app.audit_row() returns trigger
  language plpgsql security definer set search_path = app, public
as $$
declare
  v_old jsonb := case when tg_op = 'INSERT' then null else to_jsonb(old) end;
  v_new jsonb := case when tg_op = 'DELETE' then null else to_jsonb(new) end;
  v_row_id uuid := coalesce((v_new ->> 'id')::uuid, (v_old ->> 'id')::uuid);
  v_family uuid;
  v_changed text[];
begin
  -- Resolve the owning family for visibility scoping.
  if tg_table_name = 'families' then
    v_family := v_row_id;
  elsif tg_table_name = 'matters' then
    v_family := coalesce((v_new ->> 'family_id')::uuid, (v_old ->> 'family_id')::uuid);
  elsif tg_table_name = 'tasks' then
    select m.family_id into v_family from app.matters m
     where m.id = coalesce((v_new ->> 'matter_id')::uuid, (v_old ->> 'matter_id')::uuid);
  elsif tg_table_name = 'family_access' then
    v_family := coalesce((v_new ->> 'family_id')::uuid, (v_old ->> 'family_id')::uuid);
    -- family_access has no id column; key the audit row on the family.
    v_row_id := v_family;
  elsif tg_table_name = 'docket_rules' then
    v_row_id := coalesce((v_new ->> 'rule_id')::uuid, (v_old ->> 'rule_id')::uuid);
  elsif tg_table_name = 'permission_grants' then
    v_row_id := coalesce((v_new ->> 'user_id')::uuid, (v_old ->> 'user_id')::uuid);
  end if;

  if tg_op = 'UPDATE' then
    select array_agg(n.key) into v_changed
      from jsonb_each(v_new) n join jsonb_each(v_old) o on o.key = n.key
     where n.value is distinct from o.value;
    -- No-op updates leave no audit residue.
    if v_changed is null then
      return new;
    end if;
  end if;

  insert into app.audit_log
    (table_name, row_id, family_id, action, changed_by, old_row, new_row, changed_fields)
  values
    (tg_table_name, v_row_id, v_family, lower(tg_op)::app.audit_action, app.jwt_uid(),
     v_old, v_new, v_changed);
  return coalesce(new, old);
end;
$$;

revoke all on function app.audit_row() from public;

-- ---------------------------------------------------------------------------
-- Attach to the docket-core tables (M1-R6) + the permission surfaces (D43)
-- ---------------------------------------------------------------------------

create trigger audit_families after insert or update or delete on app.families
  for each row execute function app.audit_row();
create trigger audit_matters after insert or update or delete on app.matters
  for each row execute function app.audit_row();
create trigger audit_tasks after insert or update or delete on app.tasks
  for each row execute function app.audit_row();
create trigger audit_docket_rules after insert or update or delete on app.docket_rules
  for each row execute function app.audit_row();
create trigger audit_family_access after insert or update or delete on app.family_access
  for each row execute function app.audit_row();
create trigger audit_permission_grants after insert or update or delete on app.permission_grants
  for each row execute function app.audit_row();

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table app.audit_log enable row level security;
-- NOT forced — see header. Only the owner-run trigger writes; users hold SELECT alone.

create policy audit_log_select on app.audit_log
  for select using (
    (family_id is not null and app.can_see_family(family_id))
    or (family_id is null and app.is_permissions_admin())
  );

grant select on app.audit_log to authenticated;
