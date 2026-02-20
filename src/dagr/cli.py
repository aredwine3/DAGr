"""Typer CLI for DAGr."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from dagr.models import ProjectConfig, Task, TaskStatus
from dagr.persistence import Store
from dagr.scheduler import add_working_hours, build_dag, calculate_schedule, get_critical_path, resource_level

app = typer.Typer(
    name="dagr",
    help="Thesis timeline optimizer using DAG-based scheduling.",
    no_args_is_help=True,
)
console = Console()


def _get_store() -> Store:
    return Store()


def _require_config(config: ProjectConfig | None) -> ProjectConfig:
    if config is None:
        console.print("[red]No project config found. Run 'dagr init' first.[/red]")
        raise typer.Exit(1)
    return config


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    start: Annotated[
        str,
        typer.Option(help="Project start date (YYYY-MM-DD)", prompt="Project start date (YYYY-MM-DD)"),
    ],
    hours_per_day: float = 8.0,
    day_start: str = "09:00",
    skip_weekends: bool = True,
) -> None:
    """Initialize (or reinitialize) project configuration."""
    store = _get_store()
    _, tasks = store.load()
    h, m = (int(x) for x in day_start.split(":"))
    start_dt = datetime.fromisoformat(start)
    # If user only provided a date (no time), set time to day_start
    if start_dt.hour == 0 and start_dt.minute == 0 and "T" not in start:
        start_dt = start_dt.replace(hour=h, minute=m)
    config = ProjectConfig(
        start_date=start_dt,
        hours_per_day=hours_per_day,
        day_start_hour=h,
        day_start_minute=m,
        skip_weekends=skip_weekends,
    )
    store.save(config, tasks)
    console.print(f"[green]Project initialized. Start: {start}[/green]")


@app.command()
def add(
    name: str,
    duration: Annotated[float, typer.Option("--duration", "-d", help="Duration in hours")],
    depends: Annotated[Optional[list[str]], typer.Option("--depends", help="Task IDs this depends on")] = None,
    deadline: Annotated[Optional[str], typer.Option(help="Deadline date (YYYY-MM-DD)")] = None,
    start: Annotated[Optional[str], typer.Option(help="Proposed start date (YYYY-MM-DD)")] = None,
    background: Annotated[bool, typer.Option("--bg", help="Task runs unattended (e.g. a pipeline)")] = False,
) -> None:
    """Add a new task.

    Dependencies can be specified individually (--depends T-1 --depends T-2)
    or comma-separated (--depends T-1,T-2,T-3).
    """
    store = _get_store()
    config, tasks = store.load()
    tid = store.generate_id(tasks)

    # Expand comma-separated dependencies
    expanded_deps: list[str] = []
    for d in depends or []:
        expanded_deps.extend(part.strip() for part in d.split(",") if part.strip())

    tasks[tid] = Task(
        id=tid,
        name=name,
        duration_hrs=duration,
        depends_on=expanded_deps,
        deadline=deadline,
        proposed_start=start,
        background=background,
    )
    store.save(config, tasks)
    console.print(f"[green]Added '{name}' as {tid}[/green]")


@app.command("list")
def list_tasks(
    status_filter: Annotated[Optional[str], typer.Option("--status", "-s", help="Filter by status (not_started, in_progress, done)")] = None,
    search: Annotated[Optional[str], typer.Option("--search", "-q", help="Filter by name (case-insensitive substring match)")] = None,
) -> None:
    """List all tasks and their status."""
    store = _get_store()
    _, tasks = store.load()
    if not tasks:
        console.print("No tasks found.")
        return

    filtered = list(tasks.values())

    if status_filter:
        try:
            sf = TaskStatus(status_filter)
        except ValueError:
            console.print(f"[red]Invalid status '{status_filter}'. Use: not_started, in_progress, done[/red]")
            raise typer.Exit(1)
        filtered = [t for t in filtered if t.status == sf]

    if search:
        q = search.lower()
        filtered = [t for t in filtered if q in t.name.lower() or q in t.id.lower()]

    if not filtered:
        console.print("No tasks match the filter.")
        return

    table = Table(title="Tasks")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Hours")
    table.add_column("Depends On")
    table.add_column("Status")
    table.add_column("BG")
    table.add_column("Deadline")

    for t in filtered:
        table.add_row(
            t.id,
            t.name,
            f"{t.duration_hrs:.1f}",
            ", ".join(t.depends_on) or "-",
            t.status.value,
            "bg" if t.background else "",
            t.deadline or "-",
        )

    console.print(table)
    if status_filter or search:
        console.print(f"[dim]Showing {len(filtered)} of {len(tasks)} tasks[/dim]")


@app.command()
def update(
    task_id: str,
    name: Annotated[Optional[str], typer.Option(help="New task name")] = None,
    duration: Annotated[Optional[float], typer.Option(help="New duration in hours")] = None,
    deadline: Annotated[Optional[str], typer.Option(help="New deadline (YYYY-MM-DD)")] = None,
    start: Annotated[Optional[str], typer.Option(help="New proposed start (YYYY-MM-DD)")] = None,
    background: Annotated[Optional[bool], typer.Option("--bg/--no-bg", help="Runs unattended (e.g. a pipeline)")] = None,
    add_dep: Annotated[Optional[list[str]], typer.Option("--add-dep", help="Add a dependency (task ID)")] = None,
    remove_dep: Annotated[Optional[list[str]], typer.Option("--remove-dep", help="Remove a dependency (task ID)")] = None,
) -> None:
    """Update fields of an existing task."""
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]
    if name is not None:
        t.name = name
    if duration is not None:
        t.duration_hrs = duration
    if deadline is not None:
        t.deadline = deadline
    if start is not None:
        t.proposed_start = start
    if background is not None:
        t.background = background

    if add_dep:
        for dep in add_dep:
            if dep not in tasks:
                console.print(f"[red]Dependency {dep} not found.[/red]")
                raise typer.Exit(1)
            if dep == task_id:
                console.print(f"[red]A task cannot depend on itself.[/red]")
                raise typer.Exit(1)
            if dep not in t.depends_on:
                t.depends_on.append(dep)

    if remove_dep:
        for dep in remove_dep:
            if dep in t.depends_on:
                t.depends_on.remove(dep)
            else:
                console.print(f"[yellow]{task_id} does not depend on {dep}, skipping.[/yellow]")

    store.save(config, tasks)
    console.print(f"[green]Updated {task_id}.[/green]")


@app.command()
def delete(task_id: str) -> None:
    """Delete a task and remove it from dependency lists."""
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    del tasks[task_id]
    for t in tasks.values():
        if task_id in t.depends_on:
            t.depends_on.remove(task_id)

    store.save(config, tasks)
    console.print(f"[green]Deleted {task_id}.[/green]")


@app.command()
def show(task_id: str) -> None:
    """Show all details for a single task, including scheduled times and slack."""
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]

    console.print(f"\n[bold]{t.id}[/bold]  {t.name}")
    console.print(f"  Status:     {t.status.value}")
    console.print(f"  Duration:   {t.duration_hrs:.1f}h")
    console.print(f"  Background: {'yes' if t.background else 'no'}")
    console.print(f"  Depends on: {', '.join(t.depends_on) or 'none'}")

    # Show what depends on this task
    dependents = [tid for tid, task in tasks.items() if t.id in task.depends_on]
    console.print(f"  Blocks:     {', '.join(dependents) or 'none'}")

    if t.deadline:
        console.print(f"  Deadline:   {t.deadline}")
    if t.proposed_start:
        console.print(f"  Proposed start: {t.proposed_start}")
    if t.actual_start:
        console.print(f"  Actual start:   {t.actual_start}")
    if t.actual_end:
        console.print(f"  Actual end:     {t.actual_end}")

    # Show scheduled times if project is initialized
    if config:
        try:
            scheduled = calculate_schedule(tasks, config)
            for s in scheduled:
                if s.task.id == task_id:
                    console.print(f"\n  [dim]── Scheduled ──[/dim]")
                    console.print(f"  Earliest start:  {s.earliest_start.strftime('%a %b %d, %H:%M')}")
                    console.print(f"  Earliest finish: {s.earliest_finish.strftime('%a %b %d, %H:%M')}")
                    console.print(f"  Latest start:    {s.latest_start.strftime('%a %b %d, %H:%M')}")
                    console.print(f"  Latest finish:   {s.latest_finish.strftime('%a %b %d, %H:%M')}")
                    console.print(f"  Slack:           {s.total_slack_hrs:.1f}h")
                    if s.is_critical:
                        console.print(f"  [bold yellow]On the critical path[/bold yellow]")
                    if t.deadline:
                        dl = datetime.fromisoformat(t.deadline)
                        if s.earliest_finish > dl:
                            console.print(f"  [bold red]Projected LATE by {(s.earliest_finish - dl).days} day(s)[/bold red]")
                    break
        except ValueError:
            pass

    console.print()


@app.command("start")
def start_task(task_id: str) -> None:
    """Mark a task as in-progress with current timestamp."""
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]
    t.status = TaskStatus.IN_PROGRESS
    t.actual_start = datetime.now().isoformat()
    store.save(config, tasks)
    console.print(f"[green]Started {task_id} at {t.actual_start}[/green]")


@app.command()
def done(task_id: str) -> None:
    """Mark a task as done with current timestamp."""
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]
    t.status = TaskStatus.DONE
    t.actual_end = datetime.now().isoformat()
    if not t.actual_start:
        t.actual_start = t.actual_end
    store.save(config, tasks)
    console.print(f"[green]Completed {task_id} at {t.actual_end}[/green]")

    # Show actual vs estimated comparison
    if t.actual_start:
        start_dt = datetime.fromisoformat(t.actual_start)
        end_dt = datetime.fromisoformat(t.actual_end)
        if config:
            from dagr.scheduler import _working_hours_between
            actual_hrs = _working_hours_between(start_dt, end_dt, config)
        else:
            actual_hrs = (end_dt - start_dt).total_seconds() / 3600
        estimated = t.duration_hrs
        diff = actual_hrs - estimated
        if abs(diff) < 0.1:
            console.print(f"  Estimated: {estimated:.1f}h  Actual: {actual_hrs:.1f}h  [green]Right on target[/green]")
        elif diff > 0:
            console.print(f"  Estimated: {estimated:.1f}h  Actual: {actual_hrs:.1f}h  [red]+{diff:.1f}h over[/red]")
        else:
            console.print(f"  Estimated: {estimated:.1f}h  Actual: {actual_hrs:.1f}h  [green]{diff:.1f}h under[/green]")


@app.command()
def reset(task_id: str) -> None:
    """Reset a task back to not_started (undo start/done)."""
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]
    old_status = t.status.value
    t.status = TaskStatus.NOT_STARTED
    t.actual_start = None
    t.actual_end = None
    store.save(config, tasks)
    console.print(f"[green]Reset {task_id} from {old_status} to not_started.[/green]")


@app.command()
def schedule(
    remaining: Annotated[bool, typer.Option("--remaining", "-r", help="Hide completed tasks")] = False,
    csv: Annotated[Optional[str], typer.Option("--csv", help="Export schedule to CSV file")] = None,
) -> None:
    """Calculate and display the full schedule (unconstrained, for slack/critical-path analysis)."""
    store = _get_store()
    config, tasks = store.load()
    config = _require_config(config)
    if not tasks:
        console.print("No tasks to schedule.")
        return

    try:
        scheduled = calculate_schedule(tasks, config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if remaining:
        scheduled = [s for s in scheduled if s.task.status != TaskStatus.DONE]

    if csv:
        import csv as csv_mod
        from pathlib import Path

        with Path(csv).open("w", newline="") as f:
            writer = csv_mod.writer(f)
            writer.writerow(["ID", "Task Name", "Hours", "Status", "Start", "End", "Slack (h)", "Deadline", "Flags"])
            for s in scheduled:
                flags = []
                if s.is_critical:
                    flags.append("CRITICAL")
                if s.task.deadline:
                    dl = datetime.fromisoformat(s.task.deadline)
                    if s.earliest_finish > dl:
                        flags.append("LATE")
                writer.writerow([
                    s.task.id,
                    s.task.name,
                    f"{s.task.duration_hrs:.1f}",
                    s.task.status.value,
                    s.earliest_start.strftime("%Y-%m-%d %H:%M"),
                    s.earliest_finish.strftime("%Y-%m-%d %H:%M"),
                    f"{s.total_slack_hrs:.1f}",
                    s.task.deadline or "",
                    " | ".join(flags),
                ])
        console.print(f"[green]Exported {len(scheduled)} tasks to {csv}[/green]")
        return

    table = Table(title="Schedule")
    table.add_column("ID")
    table.add_column("Task Name")
    table.add_column("Hours")
    table.add_column("Status")
    table.add_column("Start")
    table.add_column("End")
    table.add_column("Slack (h)")
    table.add_column("Deadline")
    table.add_column("Flags")

    for s in scheduled:
        flags = []
        if s.is_critical:
            flags.append("CRITICAL")
        if s.task.deadline:
            dl = datetime.fromisoformat(s.task.deadline)
            if s.earliest_finish > dl:
                flags.append("LATE")

        style = (
            "bold red"
            if "LATE" in flags
            else ("bold yellow" if "CRITICAL" in flags else None)
        )

        table.add_row(
            s.task.id,
            s.task.name,
            f"{s.task.duration_hrs:.1f}",
            s.task.status.value,
            s.earliest_start.strftime("%b %d, %H:%M"),
            s.earliest_finish.strftime("%b %d, %H:%M"),
            f"{s.total_slack_hrs:.1f}",
            s.task.deadline or "-",
            " | ".join(flags) or "-",
            style=style,
        )

    console.print(table)


@app.command("critical-path")
def critical_path() -> None:
    """Display only the tasks on the critical path."""
    store = _get_store()
    config, tasks = store.load()
    config = _require_config(config)

    try:
        scheduled = calculate_schedule(tasks, config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    crit = get_critical_path(scheduled)
    if not crit:
        console.print("No critical path found.")
        return

    table = Table(title="Critical Path")
    table.add_column("ID")
    table.add_column("Task Name")
    table.add_column("Hours")
    table.add_column("Start")
    table.add_column("End")

    total_hrs = 0.0
    for s in crit:
        table.add_row(
            s.task.id,
            s.task.name,
            f"{s.task.duration_hrs:.1f}",
            s.earliest_start.strftime("%b %d, %H:%M"),
            s.earliest_finish.strftime("%b %d, %H:%M"),
        )
        total_hrs += s.task.duration_hrs

    console.print(table)
    console.print(f"\nTotal critical path duration: [bold]{total_hrs:.1f}[/bold] hours")


@app.command()
def status() -> None:
    """Project health dashboard: progress, hours, and projected completion."""
    store = _get_store()
    config, tasks = store.load()
    config = _require_config(config)
    if not tasks:
        console.print("No tasks found.")
        return

    try:
        scheduled = calculate_schedule(tasks, config)
        leveled = resource_level(tasks, config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    total = len(tasks)
    done_count = sum(1 for t in tasks.values() if t.status == TaskStatus.DONE)
    ip_count = sum(1 for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS)
    ns_count = total - done_count - ip_count

    total_hrs = sum(t.duration_hrs for t in tasks.values())
    done_hrs = sum(t.duration_hrs for t in tasks.values() if t.status == TaskStatus.DONE)
    remaining_hrs = total_hrs - done_hrs

    pct = (done_hrs / total_hrs * 100) if total_hrs > 0 else 0

    # Projected completion from the resource-leveled (single-person) schedule
    proj_end = max(s.earliest_finish for s in leveled)

    # Build a progress bar
    bar_width = 30
    filled = int(bar_width * pct / 100)
    bar = f"[green]{'█' * filled}[/green][dim]{'░' * (bar_width - filled)}[/dim]"

    console.print(f"\n[bold underline]Project Status[/bold underline]\n")
    console.print(f"  Tasks:  [green]{done_count} done[/green]  [yellow]{ip_count} in progress[/yellow]  {ns_count} remaining  ({total} total)")
    console.print(f"  Hours:  [green]{done_hrs:.1f}h done[/green]  {remaining_hrs:.1f}h remaining  ({total_hrs:.1f}h total)")
    console.print(f"  Progress: {bar} {pct:.0f}%")
    console.print(f"  Projected completion: [bold]{proj_end.strftime('%a %b %d, %Y')}[/bold]")

    # Check deadlines using resource-leveled schedule
    late_tasks = []
    for s in leveled:
        if s.task.deadline:
            dl = datetime.fromisoformat(s.task.deadline)
            if s.earliest_finish > dl:
                late_tasks.append((s.task.id, s.task.name, s.task.deadline, s.earliest_finish))

    if late_tasks:
        console.print(f"\n  [bold red]⚠ {len(late_tasks)} task(s) projected LATE:[/bold red]")
        for tid, name, dl, finish in late_tasks:
            console.print(f"    {tid} {name} — deadline {dl}, projected {finish.strftime('%b %d')}")

    crit = get_critical_path(scheduled)
    if crit:
        crit_hrs = sum(s.task.duration_hrs for s in crit)
        console.print(f"\n  Critical path: {len(crit)} tasks, {crit_hrs:.1f}h total")

    console.print()


@app.command("next")
def next_task() -> None:
    """Show the single next task you should work on (and any background tasks to kick off)."""
    store = _get_store()
    config, tasks = store.load()
    config = _require_config(config)

    # If something is already in progress, show that
    in_progress = [t for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS]
    if in_progress:
        for t in in_progress:
            label = "[dim](BG)[/dim] " if t.background else ""
            console.print(f"\n  [yellow]In progress:[/yellow]  {label}[bold]{t.id}[/bold]  {t.name}  ({t.duration_hrs:.1f}h)")
            if t.actual_start:
                console.print(f"    Started: {t.actual_start}")
        # Even with in-progress tasks, check for ready background tasks to kick off
        if not any(t.background for t in in_progress):
            try:
                leveled = resource_level(tasks, config)
            except ValueError:
                leveled = []
            for s in leveled:
                if s.task.status != TaskStatus.NOT_STARTED or not s.task.background:
                    continue
                # Check all deps are done
                if all(tasks[d].status == TaskStatus.DONE for d in s.task.depends_on if d in tasks):
                    console.print(f"\n  [dim]Kick off background job:[/dim]  [bold]{s.task.id}[/bold]  {s.task.name}  ({s.task.duration_hrs:.1f}h)")
                    if s.is_critical:
                        console.print("    [bold yellow]On the critical path[/bold yellow]")
                    console.print(f"    Run [bold]dagr start {s.task.id}[/bold]")
                    break
        console.print()
        return

    try:
        leveled = resource_level(tasks, config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    # Show ready background tasks that should be kicked off
    bg_shown = False
    for s in leveled:
        if s.task.status != TaskStatus.NOT_STARTED or not s.task.background:
            continue
        # Check all deps are done
        if all(tasks[d].status == TaskStatus.DONE for d in s.task.depends_on if d in tasks):
            if not bg_shown:
                console.print("\n  [dim]Kick off background job(s) first:[/dim]")
                bg_shown = True
            crit_flag = "  [bold yellow]CRIT[/bold yellow]" if s.is_critical else ""
            console.print(f"  [bold]{s.task.id}[/bold]  {s.task.name}  ({s.task.duration_hrs:.1f}h){crit_flag}")

    # Find the next foreground task
    for s in leveled:
        if s.task.status == TaskStatus.DONE:
            continue
        if s.task.background:
            continue
        console.print(f"\n  [green]Next up:[/green]")
        console.print(f"  [bold]{s.task.id}[/bold]  {s.task.name}  ({s.task.duration_hrs:.1f}h)")
        console.print(f"  Projected start: {s.earliest_start.strftime('%a %b %d, %H:%M')}")
        if s.is_critical:
            console.print(f"  [bold yellow]On the critical path[/bold yellow]")
        console.print(f"\n  Run [bold]dagr start {s.task.id}[/bold] to begin.")
        console.print()
        return

    console.print("[green]All tasks are done![/green]")


@app.command()
def daily(
    days: Annotated[int, typer.Option("--days", "-n", help="Number of working days to show")] = 10,
) -> None:
    """Show a day-by-day breakdown of scheduled tasks (single-person, serialized)."""
    from collections import defaultdict
    from dagr.scheduler import _skip_weekends_forward

    store = _get_store()
    config, tasks = store.load()
    config = _require_config(config)
    if not tasks:
        console.print("No tasks to schedule.")
        return

    try:
        leveled = resource_level(tasks, config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    crit_ids = {s.task.id for s in get_critical_path(
        calculate_schedule(tasks, config)
    )}

    from datetime import timedelta

    day_tasks: dict[str, list[dict]] = defaultdict(list)

    for s in leveled:
        if s.task.status == TaskStatus.DONE:
            continue

        task_start = s.earliest_start
        task_end = s.earliest_finish
        remaining = s.task.duration_hrs
        current = task_start

        while remaining > 0.01 and current < task_end:
            current = _skip_weekends_forward(current, config)
            day_key = current.strftime("%a %b %d")

            day_begin = current.replace(
                hour=config.day_start_hour,
                minute=config.day_start_minute,
                second=0, microsecond=0,
            )
            day_end_time = day_begin + timedelta(hours=config.hours_per_day)

            block_start = max(current, day_begin)
            block_end = min(task_end, day_end_time)
            hours_today = max(0, (block_end - block_start).total_seconds() / 3600)

            if hours_today > 0.01:
                day_tasks[day_key].append({
                    "id": s.task.id,
                    "name": s.task.name,
                    "hours": round(hours_today, 1),
                    "time": f"{block_start.strftime('%H:%M')}-{block_end.strftime('%H:%M')}",
                    "critical": s.task.id in crit_ids,
                    "bg": s.task.background,
                })
                remaining -= hours_today

            next_day = (current + timedelta(days=1)).replace(
                hour=config.day_start_hour,
                minute=config.day_start_minute,
                second=0, microsecond=0,
            )
            current = next_day

    shown = 0
    for day_key in sorted(day_tasks.keys(), key=lambda d: datetime.strptime(d, "%a %b %d")):
        if shown >= days:
            break

        entries = day_tasks[day_key]
        attended_hrs = sum(e["hours"] for e in entries if not e["bg"])
        bg_hrs = sum(e["hours"] for e in entries if e["bg"])

        summary = f"{attended_hrs:.1f}h"
        if bg_hrs > 0:
            summary += f" + {bg_hrs:.1f}h background"
        console.print(f"\n[bold underline]{day_key}[/bold underline]  ({summary})")

        table = Table(show_header=True, box=None, pad_edge=False)
        table.add_column("Time", style="dim")
        table.add_column("ID", style="bold")
        table.add_column("Task")
        table.add_column("Hours", justify="right")
        table.add_column("", justify="right")

        for e in sorted(entries, key=lambda x: x["time"]):
            flags = []
            if e["critical"]:
                flags.append("[bold yellow]CRIT[/bold yellow]")
            if e["bg"]:
                flags.append("[dim]BG[/dim]")
            table.add_row(e["time"], e["id"], e["name"], f"{e['hours']:.1f}h", " ".join(flags))

        console.print(table)
        shown += 1

    if not day_tasks:
        console.print("All tasks are done!")


@app.command()
def viz(
    output: Annotated[str, typer.Option("-o", "--output", help="Output file path")] = "dag.md",
    hide_done: bool = False,
) -> None:
    """Generate a Mermaid flowchart of the task DAG."""
    store = _get_store()
    config, tasks = store.load()
    if not tasks:
        console.print("No tasks to visualize.")
        return

    try:
        G = build_dag(tasks)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    # Optionally compute critical path for highlighting
    crit_ids: set[str] = set()
    if config is not None:
        try:
            scheduled = calculate_schedule(tasks, config)
            crit_ids = {s.task.id for s in get_critical_path(scheduled)}
        except ValueError:
            pass

    lines = ["```mermaid", "flowchart LR"]

    # Style classes
    lines.append("    classDef done fill:#2d6a4f,stroke:#1b4332,color:#d8f3dc")
    lines.append("    classDef inprog fill:#e76f51,stroke:#f4a261,color:#fff")
    lines.append("    classDef crit fill:#d4a373,stroke:#e76f51,color:#000,stroke-width:3px")
    lines.append("    classDef default fill:#457b9d,stroke:#1d3557,color:#f1faee")

    for tid, task in tasks.items():
        if hide_done and task.status == TaskStatus.DONE:
            continue

        # Sanitize name for mermaid (escape quotes)
        label = task.name.replace('"', "'")
        hrs = f"{task.duration_hrs:.1f}h"
        lines.append(f'    {tid}["{tid}: {label}<br/>{hrs}"]')

    # Edges
    for tid, task in tasks.items():
        if hide_done and task.status == TaskStatus.DONE:
            continue
        for dep in task.depends_on:
            if hide_done and dep in tasks and tasks[dep].status == TaskStatus.DONE:
                continue
            if dep in tasks:
                lines.append(f"    {dep} --> {tid}")

    # Apply styles
    done_ids = [tid for tid, t in tasks.items() if t.status == TaskStatus.DONE and not (hide_done)]
    inprog_ids = [tid for tid, t in tasks.items() if t.status == TaskStatus.IN_PROGRESS]
    # Critical but not done/in-progress
    crit_only = [tid for tid in crit_ids
                 if tid not in done_ids and tid not in inprog_ids
                 and not (hide_done and tasks[tid].status == TaskStatus.DONE)]

    if done_ids:
        lines.append(f"    class {','.join(done_ids)} done")
    if inprog_ids:
        lines.append(f"    class {','.join(inprog_ids)} inprog")
    if crit_only:
        lines.append(f"    class {','.join(crit_only)} crit")

    lines.append("```")

    from pathlib import Path
    Path(output).write_text("\n".join(lines) + "\n")
    console.print(f"[green]Wrote Mermaid diagram to {output}[/green]")
    console.print("Open in VS Code and use Markdown Preview (Cmd+Shift+V) to view.")
