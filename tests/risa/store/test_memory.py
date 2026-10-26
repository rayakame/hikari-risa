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

import pytest

import risa
from risa.store import memory
from risa.store import protocol


class Clock:
    __slots__ = ("now",)

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def clocked() -> tuple[memory.MemoryStore, Clock]:
    clock = Clock()
    return memory.MemoryStore(clock=clock), clock


def warnings_in(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno == logging.WARNING]


# ---- conformance ---------------------------------------------------------


def test_a_memory_store_satisfies_the_store_protocol() -> None:
    store: protocol.Store = memory.MemoryStore()

    assert store is not None


def test_the_store_is_part_of_the_public_surface() -> None:
    assert risa.MemoryStore is memory.MemoryStore


# ---- reads and writes ----------------------------------------------------


async def test_a_value_survives_the_round_trip() -> None:
    store = memory.MemoryStore()

    await store.put("k", b"value", ttl=None)

    assert await store.get("k") == b"value"


async def test_an_unknown_key_reads_as_nothing() -> None:
    store = memory.MemoryStore()

    assert await store.get("missing") is None
    assert await store.get_versioned("missing") is None


async def test_a_versioned_read_carries_the_data_and_its_version() -> None:
    store = memory.MemoryStore()

    await store.put("k", b"value", ttl=None)

    assert await store.get_versioned("k") == protocol.VersionedEntry(data=b"value", version=1)


@pytest.mark.parametrize("value", [b"", b"\x00", b"\xff\xfe binary \x00 soup"])
async def test_bytes_come_back_byte_identical(value: bytes) -> None:
    store = memory.MemoryStore()

    await store.put("k", value, ttl=None)

    assert await store.get("k") == value


# ---- versions ------------------------------------------------------------


async def test_every_write_advances_the_version() -> None:
    store = memory.MemoryStore()

    await store.put("k", b"one", ttl=None)
    await store.put("k", b"two", ttl=None)

    versioned = await store.get_versioned("k")
    assert versioned is not None
    assert versioned.version == 2


async def test_a_deleted_key_restarts_at_version_one() -> None:
    store = memory.MemoryStore()
    await store.put("k", b"one", ttl=None)
    await store.put("k", b"two", ttl=None)

    await store.delete("k")
    await store.put("k", b"fresh", ttl=None)

    versioned = await store.get_versioned("k")
    assert versioned is not None
    assert versioned.version == 1


async def test_deleting_a_missing_key_is_not_an_error() -> None:
    store = memory.MemoryStore()

    await store.delete("missing")


# ---- compare and swap ----------------------------------------------------


async def test_a_cas_with_the_current_version_wins_and_advances_it() -> None:
    store = memory.MemoryStore()
    await store.put("k", b"one", ttl=None)

    assert await store.put_if_version("k", b"two", expected=1, ttl=None) == 2

    assert await store.get_versioned("k") == protocol.VersionedEntry(data=b"two", version=2)


@pytest.mark.parametrize("expected", [0, 2, 99])
async def test_a_cas_against_any_other_version_is_refused(expected: int) -> None:
    store = memory.MemoryStore()
    await store.put("k", b"one", ttl=None)

    assert await store.put_if_version("k", b"loser", expected=expected, ttl=None) is None


async def test_a_cas_on_a_missing_key_is_refused() -> None:
    store = memory.MemoryStore()

    assert await store.put_if_version("missing", b"value", expected=1, ttl=None) is None


async def test_a_refused_cas_changes_nothing() -> None:
    store = memory.MemoryStore()
    await store.put("k", b"one", ttl=None)

    await store.put_if_version("k", b"loser", expected=7, ttl=None)

    assert await store.get_versioned("k") == protocol.VersionedEntry(data=b"one", version=1)


# ---- expiry --------------------------------------------------------------


async def test_a_record_past_its_deadline_is_gone() -> None:
    store, clock = clocked()
    await store.put("k", b"value", ttl=10.0)

    clock.now = 10.5

    assert await store.get("k") is None


async def test_a_record_exactly_at_its_deadline_is_gone() -> None:
    store, clock = clocked()
    await store.put("k", b"value", ttl=10.0)

    clock.now = 10.0

    assert await store.get("k") is None


async def test_a_record_with_no_ttl_never_expires() -> None:
    store, clock = clocked()
    await store.put("k", b"value", ttl=None)

    clock.now = 1e12

    assert await store.get("k") == b"value"


async def test_an_expired_record_is_forgotten_rather_than_hidden() -> None:
    store, clock = clocked()
    await store.put("k", b"one", ttl=5.0)
    await store.put("k", b"two", ttl=5.0)

    clock.now = 6.0
    await store.put("k", b"fresh", ttl=None)

    versioned = await store.get_versioned("k")
    assert versioned is not None
    assert versioned.version == 1


async def test_an_expired_record_refuses_a_cas() -> None:
    store, clock = clocked()
    await store.put("k", b"one", ttl=5.0)

    clock.now = 6.0

    assert await store.put_if_version("k", b"late", expected=1, ttl=None) is None


async def test_touch_keeps_a_record_alive() -> None:
    store, clock = clocked()
    await store.put("k", b"value", ttl=10.0)

    clock.now = 8.0
    await store.touch("k", ttl=10.0)
    clock.now = 15.0

    assert await store.get("k") == b"value"


async def test_touch_changes_neither_the_data_nor_the_version() -> None:
    store = memory.MemoryStore()
    await store.put("k", b"value", ttl=10.0)

    await store.touch("k", ttl=100.0)

    assert await store.get_versioned("k") == protocol.VersionedEntry(data=b"value", version=1)


async def test_touching_a_missing_key_is_a_quiet_no_op() -> None:
    store = memory.MemoryStore()

    await store.touch("missing", ttl=10.0)


# ---- the size bound ------------------------------------------------------


async def test_writing_past_the_bound_drops_the_least_recently_used() -> None:
    store = memory.MemoryStore(max_entries=2)
    await store.put("a", b"a", ttl=None)
    await store.put("b", b"b", ttl=None)

    await store.put("c", b"c", ttl=None)

    assert await store.get("a") is None
    assert await store.get("b") == b"b"
    assert await store.get("c") == b"c"


async def test_a_read_counts_as_use() -> None:
    store = memory.MemoryStore(max_entries=2)
    await store.put("a", b"a", ttl=None)
    await store.put("b", b"b", ttl=None)

    await store.get("a")
    await store.put("c", b"c", ttl=None)

    assert await store.get("a") == b"a"
    assert await store.get("b") is None


async def test_the_capacity_warning_fires_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    store = memory.MemoryStore(max_entries=1)

    await store.put("a", b"a", ttl=None)
    await store.put("b", b"b", ttl=None)
    await store.put("c", b"c", ttl=None)

    warnings = warnings_in(caplog)
    assert len(warnings) == 1
    assert "max_entries" in warnings[0].getMessage()


# ---- locking -------------------------------------------------------------


async def test_the_lock_serializes_work_on_one_key() -> None:
    store = memory.MemoryStore()
    events: list[str] = []

    async def worker(label: str) -> None:
        async with store.lock("k"):
            events.append(f"{label}-in")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            events.append(f"{label}-out")

    await asyncio.gather(worker("a"), worker("b"))

    assert events == ["a-in", "a-out", "b-in", "b-out"]


async def test_independent_keys_do_not_block_each_other() -> None:
    store = memory.MemoryStore()
    events: list[str] = []

    async def worker(label: str, key: str) -> None:
        async with store.lock(key):
            events.append(f"{label}-in")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            events.append(f"{label}-out")

    await asyncio.gather(worker("a", "one"), worker("b", "two"))

    assert events[:2] == ["a-in", "b-in"]


async def test_a_lock_that_cannot_be_acquired_times_out() -> None:
    store = memory.MemoryStore()

    async with store.lock("k"):
        with pytest.raises(risa.LockTimeoutError) as exc_info:
            async with store.lock("k", timeout=0.01):
                pass

    assert exc_info.value.key == "k"
    assert exc_info.value.timeout == pytest.approx(0.01)


async def test_a_raising_handler_still_releases_the_lock() -> None:
    store = memory.MemoryStore()

    with pytest.raises(RuntimeError):
        async with store.lock("k"):
            raise RuntimeError

    async with store.lock("k", timeout=0.01):
        pass


async def test_the_locks_do_not_outlive_their_holders() -> None:
    store = memory.MemoryStore()

    async with store.lock("k"):
        with pytest.raises(risa.LockTimeoutError):
            async with store.lock("k", timeout=0.01):
                pass
        assert store._locks  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not store._locks  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
