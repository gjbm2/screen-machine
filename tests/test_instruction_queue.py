import builtins
import types
import pytest

from routes.scheduler_queue import InstructionQueue, check_urgent_events


class DummyEvent:  # Simple stand-in for scheduler_utils.EventEntry
    def __init__(self, payload=None):
        self.payload = payload or {}
        self.created_at = None
        self.display_name = None
        self.unique_id = "dummy-id"


@pytest.fixture()
def fresh_queue():
    return InstructionQueue()


# 1. Normal block only enqueues when queue is empty

def test_normal_block_only_when_empty(fresh_queue):
    normal_block = [{"action": "noop"}]
    fresh_queue.push_block(normal_block, important=False, urgent=False)
    assert fresh_queue.get_size() == 1

    # Pushing a second normal block should be ignored (queue not empty)
    fresh_queue.push_block(normal_block, important=False, urgent=False)
    assert fresh_queue.get_size() == 1  # unchanged


# 2. Important block is always appended and preserved

def test_important_block_preserved_on_urgent(fresh_queue):
    important_block = [{"action": "important"}]
    fresh_queue.push_block(important_block, important=True, urgent=False)
    assert fresh_queue.get_size() == 1

    # Add a normal block behind important -> should be ignored because queue not empty
    normal_block = [{"action": "normal"}]
    fresh_queue.push_block(normal_block, important=False, urgent=False)
    assert fresh_queue.get_size() == 1  # still only important

    # Urgent block arrives and should execute first but keep important
    urgent_block = [{"action": "urgent"}]
    fresh_queue.push_block(urgent_block, urgent=True)
    # After urgent push, queue should have urgent + important
    assert fresh_queue.get_size() == 2
    first = fresh_queue.pop_next()
    assert first["instruction"]["action"] == "urgent"
    second = fresh_queue.pop_next()
    assert second["instruction"]["action"] == "important"


# 3. Urgent pre-emption removes non-important entries

def test_urgent_removes_nonimportant():
    q = InstructionQueue()
    q.push_block([{"a": 1}], important=False)  # normal
    q.push_block([{"a": 2}], important=True)   # important
    assert q.get_size() == 2
    q.push_block([{"urgent": 1}], urgent=True)  # urgent removes non-important
    # Expected order: urgent, important
    assert q.get_size() == 2
    assert q.pop_next()["instruction"].get("urgent") == 1
    assert q.pop_next()["instruction"].get("a") == 2  # important preserved


# 4. check_urgent_events produces correct synthetic terminate block

def test_check_urgent_events_generates_block(monkeypatch):
    # Monkeypatch scheduler_utils.pop_next_event so that it returns a DummyEvent once
    events_yielded = {"__terminate_immediate__": False}

    def fake_pop_next_event(dest, key, now, event_trigger_mode=True):
        if key == "__terminate_immediate__" and not events_yielded[key]:
            events_yielded[key] = True
            return DummyEvent()
        return None

    monkeypatch.setitem(builtins.__dict__, "_fake_pop_next_event", fake_pop_next_event)

    import importlib
    from routes import scheduler_utils as s_utils
    monkeypatch.setattr(s_utils, "pop_next_event", fake_pop_next_event)

    result = check_urgent_events("dest1")
    assert result is not None
    assert result["block"][0]["action"] == "terminate"
    assert result["block"][0]["mode"] == "immediate"
    assert result["block"][0]["from_event"] is True 