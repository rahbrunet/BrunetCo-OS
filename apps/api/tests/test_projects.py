"""Template validation and project scheduling (WP 5.1, §M9) — pure, no DB.

The scheduler is the substantive part: a project plan with quietly-wrong dates is worse than one
with no dates, because people trust it.
"""
from __future__ import annotations

from datetime import date

import pytest
from py_shared.domain import projects as pj
from py_shared.domain.deadlines import add_business_days

# 2026-07-20 is a Monday; the week is clear of holidays unless a test adds one.
MONDAY = date(2026, 7, 20)
NO_HOLIDAYS: dict[date, str] = {}


def _task(ref: str, **kw: object) -> pj.TemplateTask:
    base = {"task_ref": ref, "title": ref.title()}
    base.update(kw)
    return pj.TemplateTask(**base)  # type: ignore[arg-type]


# --- business-day helper (WP 1.2 primitive, extended here) ---------------------


def test_business_days_skip_the_weekend() -> None:
    """Friday + 1 business day is Monday. Calendar arithmetic would say Saturday and quietly
    shorten every task spanning a weekend."""
    friday = date(2026, 7, 24)
    assert add_business_days(friday, 1, NO_HOLIDAYS) == date(2026, 7, 27)


def test_zero_business_days_returns_the_next_working_day() -> None:
    """Work does not begin on a Saturday."""
    saturday = date(2026, 7, 25)
    assert add_business_days(saturday, 0, NO_HOLIDAYS) == date(2026, 7, 27)


def test_holidays_are_skipped_like_weekends() -> None:
    holidays = {date(2026, 7, 21): "Test Holiday"}
    assert add_business_days(MONDAY, 1, holidays) == date(2026, 7, 22)


def test_negative_business_days_are_refused() -> None:
    with pytest.raises(ValueError):
        add_business_days(MONDAY, -1, NO_HOLIDAYS)


# --- validation ----------------------------------------------------------------


def test_a_simple_chain_validates() -> None:
    tasks = [_task("draft"), _task("review"), _task("file")]
    edges = [pj.TemplateEdge("review", "draft"), pj.TemplateEdge("file", "review")]
    pj.validate_template(tasks, edges)


def test_an_empty_template_is_refused() -> None:
    with pytest.raises(pj.TemplateInvalid, match="no tasks"):
        pj.validate_template([], [])


def test_duplicate_task_refs_are_refused() -> None:
    with pytest.raises(pj.TemplateInvalid, match="duplicate"):
        pj.validate_template([_task("draft"), _task("draft")], [])


def test_a_dangling_dependency_is_refused() -> None:
    with pytest.raises(pj.TemplateInvalid, match="unknown task"):
        pj.validate_template([_task("draft")], [pj.TemplateEdge("draft", "nonexistent")])


def test_a_two_task_cycle_is_refused() -> None:
    """A published cycle instantiates a project whose tasks can never all complete."""
    tasks = [_task("a"), _task("b")]
    edges = [pj.TemplateEdge("a", "b"), pj.TemplateEdge("b", "a")]
    with pytest.raises(pj.TemplateInvalid, match="cycle"):
        pj.validate_template(tasks, edges)


def test_a_long_cycle_is_refused_and_reported_as_a_path() -> None:
    """The path is what lets an author actually find the loop."""
    tasks = [_task(r) for r in "abcd"]
    edges = [
        pj.TemplateEdge("b", "a"), pj.TemplateEdge("c", "b"),
        pj.TemplateEdge("d", "c"), pj.TemplateEdge("b", "d"),
    ]
    with pytest.raises(pj.TemplateInvalid) as exc:
        pj.validate_template(tasks, edges)
    assert "->" in str(exc.value)


def test_a_diamond_is_not_a_cycle() -> None:
    """The shape every real workflow has: two parallel branches rejoining."""
    tasks = [_task(r) for r in ("start", "left", "right", "end")]
    edges = [
        pj.TemplateEdge("left", "start"), pj.TemplateEdge("right", "start"),
        pj.TemplateEdge("end", "left"), pj.TemplateEdge("end", "right"),
    ]
    pj.validate_template(tasks, edges)


def test_all_problems_are_reported_together() -> None:
    """An author fixing one dangling reference per attempt stops using templates."""
    with pytest.raises(pj.TemplateInvalid) as exc:
        pj.validate_template(
            [_task("a"), _task("a")], [pj.TemplateEdge("a", "ghost")],
        )
    assert len(exc.value.problems) >= 2


def test_negative_cycle_time_is_refused() -> None:
    with pytest.raises(pj.TemplateInvalid, match="negative cycle"):
        pj.validate_template([_task("a", cycle_days=-1)], [])


# --- ordering ------------------------------------------------------------------


def test_topological_order_respects_dependencies() -> None:
    tasks = [_task("file", ordinal=0), _task("draft", ordinal=1), _task("review", ordinal=2)]
    edges = [pj.TemplateEdge("review", "draft"), pj.TemplateEdge("file", "review")]
    order = [t.task_ref for t in pj.topological_order(tasks, edges)]
    assert order == ["draft", "review", "file"]


def test_ordering_is_stable_for_independent_tasks() -> None:
    """An unstable order would make a launched project's item numbering vary run to run."""
    tasks = [_task("b", ordinal=1), _task("a", ordinal=0), _task("c", ordinal=2)]
    first = [t.task_ref for t in pj.topological_order(tasks, [])]
    second = [t.task_ref for t in pj.topological_order(tasks, [])]
    assert first == second == ["a", "b", "c"]


# --- scheduling ----------------------------------------------------------------


def test_a_chain_schedules_sequentially() -> None:
    tasks = [_task("draft", cycle_days=3), _task("review", cycle_days=2)]
    edges = [pj.TemplateEdge("review", "draft")]
    schedule = pj.compute_schedule(tasks, edges, MONDAY, NO_HOLIDAYS)

    draft = schedule.by_ref("draft")
    review = schedule.by_ref("review")
    assert draft.start_date == MONDAY
    assert draft.due_date == date(2026, 7, 23)          # Mon + 3 business days
    assert review.start_date == date(2026, 7, 24)       # the day after draft finishes
    assert review.due_date == date(2026, 7, 28)         # skips the weekend


def test_a_successor_never_starts_before_its_predecessor_finishes() -> None:
    """A fixed offset alone would schedule the chained task on top of the work it depends on."""
    tasks = [_task("draft", cycle_days=10), _task("review", cycle_days=1, start_offset_days=0)]
    edges = [pj.TemplateEdge("review", "draft")]
    schedule = pj.compute_schedule(tasks, edges, MONDAY, NO_HOLIDAYS)
    assert schedule.by_ref("review").start_date > schedule.by_ref("draft").due_date


def test_a_task_waits_for_its_slowest_predecessor() -> None:
    tasks = [
        _task("fast", cycle_days=1), _task("slow", cycle_days=8), _task("merge", cycle_days=1),
    ]
    edges = [pj.TemplateEdge("merge", "fast"), pj.TemplateEdge("merge", "slow")]
    schedule = pj.compute_schedule(tasks, edges, MONDAY, NO_HOLIDAYS)
    assert schedule.by_ref("merge").start_date > schedule.by_ref("slow").due_date


def test_independent_tasks_run_in_parallel() -> None:
    tasks = [_task("a", cycle_days=2), _task("b", cycle_days=2)]
    schedule = pj.compute_schedule(tasks, [], MONDAY, NO_HOLIDAYS)
    assert schedule.by_ref("a").start_date == schedule.by_ref("b").start_date


def test_start_offset_delays_an_unblocked_task() -> None:
    tasks = [_task("later", cycle_days=1, start_offset_days=5)]
    schedule = pj.compute_schedule(tasks, [], MONDAY, NO_HOLIDAYS)
    assert schedule.by_ref("later").start_date == date(2026, 7, 27)


def test_a_milestone_consumes_no_working_time() -> None:
    """A zero-cycle task marks a moment; it does not book a day of someone's capacity."""
    tasks = [_task("filed", cycle_days=0, is_milestone=True)]
    schedule = pj.compute_schedule(tasks, [], MONDAY, NO_HOLIDAYS)
    milestone = schedule.by_ref("filed")
    assert milestone.start_date == milestone.due_date


def test_target_end_is_the_last_due_date() -> None:
    tasks = [_task("draft", cycle_days=3), _task("review", cycle_days=2)]
    edges = [pj.TemplateEdge("review", "draft")]
    schedule = pj.compute_schedule(tasks, edges, MONDAY, NO_HOLIDAYS)
    assert schedule.target_end == schedule.by_ref("review").due_date


def test_holidays_push_the_whole_chain_out() -> None:
    holidays = {date(2026, 7, 22): "Test Holiday"}
    tasks = [_task("draft", cycle_days=3), _task("review", cycle_days=1)]
    edges = [pj.TemplateEdge("review", "draft")]
    plain = pj.compute_schedule(tasks, edges, MONDAY, NO_HOLIDAYS)
    with_holiday = pj.compute_schedule(tasks, edges, MONDAY, holidays)
    assert with_holiday.target_end > plain.target_end


def test_a_project_starting_on_a_weekend_begins_monday() -> None:
    saturday = date(2026, 7, 25)
    schedule = pj.compute_schedule([_task("a", cycle_days=1)], [], saturday, NO_HOLIDAYS)
    assert schedule.by_ref("a").start_date == date(2026, 7, 27)


def test_schedule_covers_every_task_exactly_once() -> None:
    tasks = [_task(r) for r in ("start", "left", "right", "end")]
    edges = [
        pj.TemplateEdge("left", "start"), pj.TemplateEdge("right", "start"),
        pj.TemplateEdge("end", "left"), pj.TemplateEdge("end", "right"),
    ]
    schedule = pj.compute_schedule(tasks, edges, MONDAY, NO_HOLIDAYS)
    refs = [s.task.task_ref for s in schedule.tasks]
    assert sorted(refs) == ["end", "left", "right", "start"]
