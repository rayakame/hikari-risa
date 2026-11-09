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

import pytest

import risa
from risa.store import memory
from risa.store import protocol
from risa.store import testing

if typing.TYPE_CHECKING:
    import collections.abc

LATENCY = 0.06
SLOW_RACER = 1.5
SHORT_DEADLINE = 1.5


class IgnoresExpectedVersion(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        del expected
        current = await self.get_versioned(key)
        if current is None:
            return None
        return await super().put_if_version(key, value, expected=current.version, ttl=ttl)


class IgnoresTtl(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        del ttl
        await super().put(key, value, ttl=None)


class TouchCreates(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        if await self.get_versioned(key) is None:
            await super().put(key, b"", ttl=ttl)
        await super().touch(key, ttl=ttl)


class TrimsTrailingZeros(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        await super().put(key, value.rstrip(b"\x00"), ttl=ttl)


class Distant(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def get_versioned(self, key: str) -> protocol.VersionedEntry | None:
        await asyncio.sleep(LATENCY)
        return await super().get_versioned(key)

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        await asyncio.sleep(LATENCY)
        await super().put(key, value, ttl=ttl)

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        await asyncio.sleep(LATENCY)
        return await super().put_if_version(key, value, expected=expected, ttl=ttl)

    @typing.override
    async def delete(self, key: str) -> None:
        await asyncio.sleep(LATENCY)
        await super().delete(key)

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        await asyncio.sleep(LATENCY)
        await super().touch(key, ttl=ttl)


class KeepsExpiryWhenOverwriting(Distant):
    __slots__ = ("deadlines",)

    def __init__(self) -> None:
        super().__init__()
        self.deadlines: dict[str, float] = {}

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        now = asyncio.get_running_loop().time()
        if ttl is not None:
            self.deadlines[key] = now + ttl
        elif (deadline := self.deadlines.get(key)) is not None and deadline > now:
            ttl = deadline - now
        await super().put(key, value, ttl=ttl)


class TouchRaises(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        msg = "touch is broken"
        raise RuntimeError(msg)


class OneRacerHangs(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        if key.endswith(":racing_cas_writes_have_one_winner"):
            if value == b"writer 0":
                msg = "one racer broke"
                raise RuntimeError(msg)
            if value == b"writer 1":
                await asyncio.Event().wait()
        return await super().put_if_version(key, value, expected=expected, ttl=ttl)


class DeleteHangs(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def delete(self, key: str) -> None:
        await asyncio.Event().wait()


class DeleteRaises(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def delete(self, key: str) -> None:
        msg = "delete is broken"
        raise RuntimeError(msg)


class LocksNothing(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        del key, timeout
        return contextlib.nullcontext()


class Ledger(memory.MemoryStore):
    __slots__ = ("live",)

    def __init__(self) -> None:
        super().__init__()
        self.live: set[str] = set()

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        self.live.add(key)
        await super().put(key, value, ttl=ttl)

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        written = await super().put_if_version(key, value, expected=expected, ttl=ttl)
        if written is not None:
            self.live.add(key)
        return written

    @typing.override
    async def delete(self, key: str) -> None:
        self.live.discard(key)
        await super().delete(key)


class OneRacingCasRaises(memory.MemoryStore):
    __slots__ = ("in_flight",)

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        if not key.endswith(":racing_cas_writes_have_one_winner"):
            return await super().put_if_version(key, value, expected=expected, ttl=ttl)
        if value == b"writer 0":
            msg = "one racer broke"
            raise RuntimeError(msg)
        self.in_flight += 1
        try:
            await asyncio.sleep(SLOW_RACER)
            return await super().put_if_version(key, value, expected=expected, ttl=ttl)
        finally:
            self.in_flight -= 1


class SwallowsErrors:
    __slots__ = ("_held",)

    def __init__(self, held: contextlib.AbstractAsyncContextManager[None]) -> None:
        self._held = held

    async def __aenter__(self) -> None:
        await self._held.__aenter__()

    async def __aexit__(self, *exc: object) -> bool:
        await self._held.__aexit__(None, None, None)
        return True


class LeaksOnError:
    __slots__ = ("_held", "_kept")

    def __init__(
        self,
        held: contextlib.AbstractAsyncContextManager[None],
        kept: list[contextlib.AbstractAsyncContextManager[None]],
    ) -> None:
        self._held = held
        self._kept = kept

    async def __aenter__(self) -> None:
        await self._held.__aenter__()

    async def __aexit__(self, exc_type: type[BaseException] | None, *exc: object) -> None:
        if exc_type is None:
            await self._held.__aexit__(None, None, None)
        else:
            # Kept alive, or the collector would close it and release the lock after all.
            self._kept.append(self._held)


class ForgetsEverything(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def get_versioned(self, key: str) -> protocol.VersionedEntry | None:
        del key
        return None


class InventsMissingKeysAndSharesOneLock(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def get_versioned(self, key: str) -> protocol.VersionedEntry | None:
        found = await super().get_versioned(key)
        return protocol.VersionedEntry(data=b"", version=0) if found is None else found

    @typing.override
    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        del key
        return super().lock("one lock for every key", timeout=timeout)


class VersionsNeverMove(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        self._write(key, value, version=1, ttl=ttl)

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        current = await self.get_versioned(key)
        if current is None or current.version != expected:
            return None
        self._write(key, value, version=expected, ttl=ttl)
        return expected


class RefusesEveryCasAndTouch(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        del key, value, expected, ttl
        return None

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        del key, ttl


class CasWritesBackwardsAndTouchWrites(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        del ttl
        current = await self.get_versioned(key)
        if current is None:
            self._write(key, value, version=1, ttl=None)
            return 1
        if current.version != expected:
            return None
        self._write(key, value[::-1], version=expected + 1, ttl=None)
        return expected + 1

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        current = await self.get_versioned(key)
        if current is not None:
            self._write(key, current.data, version=current.version + 1, ttl=ttl)


class RefusedCasStillWritesAndLockSwallowsErrors(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        current = await self.get_versioned(key)
        if current is None or current.version != expected:
            await self.put(key, value, ttl=ttl)
            return None
        return await super().put_if_version(key, value, expected=expected, ttl=ttl)

    @typing.override
    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return SwallowsErrors(super().lock(key, timeout=timeout))


class LockLeaksWhenItsHolderRaises(memory.MemoryStore):
    __slots__ = ("_leaked",)

    def __init__(self) -> None:
        super().__init__()
        self._leaked: list[contextlib.AbstractAsyncContextManager[None]] = []

    @typing.override
    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return LeaksOnError(super().lock(key, timeout=timeout), self._leaked)


async def failures_of(store: protocol.Store) -> collections.abc.Sequence[str]:
    with pytest.raises(risa.StoreConformanceError) as caught:
        await testing.verify_store(store)
    return caught.value.failures


async def test_the_memory_store_conforms() -> None:
    await testing.verify_store(memory.MemoryStore())


async def test_a_conforming_store_across_a_network_passes() -> None:
    await testing.verify_store(Distant())


async def test_a_slow_store_that_keeps_an_expiry_on_overwrite_is_caught() -> None:
    failures = await failures_of(KeepsExpiryWhenOverwriting())

    assert [failure.split(":")[0] for failure in failures] == ["a_put_without_ttl_clears_the_expiry"]


async def test_a_racer_that_raises_is_reported_after_every_racer_settled() -> None:
    store = OneRacingCasRaises()

    failures = await failures_of(store)

    assert failures == ["racing_cas_writes_have_one_winner: raised RuntimeError: one racer broke"]
    assert store.in_flight == 0


async def test_a_check_that_hangs_is_reported_once_its_deadline_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testing, "_CHECK_DEADLINE", SHORT_DEADLINE)

    failures = await asyncio.wait_for(failures_of(OneRacerHangs()), timeout=SHORT_DEADLINE * 4)

    assert failures == [f"racing_cas_writes_have_one_winner: did not finish within {SHORT_DEADLINE}s"]


async def test_a_cleanup_that_hangs_is_reported_once_its_deadline_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testing, "_CHECK_DEADLINE", SHORT_DEADLINE)

    failures = await asyncio.wait_for(failures_of(DeleteHangs()), timeout=SHORT_DEADLINE * 4)

    assert len(failures) == 17
    assert all(f"cleaning up did not finish within {SHORT_DEADLINE}s" in failure for failure in failures)


async def test_cancelling_the_kit_still_cleans_up() -> None:
    store = Ledger()
    running = asyncio.create_task(testing.verify_store(store))
    await asyncio.sleep(0.1)

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert store.live == set()


async def test_a_cas_that_ignores_the_expected_version_is_caught() -> None:
    failures = await failures_of(IgnoresExpectedVersion())

    assert any(failure.startswith("a_stale_cas_is_refused:") for failure in failures)
    assert any(failure.startswith("racing_cas_writes_have_one_winner:") for failure in failures)


async def test_a_store_that_ignores_ttl_is_caught() -> None:
    failures = await failures_of(IgnoresTtl())

    assert any(failure.startswith("an_entry_expires_after_its_ttl:") for failure in failures)


async def test_a_touch_that_creates_entries_is_caught() -> None:
    failures = await failures_of(TouchCreates())

    assert [failure.split(":")[0] for failure in failures] == ["touching_a_missing_key_creates_nothing"]


async def test_a_store_that_alters_bytes_is_caught() -> None:
    failures = await failures_of(TrimsTrailingZeros())

    assert [failure.split(":")[0] for failure in failures] == ["bytes_round_trip_unchanged"]


async def test_a_lock_that_excludes_nothing_is_caught() -> None:
    failures = await failures_of(LocksNothing())

    assert "the_lock_excludes_a_second_holder" in [failure.split(":")[0] for failure in failures]
    assert "a_held_lock_times_out_a_second_holder" in [failure.split(":")[0] for failure in failures]


async def test_every_check_runs_even_after_one_fails() -> None:
    failures = await failures_of(IgnoresExpectedVersion())

    assert len(failures) == 2


async def test_a_store_that_raises_is_reported_rather_than_crashing_the_kit() -> None:
    failures = await failures_of(TouchRaises())

    assert sorted(failure.split(":")[0] for failure in failures) == [
        "touch_keeps_an_entry_alive",
        "touching_a_missing_key_creates_nothing",
    ]
    assert all("raised RuntimeError: touch is broken" in failure for failure in failures)


async def test_a_cleanup_that_raises_is_reported_for_every_check() -> None:
    failures = await failures_of(DeleteRaises())

    assert len(failures) == 17
    assert all("delete is broken" in failure for failure in failures)


def test_one_failed_check_reads_in_the_singular() -> None:
    error = risa.StoreConformanceError(["a_stale_cas_is_refused: accepted a stale version"])

    assert "failed 1 conformance check:" in str(error)


async def test_the_kit_leaves_nothing_behind() -> None:
    store = Ledger()

    await testing.verify_store(store)

    assert store.live == set()


@pytest.mark.parametrize(
    ("broken", "caught"),
    [
        (
            ForgetsEverything,
            {
                "writes_change_the_version",
                "a_current_cas_wins",
                "a_stale_cas_is_refused",
                "racing_cas_writes_have_one_winner",
                "an_entry_expires_after_its_ttl",
                "a_cas_without_ttl_clears_the_expiry",
            },
        ),
        (
            InventsMissingKeysAndSharesOneLock,
            {"missing_keys_read_as_nothing", "delete_removes_the_entry", "independent_keys_do_not_block_each_other"},
        ),
        (VersionsNeverMove, {"writes_change_the_version", "a_current_cas_wins"}),
        (
            RefusesEveryCasAndTouch,
            {"a_current_cas_wins", "a_cas_without_ttl_clears_the_expiry", "touch_keeps_an_entry_alive"},
        ),
        (
            CasWritesBackwardsAndTouchWrites,
            {
                "a_current_cas_wins",
                "a_cas_on_a_missing_key_is_refused",
                "racing_cas_writes_have_one_winner",
                "an_entry_expires_after_its_ttl",
                "touch_keeps_an_entry_alive",
            },
        ),
        (
            RefusedCasStillWritesAndLockSwallowsErrors,
            {
                "a_stale_cas_is_refused",
                "a_cas_on_a_missing_key_is_refused",
                "a_lock_is_released_when_its_holder_raises",
            },
        ),
        (LockLeaksWhenItsHolderRaises, {"a_lock_is_released_when_its_holder_raises"}),
    ],
    ids=lambda value: value.__name__ if isinstance(value, type) else "",
)
async def test_each_check_catches_the_store_it_was_written_against(
    broken: type[memory.MemoryStore], caught: set[str]
) -> None:
    failed = {failure.split(":")[0] for failure in await failures_of(broken())}

    assert caught <= failed
