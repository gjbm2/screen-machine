"""A wait is interrupted by a newer urgent block, never by its own block's later steps (routes/scheduler_queue.py;
24 Nov: an urgent replay's own publishes cut through its waits and the day went by in four seconds)."""
from routes.scheduler_queue import InstructionQueue


def acts(*names):
    return [{"action": n} for n in names]


def test_an_urgent_block_does_not_interrupt_its_own_wait():
    q = InstructionQueue()
    q.push_block(acts("publish", "wait", "publish", "wait", "log"), important=True, urgent=True)
    assert q.pop_next()["instruction"]["action"] == "publish"
    assert q.pop_next()["instruction"]["action"] == "wait"        # now waiting
    assert q.peek_next_urgent() is None, "its own next publish must wait for the wait"
    assert q.pop_next(urgent_only=True) is None


def test_a_newer_urgent_block_does_interrupt_it():
    q = InstructionQueue()
    q.push_block(acts("wait", "publish"), important=True, urgent=True)
    q.pop_next()                                                   # waiting
    q.push_block(acts("freeze"), urgent=True)
    assert q.peek_next_urgent()["instruction"]["action"] == "freeze"
    assert q.pop_next(urgent_only=True)["instruction"]["action"] == "freeze"


def test_a_for_each_round_belongs_to_its_block():
    q = InstructionQueue()
    q.push_block(acts("for_each", "log"), important=True, urgent=True)
    q.pop_next()                                                   # the for_each runs and expands its rounds
    q.push_front(acts("publish", "wait", "publish", "wait"))
    assert [e["urgent"] and e["important"] for e in q.queue] == [True] * 5
    assert q.pop_next()["instruction"]["action"] == "publish"
    assert q.pop_next()["instruction"]["action"] == "wait"
    assert q.peek_next_urgent() is None, "the next round waits its turn"


def test_a_normal_wait_is_still_interrupted_by_an_urgent_block():
    q = InstructionQueue()
    q.push_block(acts("wait", "publish"))
    q.pop_next()
    q.push_block(acts("publish"), urgent=True)
    assert q.peek_next_urgent() is not None
