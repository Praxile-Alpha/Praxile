import asyncio

import pytest

from praxile.eval.async_ownership import await_owned


@pytest.mark.parametrize("propagate", [True, False])
def test_repeated_cancellation_does_not_lose_owned_operation(propagate):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        finished = []
        async def operation():
            started.set()
            await release.wait()
            finished.append(True)
            return 42
        waiting = asyncio.create_task(await_owned(operation(), propagate_cancellation=propagate))
        await started.wait()
        for _ in range(3):
            waiting.cancel()
            await asyncio.sleep(0)
            assert not waiting.done()
        release.set()
        if propagate:
            with pytest.raises(asyncio.CancelledError):
                await waiting
        else:
            assert await waiting == 42
        assert finished == [True]
    asyncio.run(exercise())


def test_inner_failure_and_inner_cancellation_are_not_hidden():
    async def exercise():
        async def fail():
            raise OSError("cleanup failed")
        with pytest.raises(OSError, match="cleanup failed"):
            await await_owned(fail())
        async def cancel():
            raise asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await await_owned(cancel(), propagate_cancellation=False)
    asyncio.run(exercise())
