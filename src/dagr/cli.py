"""Typer CLI for DAGr."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from dagr.models import ProjectConfig, Task, TaskStatus
from dagr.persistence import Store
import networkx as nx

from dagr.scheduler import (
    ScheduledTask,
    add_working_hours,
    build_dag,
    calculate_schedule,
    get_critical_path,
    resource_level,
)

app = typer.Typer(
    name="dagr",
    help="DAG-powered project scheduler for the command line.",
    no_args_is_help=True,
)
console = Console()


def _get_store() -> Store:
    return Store()


def _complete_task_id(incomplete: str) -> list[str]:
    """Shell completion for task IDs. Matches against both ID and name."""
    try:
        store = Store()
        _, tasks = store.load()
    except Exception:
        return []
        
    results: list[str] = []
    q = incomplete.lower()
    
    for tid, task in tasks.items():
        # Match against either ID or Name
        if q in tid.lower() or q in task.name.lower():
            # IMPORTANT: Put the name FIRST so the shell prefix-matching works!
            # It will output: "DEFENSE (T-85)"
            results.append(f"{task.name} ({tid})")
            
    return results

def _parse_task_id(task_id_arg: str) -> str:
    """Extracts the ID if the user used the autocompleted 'Name (ID)' format."""
    # If it contains "(T-", we assume it's our formatted string
    if "(" in task_id_arg and task_id_arg.endswith(")"):
        # Split by '(' and take the last part, then remove the trailing ')'
        return task_id_arg.split("(")[-1].strip(")")
    
    # Otherwise, assume they just typed the ID normally (e.g. "T-85")
    return task_id_arg.strip()


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
    project: Annotated[str, typer.Option("--project", "-p", help="Organizational tag (e.g., thesis, life)")] = "thesis",
    flexible: Annotated[bool, typer.Option("--flexible", "--flex", help="Bypasses normal critical path calculation")] = False,
    tags: Annotated[Optional[list[str]], typer.Option("--tag", "-t", help="Context tags (e.g. low-energy)")] = None,
    notes: Annotated[Optional[str], typer.Option(help="Markdown notes for the task")] = None,
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

    # Validate that all dependencies exist
    for dep in expanded_deps:
        if dep not in tasks:
            console.print(f"[red]Dependency {dep} not found.[/red]")
            raise typer.Exit(1)

    tasks[tid] = Task(
        id=tid,
        name=name,
        duration_hrs=duration,
        depends_on=expanded_deps,
        deadline=deadline,
        proposed_start=start,
        background=background,
        project=project,
        flexible=flexible,
        tags=tags or [],
        notes=notes,
    )
    store.save(config, tasks)
    console.print(f"[green]Added '{name}' as {tid}[/green]")


@app.command("list")
def list_tasks(
    status_filter: Annotated[Optional[str], typer.Option("--status", "-s", help="Filter by status (not_started, in_progress, done)")] = None,
    search: Annotated[Optional[str], typer.Option("--search", "-q", help="Filter by name (case-insensitive substring match)")] = None,
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Filter by project")] = None,
    tag: Annotated[Optional[str], typer.Option("--tag", "-t", help="Filter by tag")] = None,
    csv: Annotated[Optional[str], typer.Option("--csv", help="Export list to CSV file")] = None,
) -> None:
    """List all tasks and their status."""
    store = _get_store()
    config, tasks = store.load()
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

    if project:
        p = project.lower()
        filtered = [t for t in filtered if t.project.lower() == p]

    if tag:
        qt = tag.lower()
        filtered = [t for t in filtered if any(qt == tg.lower() for tg in t.tags)]

    if not filtered:
        console.print("No tasks match the filter.")
        return

    # Calculate schedules to populate dates and slack
    unconstrained_map = {}
    leveled_map = {}
    if config:
        try:
            # Used for Slack and absolute LATE boundaries
            scheduled = calculate_schedule(tasks, config)
            for s in scheduled:
                unconstrained_map[s.task.id] = s
            
            # Used for realistic Projected Start and Projected Finish
            leveled = resource_level(tasks, config)
            for s in leveled:
                leveled_map[s.task.id] = s
        except ValueError:
            pass # If DAG has cycles, ignore and just print basic list

    if csv:
        import csv as csv_mod
        from pathlib import Path

        with Path(csv).open("w", newline="") as f:
            writer = csv_mod.writer(f)
            writer.writerow([
                "ID", "Name", "Project", "Hours", "Depends On", "Status", "BG",
                "Projected Start", "Projected Finish", "Slack (h)", "Deadline", "Flags"
            ])
            for t in filtered:
                p_start, p_finish, slack_str, flags_str = "", "", "", ""
                flags = []

                if t.id in unconstrained_map:
                    u_sched = unconstrained_map[t.id]
                    slack_str = f"{u_sched.total_slack_hrs:.1f}"
                    if u_sched.is_critical:
                        flags.append("CRITICAL")
                    
                    if t.deadline:
                        dl = datetime.fromisoformat(t.deadline)
                        if t.id in leveled_map:
                            if leveled_map[t.id].earliest_finish > dl:
                                flags.append("COMPLETED LATE" if t.status == TaskStatus.DONE else "PROJ. LATE")
                        else:
                            if u_sched.earliest_finish > dl:
                                flags.append("COMPLETED LATE" if t.status == TaskStatus.DONE else "LATE")
                
                if t.id in leveled_map:
                    l_sched = leveled_map[t.id]
                    p_start = l_sched.earliest_start.strftime("%Y-%m-%d %H:%M")
                    p_finish = l_sched.earliest_finish.strftime("%Y-%m-%d %H:%M")

                if t.flexible:
                    flags.append("FLEX")

                if flags:
                    flags_str = " | ".join(flags)

                writer.writerow([
                    t.id,
                    t.name,
                    t.project,
                    f"{t.duration_hrs:.1f}",
                    ", ".join(t.depends_on) or "",
                    t.status.value,
                    "bg" if t.background else "",
                    p_start,
                    p_finish,
                    slack_str,
                    t.deadline or "",
                    flags_str,
                ])
        console.print(f"[green]Exported {len(filtered)} tasks to {csv}[/green]")
        return

    table = Table(title="Tasks")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Project")
    table.add_column("Hours")
    table.add_column("Depends On")
    table.add_column("Status")
    table.add_column("BG")
    table.add_column("Projected Start")
    table.add_column("Projected Finish")
    table.add_column("Slack (h)")
    table.add_column("Deadline")
    table.add_column("Flags")

    for t in filtered:
        p_start, p_finish, slack_str, flags_str = "-", "-", "-", "-"
        flags = []
        style = None

        if t.id in unconstrained_map:
            u_sched = unconstrained_map[t.id]
            slack_str = f"{u_sched.total_slack_hrs:.1f}"
            if u_sched.is_critical:
                flags.append("CRITICAL")
                style = "bold yellow"
            
            if t.deadline:
                dl = datetime.fromisoformat(t.deadline)
                # We check lateness against the realistic constrained finish
                if t.id in leveled_map:
                    if leveled_map[t.id].earliest_finish > dl:
                        flags.append("COMPLETED LATE" if t.status == TaskStatus.DONE else "PROJ. LATE")
                        style = "bold red"
                else:
                    # Fallback to unconstrained if resource leveling wasn't possible
                    if u_sched.earliest_finish > dl:
                        flags.append("COMPLETED LATE" if t.status == TaskStatus.DONE else "LATE")
                        style = "bold red"
        
        if t.id in leveled_map:
            l_sched = leveled_map[t.id]
            p_start = l_sched.earliest_start.strftime("%b %d, %H:%M")
            p_finish = l_sched.earliest_finish.strftime("%b %d, %H:%M")

        if t.flexible:
            flags.append("FLEX")

        if flags:
            flags_str = " | ".join(flags)

        table.add_row(
            t.id,
            t.name,
            t.project,
            f"{t.duration_hrs:.1f}",
            ", ".join(t.depends_on) or "-",
            t.status.value,
            "bg" if t.background else "",
            p_start,
            p_finish,
            slack_str,
            t.deadline or "-",
            flags_str,
            style=style,
        )

    console.print(table)
    if status_filter or search:
        console.print(f"[dim]Showing {len(filtered)} of {len(tasks)} tasks[/dim]")


@app.command()
def update(
    task_id: Annotated[str, typer.Argument(autocompletion=_complete_task_id)],
    name: Annotated[Optional[str], typer.Option(help="New task name")] = None,
    duration: Annotated[Optional[float], typer.Option(help="New duration in hours")] = None,
    deadline: Annotated[Optional[str], typer.Option(help="New deadline (YYYY-MM-DD)")] = None,
    start: Annotated[Optional[str], typer.Option(help="New proposed start (YYYY-MM-DD)")] = None,
    background: Annotated[Optional[bool], typer.Option("--bg/--no-bg", help="Runs unattended (e.g. a pipeline)")] = None,
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Organizational tag (e.g., thesis, life)")] = None,
    flexible: Annotated[Optional[bool], typer.Option("--flexible/--no-flexible", "--flex/--no-flex", help="Bypasses normal critical path calculation")] = None,
    add_dep: Annotated[Optional[list[str]], typer.Option("--add-dep", help="Add a dependency (task ID)")] = None,
    remove_dep: Annotated[Optional[list[str]], typer.Option("--remove-dep", help="Remove a dependency (task ID)")] = None,
    add_tag: Annotated[Optional[list[str]], typer.Option("--add-tag", help="Add a tag to the task")] = None,
    remove_tag: Annotated[Optional[list[str]], typer.Option("--remove-tag", help="Remove a tag from the task")] = None,
    notes: Annotated[Optional[str], typer.Option(help="Update markdown notes for the task")] = None,
) -> None:
    """Update fields of an existing task."""
    task_id = _parse_task_id(task_id)
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
    if project is not None:
        t.project = project
    if flexible is not None:
        t.flexible = flexible
    if notes is not None:
        t.notes = notes

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

    if add_tag:
        for tg in add_tag:
            if tg not in t.tags:
                t.tags.append(tg)

    if remove_tag:
        for tg in remove_tag:
            if tg in t.tags:
                t.tags.remove(tg)

    store.save(config, tasks)
    console.print(f"[green]Updated {task_id}.[/green]")


@app.command()
def delete(task_id: Annotated[str, typer.Argument(autocompletion=_complete_task_id)]) -> None:
    """Delete a task and remove it from dependency lists."""
    task_id = _parse_task_id(task_id)
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
def show(task_id: Annotated[str, typer.Argument(autocompletion=_complete_task_id)]) -> None:
    """Show all details for a single task..."""
    task_id = _parse_task_id(task_id) # Extract "T-1" from "T-1: Setup Database"
    
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]

    console.print(f"\n[bold]{t.id}[/bold]  {t.name}")
    console.print(f"  Status:     {t.status.value}")
    console.print(f"  Duration:   {t.duration_hrs:.1f}h")
    console.print(f"  Project:    {t.project}")
    console.print(f"  Flexible:   {'yes' if t.flexible else 'no'}")
    if t.tags:
        console.print(f"  Tags:       {', '.join(t.tags)}")
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

    if t.notes:
        console.print("\n  [dim]── Notes ──[/dim]")
        for line in t.notes.splitlines():
            console.print(f"  {line}")

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
                    if s.total_slack_hrs < 0:
                        console.print(f"  Slack:           [bold red]{s.total_slack_hrs:.1f}h[/bold red]")
                    else:
                        console.print(f"  Slack:           {s.total_slack_hrs:.1f}h")
                    if s.is_critical:
                        console.print("  [bold yellow]On the critical path[/bold yellow]")
                    if t.deadline:
                        dl = datetime.fromisoformat(t.deadline)
                        if s.earliest_finish > dl:
                            days_late = (s.earliest_finish - dl).days
                            if t.status == TaskStatus.DONE:
                                console.print(f"  [bold red]Completed LATE by {days_late} day(s)[/bold red]")
                            else:
                                console.print(f"  [bold red]Projected LATE by {days_late} day(s)[/bold red]")
                    break
        except ValueError:
            pass

    console.print()


@app.command("start")
def start_task(task_id: Annotated[str, typer.Argument(autocompletion=_complete_task_id)]) -> None:
    """Mark a task as in-progress with current timestamp."""
    task_id = _parse_task_id(task_id)
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
def done(task_id: Annotated[str, typer.Argument(autocompletion=_complete_task_id)]) -> None:
    """Mark a task as done with current timestamp."""
    task_id = _parse_task_id(task_id)
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]
    was_started = t.actual_start is not None
    t.status = TaskStatus.DONE
    t.actual_end = datetime.now().isoformat()
    if not t.actual_start:
        t.actual_start = t.actual_end
    store.save(config, tasks)
    console.print(f"[green]Completed {task_id} at {t.actual_end}[/green]")

    if not was_started:
        console.print(f"  [yellow]Note: task was not started first (dagr start {task_id}), so actual time cannot be measured.[/yellow]")
    elif t.actual_start:
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
def reset(task_id: Annotated[str, typer.Argument(autocompletion=_complete_task_id)]) -> None:
    """Reset a task back to not_started (undo start/done)."""
    task_id = _parse_task_id(task_id)
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


@app.command("set-status")
def set_status(
    task_id: Annotated[str, typer.Argument(autocompletion=_complete_task_id)],
    status: Annotated[str, typer.Argument(help="Target status: not_started, in_progress, done")],
) -> None:
    """Override a task's status directly.

    Use this to correct status when the normal start/done/reset flow doesn't
    fit — e.g. reopening a done task or pausing an in-progress one.
    """
    task_id = _parse_task_id(task_id)
    try:
        new_status = TaskStatus(status)
    except ValueError:
        valid = ", ".join(s.value for s in TaskStatus)
        console.print(f"[red]Invalid status '{status}'. Valid statuses: {valid}[/red]")
        raise typer.Exit(1)

    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        console.print(f"[red]Task {task_id} not found.[/red]")
        raise typer.Exit(1)

    t = tasks[task_id]
    old_status = t.status

    if t.status == new_status:
        console.print(f"{task_id} is already {new_status.value}.")
        return

    t.status = new_status

    if new_status == TaskStatus.NOT_STARTED:
        t.actual_start = None
        t.actual_end = None
    elif new_status == TaskStatus.IN_PROGRESS:
        t.actual_end = None
        if not t.actual_start:
            t.actual_start = datetime.now().isoformat()
    elif new_status == TaskStatus.DONE:
        if not t.actual_end:
            t.actual_end = datetime.now().isoformat()
        if not t.actual_start:
            t.actual_start = t.actual_end

    store.save(config, tasks)
    console.print(f"[green]Set {task_id} from {old_status.value} to {new_status.value}.[/green]")


@app.command("import")
def import_tasks(
    file: Annotated[str, typer.Argument(help="JSON file path, or - for stdin")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview changes without saving")] = False,
) -> None:
    """Import tasks from a JSON file (or stdin with -).

    The JSON should have a "tasks" array. Each task needs at minimum "name" and
    "duration_hrs". Dependencies can reference existing task IDs (e.g. "T-5") or
    names of other tasks in the same import batch.

    To update an existing task, include its "id" field (e.g. "id": "T-5").

    Example JSON:

        {"tasks": [

            {"name": "Design API", "duration_hrs": 4},

            {"name": "Build API", "duration_hrs": 8, "depends_on": ["Design API"]}

        ]}
    """
    import json
    import sys

    # Read input
    if file == "-":
        raw_text = sys.stdin.read()
    else:
        from pathlib import Path

        path = Path(file)
        if not path.exists():
            console.print(f"[red]File not found: {file}[/red]")
            raise typer.Exit(1)
        raw_text = path.read_text()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON: {e}[/red]")
        raise typer.Exit(1)

    if "tasks" not in data or not isinstance(data["tasks"], list):
        console.print('[red]JSON must have a "tasks" array.[/red]')
        raise typer.Exit(1)

    store = _get_store()
    config, tasks = store.load()

    # Build name→ID lookup for existing tasks
    existing_name_to_id: dict[str, str] = {}
    for tid, t in tasks.items():
        existing_name_to_id[t.name] = tid

    # First pass: create/update tasks and build name→ID mapping for new tasks
    added: list[str] = []
    updated: list[str] = []
    import_name_to_id: dict[str, str] = {}

    for i, entry in enumerate(data["tasks"]):
        if not isinstance(entry, dict):
            console.print(f"[red]Task at index {i} is not an object.[/red]")
            raise typer.Exit(1)

        task_id = entry.get("id")

        if task_id and task_id in tasks:
            # Update mode
            t = tasks[task_id]
            if "name" in entry:
                t.name = entry["name"]
            if "duration_hrs" in entry:
                t.duration_hrs = entry["duration_hrs"]
            if "deadline" in entry:
                t.deadline = entry["deadline"]
            if "proposed_start" in entry:
                t.proposed_start = entry["proposed_start"]
            if "background" in entry:
                t.background = entry["background"]
            if "project" in entry:
                t.project = entry["project"]
            if "flexible" in entry:
                t.flexible = entry["flexible"]
            if "notes" in entry:
                t.notes = entry["notes"]
            # depends_on handled in second pass
            import_name_to_id[t.name] = task_id
            updated.append(task_id)
        else:
            # Create mode — validate required fields
            if "name" not in entry:
                console.print(f'[red]Task at index {i} missing required "name" field.[/red]')
                raise typer.Exit(1)
            if "duration_hrs" not in entry:
                console.print(f'[red]Task "{entry["name"]}" missing required "duration_hrs" field.[/red]')
                raise typer.Exit(1)

            new_id = store.generate_id(tasks)
            t = Task(
                id=new_id,
                name=entry["name"],
                duration_hrs=entry["duration_hrs"],
                deadline=entry.get("deadline"),
                proposed_start=entry.get("proposed_start"),
                background=entry.get("background", False),
                project=entry.get("project", "thesis"),
                flexible=entry.get("flexible", False),
                notes=entry.get("notes"),
            )
            tasks[new_id] = t
            import_name_to_id[t.name] = new_id
            added.append(new_id)

    # Second pass: resolve dependencies
    for entry in data["tasks"]:
        raw_deps = entry.get("depends_on", [])
        if not raw_deps:
            continue

        # Find the task we created/updated for this entry
        task_id = entry.get("id")
        if not task_id or task_id not in tasks:
            task_id = import_name_to_id.get(entry.get("name", ""))
        if not task_id:
            continue

        resolved_deps: list[str] = []
        for dep in raw_deps:
            if dep in tasks:
                # Direct task ID reference
                resolved_deps.append(dep)
            elif dep in import_name_to_id:
                # Name of a task in this import batch
                resolved_deps.append(import_name_to_id[dep])
            elif dep in existing_name_to_id:
                # Name of an existing task in the project
                resolved_deps.append(existing_name_to_id[dep])
            else:
                console.print(f'[red]Task "{entry.get("name", task_id)}": unresolvable dependency "{dep}"[/red]')
                raise typer.Exit(1)

        t = tasks[task_id]
        if task_id in updated:
            # For updates, merge with existing deps
            for d in resolved_deps:
                if d not in t.depends_on:
                    t.depends_on.append(d)
        else:
            t.depends_on = resolved_deps

    # Dry run: show what would happen
    if dry_run:
        console.print("\n[bold]Dry run — no changes saved[/bold]\n")
        if added:
            console.print(f"[green]Would add {len(added)} task(s):[/green]")
            for tid in added:
                t = tasks[tid]
                deps = f" → depends on {', '.join(t.depends_on)}" if t.depends_on else ""
                console.print(f"  {tid}  {t.name}  ({t.duration_hrs:.1f}h){deps}")
        if updated:
            console.print(f"[yellow]Would update {len(updated)} task(s):[/yellow]")
            for tid in updated:
                t = tasks[tid]
                console.print(f"  {tid}  {t.name}")
        if not added and not updated:
            console.print("[dim]Nothing to import.[/dim]")
        console.print()
        return

    store.save(config, tasks)

    if added:
        console.print(f"[green]Added {len(added)} task(s):[/green]")
        for tid in added:
            t = tasks[tid]
            console.print(f"  {tid}  {t.name}  ({t.duration_hrs:.1f}h)")
    if updated:
        console.print(f"[yellow]Updated {len(updated)} task(s):[/yellow]")
        for tid in updated:
            t = tasks[tid]
            console.print(f"  {tid}  {t.name}")
    if not added and not updated:
        console.print("[dim]Nothing to import.[/dim]")


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
                        flags.append("COMPLETED LATE" if s.task.status == TaskStatus.DONE else "LATE")
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
            if "LATE" in flags or "COMPLETED LATE" in flags
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
def critical_path(
    sort: Annotated[str, typer.Option(help="Sort order: topo (default), chrono, chain")] = "topo",
) -> None:
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

    if sort == "chrono":
        crit.sort(key=lambda s: s.earliest_start)
        _print_critical_table(crit, title="Critical Path (chronological)")
    elif sort == "chain":
        G = build_dag(tasks)
        crit_ids = {s.task.id for s in crit}
        crit_map = {s.task.id: s for s in crit}

        # Build a subgraph of only critical tasks and find connected chains
        sub = G.subgraph(crit_ids).copy()
        chains: list[list[str]] = []
        visited: set[str] = set()

        # Find roots (no critical predecessors) and trace chains from each
        for tid in nx.topological_sort(sub):
            if tid in visited:
                continue
            chain: list[str] = []
            _trace_chain(sub, tid, visited, chain)
            chains.append(chain)

        for i, chain in enumerate(chains, 1):
            chain_tasks = [crit_map[tid] for tid in chain]
            chain_hrs = sum(s.task.duration_hrs for s in chain_tasks)
            console.print(f"\n[bold]Chain {i}[/bold]  ({chain_hrs:.1f}h)")
            _print_critical_table(chain_tasks, title=None)

        console.print(f"\n[dim]{len(chains)} chain(s), {len(crit)} critical tasks total[/dim]")
    else:
        _print_critical_table(crit, title="Critical Path")

    total_hrs = sum(s.task.duration_hrs for s in crit)
    console.print(f"\nTotal critical path duration: [bold]{total_hrs:.1f}[/bold] hours")


def _trace_chain(G: "nx.DiGraph", start: str, visited: set[str], chain: list[str]) -> None:
    """Walk a chain of critical tasks depth-first, collecting in topological order."""
    visited.add(start)
    chain.append(start)
    for succ in G.successors(start):
        if succ not in visited:
            _trace_chain(G, succ, visited, chain)


def _print_critical_table(tasks_list: list[ScheduledTask], title: str | None) -> None:
    """Print a critical path table."""
    table = Table(title=title)
    table.add_column("ID")
    table.add_column("Task Name")
    table.add_column("Hours")
    table.add_column("Slack")
    table.add_column("Start")
    table.add_column("End")

    for s in tasks_list:
        slack_str = f"{s.total_slack_hrs:.1f}"
        style = "bold red" if s.total_slack_hrs < 0 else None
        table.add_row(
            s.task.id,
            s.task.name,
            f"{s.task.duration_hrs:.1f}",
            slack_str,
            s.earliest_start.strftime("%b %d, %H:%M"),
            s.earliest_finish.strftime("%b %d, %H:%M"),
            style=style,
        )

    console.print(table)


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
        if s.task.status == TaskStatus.DONE:
            continue
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

    # If a foreground task is already in progress, show that and return
    in_progress = [t for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS]
    fg_in_progress = [t for t in in_progress if not t.background and not t.flexible]
    bg_in_progress = [t for t in in_progress if t.background]
    flex_in_progress = [t for t in in_progress if t.flexible]

    if fg_in_progress:
        for t in fg_in_progress:
            console.print(f"\n  [yellow]In progress:[/yellow]  [bold]{t.id}[/bold]  {t.name}  ({t.duration_hrs:.1f}h)")
            if t.actual_start:
                console.print(f"    Started: {t.actual_start}")
        for t in bg_in_progress:
            console.print(f"\n  [dim]Running in background:[/dim]  [bold]{t.id}[/bold]  {t.name}  ({t.duration_hrs:.1f}h)")
        for t in flex_in_progress:
            console.print(f"\n  [dim]Flexible task in progress:[/dim]  [bold]{t.id}[/bold]  {t.name}  ({t.project})")
        console.print()
        return

    try:
        leveled = resource_level(tasks, config)
        leveled.sort(key=lambda x: (x.earliest_start, x.total_slack_hrs))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    # Show background tasks that are running or ready to kick off
    for t in bg_in_progress:
        console.print(f"\n  [dim]Running in background:[/dim]  [bold]{t.id}[/bold]  {t.name}  ({t.duration_hrs:.1f}h)")
    for t in flex_in_progress:
        console.print(f"\n  [dim]Flexible task in progress:[/dim]  [bold]{t.id}[/bold]  {t.name}  ({t.project})")

    ready_bg = []
    ready_flex = []

    for s in leveled:
        if s.task.status != TaskStatus.NOT_STARTED:
            continue
        if all(tasks[d].status == TaskStatus.DONE for d in s.task.depends_on if d in tasks):
            if s.task.background:
                ready_bg.append(s)
            elif s.task.flexible:
                ready_flex.append(s)

    if ready_bg:
        console.print("\n  [dim]Kick off background job(s) first:[/dim]")
        for s in ready_bg:
            crit_flag = "  [bold yellow]CRIT[/bold yellow]" if s.is_critical else ""
            console.print(f"  [bold]{s.task.id}[/bold]  {s.task.name}  ({s.task.duration_hrs:.1f}h){crit_flag}")

    if ready_flex:
        quick_wins = []
        low_energy = []
        hyperfocus = []
        others = []

        for s in ready_flex:
            tags_lower = set(t.lower() for t in s.task.tags)
            if s.task.duration_hrs < 1.0 or "quick" in tags_lower:
                quick_wins.append(s)
            elif "low-energy" in tags_lower or "braindead" in tags_lower:
                low_energy.append(s)
            elif "hyperfocus" in tags_lower or "deep-work" in tags_lower:
                hyperfocus.append(s)
            else:
                others.append(s)

        console.print("\n  [bold cyan]⚡ Dopamine Menu (Flexible Tasks)[/bold cyan]")
        
        if quick_wins:
            console.print("\n  [bold]🏃 Quick Wins[/bold]")
            for s in quick_wins:
                console.print(f"    [bold]{s.task.id}[/bold]  {s.task.name}  [dim]({s.task.duration_hrs:.1f}h, {s.task.project})[/dim]")
                
        if low_energy:
            console.print("\n  [bold]🔋 Low Energy[/bold]")
            for s in low_energy:
                console.print(f"    [bold]{s.task.id}[/bold]  {s.task.name}  [dim]({s.task.duration_hrs:.1f}h, {s.task.project})[/dim]")
                
        if hyperfocus:
            console.print("\n  [bold]🧠 Hyperfocus[/bold]")
            for s in hyperfocus:
                console.print(f"    [bold]{s.task.id}[/bold]  {s.task.name}  [dim]({s.task.duration_hrs:.1f}h, {s.task.project})[/dim]")
                
        if others:
            console.print("\n  [bold]🗺️  Other Side Quests[/bold]")
            for s in others:
                console.print(f"    [bold]{s.task.id}[/bold]  {s.task.name}  [dim]({s.task.duration_hrs:.1f}h, {s.task.project})[/dim]")

    # Find the next foreground task
    for s in leveled:
        if s.task.status == TaskStatus.DONE:
            continue
        if s.task.background or s.task.flexible:
            continue
        console.print("\n  [green]Next up:[/green]")
        console.print(f"  [bold]{s.task.id}[/bold]  {s.task.name}  ({s.task.duration_hrs:.1f}h)")
        console.print(f"  Projected start: {s.earliest_start.strftime('%a %b %d, %H:%M')}")
        if s.is_critical:
            console.print("  [bold yellow]On the critical path[/bold yellow]")
        console.print(f"\n  Run [bold]dagr start {s.task.id}[/bold] to begin.")
        console.print()
        return

    if not bg_in_progress:
        console.print("[green]All tasks are done![/green]")
    else:
        console.print("\n  [green]All hands-on work is done! Waiting on background tasks.[/green]")
    console.print()


@app.command()
def today() -> None:
    """Morning briefing: status summary, today's tasks, and what to do next."""
    from collections import defaultdict
    from datetime import timedelta

    from dagr.scheduler import _skip_weekends_forward

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

    # --- Status summary ---
    total = len(tasks)
    done_count = sum(1 for t in tasks.values() if t.status == TaskStatus.DONE)
    ip_count = sum(1 for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS)
    ns_count = total - done_count - ip_count
    total_hrs = sum(t.duration_hrs for t in tasks.values())
    done_hrs = sum(t.duration_hrs for t in tasks.values() if t.status == TaskStatus.DONE)
    remaining_hrs = total_hrs - done_hrs
    pct = (done_hrs / total_hrs * 100) if total_hrs > 0 else 0
    proj_end = max(s.earliest_finish for s in leveled)

    bar_width = 30
    filled = int(bar_width * pct / 100)
    bar = f"[green]{'█' * filled}[/green][dim]{'░' * (bar_width - filled)}[/dim]"

    console.print(f"\n[bold underline]Good morning![/bold underline]\n")
    console.print(f"  {bar} {pct:.0f}%  ({done_count}/{total} tasks, {remaining_hrs:.0f}h remaining)")
    console.print(f"  Projected completion: [bold]{proj_end.strftime('%a %b %d, %Y')}[/bold]")

    # Late warnings (compact)
    late_tasks = []
    for s in leveled:
        if s.task.status == TaskStatus.DONE:
            continue
        if s.task.deadline:
            dl = datetime.fromisoformat(s.task.deadline)
            if s.earliest_finish > dl:
                late_tasks.append((s.task.id, s.task.name, s.task.deadline))
    if late_tasks:
        console.print(f"\n  [bold red]⚠ {len(late_tasks)} task(s) at risk of being late[/bold red]")

    in_progress = [t for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS]
    if in_progress:
        console.print(f"\n[bold underline]In progress[/bold underline]")
        for t in in_progress:
            label = ""
            if t.background: label += " [dim](BG)[/dim]"
            if t.flexible: label += " [dim](FLEX)[/dim]"
            
            console.print(f"  [bold]{t.id}[/bold]  {t.name}  ({t.duration_hrs:.1f}h){label}")

    # --- Background and Flexible tasks to kick off ---
    crit_ids = {s.task.id for s in get_critical_path(scheduled)}
    bg_ready = []
    flex_ready = []
    
    for s in leveled:
        if s.task.status != TaskStatus.NOT_STARTED:
            continue
        if all(tasks[d].status == TaskStatus.DONE for d in s.task.depends_on if d in tasks):
            if s.task.background:
                bg_ready.append(s)
            elif s.task.flexible:
                flex_ready.append(s)
                
    if bg_ready:
        console.print(f"\n[bold underline]Kick off background jobs[/bold underline]")
        for s in bg_ready:
            crit_flag = "  [bold yellow]CRIT[/bold yellow]" if s.task.id in crit_ids else ""
            console.print(f"  [bold]{s.task.id}[/bold]  {s.task.name}  ({s.task.duration_hrs:.1f}h){crit_flag}")
            
    if flex_ready:
        console.print(f"\n[bold underline]Flexible / Side Tasks Ready[/bold underline]")
        for s in flex_ready:
            console.print(f"  [bold]{s.task.id}[/bold]  {s.task.name}  [dim]({s.task.project})[/dim]")

    # --- Today's tasks (from resource-leveled schedule) ---
    now = datetime.now()
    today_key = now.strftime("%Y-%m-%d")

    day_tasks: list[dict] = []
    for s in leveled:
        if s.task.status == TaskStatus.DONE:
            continue
        task_start = s.earliest_start
        task_end = s.earliest_finish
        remaining = s.task.duration_hrs
        current = task_start

        while remaining > 0.01 and current < task_end:
            current = _skip_weekends_forward(current, config)
            day_key = current.strftime("%Y-%m-%d")

            day_begin = current.replace(
                hour=config.day_start_hour, minute=config.day_start_minute,
                second=0, microsecond=0,
            )
            day_end_time = day_begin + timedelta(hours=config.hours_per_day)

            block_start = max(current, day_begin)
            block_end = min(task_end, day_end_time)
            hours_today = max(0, (block_end - block_start).total_seconds() / 3600)

            if hours_today > 0.01 and day_key == today_key:
                day_tasks.append({
                    "id": s.task.id,
                    "name": s.task.name,
                    "hours": round(hours_today, 1),
                    "time": f"{block_start.strftime('%H:%M')}-{block_end.strftime('%H:%M')}",
                    "critical": s.task.id in crit_ids,
                    "bg": s.task.background,
                    "flexible": s.task.flexible,
                })
                remaining -= hours_today

            next_day = (current + timedelta(days=1)).replace(
                hour=config.day_start_hour, minute=config.day_start_minute,
                second=0, microsecond=0,
            )
            current = next_day

    if day_tasks:
        attended_hrs = sum(e["hours"] for e in day_tasks if not e["bg"])
        bg_hrs = sum(e["hours"] for e in day_tasks if e["bg"])
        summary = f"{attended_hrs:.1f}h"
        if bg_hrs > 0:
            summary += f" + {bg_hrs:.1f}h background"

        console.print(f"\n[bold underline]Today's tasks[/bold underline]  ({summary})")
        table = Table(show_header=True, box=None, pad_edge=False)
        table.add_column("Time", style="dim")
        table.add_column("ID", style="bold")
        table.add_column("Task")
        table.add_column("Hours", justify="right")
        table.add_column("", justify="right")

        for e in sorted(day_tasks, key=lambda x: x["time"]):
            flags = []
            if e["critical"]:
                flags.append("[bold yellow]CRIT[/bold yellow]")
            if e["bg"]:
                flags.append("[dim]BG[/dim]")
            if e["flexible"]:
                flags.append("[dim]FLEX[/dim]")
            table.add_row(e["time"], e["id"], e["name"], f"{e['hours']:.1f}h", " ".join(flags))
        console.print(table)
    else:
        console.print("\n  [dim]No tasks scheduled for today.[/dim]")

    fg_in_progress = [t for t in in_progress if not t.background and not t.flexible]
    if not fg_in_progress:
        for s in leveled:
            if s.task.status == TaskStatus.DONE or s.task.background or s.task.flexible:
                continue
            console.print(f"\n  Run [bold]dagr start {s.task.id}[/bold] to begin.")
            break

    console.print()


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
    day_labels: dict[str, str] = {}  # ISO date -> display label

    for s in leveled:
        if s.task.status == TaskStatus.DONE:
            continue

        task_start = s.earliest_start
        task_end = s.earliest_finish
        remaining = s.task.duration_hrs
        current = task_start

        while remaining > 0.01 and current < task_end:
            current = _skip_weekends_forward(current, config)
            day_key = current.strftime("%Y-%m-%d")
            day_labels[day_key] = current.strftime("%a %b %d")

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
                    "flexible": s.task.flexible,
                })
                remaining -= hours_today

            next_day = (current + timedelta(days=1)).replace(
                hour=config.day_start_hour,
                minute=config.day_start_minute,
                second=0, microsecond=0,
            )
            current = next_day

    shown = 0
    for day_key in sorted(day_tasks.keys()):
        if shown >= days:
            break

        entries = day_tasks[day_key]
        attended_hrs = sum(e["hours"] for e in entries if not e["bg"])
        bg_hrs = sum(e["hours"] for e in entries if e["bg"])

        summary = f"{attended_hrs:.1f}h"
        if bg_hrs > 0:
            summary += f" + {bg_hrs:.1f}h background"
        console.print(f"\n[bold underline]{day_labels[day_key]}[/bold underline]  ({summary})")

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
            if e["flexible"]:
                flags.append("[dim]FLEX[/dim]")
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

@app.command()
def viz_html(
    output: Annotated[str, typer.Option("-o", "--output", help="Output file path")] = "dag.html",
    hide_done: bool = False,
) -> None:
    """Generate an interactive PyVis HTML flowchart of the task DAG."""
    from pyvis.network import Network
    
    store = _get_store()
    config, tasks = store.load()
    if not tasks:
        console.print("No tasks to visualize.")
        return

    # Get critical path
    crit_ids: set[str] = set()
    if config is not None:
        try:
            scheduled = calculate_schedule(tasks, config)
            crit_ids = {s.task.id for s in get_critical_path(scheduled)}
        except ValueError:
            pass

    # Initialize PyVis network
    net = Network(height="800px", width="100%", directed=True, notebook=False)

    # Add nodes
    for tid, task in tasks.items():
        if hide_done and task.status == TaskStatus.DONE:
            continue
            
        if task.status == TaskStatus.DONE:
            color = "#2d6a4f"  
            font_color = "#d8f3dc"
        elif task.status == TaskStatus.IN_PROGRESS:
            color = "#e76f51"  
            font_color = "#ffffff"
        elif tid in crit_ids:
            color = "#d4a373"  
            font_color = "#000000"
        else:
            color = "#457b9d"  
            font_color = "#f1faee"
            
        # Clean label with line breaks
        label = f"{tid}\n{task.name}\n{task.duration_hrs:.1f}h"
        
        net.add_node(
            tid, 
            label=label, 
            title=task.name,
            color=color, 
            shape="box",
            font={"color": font_color, "face": "Helvetica", "size": 14}
        )

    # Add edges
    for tid, task in tasks.items():
        if hide_done and task.status == TaskStatus.DONE:
            continue
        for dep in task.depends_on:
            if hide_done and dep in tasks and tasks[dep].status == TaskStatus.DONE:
                continue
            if dep in tasks:
                net.add_edge(dep, tid, color="#bdc3c7")

    # Use PyVis layout settings that strictly mimic Mermaid's Dagre engine
    net.set_options("""
    var options = {
      "nodes": {
        "margin": 10,
        "widthConstraint": {
          "maximum": 220
        }
      },
      "edges": {
        "smooth": {
          "type": "cubicBezier",
          "forceDirection": "horizontal",
          "roundness": 0.4
        },
        "arrows": {
          "to": {"enabled": true, "scaleFactor": 0.6}
        }
      },
      "layout": {
        "hierarchical": {
          "enabled": true,
          "direction": "LR",
          "sortMethod": "directed",
          "levelSeparation": 280,
          "nodeSpacing": 120,
          "treeSpacing": 120,
          "blockShifting": true,
          "edgeMinimization": true,
          "parentCentralization": true
        }
      },
      "physics": {
        "enabled": false
      },
      "interaction": {
        "navigationButtons": true,
        "dragNodes": true,
        "hover": true
      }
    }
    """)

    net.save_graph(output)
    console.print(f"[green]Wrote structured, interactive HTML diagram to {output}[/green]")

@app.command()
def capacity(
    date_str: Annotated[str, typer.Argument(help="Date in YYYY-MM-DD format (e.g. 2026-03-05)")],
    hours: Annotated[float, typer.Argument(help="Working hours capacity for this date")],
) -> None:
    """Set the working hour capacity for a specific day."""
    store = _get_store()
    config, tasks = store.load()
    config = _require_config(config)

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        console.print(f"[red]Invalid date format '{date_str}'. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    config.capacity_overrides[date_str] = hours
    store.save(config, tasks)
    console.print(f"[green]Set capacity for {date_str} to {hours} hours.[/green]")

if __name__ == "__main__":
    app()