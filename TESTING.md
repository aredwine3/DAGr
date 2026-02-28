# Testing DAGr

DAGr includes a suite of automated tests using `pytest` to ensure that core scheduling logic, data serialization, and command-line interfaces function correctly.

## Setting Up and Running Tests

The test suite requires `pytest` and uses `typer.testing.CliRunner` to execute CLI commands in an isolated environment.

1. Ensure `pytest` is installed in your project path:
   ```bash
   uv add --dev pytest
   ```
2. Run the test suite:
   ```bash
   uv run pytest
   ```

## What the Tests Cover

The tests are broken down into three main files under the `/tests/` directory natively testing DAGr's core functionality, specifically focusing on flexible tasks, tags, and capacity overrides.

### 1. Model Serialization (`tests/test_models.py`)
These tests ensure that our core data objects can correctly save and load from the JSON state file (`dagr.json`) without data loss.
- **`test_task_serialization`**: Validates that custom properties on the `Task` object, such as `tags` (e.g., `#low-energy`), `project` tracking, and the `flexible` flag, are accurately serialized to dictionary format and recreated identically during deserialization.
- **`test_project_config_serialization`**: Ensures that the `ProjectConfig` dataclass correctly maintains date-specific `capacity_overrides` mapping dicts across save states. 

### 2. Time & Scheduling Logic (`tests/test_scheduler.py`)
These tests validate that DAGr respects constraints during time calculations.
- **`test_capacity_overrides`**: Verifies that the internal `add_working_hours` core pacing function respects custom capacities. Specifically, it tests adding hours across a weekend where a typically skipped Saturday has been manually given 4 hours of capacity to confirm the scheduler uses it.
- **`test_resource_level_flexible_tasks`**: Tests the `resource_level` single-resource constraint algorithm. It sets up a traditional blocking task and a parallel `flexible` task, asserting that the flexible task is assigned infinite slack and does not push back the start times of critical-path tasks.

### 3. Command Line Interface (`tests/test_cli.py`)
These tests simulate end-to-end interactions by running `dagr` terminal commands via Typer's isolated runner.
- **`test_dagr_next_dopamine_menu`**: End-to-end simulation of the Context Tags and Dopamine Menu. It initializes a temporary project, adds a main task, and several side tasks tagged with specific context tags (`quick`, `low-energy`, `hyperfocus`, `errands`). It then runs `dagr next` and validates that the standard output successfully groups and parses the tasks under the formatted "⚡ Dopamine Menu" categories.
