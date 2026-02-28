from datetime import datetime
from dagr.models import Task, TaskStatus, ProjectConfig

def test_task_serialization():
    t = Task(
        id="T-1",
        name="Test",
        duration_hrs=5.0,
        status=TaskStatus.IN_PROGRESS,
        project="life",
        flexible=True,
        tags=["urgent", "errand"],
        depends_on=["T-2"]
    )
    d = t.to_dict()
    assert d["project"] == "life"
    assert d["flexible"] is True
    assert d["tags"] == ["urgent", "errand"]
    
    t2 = Task.from_dict("T-1", d)
    assert t2.project == "life"
    assert t2.flexible is True
    assert t2.tags == ["urgent", "errand"]

def test_project_config_serialization():
    config = ProjectConfig(
        start_date=datetime.fromisoformat("2026-02-23"),
        capacity_overrides={"2026-03-05": 12.0, "2026-03-06": 0.0}
    )
    d = config.to_dict()
    assert d["capacity_overrides"]["2026-03-05"] == 12.0
    
    c2 = ProjectConfig.from_dict(d)
    assert c2.capacity_overrides["2026-03-05"] == 12.0
    assert c2.capacity_overrides["2026-03-06"] == 0.0
