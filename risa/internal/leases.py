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
import logging
import secrets
import typing

from risa import errors
from risa.internal import locks

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "DEFAULT_LEASE",
    "DEFAULT_MAX_HOLD",
    "LeaseLocks",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.internal.leases")

DEFAULT_LEASE: typing.Final[float] = 5.0
DEFAULT_MAX_HOLD: typing.Final[float] = 60.0

_RENEWALS_PER_LEASE: typing.Final[int] = 3
_FIRST_PAUSE: typing.Final[float] = 0.005
_LONGEST_PAUSE: typing.Final[float] = 0.1

type Claim = collections.abc.Callable[[str, str, float], collections.abc.Awaitable[bool]]
type Release = collections.abc.Callable[[str, str], collections.abc.Awaitable[bool]]
type Prepare = collections.abc.Callable[[], collections.abc.Awaitable[None]]


class LeaseLocks:
    __slots__ = ("_claim", "_lease", "_local", "_max_hold", "_prepare", "_release", "_renew", "_transient")

    def __init__(
        self,
        *,
        claim: Claim,
        renew: Claim,
        release: Release,
        prepare: Prepare,
        transient: collections.abc.Sequence[type[Exception]],
        lease: float,
        max_hold: float,
    ) -> None:
        if lease <= 0:
            msg = f"lease must be positive, got {lease}"
            raise ValueError(msg)
        if max_hold < lease:
            msg = f"max_hold ({max_hold}s) must be at least the lease ({lease}s)"
            raise ValueError(msg)
        self._claim = claim
        self._renew = renew
        self._release = release
        self._prepare = prepare
        self._transient = tuple(transient)
        self._lease = lease
        self._max_hold = max_hold
        self._local = locks.KeyedLocks()

    @contextlib.asynccontextmanager
    async def hold(
        self,
        key: str,
        *,
        timeout: float,  # ruff:ignore[async-function-with-timeout]
    ) -> collections.abc.AsyncGenerator[None]:
        deadline = asyncio.get_running_loop().time() + timeout
        async with self._local.hold(key, timeout=timeout):
            token = secrets.token_hex(16)
            try:
                async with asyncio.timeout_at(deadline):
                    await self._prepare()
                    await self._acquire(key, token)
            except TimeoutError as exc:
                raise errors.LockTimeoutError(key, timeout) from exc
            keeper = asyncio.create_task(self._keep(key, token))
            try:
                yield
            finally:
                keeper.cancel()
                await asyncio.wait([keeper])
                await self._let_go(key, token)

    async def _acquire(self, key: str, token: str) -> None:
        pause = _FIRST_PAUSE
        while not await self._claim(key, token, self._lease):
            await asyncio.sleep(pause)
            pause = min(pause * 2, _LONGEST_PAUSE)

    async def _keep(self, key: str, token: str) -> None:
        loop = asyncio.get_running_loop()
        stop_at = loop.time() + self._max_hold
        while True:
            await asyncio.sleep(self._lease / _RENEWALS_PER_LEASE)
            if loop.time() >= stop_at:
                _LOGGER.warning(
                    "the lock on state key %r has been held past max_hold=%.1fs; its lease is no longer"
                    " renewed, so another process may enter once it lapses - the version check still"
                    " keeps the state consistent",
                    key,
                    self._max_hold,
                )
                return
            try:
                renewed = await self._renew(key, token, self._lease)
            except self._transient as exc:
                _LOGGER.warning(
                    "renewing the lease on state key %r failed (%s); it lapses within %.1fs unless a"
                    " later renewal gets through",
                    key,
                    exc,
                    self._lease,
                )
                continue
            if not renewed:
                _LOGGER.warning(
                    "the lease on state key %r lapsed while its holder was still inside; another process"
                    " may enter, and the version check settles whose write lands",
                    key,
                )
                return

    async def _let_go(self, key: str, token: str) -> None:
        if not await self._release(key, token):
            _LOGGER.warning(
                "the lease on state key %r had already lapsed when its holder let go; another process may"
                " have worked on the same state meanwhile, and the version check settled it",
                key,
            )
