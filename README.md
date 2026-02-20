# DAGr - Thesis Timeline Optimizer

A dynamic project management CLI that uses a **Directed Acyclic Graph (DAG)** to schedule tasks, track progress, and identify your critical path. Built for computationally heavy academic work where tasks have dependencies and timelines shift constantly.

## Installation

Requires Python 3.13+. Install with [uv](https://docs.astral.sh/uv/):

```bash
uv venv && uv pip install -e .
```

This makes the `dagr` command available in your virtualenv. Activate it with `source .venv/bin/activate`, or run commands directly via `.venv/bin/dagr`.

## Quick Start

### 1. Initialize your project

Set your project start date and working hours:

```bash
dagr init --start 2026-02-23
```

This defaults to 8-hour work days starting at 9:00 AM, skipping weekends. Customize with:

```bash
dagr init --start 2026-02-23 --hours-per-day 6 --day-start 10:00 --no-skip-weekends
```

### 2. Add tasks

```bash
dagr add "Run Association of Pain Phenotype" --duration 10
dagr add "Interpret Association results" --duration 10 --depends T-1
dagr add "Generate initial visualizations" --duration 3 --depends T-2
dagr add "ABX pilot study results & plotting" --duration 1.5
dagr add "Q2 Behavior Analysis" --duration 8
dagr add "Generate Q1 & Q2 Draft" --duration 6 --depends T-3 --depends T-4 --depends T-5 --deadline 2026-03-02
```

Each task gets an auto-generated ID (`T-1`, `T-2`, ...).

- `--depends T-1` — this task can't start until T-1 finishes (repeat for multiple dependencies)
- `--deadline 2026-03-02` — hard due date; flagged as LATE if the schedule overshoots it
- `--start 2026-02-25` — earliest date work can begin (e.g., waiting on lab access)
- `--bg` — marks a task as **background** (runs unattended, like a compute pipeline)

### 3. View your schedule

```bash
dagr schedule
```

Outputs a table showing each task's computed start/end times (respecting working hours and weekends), slack, and flags:

- **CRITICAL** — on the critical path (zero slack; any delay here delays everything)
- **LATE** — projected to finish after its deadline

### 4. See your daily plan

```bash
dagr daily
```

Shows a realistic day-by-day breakdown with tasks serialized for a single person. The resource leveler schedules one task at a time, always picking the most critical (lowest slack) ready task next. Background tasks run in parallel and don't block your hands-on work.

```bash
dagr daily -n 5    # show only the next 5 working days
```

Each day shows:
- Attended work hours and background hours separately
- Time blocks for each task
- **CRIT** flag for critical path tasks
- **BG** flag for background tasks

### 5. Track progress

When you start working on a task:

```bash
dagr start T-1
```

When you finish it:

```bash
dagr done T-1
```

These record actual timestamps. When you complete a task, DAGr shows how your actual time compared to the estimate:

```
Completed T-1 at 2026-02-24T15:30:00
  Estimated: 10.0h  Actual: 12.5h  +2.5h over
```

The scheduler uses real completion times for finished tasks instead of estimates, so downstream projections get more accurate as you go.

Made a mistake? Reset a task back to not-started:

```bash
dagr reset T-1
```

### 6. Adapt as things change

Update a task's duration when reality doesn't match the estimate:

```bash
dagr update T-2 --duration 15
```

All downstream tasks automatically recalculate. Mark a task as background if it runs unattended:

```bash
dagr update T-5 --bg       # mark as background
dagr update T-5 --no-bg    # revert to attended
```

### 7. Check project health

```bash
dagr status
```

Shows a dashboard with tasks done/remaining, hours completed, a progress bar, projected completion date, and any tasks at risk of missing their deadline. The projected completion uses the resource-leveled (single-person) schedule, so it reflects when you'll realistically finish -- not an optimistic parallel estimate.

### 8. What should I work on?

```bash
dagr next
```

Shows the single most important task to work on right now (lowest slack, highest urgency). If a task is already in progress, it reminds you of that instead.

### 9. Export for sharing

```bash
dagr schedule --csv schedule.csv           # full schedule to CSV
dagr schedule --remaining --csv todo.csv   # only remaining tasks
```

## All Commands

| Command | Description |
|---|---|
| `dagr init` | Set project start date and working hours config |
| `dagr add` | Add a new task (`-d`, `--depends`, `--deadline`, `--start`, `--bg`) |
| `dagr list` | Show all tasks with status and background flag |
| `dagr update <ID>` | Update a task's name, duration, deadline, start, or bg status |
| `dagr delete <ID>` | Remove a task and clean up dependency references |
| `dagr start <ID>` | Mark a task as in-progress (records timestamp) |
| `dagr done <ID>` | Mark a task as completed (shows actual vs estimated time) |
| `dagr reset <ID>` | Reset a task back to not_started (undo start/done) |
| `dagr schedule` | Full schedule table (`--remaining` to hide done, `--csv` to export) |
| `dagr critical-path` | Show only the critical path tasks and total duration |
| `dagr status` | Project health dashboard (progress, deadlines, critical path) |
| `dagr next` | Show the single next task you should work on |
| `dagr daily` | Day-by-day plan, serialized for one person (`-n` for day count) |
| `dagr viz` | Generate a Mermaid flowchart of the DAG (`-o`, `--hide-done`) |

Run `dagr <command> --help` for detailed options on any command.

## How It Works

### The DAG

Tasks are **nodes** in a directed graph. Dependencies are **edges**. A topological sort determines a valid execution order where no task starts before its prerequisites finish.

### Working Hours Model

Time is measured in **working hours**, not wall-clock hours. A 10-hour task starting Friday at 3 PM doesn't end at 1 AM Saturday -- it ends Tuesday at 1 PM (assuming 8h days, weekends skipped). This makes projected dates realistic.

### Critical Path Analysis

The scheduler runs a **forward pass** (earliest start/finish for each task) and a **backward pass** (latest start/finish without delaying the project). The difference is **slack** -- how much a task can slip without affecting the end date. Tasks with zero slack form the **critical path**: the longest chain of dependencies that determines total project duration.

### Resource Leveling

`dagr schedule` shows the *unconstrained* schedule (unlimited parallelism), useful for slack and critical path analysis. `dagr daily` applies **resource leveling** for a single person: independent tasks are serialized, with the most critical (lowest slack) task scheduled first. Background tasks bypass this constraint and run in parallel.

### Background Tasks

Some tasks run unattended (compute pipelines, overnight builds). Mark them with `--bg` and the resource leveler will schedule other hands-on work alongside them. Downstream tasks that depend on a background task still wait for it to finish.

### Persistence

Everything is stored in a single `thesis_tasks.json` file in the working directory. The file is human-readable and can be version-controlled if needed.

## Visualize the DAG

```bash
dagr viz                    # full DAG to dag.md
dagr viz --hide-done        # only remaining tasks
dagr viz -o my-graph.md     # custom output file
```

Color-coded nodes:
- **Green** -- done
- **Orange** -- in progress
- **Tan with thick border** -- critical path
- **Blue** -- default

Open the output file in VS Code and use Markdown Preview (`Cmd+Shift+V`) to render the Mermaid diagram.

## The Workflow

1. **Brain dump** -- Add all your tasks with rough hour estimates
2. **Link** -- Declare dependencies between tasks
3. **Plan** -- Run `dagr daily` to see your realistic day-by-day plan
4. **Execute** -- Use `dagr start` and `dagr done` as you work
5. **Adapt** -- When estimates are wrong, `dagr update` the duration and re-check
