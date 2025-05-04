# Scheduler Testing Suite

This directory contains tests for the scheduler functionality of the Screen Machine application. The tests are structured to cover different aspects of the scheduler system, with particular focus on the variable sharing and scheduler lifecycle management.

## Test Structure

Tests are organized into several categories:

- **Unit Tests**: Test individual components in isolation
- **Integration Tests**: Test interactions between multiple components
- **Behavioral Tests**: Test complete workflow behaviors
- **Snapshot Tests**: Test state persistence
- **Mock Tests**: Test component behavior using mocks

## Test Files

- `conftest.py`: Contains shared pytest fixtures for all tests
- **Unit Tests**:
  - `unit/test_vars_registry.py`: Tests the variable registry functions
  - `unit/test_instruction_handlers.py`: Tests all instruction handlers individually
- **Integration Tests**:
  - `integration/test_var_instruction_handlers.py`: Tests variable import/export handlers
  - `integration/test_instruction_execution.py`: Tests integration between instruction handlers and execution flow
  - `integration/test_schedule_execution.py`: Tests executing complete schedules including generate and animate
- **Behavioral Tests**:
  - `behavioral/test_scheduler_stop.py`: Tests the scheduler stop functionality
- **Snapshot Tests**:
  - `snapshot/test_scheduler_state.py`: Tests scheduler state persistence
- **Mock Tests**:
  - `mock/test_scheduler_events.py`: Tests scheduler event handling using mocks

## Mock Services for Testing

The test suite uses mock services to avoid calling expensive external APIs:

1. **Service Factory**: The system uses a service factory pattern (`routes/service_factory.py`) that switches between real and mock implementations based on the `TESTING` environment variable.

2. **Mock Generation Service**: A mock implementation of the image generation service that returns predictable results without calling real APIs.

3. **Mock Animation Service**: A mock implementation of the animation service for testing.

To use the mock services in tests, simply include the `enable_testing_mode` fixture:

```python
@pytest.mark.usefixtures("enable_testing_mode")
def test_something_that_needs_generation():
    # The test will use mock services
    ...
```

## Instruction Type Coverage

The tests cover all instruction types defined in the schema:

| Instruction Type     | Test File                                  |
|----------------------|-------------------------------------------|
| `set_var`            | `unit/test_instruction_handlers.py`        |
| `import_var`         | `integration/test_var_instruction_handlers.py` |
| `export_var`         | `integration/test_var_instruction_handlers.py` |
| `generate`           | `unit/test_instruction_handlers.py`, `integration/test_schedule_execution.py` |
| `devise_prompt`      | `unit/test_instruction_handlers.py`        |
| `display`            | `unit/test_instruction_handlers.py`        |
| `random_choice`      | `unit/test_instruction_handlers.py`        |
| `device-media-sync`  | `unit/test_instruction_handlers.py`        |
| `device-wake`        | `unit/test_instruction_handlers.py`        |
| `device-sleep`       | `unit/test_instruction_handlers.py`        |
| `wait`               | `unit/test_instruction_handlers.py`        |
| `animate`            | `unit/test_instruction_handlers.py`, `integration/test_schedule_execution.py` |
| `unload`             | `unit/test_instruction_handlers.py`        |
| `stop`               | `behavioral/test_scheduler_stop.py`        |

## Schedule Testing

The test suite includes various predefined schedule fixtures that can be used for testing:

- `test_schedule_basic`: A simple schedule with just initial actions
- `test_schedule_with_final`: A schedule with both initial and final actions
- `test_schedule_with_trigger`: A schedule with a day-of-week trigger
- `test_schedule_with_event`: A schedule with an event trigger
- `test_schedule_generate_animate`: A schedule that includes generate and animate instructions

## Running Tests

### Prerequisites

Ensure you have `pytest` and `pytest-asyncio` installed:

```bash
pip install pytest pytest-asyncio pytest-cov
```

### Running All Tests

From the project root directory:

```bash
python -m pytest tests/
```

### Running Specific Test Categories

```bash
# Run unit tests only
python -m pytest tests/unit/

# Run integration tests only
python -m pytest tests/integration/

# Run a specific test file
python -m pytest tests/unit/test_vars_registry.py
```

### Run with Verbose Output

```bash
python -m pytest -v tests/
```

### Run with Coverage Report

```bash
python -m pytest --cov=routes.scheduler --cov=routes.scheduler_utils --cov=routes.scheduler_handlers tests/
```

## Adding New Tests

When adding new tests:

1. Place them in the appropriate category directory
2. Follow the naming convention: `test_*.py` for files and `test_*` for functions
3. Use existing fixtures from `conftest.py` where possible
4. Ensure proper isolation by using `monkeypatch` and mock fixtures
5. Write tests that target specific functionality rather than trying to test everything at once

## Common Fixtures

| Fixture | Description |
|---------|-------------|
| `temp_registry_file` | Creates a temporary registry file for testing |
| `mock_scheduler_context` | Creates a mock scheduler context |
| `clean_scheduler_state` | Provides clean scheduler state dictionaries |
| `setup_test_destination` | Sets up a test destination with basic context |
| `current_time` | Returns the current time |
| `mock_scheduler_storage_path` | Mocks the scheduler storage path to use a temp directory |
| `test_schedule_with_instructions` | Create a test schedule with initial, trigger, and final instructions |
| `mock_now` | Returns the current datetime (alternative to current_time) |
| `base_context` | Provides a basic context with pre-populated variables |
| `output_list` | Provides an empty output list for logging |
| `enable_testing_mode` | Enables mock services by setting the TESTING environment variable | 