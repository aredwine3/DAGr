"""JSON file persistence for tasks and project config."""

from __future__ import annotations

import json
from pathlib import Path

from dagr.models import ProjectConfig, Task

DEFAULT_DB_FILE = "thesis_tasks.json"


class Store:
    """Reads and writes the project database (JSON file)."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_FILE):
        self.db_path = Path(db_path)

    def load(self) -> tuple[ProjectConfig | None, dict[str, Task]]:
        """Return (config_or_None, {task_id: Task})."""
        if not self.db_path.exists():
            return None, {}

        raw = json.loads(self.db_path.read_text())

        # New format: {"config": {...}, "tasks": {...}}
        # Old format: {"T-1": {...}, "T-2": {...}}  (flat dict of tasks)
        config = None
        if "config" in raw:
            config = ProjectConfig.from_dict(raw["config"])

        task_source = raw.get("tasks", raw)
        tasks: dict[str, Task] = {}
        for tid, tdata in task_source.items():
            if tid == "config":
                continue
            tasks[tid] = Task.from_dict(tid, tdata)

        return config, tasks

    def save(self, config: ProjectConfig | None, tasks: dict[str, Task]) -> None:
        """Persist config + tasks to disk."""
        raw: dict = {}
        if config is not None:
            raw["config"] = config.to_dict()
        raw["tasks"] = {tid: t.to_dict() for tid, t in tasks.items()}
        self.db_path.write_text(json.dumps(raw, indent=4))

    def generate_id(self, tasks: dict[str, Task]) -> str:
        """Generate the next T-N id."""
        existing = [int(k.split("-")[1]) for k in tasks if k.startswith("T-")]
        next_num = max(existing, default=0) + 1
        return f"T-{next_num}"
