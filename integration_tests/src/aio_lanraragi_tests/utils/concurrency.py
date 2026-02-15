import asyncio
import sys
from typing import Awaitable, Callable, TypeVar

from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse

ResponseT = TypeVar('ResponseT', bound=LanraragiResponse)

def get_bounded_sem(on_unix: int=8, on_windows: int=2) -> asyncio.Semaphore:
    """
    Return a semaphore based on appropriate environment.
    """
    match sys.platform:
        case "win32":
            return asyncio.BoundedSemaphore(value=on_windows)
        case _:
            return asyncio.BoundedSemaphore(value=on_unix)

async def retry_on_lock(
    operation_func: Callable[[], Awaitable[tuple[ResponseT, LanraragiErrorResponse]]],
    max_retries: int = 10
) -> tuple[ResponseT, LanraragiErrorResponse]:
    """
    Wrapper function that retries an operation if it encounters a 423 locked resource error.
    """
    retry_count = 0
    while True:
        response, error = await operation_func()
        if error and error.status == 423:
            retry_count += 1
            if retry_count > max_retries:
                return response, error
            await asyncio.sleep(2 ** retry_count)
            continue
        return response, error
