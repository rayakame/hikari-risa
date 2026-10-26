# Copyright (c) 2025 Rayakame
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import annotations

import asyncio
import contextlib
import typing

import msgspec

from risa import errors

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("KeyedLocks",)


class _KeyLock(msgspec.Struct):
    lock: asyncio.Lock
    waiters: int = 0


class KeyedLocks:
    __slots__ = ("_locks",)

    def __init__(self) -> None:
        self._locks: dict[str, _KeyLock] = {}

    def __len__(self) -> int:
        return len(self._locks)

    @contextlib.asynccontextmanager
    async def hold(
        self,
        key: str,
        *,
        timeout: float,  # ruff:ignore[async-function-with-timeout]
    ) -> collections.abc.AsyncGenerator[None]:
        entry = self._locks.setdefault(key, _KeyLock(lock=asyncio.Lock()))
        entry.waiters += 1
        try:
            try:
                async with asyncio.timeout(timeout):
                    await entry.lock.acquire()
            except TimeoutError as exc:
                raise errors.LockTimeoutError(key, timeout) from exc
            try:
                yield
            finally:
                entry.lock.release()
        finally:
            entry.waiters -= 1
            if entry.waiters == 0:
                del self._locks[key]
