from datetime import datetime
from dagr.models import ProjectConfig, Task, TaskStatus
from dagr.scheduler import add_working_hours, resource_level

def test_capacity_overrides():
    config = ProjectConfig(
        start_date=datetime.fromisoformat("2026-02-23"), # a Monday
        hours_per_day=8.0,
        day_start_hour=9,
        day_start_minute=0,
        skip_weekends=True,
        capacity_overrides={"2026-02-28": 4.0} # Saturday with 4 hours
    )
    
    start = datetime(2026, 2, 27, 13, 0) # Friday 1pm
    # Add 12 hours. 
    # Friday: 4 hours left (until 5pm) -> 8 hours remaining
    # Saturday: custom capacity 4 hours -> 4 hours remaining
    # Sunday: skipped
    # Monday: starts 9am, +4 hours -> 1pm

    end = add_working_hours(start, 12.0, config)
    assert end == datetime(2026, 3, 2, 13, 0)

def test_resource_level_flexible_tasks():
    config = ProjectConfig(start_date=datetime.fromisoformat("2026-02-23"))
    tasks = {
        "T-1": Task("T-1", "Main Task", 8.0, status=TaskStatus.NOT_STARTED),
        "T-2": Task("T-2", "Flex Task", 4.0, status=TaskStatus.NOT_STARTED, flexible=True)
    }
    
    leveled = resource_level(tasks, config)
    
    sched_t1 = next(s for s in leveled if s.task.id == "T-1")
    sched_t2 = next(s for s in leveled if s.task.id == "T-2")
    
    # Flexible task should have infinite slack and not block others
    assert sched_t2.total_slack_hrs == float("inf")
    
    # Both start at the same time because T-2 doesn't block T-1 in resource leveling (it evaluates independently)
    assert sched_t1.earliest_start == sched_t2.earliest_start
