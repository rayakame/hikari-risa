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

import logging
import math
import typing

import redis.asyncio
import redis.exceptions

from risa.internal import leases
from risa.store import protocol

if typing.TYPE_CHECKING:
    import contextlib

__all__ = (
    "DEFAULT_LEASE",
    "DEFAULT_MAX_HOLD",
    "RedisStore",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.store.redis")

DEFAULT_LEASE: typing.Final[float] = leases.DEFAULT_LEASE
DEFAULT_MAX_HOLD: typing.Final[float] = leases.DEFAULT_MAX_HOLD

_SAFE_POLICY: typing.Final[str] = "noeviction"
_NO_TTL: typing.Final[str] = ""

_PUT: typing.Final[str] = """
local version = redis.call('HINCRBY', KEYS[1], 'v', 1)
redis.call('HSET', KEYS[1], 'd', ARGV[1])
if ARGV[2] == '' then
    redis.call('PERSIST', KEYS[1])
else
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return version
"""

_PUT_IF_VERSION: typing.Final[str] = """
local current = redis.call('HGET', KEYS[1], 'v')
if not current or tonumber(current) ~= tonumber(ARGV[2]) then
    return false
end
local version = tonumber(current) + 1
redis.call('HSET', KEYS[1], 'd', ARGV[1], 'v', version)
if ARGV[3] == '' then
    redis.call('PERSIST', KEYS[1])
else
    redis.call('PEXPIRE', KEYS[1], ARGV[3])
end
return version
"""

_RENEW: typing.Final[str] = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE: typing.Final[str] = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisStore:
    __slots__ = (
        "_cas_script",
        "_client",
        "_inspected",
        "_locks",
        "_put_script",
        "_release_script",
        "_renew_script",
    )

    def __init__(
        self,
        client: redis.asyncio.Redis,
        *,
        lease: float = DEFAULT_LEASE,
        max_hold: float = DEFAULT_MAX_HOLD,
    ) -> None:
        if client.get_encoder().decode_responses:
            msg = "RedisStore keeps raw bytes; give it a client built with decode_responses=False, the default"
            raise ValueError(msg)
        self._client = client
        self._inspected = False
        self._put_script = client.register_script(_PUT)
        self._cas_script = client.register_script(_PUT_IF_VERSION)
        self._renew_script = client.register_script(_RENEW)
        self._release_script = client.register_script(_RELEASE)
        self._locks = leases.LeaseLocks(
            claim=self._claim,
            renew=self._renew,
            release=self._release,
            prepare=self._inspect,
            transient=[redis.exceptions.RedisError],
            lease=lease,
            max_hold=max_hold,
        )

    async def get(self, key: str) -> bytes | None:
        await self._inspect()
        return _bytes_or_none(await self._client.hget(_state_key(key), "d"))

    async def get_versioned(self, key: str) -> protocol.VersionedEntry | None:
        await self._inspect()
        data, version = await self._client.hmget(_state_key(key), ["d", "v"])
        data = _bytes_or_none(data)
        version = _bytes_or_none(version)
        if data is None or version is None:
            return None
        return protocol.VersionedEntry(data=data, version=int(version))

    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        await self._inspect()
        await self._put_script(keys=[_state_key(key)], args=[value, _ttl_argument(ttl)])

    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        await self._inspect()
        written = await self._cas_script(keys=[_state_key(key)], args=[value, expected, _ttl_argument(ttl)])
        return None if written is None else int(written)

    async def delete(self, key: str) -> None:
        await self._inspect()
        await self._client.delete(_state_key(key))

    async def touch(self, key: str, *, ttl: float) -> None:
        await self._inspect()
        if not await self._client.pexpire(_state_key(key), _milliseconds(ttl)):
            _LOGGER.debug("state key %r is gone; nothing to keep alive", key)

    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return self._locks.hold(key, timeout=timeout)

    async def _inspect(self) -> None:
        if self._inspected:
            return
        self._inspected = True
        try:
            reply = await self._client.config_get("maxmemory-policy")  # type: ignore[reportUnknownMemberType]
        except redis.exceptions.RedisError as exc:
            _LOGGER.debug(
                "could not read the Redis maxmemory-policy (%s); risa cannot tell whether Redis may evict live state",
                exc,
            )
            return
        policy = reply.get("maxmemory-policy")
        if policy is None:
            _LOGGER.debug(
                "Redis did not report its maxmemory-policy; risa cannot tell whether Redis may evict live state",
            )
        elif policy != _SAFE_POLICY:
            _LOGGER.warning(
                "Redis runs with maxmemory-policy=%s, so under memory pressure it may evict view state that"
                " has not expired yet, and components whose state is evicted stop working; with"
                " %s a full Redis refuses writes instead, which risa reports",
                policy,
                _SAFE_POLICY,
            )

    async def _claim(self, key: str, token: str, lease: float) -> bool:
        return bool(await self._client.set(_lease_key(key), token, nx=True, px=_milliseconds(lease)))

    async def _renew(self, key: str, token: str, lease: float) -> bool:
        return bool(await self._renew_script(keys=[_lease_key(key)], args=[token, _milliseconds(lease)]))

    async def _release(self, key: str, token: str) -> bool:
        return bool(await self._release_script(keys=[_lease_key(key)], args=[token]))


def _state_key(key: str) -> str:
    return f"{{{key}}}:s"


def _lease_key(key: str) -> str:
    return f"{{{key}}}:l"


def _milliseconds(seconds: float) -> int:
    return max(1, math.ceil(seconds * 1000))


def _ttl_argument(ttl: float | None) -> int | str:
    return _NO_TTL if ttl is None else _milliseconds(ttl)


def _bytes_or_none(value: object) -> bytes | None:
    if value is None or isinstance(value, bytes):
        return value
    msg = f"Redis answered with {type(value).__name__}, not bytes; the client must not decode responses"
    raise TypeError(msg)
