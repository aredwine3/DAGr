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

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "hours_per_day": self.hours_per_day,
            "day_start_hour": self.day_start_hour,
            "day_start_minute": self.day_start_minute,
            "skip_weekends": self.skip_weekends,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProjectConfig:
        return cls(
            start_date=datetime.fromisoformat(d["start_date"]),
            hours_per_day=d.get("hours_per_day", 8.0),
            day_start_hour=d.get("day_start_hour", 9),
            day_start_minute=d.get("day_start_minute", 0),
            skip_weekends=d.get("skip_weekends", True),
        )


@dataclass
class Task:
    """A single schedulable task."""

    id: str
    name: str
    duration_hrs: float
    depends_on: list[str] = field(default_factory=list)
    deadline: str | None = None
    proposed_start: str | None = None
    status: TaskStatus = TaskStatus.NOT_STARTED
    actual_start: str | None = None
    actual_end: str | None = None
    background: bool = False  # runs unattended; doesn't block the person

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_hrs": self.duration_hrs,
            "depends_on": self.depends_on,
            "deadline": self.deadline,
            "proposed_start": self.proposed_start,
            "status": self.status.value,
            "actual_start": self.actual_start,
            "actual_end": self.actual_end,
            "background": self.background,
        }

    @classmethod
    def from_dict(cls, task_id: str, d: dict) -> Task:
        return cls(
            id=task_id,
            name=d["name"],
            duration_hrs=d["duration_hrs"],
            depends_on=d.get("depends_on", []),
            deadline=d.get("deadline"),
            proposed_start=d.get("proposed_start"),
            status=TaskStatus(d.get("status", "not_started")),
            actual_start=d.get("actual_start"),
            actual_end=d.get("actual_end"),
            background=d.get("background", False),
        )
