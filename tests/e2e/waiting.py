"""Polling helper of the end-to-end tests."""

import asyncio
from typing import Callable


async def wait_until(condition: Callable[[], bool], timeout: float = 5.0, description: str = "condition") -> None:
    """
    Wait until the condition is true.
    :param condition: Checked every 50 ms.
    :param timeout: Seconds to wait before a TimeoutError is raised.
    :param description: What is waited for, named in the error.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            raise TimeoutError(f"timed out after {timeout} s waiting for {description}")
        await asyncio.sleep(0.05)
