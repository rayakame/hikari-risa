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
import logging
import os
import secrets
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys
import typing

import fakeredis
import pytest
import redis.asyncio
import redis.crc
import redis.exceptions

import risa
from risa.store import redis as redis_store
from risa.store import testing

if typing.TYPE_CHECKING:
    import collections.abc

REDIS_URL = os.environ.get("RISA_TEST_REDIS_URL")

# A renewal survives a stall of two thirds of the lease, and CI runners have stalled for more than 0.2 seconds.
_LEASE = 1.0


@pytest.fixture(
    params=[
        "fakeredis",
        pytest.param(
            "redis",
            marks=pytest.mark.skipif(REDIS_URL is None, reason="set RISA_TEST_REDIS_URL to test a real Redis"),
        ),
    ],
)
async def client(request: pytest.FixtureRequest) -> collections.abc.AsyncIterator[redis.asyncio.Redis]:
    if request.param == "fakeredis":
        made: redis.asyncio.Redis = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer())
    else:
        assert REDIS_URL is not None
        made = redis.asyncio.Redis.from_url(REDIS_URL)  # type: ignore[reportUnknownMemberType]
    yield made
    await made.aclose()


def fresh_key() -> str:
    return f"risa:test:{secrets.token_hex(8)}"


async def test_a_redis_store_conforms(client: redis.asyncio.Redis) -> None:
    await testing.verify_store(redis_store.RedisStore(client))


async def test_the_lock_excludes_another_process(client: redis.asyncio.Redis) -> None:
    first = redis_store.RedisStore(client)
    second = redis_store.RedisStore(client)
    key = fresh_key()

    async with first.lock(key):
        with pytest.raises(risa.LockTimeoutError):
            async with second.lock(key, timeout=0.2):
                pytest.fail("the second process entered while the first held the lock")


async def test_another_process_enters_once_the_lock_is_released(client: redis.asyncio.Redis) -> None:
    first = redis_store.RedisStore(client)
    second = redis_store.RedisStore(client)
    key = fresh_key()
    entered = asyncio.Event()

    async def wait_for_it() -> None:
        async with second.lock(key, timeout=2.0):
            entered.set()

    async with first.lock(key):
        waiter = asyncio.create_task(wait_for_it())
        await asyncio.sleep(0.1)
        assert not entered.is_set()
    await asyncio.wait_for(waiter, timeout=2.0)

    assert entered.is_set()


async def test_a_held_lease_is_renewed_past_its_length(client: redis.asyncio.Redis) -> None:
    first = redis_store.RedisStore(client, lease=_LEASE)
    second = redis_store.RedisStore(client, lease=_LEASE)
    key = fresh_key()

    async with first.lock(key):
        with pytest.raises(risa.LockTimeoutError):
            async with second.lock(key, timeout=_LEASE * 1.5):
                pytest.fail("the lease lapsed while its holder was still inside")


async def test_a_holder_past_max_hold_lets_the_next_one_in(
    client: redis.asyncio.Redis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stalled = redis_store.RedisStore(client, lease=_LEASE, max_hold=_LEASE)
    second = redis_store.RedisStore(client)
    key = fresh_key()

    with caplog.at_level(logging.WARNING, logger="risa"):
        async with stalled.lock(key), second.lock(key, timeout=_LEASE * 5):
            pass

    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("max_hold" in message for message in messages)
    assert any("lapsed" in message for message in messages)


async def test_a_lapsed_holder_letting_go_leaves_the_next_holder_alone(client: redis.asyncio.Redis) -> None:
    stalled = redis_store.RedisStore(client, lease=0.1, max_hold=0.2)
    successor = redis_store.RedisStore(client)
    latecomer = redis_store.RedisStore(client)
    key = fresh_key()
    inside = asyncio.Event()
    leave = asyncio.Event()

    async def hold() -> None:
        async with successor.lock(key, timeout=2.0):
            inside.set()
            await leave.wait()

    async with stalled.lock(key):
        holder = asyncio.create_task(hold())
        await asyncio.wait_for(inside.wait(), timeout=2.0)
    try:
        with pytest.raises(risa.LockTimeoutError):
            async with latecomer.lock(key, timeout=0.2):
                pytest.fail("letting go of a lapsed lease freed the lock its successor holds")
    finally:
        leave.set()
        await holder


async def stall(*args: object, **kwargs: object) -> typing.NoReturn:
    del args, kwargs
    await asyncio.Event().wait()
    msg = "a stalled call never returns"
    raise AssertionError(msg)


@pytest.mark.parametrize("command", ["set", "config_get"])
async def test_a_stalled_redis_cannot_stretch_the_lock_timeout(
    client: redis.asyncio.Redis,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    monkeypatch.setattr(client, command, stall)
    store = redis_store.RedisStore(client)

    with pytest.raises(risa.LockTimeoutError):
        async with asyncio.timeout(2.0), store.lock(fresh_key(), timeout=0.1):
            pytest.fail("the lock was granted by a Redis that never answered")


async def test_contention_within_one_process_queues_without_timing_out(client: redis.asyncio.Redis) -> None:
    store = redis_store.RedisStore(client)
    key = fresh_key()
    order: list[int] = []

    async def take(index: int) -> None:
        async with store.lock(key, timeout=2.0):
            order.append(index)
            await asyncio.sleep(0.01)

    await asyncio.gather(*(take(index) for index in range(5)))

    assert sorted(order) == [0, 1, 2, 3, 4]


async def test_state_and_its_lease_share_one_cluster_slot(client: redis.asyncio.Redis) -> None:
    store = redis_store.RedisStore(client)
    key = fresh_key()
    await store.put(key, b"one", ttl=60.0)

    async with store.lock(key):
        written = [name for name in await client.keys(f"*{key}*") if isinstance(name, bytes)]  # type: ignore[reportUnknownMemberType]
    await store.delete(key)

    assert len(written) == 2
    assert len({redis.crc.key_slot(name) for name in written}) == 1


async def test_an_evicting_memory_policy_is_warned_about_once(
    client: redis.asyncio.Redis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def config_get(pattern: str) -> dict[str | None, str | None]:  # ruff:ignore[unused-async]
        return {pattern: "allkeys-lru"}

    monkeypatch.setattr(client, "config_get", config_get)
    store = redis_store.RedisStore(client)

    with caplog.at_level(logging.WARNING, logger="risa"):
        await store.get(fresh_key())
        await store.put(fresh_key(), b"one", ttl=1.0)

    (warning,) = [record.getMessage() for record in caplog.records]
    assert "maxmemory-policy=allkeys-lru" in warning


async def test_noeviction_draws_no_warning(
    client: redis.asyncio.Redis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def config_get(pattern: str) -> dict[str | None, str | None]:  # ruff:ignore[unused-async]
        return {pattern: "noeviction"}

    monkeypatch.setattr(client, "config_get", config_get)

    with caplog.at_level(logging.WARNING, logger="risa"):
        await redis_store.RedisStore(client).get(fresh_key())

    assert not caplog.records


async def test_a_reply_without_the_policy_is_noted_at_debug(
    client: redis.asyncio.Redis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def config_get(pattern: str) -> dict[str | None, str | None]:  # ruff:ignore[unused-async]
        del pattern
        return {}

    monkeypatch.setattr(client, "config_get", config_get)

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await redis_store.RedisStore(client).get(fresh_key())

    assert [record.levelno for record in caplog.records] == [logging.DEBUG]
    assert "maxmemory-policy" in caplog.records[0].getMessage()


async def test_a_server_that_hides_its_policy_is_only_noted_at_debug(
    client: redis.asyncio.Redis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def config_get(pattern: str) -> dict[str | None, str | None]:  # ruff:ignore[unused-async]
        msg = f"unknown command 'config get {pattern}'"
        raise redis.exceptions.ResponseError(msg)

    monkeypatch.setattr(client, "config_get", config_get)

    with caplog.at_level(logging.DEBUG, logger="risa"):
        assert await redis_store.RedisStore(client).get(fresh_key()) is None

    assert [record.levelno for record in caplog.records] == [logging.DEBUG]
    assert "maxmemory-policy" in caplog.records[0].getMessage()


async def test_a_client_that_decodes_responses_is_refused() -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)

    with pytest.raises(ValueError, match="decode_responses"):
        redis_store.RedisStore(client)

    await client.aclose()


@pytest.mark.parametrize("lease", [0.0, -1.0])
def test_a_lease_must_be_positive(lease: float) -> None:
    with pytest.raises(ValueError, match="lease"):
        redis_store.RedisStore(fakeredis.FakeAsyncRedis(), lease=lease)


def test_a_max_hold_shorter_than_the_lease_is_refused() -> None:
    with pytest.raises(ValueError, match="max_hold"):
        redis_store.RedisStore(fakeredis.FakeAsyncRedis(), lease=5.0, max_hold=1.0)


def test_importing_risa_leaves_redis_unimported() -> None:
    probe = "import sys, risa; print('redis' in sys.modules)"

    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"
