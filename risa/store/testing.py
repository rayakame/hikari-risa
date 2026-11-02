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
import secrets
import typing

from risa import errors

if typing.TYPE_CHECKING:
    import collections.abc

    from risa.store import protocol

__all__ = ("verify_store",)

_SHORT_TTL: typing.Final[float] = 0.05
_LAPSE: typing.Final[float] = 0.15
_GRACE_TTL: typing.Final[float] = 0.5
_GRACE_LAPSE: typing.Final[float] = 0.75
_LONG_TTL: typing.Final[float] = 5.0
_PATIENCE: typing.Final[float] = 2.0
_CHECK_DEADLINE: typing.Final[float] = 10.0
_SAMPLES: typing.Final[collections.abc.Sequence[bytes]] = (b"", b"\x00", b"\xff\xfe\x00 mixed", bytes(range(256)))
_RACERS: typing.Final[int] = 8
_HOLDERS: typing.Final[int] = 3
_INSIDE: typing.Final[str] = "raised inside the lock"

type _Check = collections.abc.Callable[[protocol.Store, str], collections.abc.Awaitable[str | None]]


def _other(key: str) -> str:
    return f"{key}:other"


async def _missing_keys_read_as_nothing(store: protocol.Store, key: str) -> str | None:
    if (found := await store.get(key)) is not None:
        return f"get on a missing key returned {found!r}, not None"
    if (versioned := await store.get_versioned(key)) is not None:
        return f"get_versioned on a missing key returned {versioned!r}, not None"
    return None


async def _bytes_round_trip_unchanged(store: protocol.Store, key: str) -> str | None:
    for sample in _SAMPLES:
        await store.put(key, sample, ttl=None)
        if (found := await store.get(key)) != sample:
            return f"put {sample!r}, but get returned {found!r}"
        versioned = await store.get_versioned(key)
        if versioned is None or versioned.data != sample:
            return f"put {sample!r}, but get_versioned returned {versioned!r}"
    return None


async def _writes_change_the_version(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=None)
    first = await store.get_versioned(key)
    await store.put(key, b"two", ttl=None)
    second = await store.get_versioned(key)
    if first is None or second is None:
        return "an entry written with put could not be read back with get_versioned"
    if first.version == second.version:
        return f"two puts both left the version at {first.version}"
    return None


async def _a_current_cas_wins(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=None)
    current = await store.get_versioned(key)
    if current is None:
        return "an entry written with put could not be read back with get_versioned"
    written = await store.put_if_version(key, b"two", expected=current.version, ttl=None)
    if written is None:
        return f"put_if_version with the current version {current.version} was refused"
    if written == current.version:
        return f"put_if_version succeeded but left the version at {written}"
    after = await store.get_versioned(key)
    if after is None or after.data != b"two" or after.version != written:
        return f"put_if_version returned version {written}, but get_versioned then read {after!r}"
    return None


async def _a_stale_cas_is_refused(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=None)
    stale = await store.get_versioned(key)
    await store.put(key, b"two", ttl=None)
    current = await store.get_versioned(key)
    if stale is None or current is None:
        return "an entry written with put could not be read back with get_versioned"
    if await store.put_if_version(key, b"three", expected=stale.version, ttl=None) is not None:
        return f"put_if_version accepted expected={stale.version} after the version moved to {current.version}"
    after = await store.get_versioned(key)
    if after is None or after.data != b"two" or after.version != current.version:
        return f"a refused put_if_version changed the entry to {after!r}"
    return None


async def _a_cas_on_a_missing_key_is_refused(store: protocol.Store, key: str) -> str | None:
    if (written := await store.put_if_version(key, b"one", expected=1, ttl=None)) is not None:
        return f"put_if_version on a missing key returned {written}, not None"
    if await store.get(key) is not None:
        return "a refused put_if_version on a missing key created the entry"
    return None


async def _racing_cas_writes_have_one_winner(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"start", ttl=None)
    current = await store.get_versioned(key)
    if current is None:
        return "an entry written with put could not be read back with get_versioned"
    contenders = [f"writer {index}".encode() for index in range(_RACERS)]
    written = await asyncio.gather(
        *(store.put_if_version(key, value, expected=current.version, ttl=None) for value in contenders),
        return_exceptions=True,
    )
    if raised := [outcome for outcome in written if isinstance(outcome, BaseException)]:
        raise raised[0]
    winners = [value for value, version in zip(contenders, written, strict=True) if version is not None]
    if len(winners) != 1:
        return f"{len(winners)} of {_RACERS} racing put_if_version calls on one version won"
    if (found := await store.get(key)) != winners[0]:
        return f"the winning write was {winners[0]!r}, but the entry holds {found!r}"
    return None


async def _delete_removes_the_entry(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=None)
    await store.delete(key)
    if (found := await store.get(key)) is not None:
        return f"get still returned {found!r} after delete"
    await store.delete(key)
    return None


async def _an_entry_expires_after_its_ttl(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=_SHORT_TTL)
    await asyncio.sleep(_LAPSE)
    if await store.get(key) is not None:
        return f"an entry put with ttl={_SHORT_TTL} was still there after {_LAPSE}s"
    await store.put(key, b"two", ttl=None)
    current = await store.get_versioned(key)
    if current is None:
        return "an entry written with put could not be read back with get_versioned"
    await store.put_if_version(key, b"three", expected=current.version, ttl=_SHORT_TTL)
    await asyncio.sleep(_LAPSE)
    if await store.get(key) is not None:
        return f"an entry written by put_if_version with ttl={_SHORT_TTL} was still there after {_LAPSE}s"
    return None


async def _a_put_without_ttl_clears_the_expiry(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=_GRACE_TTL)
    await store.put(key, b"two", ttl=None)
    await asyncio.sleep(_GRACE_LAPSE)
    if await store.get(key) != b"two":
        return "put with ttl=None kept the expiry of the write before it"
    return None


async def _a_cas_without_ttl_clears_the_expiry(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=_GRACE_TTL)
    current = await store.get_versioned(key)
    if current is None:
        return "an entry written with put could not be read back with get_versioned"
    await store.put_if_version(key, b"two", expected=current.version, ttl=None)
    await asyncio.sleep(_GRACE_LAPSE)
    if await store.get(key) != b"two":
        return "put_if_version with ttl=None kept the expiry of the write before it"
    return None


async def _touch_keeps_an_entry_alive(store: protocol.Store, key: str) -> str | None:
    await store.put(key, b"one", ttl=_GRACE_TTL)
    before = await store.get_versioned(key)
    await store.touch(key, ttl=_LONG_TTL)
    await asyncio.sleep(_GRACE_LAPSE)
    after = await store.get_versioned(key)
    if after is None:
        return f"an entry touched with ttl={_LONG_TTL} still expired on its original ttl={_GRACE_TTL}"
    if before is None or after.data != before.data or after.version != before.version:
        return f"touch changed the entry from {before!r} to {after!r}"
    return None


async def _touching_a_missing_key_creates_nothing(store: protocol.Store, key: str) -> str | None:
    await store.touch(key, ttl=_LONG_TTL)
    if (found := await store.get(key)) is not None:
        return f"touch on a missing key created an entry holding {found!r}"
    return None


async def _the_lock_excludes_a_second_holder(store: protocol.Store, key: str) -> str | None:
    inside: set[int] = set()
    crowds: list[int] = []

    async def hold(index: int) -> None:
        async with store.lock(key, timeout=_PATIENCE):
            inside.add(index)
            crowds.append(len(inside))
            await asyncio.sleep(_SHORT_TTL)
            inside.discard(index)

    await asyncio.gather(*(hold(index) for index in range(_HOLDERS)))
    if (most := max(crowds)) != 1:
        return f"{most} holders were inside lock({key!r}) at once"
    return None


async def _a_held_lock_times_out_a_second_holder(store: protocol.Store, key: str) -> str | None:
    entered = False
    async with store.lock(key):
        try:
            async with store.lock(key, timeout=_SHORT_TTL):
                entered = True
        except errors.LockTimeoutError:
            pass
    if entered:
        return f"a second lock({key!r}) entered while the first was held, instead of raising LockTimeoutError"
    return None


async def _independent_keys_do_not_block_each_other(store: protocol.Store, key: str) -> str | None:
    async with store.lock(key):
        try:
            async with store.lock(_other(key), timeout=_PATIENCE):
                pass
        except errors.LockTimeoutError:
            return f"lock({_other(key)!r}) timed out while only lock({key!r}) was held"
    return None


async def _a_lock_is_released_when_its_holder_raises(store: protocol.Store, key: str) -> str | None:
    async def raise_inside() -> None:
        async with store.lock(key):
            raise RuntimeError(_INSIDE)

    (outcome,) = await asyncio.gather(raise_inside(), return_exceptions=True)
    if not isinstance(outcome, RuntimeError):
        return f"an exception raised inside lock({key!r}) came out as {outcome!r}"
    try:
        async with store.lock(key, timeout=_PATIENCE):
            pass
    except errors.LockTimeoutError:
        return f"lock({key!r}) stayed held after its holder raised"
    return None


_CHECKS: typing.Final[collections.abc.Mapping[str, _Check]] = {
    "missing_keys_read_as_nothing": _missing_keys_read_as_nothing,
    "bytes_round_trip_unchanged": _bytes_round_trip_unchanged,
    "writes_change_the_version": _writes_change_the_version,
    "a_current_cas_wins": _a_current_cas_wins,
    "a_stale_cas_is_refused": _a_stale_cas_is_refused,
    "a_cas_on_a_missing_key_is_refused": _a_cas_on_a_missing_key_is_refused,
    "racing_cas_writes_have_one_winner": _racing_cas_writes_have_one_winner,
    "delete_removes_the_entry": _delete_removes_the_entry,
    "an_entry_expires_after_its_ttl": _an_entry_expires_after_its_ttl,
    "a_put_without_ttl_clears_the_expiry": _a_put_without_ttl_clears_the_expiry,
    "a_cas_without_ttl_clears_the_expiry": _a_cas_without_ttl_clears_the_expiry,
    "touch_keeps_an_entry_alive": _touch_keeps_an_entry_alive,
    "touching_a_missing_key_creates_nothing": _touching_a_missing_key_creates_nothing,
    "the_lock_excludes_a_second_holder": _the_lock_excludes_a_second_holder,
    "a_held_lock_times_out_a_second_holder": _a_held_lock_times_out_a_second_holder,
    "independent_keys_do_not_block_each_other": _independent_keys_do_not_block_each_other,
    "a_lock_is_released_when_its_holder_raises": _a_lock_is_released_when_its_holder_raises,
}


def _explained(exc: Exception, limit: asyncio.Timeout) -> str:
    if limit.expired():
        return f"did not finish within {_CHECK_DEADLINE}s"
    return f"raised {type(exc).__name__}: {exc}"


async def _isolated(store: protocol.Store, check: _Check, key: str) -> str | None:
    problems: list[str] = []
    limit = asyncio.timeout(_CHECK_DEADLINE)
    try:
        async with limit:
            if (problem := await check(store, key)) is not None:
                problems.append(problem)
    except Exception as exc:  # ruff:ignore[blind-except]
        problems.append(_explained(exc, limit))
    finally:
        cleanup = asyncio.timeout(_CHECK_DEADLINE)
        try:
            async with cleanup:
                await store.delete(key)
                await store.delete(_other(key))
        except Exception as exc:  # ruff:ignore[blind-except]
            problems.append(f"cleaning up {_explained(exc, cleanup)}")
    return "; ".join(problems) or None


async def verify_store(store: protocol.Store) -> None:
    namespace = f"risa:conformance:{secrets.token_hex(8)}"
    async with asyncio.TaskGroup() as group:
        outcomes = {
            name: group.create_task(_isolated(store, check, f"{namespace}:{name}")) for name, check in _CHECKS.items()
        }
    failures = [f"{name}: {problem}" for name, task in outcomes.items() if (problem := task.result()) is not None]
    if failures:
        raise errors.StoreConformanceError(failures)
