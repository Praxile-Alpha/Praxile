"""Retain ownership of cleanup operations across repeated caller cancellation."""
import asyncio


async def await_owned(awaitable, *, propagate_cancellation=True):
    """Do not cancel owned work when its caller is cancelled.

    Suppression is only for a caller already handling CancelledError and about
    to re-raise it. The operation itself must bound its I/O; this helper cannot
    stop a worker thread or promise cleanup after process termination.
    """
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled and propagate_cancellation:
        raise asyncio.CancelledError
    return result
