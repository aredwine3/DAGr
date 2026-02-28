"""Task model and status definitions."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class TaskStatus(enum.StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    DONE = "done"


@dataclass
class ProjectConfig:
    """Project-level settings stored alongside tasks."""

    start_date: datetime
    hours_per_day: float = 8.0
    day_start_hour: int = 9
    day_start_minute: int = 0
    skip_weekends: bool = True
    capacity_overrides: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "hours_per_day": self.hours_per_day,
            "day_start_hour": self.day_start_hour,
            "day_start_minute": self.day_start_minute,
            "skip_weekends": self.skip_weekends,
            "capacity_overrides": self.capacity_overrides,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProjectConfig:
        return cls(
            start_date=datetime.fromisoformat(d["start_date"]),
            hours_per_day=d.get("hours_per_day", 8.0),
            day_start_hour=d.get("day_start_hour", 9),
            day_start_minute=d.get("day_start_minute", 0),
            skip_weekends=d.get("skip_weekends", True),
            capacity_overrides=d.get("capacity_overrides", {}),
        )


@dataclass
class Task:
    """A single schedulable task."""

    id: str
    name: str
    duration_hrs: float
    depends_on: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    deadline: str | None = None
    proposed_start: str | None = None
    status: TaskStatus = TaskStatus.NOT_STARTED
    actual_start: str | None = None
    actual_end: str | None = None
    background: bool = False  # runs unattended; doesn't block the person
    project: str = "thesis"  # organizational tag (e.g., thesis, life, chores)
    flexible: bool = False  # if true, tasks bypass normal critical path calculation
    notes: str | None = None  # optional markdown notes

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "duration_hrs": self.duration_hrs,
            "depends_on": self.depends_on,
            "tags": self.tags,
            "deadline": self.deadline,
            "proposed_start": self.proposed_start,
            "status": self.status.value,
            "actual_start": self.actual_start,
            "actual_end": self.actual_end,
            "background": self.background,
            "project": self.project,
            "flexible": self.flexible,
        }
        if self.notes is not None:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, task_id: str, d: dict) -> Task:
        return cls(
            id=task_id,
            name=d["name"],
            duration_hrs=d["duration_hrs"],
            depends_on=d.get("depends_on", []),
            tags=d.get("tags", []),
            deadline=d.get("deadline"),
            proposed_start=d.get("proposed_start"),
            status=TaskStatus(d.get("status", "not_started")),
            actual_start=d.get("actual_start"),
            actual_end=d.get("actual_end"),
            background=d.get("background", False),
            project=d.get("project", "thesis"),
            flexible=d.get("flexible", False),
            notes=d.get("notes"),
        )
