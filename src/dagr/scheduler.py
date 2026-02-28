"""DAG-based scheduling with working hours and critical path analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import networkx as nx

from dagr.models import ProjectConfig, Task, TaskStatus


@dataclass
class ScheduledTask:
    """A task with its computed schedule times and slack."""

    task: Task
    earliest_start: datetime
    earliest_finish: datetime
    latest_start: datetime
    latest_finish: datetime
    total_slack_hrs: float

    @property
    def is_critical(self) -> bool:
        return self.total_slack_hrs <= 0.0


def build_dag(tasks: dict[str, Task]) -> nx.DiGraph:
    """Construct the DAG. Raises ValueError on cycle or missing deps."""
    G = nx.DiGraph()
    for tid, task in tasks.items():
        G.add_node(tid, task=task)
    for tid, task in tasks.items():
        for dep in task.depends_on:
            if dep not in tasks:
                raise ValueError(f"Task {tid} depends on non-existent task {dep}")
            G.add_edge(dep, tid)
    if not nx.is_directed_acyclic_graph(G):
        raise ValueError("Circular dependency detected")
    return G


# ---------------------------------------------------------------------------
# Working hours helpers
# ---------------------------------------------------------------------------


def _snap_to_work_start(dt: datetime, config: ProjectConfig) -> datetime:
    """Snap a datetime to the working window: if before day start, move to
    day start; if after day end, move to next day start."""
    day_start = dt.replace(
        hour=config.day_start_hour,
        minute=config.day_start_minute,
        second=0,
        microsecond=0,
    )
    if dt < day_start:
        return day_start
    day_end = day_start + timedelta(hours=config.hours_per_day)
    if dt >= day_end:
        next_day = dt + timedelta(days=1)
        return next_day.replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        )
    return dt


def _skip_weekends_forward(dt: datetime, config: ProjectConfig) -> datetime:
    """If dt falls on a weekend and skip_weekends is on, advance to Monday."""
    if not config.skip_weekends:
        return dt
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
        dt = dt.replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        )
    return dt


def _skip_weekends_backward(dt: datetime, config: ProjectConfig) -> datetime:
    """If dt falls on a weekend, go back to Friday end-of-day."""
    if not config.skip_weekends:
        return dt
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
        dt = dt.replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        ) + timedelta(hours=config.hours_per_day)
    return dt


def add_working_hours(
    start: datetime,
    hours: float,
    config: ProjectConfig,
) -> datetime:
    """Advance *start* by *hours* of working time, respecting day length
    and weekend skipping."""
    remaining = hours
    current = _snap_to_work_start(start, config)
    current = _skip_weekends_forward(current, config)

    while remaining > 0:
        day_end = current.replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        ) + timedelta(hours=config.hours_per_day)

        available = (day_end - current).total_seconds() / 3600
        if remaining <= available + 1e-9:
            return current + timedelta(hours=remaining)

        remaining -= available
        # Advance to next working day
        next_day = (current + timedelta(days=1)).replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        )
        current = _skip_weekends_forward(next_day, config)

    return current


def _subtract_working_hours(
    end: datetime,
    hours: float,
    config: ProjectConfig,
) -> datetime:
    """Find *start* such that add_working_hours(start, hours, config) ≈ end.
    Steps backward through working days."""
    remaining = hours
    current = _skip_weekends_backward(end, config)

    while remaining > 0:
        day_start = current.replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        )
        available = (current - day_start).total_seconds() / 3600

        if available <= 1e-9:
            # At or before day start — go to end of previous working day
            prev_day = (current - timedelta(days=1)).replace(
                hour=config.day_start_hour,
                minute=config.day_start_minute,
                second=0,
                microsecond=0,
            ) + timedelta(hours=config.hours_per_day)
            current = _skip_weekends_backward(prev_day, config)
            continue

        if remaining <= available + 1e-9:
            return current - timedelta(hours=remaining)

        remaining -= available
        prev_day = (day_start - timedelta(days=1)).replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        ) + timedelta(hours=config.hours_per_day)
        current = _skip_weekends_backward(prev_day, config)

    return current


def _working_hours_between(
    start: datetime,
    end: datetime,
    config: ProjectConfig,
) -> float:
    """Count working hours between two datetimes."""
    if end <= start:
        return 0.0

    total = 0.0
    current = _snap_to_work_start(start, config)
    current = _skip_weekends_forward(current, config)

    while current < end:
        day_end = current.replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        ) + timedelta(hours=config.hours_per_day)

        effective_end = min(day_end, end)
        hours_today = (effective_end - current).total_seconds() / 3600
        total += max(0.0, hours_today)

        if day_end >= end:
            break

        next_day = (current + timedelta(days=1)).replace(
            hour=config.day_start_hour,
            minute=config.day_start_minute,
            second=0,
            microsecond=0,
        )
        current = _skip_weekends_forward(next_day, config)

    return total


# ---------------------------------------------------------------------------
# Schedule calculation
# ---------------------------------------------------------------------------


def calculate_schedule(
    tasks: dict[str, Task],
    config: ProjectConfig,
) -> list[ScheduledTask]:
    """Full forward + backward pass schedule with critical path."""
    G = build_dag(tasks)
    topo_order = list(nx.topological_sort(G))
    now = datetime.now()

    # --- Forward pass (earliest start / earliest finish) ---
    es: dict[str, datetime] = {}
    ef: dict[str, datetime] = {}

    for tid in topo_order:
        task = tasks[tid]
        preds = list(G.predecessors(tid))

        if not preds:
            calc_start = config.start_date
        else:
            calc_start = max(ef[p] for p in preds)

        # Honor proposed_start constraint
        if task.proposed_start:
            prop = datetime.fromisoformat(task.proposed_start)
            calc_start = max(calc_start, prop)

        # If in_progress, use actual_start
        if task.status == TaskStatus.IN_PROGRESS and task.actual_start:
            calc_start = datetime.fromisoformat(task.actual_start)

        # If done, use actual timestamps directly
        if task.status == TaskStatus.DONE and task.actual_start and task.actual_end:
            es[tid] = datetime.fromisoformat(task.actual_start)
            ef[tid] = datetime.fromisoformat(task.actual_end)
            continue

        # Not-started tasks can't start in the past
        if task.status == TaskStatus.NOT_STARTED:
            calc_start = max(calc_start, now)

        es[tid] = calc_start
        ef[tid] = add_working_hours(calc_start, task.duration_hrs, config)

    # --- Backward pass (latest start / latest finish) ---
    project_end = max(ef.values()) if ef else config.start_date

    lf: dict[str, datetime] = {}
    ls: dict[str, datetime] = {}

    for tid in reversed(topo_order):
        task = tasks[tid]
        succs = list(G.successors(tid))

        if not succs:
            lf[tid] = project_end
        else:
            lf[tid] = min(ls[s] for s in succs)

        # Constrain latest finish by the task's own deadline
        if task.deadline:
            dl = datetime.fromisoformat(task.deadline)
            # Ensure deadline is at end of working day if only a date was given
            if dl.hour == 0 and dl.minute == 0:
                dl = dl.replace(
                    hour=config.day_start_hour,
                    minute=config.day_start_minute,
                ) + timedelta(hours=config.hours_per_day)
            lf[tid] = min(lf[tid], dl)

        ls[tid] = _subtract_working_hours(lf[tid], task.duration_hrs, config)

    # --- Build results ---
    results: list[ScheduledTask] = []
    for tid in topo_order:
        task = tasks[tid]
        if ef[tid] <= lf[tid]:
            slack = _working_hours_between(ef[tid], lf[tid], config)
        else:
            # Negative slack: task finishes after its latest allowed finish
            slack = -_working_hours_between(lf[tid], ef[tid], config)
        results.append(
            ScheduledTask(
                task=task,
                earliest_start=es[tid],
                earliest_finish=ef[tid],
                latest_start=ls[tid],
                latest_finish=lf[tid],
                total_slack_hrs=round(slack, 2),
            )
        )

    return results


def get_critical_path(scheduled: list[ScheduledTask]) -> list[ScheduledTask]:
    """Return only the tasks on the critical path (zero slack)."""
    return [s for s in scheduled if s.is_critical]


def resource_level(
    tasks: dict[str, Task],
    config: ProjectConfig,
) -> list[ScheduledTask]:
    """Single-resource schedule: only one task active at a time.

    Uses the unconstrained schedule to get slack values, then re-schedules
    tasks sequentially.  At each step the *ready* task with the lowest slack
    (i.e. most critical) is chosen next.

    Background tasks run in parallel with the person — they start at the
    earliest possible time but don't advance the clock, so other work
    gets scheduled alongside them.
    """
    # First get the unconstrained schedule for slack / critical-path info.
    unconstrained = {s.task.id: s for s in calculate_schedule(tasks, config)}
    G = build_dag(tasks)
    now = datetime.now()

    es: dict[str, datetime] = {}
    ef: dict[str, datetime] = {}
    remaining = set(tasks.keys())
    clock = max(config.start_date, now)

    while remaining:
        # A task is *ready* when all its predecessors are finished.
        ready = [
            tid for tid in remaining
            if all(p not in remaining for p in G.predecessors(tid))
        ]
        if not ready:
            break  # shouldn't happen in a DAG, but guard anyway

        # Schedule background and flexible tasks first — they don't consume the clock.
        bg_ready = [tid for tid in ready if tasks[tid].background or tasks[tid].flexible]
        fg_ready = [tid for tid in ready if not tasks[tid].background and not tasks[tid].flexible]

        for chosen in bg_ready:
            task = tasks[chosen]
            dep_end = max((ef[p] for p in G.predecessors(chosen)), default=config.start_date)
            calc_start = max(clock, dep_end)

            if task.proposed_start:
                calc_start = max(calc_start, datetime.fromisoformat(task.proposed_start))

            if task.status == TaskStatus.DONE and task.actual_start and task.actual_end:
                es[chosen] = datetime.fromisoformat(task.actual_start)
                ef[chosen] = datetime.fromisoformat(task.actual_end)
            elif task.status == TaskStatus.IN_PROGRESS and task.actual_start:
                calc_start = max(calc_start, datetime.fromisoformat(task.actual_start))
                es[chosen] = calc_start
                ef[chosen] = add_working_hours(calc_start, task.duration_hrs, config)
            else:
                es[chosen] = calc_start
                ef[chosen] = add_working_hours(calc_start, task.duration_hrs, config)
            # Background/flexible tasks do NOT advance the clock.
            remaining.remove(chosen)

        if not fg_ready:
            continue

        # Pick the foreground task with the least slack (most urgent).
        fg_ready.sort(key=lambda tid: unconstrained[tid].total_slack_hrs)
        chosen = fg_ready[0]
        task = tasks[chosen]

        # Determine start: max of clock and dependency ends.
        dep_end = max((ef[p] for p in G.predecessors(chosen)), default=config.start_date)
        calc_start = max(clock, dep_end)

        if task.proposed_start:
            calc_start = max(calc_start, datetime.fromisoformat(task.proposed_start))

        # Done tasks keep actual times
        if task.status == TaskStatus.DONE and task.actual_start and task.actual_end:
            es[chosen] = datetime.fromisoformat(task.actual_start)
            ef[chosen] = datetime.fromisoformat(task.actual_end)
            remaining.remove(chosen)
            continue

        if task.status == TaskStatus.IN_PROGRESS and task.actual_start:
            calc_start = max(calc_start, datetime.fromisoformat(task.actual_start))

        es[chosen] = calc_start
        ef[chosen] = add_working_hours(calc_start, task.duration_hrs, config)
        clock = ef[chosen]
        remaining.remove(chosen)

    # Build results, reusing unconstrained slack values. (Flexible tasks might have
    # misleading unconstrained slack because they were included in the normal calculation;
    # however, we preserve their times from the parallel resource leveling).
    results: list[ScheduledTask] = []
    for tid in nx.topological_sort(G):
        unc = unconstrained[tid]
        
        # Give flexible tasks infinite slack to visually distinguish them
        slack = float('inf') if tasks[tid].flexible else unc.total_slack_hrs
        
        results.append(
            ScheduledTask(
                task=tasks[tid],
                earliest_start=es[tid],
                earliest_finish=ef[tid],
                latest_start=unc.latest_start,
                latest_finish=unc.latest_finish,
                total_slack_hrs=slack,
            )
        )

    return results
