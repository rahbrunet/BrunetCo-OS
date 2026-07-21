-- Migration 0023 — EOS core (WP 5.7, spec §M9, D30).
--
-- The Entrepreneurial Operating System mechanics, emulated natively (Ninety.io/Bloom-Growth
-- style): Scorecard, Rocks, Accountability Chart, Issues (IDS), To-Dos, and the L10 meeting that
-- runs over them. This WP builds the data model and the computed logic (RAG status, the 90%
-- To-Do target, the IDS lifecycle); auto-population of measurables from live OS data and the L10
-- pack generation are WP 5.8.
--
-- One shape recurs and is worth stating once: a measurable/rock/to-do/issue is OWNED by exactly
-- one seat-holder (EOS insists every number and every priority has a single accountable person),
-- and the productivity data is seat-owned rather than surveillance — surfaced on the person's own
-- view first (spec §M9). RLS reflects that: you always see your own, and the firm's aggregate is
-- staff-visible because EOS is run in the open.

-- ---------------------------------------------------------------------------
-- Scorecard — weekly measurables with a red/yellow/green goal
-- ---------------------------------------------------------------------------

-- Whether "on goal" means at-or-above (revenue) or at-or-below (overdue count) the target. The
-- comparison is data, not code, because half the measurables in a firm are lower-is-better.
create type app.goal_direction as enum ('higher_is_better', 'lower_is_better');

create table app.scorecard_measurables (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  owner_id   uuid not null references app.os_users (id),
  goal       numeric not null,
  direction  app.goal_direction not null default 'higher_is_better',
  -- The yellow band: within this fraction of goal is "close" (amber), beyond it red. 0.1 = 10%.
  yellow_band numeric not null default 0.1 check (yellow_band >= 0),
  unit       text,
  -- Auto-populated measurables (5.8) name the source metric here; null = entered by hand.
  source_metric text,
  is_active  boolean not null default true,
  created_at timestamptz not null default now()
);

create index scorecard_measurables_owner_idx on app.scorecard_measurables (owner_id);

create table app.scorecard_entries (
  measurable_id uuid not null references app.scorecard_measurables (id) on delete cascade,
  week_start    date not null,               -- Monday of the scorecard week
  value         numeric,
  entered_by    uuid references app.os_users (id),
  entered_at    timestamptz not null default now(),
  primary key (measurable_id, week_start)
);

-- ---------------------------------------------------------------------------
-- Rocks — quarterly priorities
-- ---------------------------------------------------------------------------

create type app.rock_status as enum ('on_track', 'off_track', 'done', 'incomplete');

create table app.rocks (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  owner_id    uuid not null references app.os_users (id),
  quarter     text not null,                 -- '2026-Q3'
  status      app.rock_status not null default 'on_track',
  is_company  boolean not null default false,  -- company rock vs individual
  due_date    date,
  created_at  timestamptz not null default now(),
  completed_at timestamptz
);

create index rocks_owner_quarter_idx on app.rocks (owner_id, quarter);

-- ---------------------------------------------------------------------------
-- Accountability Chart — seats and who sits in them
-- ---------------------------------------------------------------------------

create table app.seats (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  parent_seat_id   uuid references app.seats (id) on delete set null,
  holder_id        uuid references app.os_users (id) on delete set null,  -- one accountable person
  responsibilities text[] not null default '{}',
  ordinal          integer not null default 0
);

create index seats_parent_idx on app.seats (parent_seat_id);

-- ---------------------------------------------------------------------------
-- Issues — the IDS list (Identify, Discuss, Solve)
-- ---------------------------------------------------------------------------

create type app.issue_status as enum ('open', 'identified', 'discussing', 'solved', 'dropped');

create table app.issues (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  detail      text,
  raised_by   uuid not null references app.os_users (id),
  owner_id    uuid references app.os_users (id),
  status      app.issue_status not null default 'open',
  priority    integer not null default 0,     -- higher = worked first in the L10
  -- Issues can be raised from anywhere (spec: "capture-from-anywhere"): a matter, a work item, or
  -- nothing in particular. Optional soft links, no cascade — solving an issue outlives its source.
  matter_id   uuid references app.matters (id) on delete set null,
  work_item_id uuid references app.work_items (id) on delete set null,
  created_at  timestamptz not null default now(),
  solved_at   timestamptz,
  resolution  text
);

create index issues_status_idx on app.issues (status, priority desc) where status <> 'solved';

-- ---------------------------------------------------------------------------
-- To-Dos — seven-day action items, tracked against the EOS 90% target
-- ---------------------------------------------------------------------------

create table app.eos_todos (
  id           uuid primary key default gen_random_uuid(),
  title        text not null,
  owner_id     uuid not null references app.os_users (id),
  due_date     date,
  done         boolean not null default false,
  done_at      timestamptz,
  -- Where it came from — an L10 meeting, an issue's solution, or ad hoc.
  from_meeting_id uuid,
  from_issue_id   uuid references app.issues (id) on delete set null,
  created_at   timestamptz not null default now()
);

create index eos_todos_owner_open_idx on app.eos_todos (owner_id) where not done;

-- ---------------------------------------------------------------------------
-- L10 meetings — the weekly Level-10 meeting that runs over all of the above
-- ---------------------------------------------------------------------------

create table app.l10_meetings (
  id          uuid primary key default gen_random_uuid(),
  held_on     date not null,
  facilitator_id uuid references app.os_users (id),
  attendees   uuid[] not null default '{}',
  -- The meeting is a review session; its rating (EOS asks attendees to score the meeting 1-10).
  rating      numeric,
  notes       text,
  created_at  timestamptz not null default now()
);

create index l10_meetings_date_idx on app.l10_meetings (held_on desc);

-- ---------------------------------------------------------------------------
-- Computed helpers
-- ---------------------------------------------------------------------------

-- RAG status for one measured value against its goal. Kept in SQL so the Scorecard view, the L10
-- pack (5.8) and any dashboard colour a value the same way rather than three near-copies drifting.
create or replace function app.scorecard_rag(
  value numeric, goal numeric, direction app.goal_direction, yellow_band numeric
) returns text
  language sql immutable
as $$
  select case
    when value is null then 'no_data'
    when direction = 'higher_is_better' then
      case when value >= goal then 'green'
           when value >= goal * (1 - yellow_band) then 'yellow'
           else 'red' end
    else  -- lower_is_better
      case when value <= goal then 'green'
           when value <= goal * (1 + yellow_band) then 'yellow'
           else 'red' end
  end;
$$;

-- The current-week scorecard: each measurable with its latest entered value and RAG colour.
create or replace view app.scorecard_current as
  select
    m.id, m.name, m.owner_id, m.goal, m.direction, m.unit, m.source_metric,
    e.week_start, e.value,
    app.scorecard_rag(e.value, m.goal, m.direction, m.yellow_band) as rag
  from app.scorecard_measurables m
  left join lateral (
    select week_start, value from app.scorecard_entries se
     where se.measurable_id = m.id
     order by week_start desc limit 1
  ) e on true
  where m.is_active;

-- ---------------------------------------------------------------------------
-- RLS — seat-owned data, firm-run in the open
-- ---------------------------------------------------------------------------

alter table app.scorecard_measurables enable row level security;
alter table app.scorecard_measurables force row level security;
alter table app.scorecard_entries     enable row level security;
alter table app.scorecard_entries     force row level security;
alter table app.rocks                 enable row level security;
alter table app.rocks                 force row level security;
alter table app.seats                 enable row level security;
alter table app.seats                 force row level security;
alter table app.issues                enable row level security;
alter table app.issues                force row level security;
alter table app.eos_todos             enable row level security;
alter table app.eos_todos             force row level security;
alter table app.l10_meetings          enable row level security;
alter table app.l10_meetings          force row level security;

-- EOS is run in the open: measurables, rocks, the accountability chart, issues, to-dos and
-- meetings are all staff-visible (the team reviews them together in the L10). Writes are
-- staff-level too — this is collaborative operating data, not gated financial data. The
-- own-a-number discipline lives in the owner_id column and the L10 process, not in RLS locks.
create policy scorecard_measurables_all on app.scorecard_measurables
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy scorecard_entries_all on app.scorecard_entries
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy rocks_all on app.rocks
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy seats_all on app.seats
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy issues_all on app.issues
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy eos_todos_all on app.eos_todos
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy l10_meetings_all on app.l10_meetings
  for all using (app.is_active_staff()) with check (app.is_active_staff());

grant select, insert, update, delete on
  app.scorecard_measurables, app.scorecard_entries, app.rocks, app.seats, app.issues,
  app.eos_todos, app.l10_meetings
  to authenticated;
grant select on app.scorecard_current to authenticated;
grant execute on function app.scorecard_rag(numeric, numeric, app.goal_direction, numeric)
  to authenticated;
