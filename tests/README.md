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
- **Integration Tests**:
  - `integration/test_var_instruction_handlers.py`: Tests variable import/export handlers
- **Behavioral Tests**:
  - `behavioral/test_scheduler_stop.py`: Tests the scheduler stop functionality
- **Snapshot Tests**:
  - `snapshot/test_scheduler_state.py`: Tests scheduler state persistence
- **Mock Tests**:
  - `mock/test_scheduler_events.py`: Tests scheduler event handling using mocks

## Running Tests

### Prerequisites

Ensure you have `pytest` and `pytest-asyncio` installed:

```bash
pip install pytest pytest-asyncio
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
pip install pytest-cov
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