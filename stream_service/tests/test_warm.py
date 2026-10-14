"""ensure_warm: one warm per GPU; callers arriving mid-warm wait on it; a failed warm lets the next caller retry."""
import asyncio
import types

import pytest

from stream_service.sessions import SessionManager


def _mgr():
    m = types.SimpleNamespace(warmed=set(), warming={})
    m._warm = lambda gpu, run: SessionManager._warm(m, gpu, run)
    m.ensure_warm = lambda gpu, run: SessionManager.ensure_warm(m, gpu, run)
    return m


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_warm():
    m, calls = _mgr(), []

    async def run():
        calls.append(1)
        await asyncio.sleep(0.05)

    await asyncio.gather(*(m.ensure_warm(0, run) for _ in range(3)))
    await m.ensure_warm(0, run)
    assert calls == [1] and m.warmed == {0} and not m.warming


@pytest.mark.asyncio
async def test_failed_warm_is_logged_and_the_waiter_retries():
    m, calls = _mgr(), []

    async def bad():
        calls.append("bad")
        await asyncio.sleep(0.02)
        raise RuntimeError("no GPU")

    async def good():
        calls.append("good")

    first = asyncio.ensure_future(m.ensure_warm(1, bad))      # e.g. the server-start warm
    await asyncio.sleep(0)
    await m.ensure_warm(1, good)                               # a session arriving mid-warm
    await first
    assert calls == ["bad", "good"] and m.warmed == {1}
