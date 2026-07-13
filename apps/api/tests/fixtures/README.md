# Test fixtures — AppColl TaskTypes CSV (WP 1.3)

`appcoll_task_types_sample.csv` is a representative sample of the AppColl TaskType export the
WP 1.3 importer ingests. The real firm export (552 rows, retained from WP 0.4/D37) plugs into the
same importer — it is data, supplied by James, not committed here. This sample exercises every
mapping path so the machinery is proven independent of the real data landing.

## CSV schema

One header row, then one row per AppColl task type:

| column | meaning |
|---|---|
| `task_type_id` | AppColl TaskType id — the idempotency key for re-runnable imports |
| `name` | task title |
| `deadline_type` | one of the six D37 labels (Hard External / Extendable External / Internal Deadline / General Reminder / Event / Transient Event) |
| `jurisdiction` | internal jurisdiction code, or empty for any-jurisdiction |
| `trigger_event` | trigger code; **empty = opaque linkage → unresolved** (D37) |
| `auto_generate` | true/false |
| `respond_by_offset` | compact offset token (`4m`, `14d`, `2y6m`), or empty |
| `final_due_offset` | compact offset token, or empty |
| `alternate_offset` | dual-path alternate final-due offset (D37: 20 such rules), or empty |
| `owner_resolution` | D37 owner vocabulary (carried as data; assignment logic is a later WP) |
| `field_action` | matter-field setter, e.g. `AllowanceDate={TriggeringTask.RefDate}` (M1-R13) |
| `reminder_of` | if set, this row is a reminder-pair half → A18 ladder stub, not a rule (WP 6.12) |
| `source_integration` | `USPTO` marks a watcher-superseded rule → imported but inactive, tagged `superseded-by-a1` |

## What the sample covers (13 rows → 9 mapped, 1 ladder stub, 3 unresolved)

- All six deadline types (counts: extendable 2, hard 2, internal 1, reminder 1, event 2, transient 1)
- Dual dates (respond_by + final_due), any-jurisdiction rule, dual-path alternate offset
- Field-setter action, auto-generate off, USPTO-superseded (inactive + tagged)
- Reminder-pair → ladder stub
- Three unresolved paths: no trigger, unknown deadline type, no offsets — none dropped
