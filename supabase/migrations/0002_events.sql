-- Migration 0002 — event queue (WP 0.7 worker demo).
--
-- Postgres-backed job/event mechanism (no external broker, D38 tooling note). Workers claim
-- rows with `FOR UPDATE SKIP LOCKED`. Event types grow at WPs 1.x / 3.3 / 4.x.
--
-- This is a system table operated by workers acting with an explicit system identity, not on a
-- user request path — so it is not user-RLS-scoped. It is owned by the platform.

create schema if not exists ops;

create type ops.event_status as enum ('pending', 'processing', 'done', 'failed');

create table ops.events (
  id            bigint generated always as identity primary key,
  type          text not null,
  payload       jsonb not null default '{}'::jsonb,
  status        ops.event_status not null default 'pending',
  attempts      int not null default 0,
  locked_by     text,
  created_at    timestamptz not null default now(),
  processed_at  timestamptz
);

create index events_pending_idx on ops.events (created_at) where status = 'pending';

-- Claim the next pending event atomically for a given worker id.
create or replace function ops.claim_next_event(worker_id text)
  returns ops.events
  language plpgsql
as $$
declare
  ev ops.events;
begin
  select * into ev
    from ops.events
   where status = 'pending'
   order by created_at
   for update skip locked
   limit 1;

  if not found then
    return null;
  end if;

  update ops.events
     set status = 'processing', locked_by = worker_id, attempts = attempts + 1
   where id = ev.id
  returning * into ev;

  return ev;
end;
$$;
