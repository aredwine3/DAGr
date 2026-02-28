"""MCP server for DAGr — exposes task management tools to AI assistants."""

from __future__ import annotations

import json
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from dagr.models import Task, TaskStatus
from dagr.persistence import Store
from dagr.scheduler import (
    calculate_schedule,
    get_critical_path as scheduler_get_critical_path,
    resource_level,
    _working_hours_between,
)

mcp = FastMCP(
    "dagr",
    instructions="""\
DAGr is a DAG-powered project scheduler. Tasks have dependencies, durations \
(in working hours), and statuses (not_started, in_progress, done). The scheduler \
computes a critical path — the longest chain of dependencies that determines \
total project duration. Tasks on the critical path have zero slack; delaying \
them delays everything.

Key concepts:
- **Working hours**: Time is measured in working hours (default 8h/day, weekends skipped). \
A 10-hour task doesn't mean 10 wall-clock hours.
- **Critical path**: Tasks with zero or negative slack. Use get_critical_path to see them.
- **Slack**: How much a task can slip without affecting the project end date. Negative slack \
means the task chain is already behind its deadline.
- **Background tasks**: Run unattended (e.g. compute pipelines). They don't block hands-on \
work. get_next_task respects this — it recommends foreground work even when background \
tasks are in progress.
- **Dependencies**: A task can't start until all tasks it depends on are done. Dependencies \
are referenced by task ID (e.g. "T-5").

Two schedule views:
- **Unconstrained schedule** (get_schedule, get_critical_path): Assumes unlimited \
parallelism. Shows theoretical earliest/latest times and slack. Use this for critical \
path analysis and understanding which tasks have flexibility.
- **Resource-leveled schedule** (get_next_task, get_status projected completion): \
Realistic single-person timeline. Tasks are serialized — only one foreground task at \
a time, prioritized by criticality. Background tasks run in parallel. Use this for \
actual planning and "when will I finish?" questions.

Typical workflow:
1. Use add_task to create tasks with durations and dependencies
2. Use get_status for project overview (uses resource-leveled projected completion)
3. Use get_schedule for slack/critical path analysis (unconstrained)
4. Use get_next_task to find what to work on (resource-leveled)
5. Use start_task / complete_task to track progress
6. Use update_task when estimates change
7. Use import_tasks for bulk operations (e.g. from meeting notes)

When the user asks what to work on, prefer get_next_task. When they want an \
overview, use get_status. When they want details on a specific task, use get_task. \
When they ask "when will I finish?", use get_status (resource-leveled). When they \
ask about slack or critical path, use get_schedule or get_critical_path (unconstrained).\
""",
)


def _get_store() -> Store:
    return Store()


def _require_config(store: Store):
    config, tasks = store.load()
    if config is None:
        raise ValueError("Project not initialized. Run 'dagr init' first.")
    return config, tasks


def _task_to_dict(t: Task) -> dict:
    """Convert a task to a JSON-friendly dict with all fields."""
    d = {
        "id": t.id,
        "name": t.name,
        "duration_hrs": t.duration_hrs,
        "depends_on": t.depends_on,
        "tags": t.tags,
        "status": t.status.value,
        "background": t.background,
        "project": t.project,
        "flexible": t.flexible,
    }
    if t.deadline:
        d["deadline"] = t.deadline
    if t.proposed_start:
        d["proposed_start"] = t.proposed_start
    if t.actual_start:
        d["actual_start"] = t.actual_start
    if t.actual_end:
        d["actual_end"] = t.actual_end
    if t.notes:
        d["notes"] = t.notes
    return d


def _scheduled_to_dict(s) -> dict:
    """Convert a ScheduledTask to a JSON-friendly dict."""
    d = _task_to_dict(s.task)
    d["earliest_start"] = s.earliest_start.strftime("%a %b %d, %H:%M")
    d["earliest_finish"] = s.earliest_finish.strftime("%a %b %d, %H:%M")
    d["latest_start"] = s.latest_start.strftime("%a %b %d, %H:%M")
    d["latest_finish"] = s.latest_finish.strftime("%a %b %d, %H:%M")
    d["slack_hrs"] = round(s.total_slack_hrs, 1)
    d["is_critical"] = s.is_critical
    if s.task.deadline:
        dl = datetime.fromisoformat(s.task.deadline)
        d["is_late"] = s.earliest_finish > dl
    return d


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


@mcp.tool()
def add_task(
    name: str,
    duration_hrs: float,
    depends_on: list[str] | None = None,
    deadline: str | None = None,
    proposed_start: str | None = None,
    background: bool = False,
    project: str = "thesis",
    flexible: bool = False,
    tags: list[str] | None = None,
    notes: str | None = None,
) -> str:
    """Add a new task to the project.

    Args:
        name: Task name/title
        duration_hrs: Estimated duration in working hours
        depends_on: List of task IDs this depends on (e.g. ["T-1", "T-3"])
        deadline: Hard deadline date (YYYY-MM-DD)
        proposed_start: Earliest start date (YYYY-MM-DD)
        background: True if task runs unattended (e.g. compute pipeline)
        project: Organizational tag (e.g., thesis, life)
        flexible: True if task bypasses normal critical path calculation
        tags: List of context tags (e.g. ["errands", "low-energy"])
        notes: Markdown notes for the task
    """
    store = _get_store()
    config, tasks = store.load()
    tid = store.generate_id(tasks)

    deps = depends_on or []
    for dep in deps:
        if dep not in tasks:
            return f"Error: dependency {dep} not found."

    tasks[tid] = Task(
        id=tid,
        name=name,
        duration_hrs=duration_hrs,
        depends_on=deps,
        deadline=deadline,
        proposed_start=proposed_start,
        background=background,
        project=project,
        flexible=flexible,
        tags=tags or [],
        notes=notes,
    )
    store.save(config, tasks)
    return f"Added '{name}' as {tid}"


@mcp.tool()
def update_task(
    task_id: str,
    name: str | None = None,
    duration_hrs: float | None = None,
    deadline: str | None = None,
    proposed_start: str | None = None,
    background: bool | None = None,
    project: str | None = None,
    flexible: bool | None = None,
    add_dep: list[str] | None = None,
    remove_dep: list[str] | None = None,
    add_tag: list[str] | None = None,
    remove_tag: list[str] | None = None,
    notes: str | None = None,
) -> str:
    """Update fields of an existing task. Only provided fields are changed.

    Args:
        task_id: Task ID (e.g. "T-5")
        name: New task name
        duration_hrs: New duration in working hours
        deadline: New deadline (YYYY-MM-DD)
        proposed_start: New proposed start date (YYYY-MM-DD)
        background: Whether task runs unattended
        project: Organizational tag
        flexible: Whether task bypasses normal critical path calculation
        add_dep: Task IDs to add as dependencies
        remove_dep: Task IDs to remove from dependencies
        add_tag: Tags to add to the task
        remove_tag: Tags to remove from the task
        notes: New markdown notes
    """
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        return f"Error: task {task_id} not found."

    t = tasks[task_id]
    if name is not None:
        t.name = name
    if duration_hrs is not None:
        t.duration_hrs = duration_hrs
    if deadline is not None:
        t.deadline = deadline
    if proposed_start is not None:
        t.proposed_start = proposed_start
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
                return f"Error: dependency {dep} not found."
            if dep == task_id:
                return "Error: a task cannot depend on itself."
            if dep not in t.depends_on:
                t.depends_on.append(dep)

    if remove_dep:
        for dep in remove_dep:
            if dep in t.depends_on:
                t.depends_on.remove(dep)

    if add_tag:
        for tag in add_tag:
            if tag not in t.tags:
                t.tags.append(tag)
                
    if remove_tag:
        for tag in remove_tag:
            if tag in t.tags:
                t.tags.remove(tag)

    store.save(config, tasks)
    return f"Updated {task_id}."


@mcp.tool()
def delete_task(task_id: str) -> str:
    """Delete a task and remove it from all dependency lists.

    Args:
        task_id: Task ID to delete (e.g. "T-5")
    """
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        return f"Error: task {task_id} not found."

    del tasks[task_id]
    for t in tasks.values():
        if task_id in t.depends_on:
            t.depends_on.remove(task_id)

    store.save(config, tasks)
    return f"Deleted {task_id}."


@mcp.tool()
def start_task(task_id: str) -> str:
    """Mark a task as in-progress with current timestamp.

    Args:
        task_id: Task ID to start (e.g. "T-5")
    """
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        return f"Error: task {task_id} not found."

    t = tasks[task_id]
    t.status = TaskStatus.IN_PROGRESS
    t.actual_start = datetime.now().isoformat()
    store.save(config, tasks)
    return f"Started {task_id} at {t.actual_start}"


@mcp.tool()
def complete_task(task_id: str) -> str:
    """Mark a task as done with current timestamp. Shows actual vs estimated time.

    Args:
        task_id: Task ID to complete (e.g. "T-5")
    """
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        return f"Error: task {task_id} not found."

    t = tasks[task_id]
    was_started = t.actual_start is not None
    t.status = TaskStatus.DONE
    t.actual_end = datetime.now().isoformat()
    if not t.actual_start:
        t.actual_start = t.actual_end
    store.save(config, tasks)

    result = f"Completed {task_id} at {t.actual_end}"
    if was_started and config:
        start_dt = datetime.fromisoformat(t.actual_start)
        end_dt = datetime.fromisoformat(t.actual_end)
        actual_hrs = _working_hours_between(start_dt, end_dt, config)
        diff = actual_hrs - t.duration_hrs
        result += f"\n  Estimated: {t.duration_hrs:.1f}h  Actual: {actual_hrs:.1f}h  "
        if abs(diff) < 0.1:
            result += "Right on target"
        elif diff > 0:
            result += f"+{diff:.1f}h over"
        else:
            result += f"{diff:.1f}h under"
    elif not was_started:
        result += "\n  Note: task was not started first, so actual time cannot be measured."

    return result


@mcp.tool()
def set_task_status(task_id: str, status: str) -> str:
    """Override a task's status directly. Use when the normal start/done flow doesn't fit.

    Args:
        task_id: Task ID (e.g. "T-5")
        status: Target status: "not_started", "in_progress", or "done"
    """
    try:
        new_status = TaskStatus(status)
    except ValueError:
        valid = ", ".join(s.value for s in TaskStatus)
        return f"Error: invalid status '{status}'. Valid: {valid}"

    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        return f"Error: task {task_id} not found."

    t = tasks[task_id]
    if t.status == new_status:
        return f"{task_id} is already {new_status.value}."

    old = t.status.value
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
    return f"Set {task_id} from {old} to {new_status.value}."


@mcp.tool()
def import_tasks(tasks_json: list[dict]) -> str:
    """Bulk import tasks from a list of task objects.

    Each task needs "name" and "duration_hrs" at minimum. Dependencies can
    reference existing task IDs or names of other tasks in this batch.
    Include "id" to update an existing task instead of creating a new one.

    Args:
        tasks_json: List of task objects, e.g. [{"name": "Design API", "duration_hrs": 4}, {"name": "Build API", "duration_hrs": 8, "depends_on": ["Design API"]}]
    """
    store = _get_store()
    config, tasks = store.load()

    existing_name_to_id = {t.name: tid for tid, t in tasks.items()}
    added, updated = [], []
    import_name_to_id: dict[str, str] = {}

    for i, entry in enumerate(tasks_json):
        task_id = entry.get("id")

        if task_id and task_id in tasks:
            t = tasks[task_id]
            for field in ("name", "duration_hrs", "deadline", "proposed_start", "background", "project", "flexible", "tags", "notes"):
                if field in entry:
                    setattr(t, field, entry[field])
            import_name_to_id[t.name] = task_id
            updated.append(task_id)
        else:
            if "name" not in entry:
                return f"Error: task at index {i} missing 'name'."
            if "duration_hrs" not in entry:
                return f"Error: task '{entry['name']}' missing 'duration_hrs'."

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
                tags=entry.get("tags", []),
                notes=entry.get("notes"),
            )
            tasks[new_id] = t
            import_name_to_id[t.name] = new_id
            added.append(new_id)

    # Resolve dependencies
    for entry in tasks_json:
        raw_deps = entry.get("depends_on", [])
        if not raw_deps:
            continue

        task_id = entry.get("id")
        if not task_id or task_id not in tasks:
            task_id = import_name_to_id.get(entry.get("name", ""))
        if not task_id:
            continue

        resolved = []
        for dep in raw_deps:
            if dep in tasks:
                resolved.append(dep)
            elif dep in import_name_to_id:
                resolved.append(import_name_to_id[dep])
            elif dep in existing_name_to_id:
                resolved.append(existing_name_to_id[dep])
            else:
                return f"Error: task '{entry.get('name', task_id)}': unresolvable dependency '{dep}'"

        t = tasks[task_id]
        if task_id in updated:
            for d in resolved:
                if d not in t.depends_on:
                    t.depends_on.append(d)
        else:
            t.depends_on = resolved

    store.save(config, tasks)

    parts = []
    if added:
        parts.append(f"Added {len(added)} task(s): {', '.join(added)}")
    if updated:
        parts.append(f"Updated {len(updated)} task(s): {', '.join(updated)}")
    return ". ".join(parts) if parts else "Nothing to import."


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_task(task_id: str) -> str:
    """Get all details for a single task including schedule information.

    Args:
        task_id: Task ID (e.g. "T-5")
    """
    store = _get_store()
    config, tasks = store.load()
    if task_id not in tasks:
        return f"Error: task {task_id} not found."

    t = tasks[task_id]
    result = _task_to_dict(t)

    dependents = [tid for tid, task in tasks.items() if t.id in task.depends_on]
    result["blocks"] = dependents

    if config:
        try:
            scheduled = calculate_schedule(tasks, config)
            for s in scheduled:
                if s.task.id == task_id:
                    result["earliest_start"] = s.earliest_start.strftime("%a %b %d, %H:%M")
                    result["earliest_finish"] = s.earliest_finish.strftime("%a %b %d, %H:%M")
                    result["latest_start"] = s.latest_start.strftime("%a %b %d, %H:%M")
                    result["latest_finish"] = s.latest_finish.strftime("%a %b %d, %H:%M")
                    result["slack_hrs"] = round(s.total_slack_hrs, 1)
                    result["is_critical"] = s.is_critical
                    if t.deadline:
                        dl = datetime.fromisoformat(t.deadline)
                        result["is_late"] = s.earliest_finish > dl
                    break
        except ValueError:
            pass

    return json.dumps(result, indent=2)


@mcp.tool()
def list_tasks(status_filter: str | None = None, search: str | None = None, tag_filter: str | None = None) -> str:
    """List all tasks with optional filtering.

    Args:
        status_filter: Filter by status: "not_started", "in_progress", or "done"
        search: Filter by name (case-insensitive substring match)
        tag_filter: Filter by specific tag (e.g. "errands")
    """
    store = _get_store()
    config, tasks = store.load()
    if not tasks:
        return "No tasks found."

    filtered = list(tasks.values())

    if status_filter:
        try:
            sf = TaskStatus(status_filter)
        except ValueError:
            return f"Error: invalid status '{status_filter}'. Use: not_started, in_progress, done"
        filtered = [t for t in filtered if t.status == sf]

    if search:
        q = search.lower()
        filtered = [t for t in filtered if q in t.name.lower()]

    if tag_filter:
        q = tag_filter.lower()
        filtered = [t for t in filtered if any(q == tag.lower() for tag in t.tags)]

    if not filtered:
        return "No matching tasks."

    result = [_task_to_dict(t) for t in filtered]
    return json.dumps(result, indent=2)


@mcp.tool()
def get_schedule(remaining_only: bool = False) -> str:
    """Get the full unconstrained schedule with slack and critical path analysis.

    This shows theoretical times assuming unlimited parallelism — useful for
    understanding slack and critical path, NOT for realistic completion estimates.
    Use get_status for resource-leveled (single-person) projected completion.

    Args:
        remaining_only: If true, hide completed tasks
    """
    store = _get_store()
    config, tasks = _require_config(store)

    scheduled = calculate_schedule(tasks, config)
    result = []
    for s in scheduled:
        if remaining_only and s.task.status == TaskStatus.DONE:
            continue
        result.append(_scheduled_to_dict(s))

    return json.dumps(result, indent=2)


@mcp.tool()
def get_status() -> str:
    """Get project health dashboard: progress, projected completion (resource-leveled for
    a single person), late warnings, and critical path summary."""
    store = _get_store()
    config, tasks = _require_config(store)

    total = len(tasks)
    done_count = sum(1 for t in tasks.values() if t.status == TaskStatus.DONE)
    ip_count = sum(1 for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS)
    ns_count = total - done_count - ip_count
    total_hrs = sum(t.duration_hrs for t in tasks.values())
    done_hrs = sum(t.duration_hrs for t in tasks.values() if t.status == TaskStatus.DONE)
    remaining_hrs = total_hrs - done_hrs
    pct = (done_hrs / total_hrs * 100) if total_hrs > 0 else 0

    leveled = resource_level(tasks, config)
    scheduled = calculate_schedule(tasks, config)
    proj_end = max(s.earliest_finish for s in leveled)
    crit_path = scheduler_get_critical_path(scheduled)

    late_tasks = []
    for s in leveled:
        if s.task.status == TaskStatus.DONE:
            continue
        if s.task.deadline:
            dl = datetime.fromisoformat(s.task.deadline)
            if s.earliest_finish > dl:
                late_tasks.append({
                    "id": s.task.id,
                    "name": s.task.name,
                    "deadline": s.task.deadline,
                    "projected_finish": s.earliest_finish.strftime("%a %b %d"),
                })

    result = {
        "tasks_total": total,
        "tasks_done": done_count,
        "tasks_in_progress": ip_count,
        "tasks_not_started": ns_count,
        "hours_total": round(total_hrs, 1),
        "hours_done": round(done_hrs, 1),
        "hours_remaining": round(remaining_hrs, 1),
        "progress_pct": round(pct, 1),
        "projected_completion": proj_end.strftime("%a %b %d, %Y"),
        "critical_path_tasks": len(crit_path),
        "critical_path_hours": round(sum(s.task.duration_hrs for s in crit_path), 1),
        "late_tasks": late_tasks,
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def get_next_task() -> str:
    """Get the next task to work on. Respects background tasks — if only
    background tasks are in progress, still recommends the next foreground task.
    Uses the resource-leveled (single-person) schedule for realistic ordering."""
    store = _get_store()
    config, tasks = _require_config(store)


    # Don't include flexible or background tasks in the strict foreground path queue.
    # But DO return flexible tasks dynamically in their own array if they are ready!
    in_progress = [t for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS]
    fg_in_progress = [t for t in in_progress if not t.background and not t.flexible]
    bg_in_progress = [t for t in in_progress if t.background]
    flex_in_progress = [t for t in in_progress if t.flexible]

    result: dict = {}

    if bg_in_progress:
        result["background_running"] = [
            {"id": t.id, "name": t.name, "duration_hrs": t.duration_hrs}
            for t in bg_in_progress
        ]

    if flex_in_progress:
        result["flexible_running"] = [
            {"id": t.id, "name": t.name, "duration_hrs": t.duration_hrs, "project": t.project}
            for t in flex_in_progress
        ]

    if fg_in_progress:
        result["foreground_in_progress"] = [
            {"id": t.id, "name": t.name, "duration_hrs": t.duration_hrs,
             "actual_start": t.actual_start}
            for t in fg_in_progress
        ]
        return json.dumps(result, indent=2)

    leveled = resource_level(tasks, config)
    leveled.sort(key=lambda x: (x.earliest_start, x.total_slack_hrs))

    # Background tasks ready to kick off
    bg_ready = []
    for s in leveled:
        if s.task.status != TaskStatus.NOT_STARTED or not s.task.background:
            continue
        if all(tasks[d].status == TaskStatus.DONE for d in s.task.depends_on if d in tasks):
            bg_ready.append({
                "id": s.task.id, "name": s.task.name,
                "duration_hrs": s.task.duration_hrs, "is_critical": s.is_critical,
            })
    if bg_ready:
        result["background_ready"] = bg_ready

    # Flexible tasks ready to kick off (Dopamine Menu options)
    quick_wins = []
    low_energy = []
    hyperfocus = []
    other_flex = []
    
    for s in leveled:
        if s.task.status != TaskStatus.NOT_STARTED or not s.task.flexible:
            continue
        if all(tasks[d].status == TaskStatus.DONE for d in s.task.depends_on if d in tasks):
            t_data = {
                "id": s.task.id, "name": s.task.name, "project": s.task.project,
                "duration_hrs": s.task.duration_hrs, "tags": s.task.tags,
            }
            tags_lower = set(t.lower() for t in s.task.tags)
            
            if s.task.duration_hrs < 1.0 or "quick" in tags_lower:
                quick_wins.append(t_data)
            elif "low-energy" in tags_lower or "braindead" in tags_lower:
                low_energy.append(t_data)
            elif "hyperfocus" in tags_lower or "deep-work" in tags_lower:
                hyperfocus.append(t_data)
            else:
                other_flex.append(t_data)

    if any([quick_wins, low_energy, hyperfocus, other_flex]):
        result["dopamine_menu"] = {
            "quick_wins": quick_wins,
            "low_energy": low_energy,
            "hyperfocus": hyperfocus,
            "other_side_quests": other_flex
        }

    # Next foreground task
    for s in leveled:
        if s.task.status == TaskStatus.DONE or s.task.background or s.task.flexible:
            continue
        result["next_task"] = {
            "id": s.task.id,
            "name": s.task.name,
            "duration_hrs": s.task.duration_hrs,
            "projected_start": s.earliest_start.strftime("%a %b %d, %H:%M"),
            "is_critical": s.is_critical,
        }
        break
    else:
        if not bg_in_progress and not flex_in_progress:
            result["all_done"] = True
        else:
            result["all_foreground_done"] = True

    return json.dumps(result, indent=2)


@mcp.tool()
def get_critical_path() -> str:
    """Get the critical path — tasks with zero or negative slack that determine project duration.
    Uses the unconstrained schedule (slack analysis requires parallel assumption)."""
    store = _get_store()
    config, tasks = _require_config(store)

    scheduled = calculate_schedule(tasks, config)
    crit = scheduler_get_critical_path(scheduled)

    result = {
        "total_hours": round(sum(s.task.duration_hrs for s in crit), 1),
        "task_count": len(crit),
        "tasks": [_scheduled_to_dict(s) for s in crit],
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def set_day_capacity(date_iso: str, hours: float) -> str:
    """Set the working hour capacity for a specific day.
    
    Args:
        date_iso: Date in YYYY-MM-DD format (e.g. "2026-03-05")
        hours: Number of working hours for that day (e.g. 0.0 for day off, 12.0 for hyperfocus)
    """
    store = _get_store()
    config, tasks = store.load()
    if config is None:
        return "Error: Project not initialized. Run 'dagr init' first."
        
    try:
        datetime.strptime(date_iso, "%Y-%m-%d")
    except ValueError:
        return "Error: date_iso must be in YYYY-MM-DD format."
        
    config.capacity_overrides[date_iso] = hours
    store.save(config, tasks)
    return f"Set capacity for {date_iso} to {hours} hours."


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
