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
dagr add "Generate Q1 & Q2 Draft" --duration 6 --depends T-3,T-4,T-5 --deadline 2026-03-02
```

Each task gets an auto-generated ID (`T-1`, `T-2`, ...).

- `--depends T-1,T-2,T-3` — this task can't start until those finish (comma-separated or repeat the flag)
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

```
                              Schedule
┏━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ ID   ┃ Task Name                ┃ Hours ┃ Status      ┃ Start         ┃ End           ┃ Slack  ┃ Deadline   ┃ Flags    ┃
┡━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━┩
│ T-1  │ Run Association Analysis │  10.0 │ not_started │ Feb 23, 09:00 │ Feb 24, 11:00 │   0.0  │ -          │ CRITICAL │
│ T-2  │ Interpret results        │  10.0 │ not_started │ Feb 24, 11:00 │ Feb 25, 13:00 │   0.0  │ -          │ CRITICAL │
│ T-3  │ Generate visualizations  │   3.0 │ not_started │ Feb 25, 13:00 │ Feb 25, 16:00 │   0.0  │ -          │ CRITICAL │
│ T-4  │ Pilot study plotting     │   1.5 │ not_started │ Feb 23, 09:00 │ Feb 23, 10:30 │  21.5  │ -          │ -        │
│ T-5  │ Behavior Analysis        │   8.0 │ not_started │ Feb 23, 09:00 │ Feb 23, 17:00 │  15.0  │ -          │ -        │
│ T-6  │ Generate Q1 & Q2 Draft   │   6.0 │ not_started │ Feb 25, 16:00 │ Feb 26, 14:00 │   0.0  │ 2026-03-02 │ CRITICAL │
└──────┴──────────────────────────┴───────┴─────────────┴───────────────┴───────────────┴────────┴────────────┴──────────┘
```

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

```
Mon Feb 23  (8.0h)
Time         ID    Task                      Hours
09:00-13:00  T-5   Behavior Analysis          4.0h
13:00-14:30  T-4   Pilot study plotting       1.5h
14:30-17:00  T-5   Behavior Analysis          2.5h

Tue Feb 24  (8.0h + 8.0h background)
Time         ID    Task                       Hours
09:00-17:00  T-1   Run Association Analysis   8.0h  CRIT BG

Wed Feb 25  (8.0h + 2.0h background)
Time         ID    Task                       Hours
09:00-15:00  T-2   Interpret results          6.0h  CRIT
15:00-17:00  T-1   Run Association Analysis   2.0h  CRIT BG
```

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

All downstream tasks automatically recalculate. Add or remove dependencies after the fact:

```bash
dagr update T-6 --add-dep T-10       # T-6 now waits for T-10
dagr update T-6 --remove-dep T-3     # T-6 no longer waits for T-3
```

Mark a task as background if it runs unattended:

```bash
dagr update T-5 --bg       # mark as background
dagr update T-5 --no-bg    # revert to attended
```

View all details for a specific task:

```bash
dagr show T-1
```

```
T-1  Run Association Analysis
  Status:     in_progress
  Duration:   10.0h
  Background: yes
  Depends on: —
  Blocks:     T-2

  ── Scheduled ──
  Earliest start:  Mon Feb 23, 09:00
  Earliest finish: Tue Feb 24, 11:00
  Latest start:    Mon Feb 23, 09:00
  Latest finish:   Tue Feb 24, 11:00
  Slack:           0.0h
  On the critical path
```

Filter the task list:

```bash
dagr list --status not_started       # only not-started tasks
dagr list --search "Q2"              # search by name
dagr list -s done -q "histology"     # combine filters
```

### 7. Check project health

```bash
dagr status
```

Shows a dashboard with tasks done/remaining, hours completed, a progress bar, projected completion date, and any tasks at risk of missing their deadline. The projected completion uses the resource-leveled (single-person) schedule, so it reflects when you'll realistically finish -- not an optimistic parallel estimate.

```
Project Status

  Tasks:  2 done  1 in progress  3 remaining  (6 total)
  Hours:  11.5h done  26.5h remaining  (38.0h total)
  Progress: █████████░░░░░░░░░░░░░░░░░░░░░░ 30%
  Projected completion: Thu Feb 26, 2026

  ⚠ 1 task(s) projected LATE:
    T-6 Generate Q1 & Q2 Draft — deadline 2026-03-02, projected Mar 04

  Critical path: 4 tasks, 29.0h total
```

### 8. Morning briefing

```bash
dagr today
```

One command to start your day. Shows your progress, any late warnings, background jobs to kick off, today's task schedule, and what to start next.

```
Good morning!

  █████████░░░░░░░░░░░░░░░░░░░░░░ 30%  (2/6 tasks, 26.5h remaining)
  Projected completion: Thu Feb 26, 2026

Kick off background jobs
  T-1  Run Association Analysis  (10.0h)  CRIT

Today's schedule
  Time         ID    Task                  Hours
  09:00-13:00  T-5   Behavior Analysis      4.0h
  13:00-14:30  T-4   Pilot study plotting   1.5h
  14:30-17:00  T-5   Behavior Analysis      2.5h

  Run dagr start T-5 to begin.
```

### 9. What should I work on?

```bash
dagr next
```

Shows the single most important task to work on right now (lowest slack, highest urgency). Also surfaces any background jobs that are ready to kick off. If a task is already in progress, it reminds you of that instead.

```
Kick off background job(s) first:
  T-1  Run Association Analysis  (10.0h)  CRIT

  Next up:
  T-5  Behavior Analysis  (8.0h)
  Projected start: Mon Feb 23, 09:00

  Run dagr start T-5 to begin.
```

### 10. Export for sharing

```bash
dagr schedule --csv schedule.csv           # full schedule to CSV
dagr schedule --remaining --csv todo.csv   # only remaining tasks
```

## All Commands

| Command | Description |
|---|---|
| `dagr init` | Set project start date and working hours config |
| `dagr add` | Add a new task (`-d`, `--depends`, `--deadline`, `--start`, `--bg`) |
| `dagr list` | Show all tasks (`--status`, `--search` to filter) |
| `dagr update <ID>` | Update task fields (`--add-dep`, `--remove-dep` for dependencies) |
| `dagr delete <ID>` | Remove a task and clean up dependency references |
| `dagr show <ID>` | View all details for a task (deps, schedule, slack) |
| `dagr start <ID>` | Mark a task as in-progress (records timestamp) |
| `dagr done <ID>` | Mark a task as completed (shows actual vs estimated time) |
| `dagr reset <ID>` | Reset a task back to not_started (undo start/done) |
| `dagr schedule` | Full schedule table (`--remaining` to hide done, `--csv` to export) |
| `dagr critical-path` | Show only the critical path tasks and total duration |
| `dagr status` | Project health dashboard (progress, deadlines, critical path) |
| `dagr next` | Show the single next task you should work on |
| `dagr today` | Morning briefing: status + today's tasks + what to do next |
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
