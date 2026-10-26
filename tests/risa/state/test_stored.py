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
import typing
import unittest.mock

import hikari
import msgspec
import pytest

import risa
from risa import codecs
from risa import ui
from risa.internal import codec
from risa.internal import registry
from risa.state import anchor
from risa.state import backend
from risa.state import serde
from risa.state import stored
from risa.store import memory
from risa.store import protocol

if typing.TYPE_CHECKING:
    import contextlib

APP_ID = hikari.Snowflake(4242)
TTL = 60.0


@risa.register(name="stored-todo", state=risa.InStore(ttl=TTL))
class TodoList(risa.View):
    items: list[str] = msgspec.field(default_factory=list[str])

    def render(self) -> ui.Layout:
        return ui.TextDisplay(", ".join(self.items))


@risa.register(name="stored-poll")
class Poll(risa.View):
    question: str = "Ship it?"

    def render(self) -> ui.Layout:
        return ui.TextDisplay(self.question)


TODO = registry.require_meta(TodoList)
POLL = registry.require_meta(Poll)


class Clock:
    __slots__ = ("now",)

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FlakyStore(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    async def get_versioned(self, key: str) -> protocol.VersionedEntry | None:
        msg = "redis went away"
        raise ConnectionError(msg)


class BrokenLock:
    async def __aenter__(self) -> None:
        msg = "connection reset"
        raise OSError(msg)

    async def __aexit__(self, *exc: object) -> None:
        pass


class LocklessStore(memory.MemoryStore):
    __slots__ = ()

    @typing.override
    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return BrokenLock()


def rest() -> unittest.mock.Mock:
    client = unittest.mock.Mock(spec=hikari.api.RESTClient)
    client.fetch_application = unittest.mock.AsyncMock(return_value=unittest.mock.Mock(id=APP_ID))
    return client


def backend_for(
    store: protocol.Store,
    *,
    key_prefix: str | None = None,
    lock_timeout: float = 1.0,
) -> stored.StoredBackend:
    return stored.StoredBackend(
        store,
        namespace=stored.KeyNamespace(rest(), key_prefix=key_prefix),
        lock_timeout=lock_timeout,
    )


def interaction() -> unittest.mock.Mock:
    return unittest.mock.Mock(spec=hikari.ComponentInteraction, id=hikari.Snowflake(900), application_id=APP_ID)


def clicked(fragment: str, *, index: int = 0, meta: registry.ViewMeta = TODO) -> codec.CustomID:
    return codec.CustomID(
        cookie=meta.cookie,
        handler=codec.make_handler_token("add", 1),
        fragment_index=index,
        fragment=fragment,
        tail="",
    )


def key_of(raw: bytes) -> str:
    located = anchor.StoreAnchor.parse(raw)
    assert located is not None
    return f"risa:{APP_ID}:1:{located.token}"


def entry_of(view: TodoList, *, name: str = TODO.name, fingerprint: str = TODO.schema.fingerprint) -> bytes:
    data = TODO.schema.pack(TODO.schema.values(view))
    return serde.pack_entry(serde.Envelope(v=TODO.version, n=name, f=fingerprint, d=data))


async def sent(owner: stored.StoredBackend, view: TodoList) -> bytes:
    raw = owner.first_anchor(meta=TODO, view=view)
    await owner.commit_first(meta=TODO, view=view, anchor=raw)
    return raw


async def opened(owner: stored.StoredBackend, fragment: str, *, index: int = 0) -> stored.StoredSession:
    session = owner.session(meta=TODO, component=clicked(fragment, index=index), interaction=interaction())
    await session.open()
    return session


async def click(owner: stored.StoredBackend, raw: bytes) -> tuple[stored.StoredSession, TodoList]:
    session = await opened(owner, codecs.BASE85.encode(raw))
    restored = await session.restore()
    assert isinstance(restored, backend.Restored)
    assert isinstance(restored.view, TodoList)
    return session, restored.view


async def another_process_writes(store: protocol.Store, raw: bytes, view: TodoList) -> None:
    current = await store.get_versioned(key_of(raw))
    assert current is not None
    await store.put_if_version(key_of(raw), entry_of(view), expected=current.version, ttl=TTL)


async def items_in(store: protocol.Store, raw: bytes) -> list[str] | None:
    current = await store.get_versioned(key_of(raw))
    if current is None:
        return None
    envelope = serde.unpack_entry(current.data)
    assert envelope is not None
    values = TODO.schema.unpack(envelope.f, envelope.d)
    assert values is not None
    return typing.cast("list[str]", values[0])


def records_at(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == level]


async def test_the_first_send_writes_the_record_under_the_application_namespace() -> None:
    store = memory.MemoryStore()

    raw = await sent(backend_for(store), TodoList(items=["milk"]))

    written = await store.get_versioned(key_of(raw))
    assert written is not None
    assert written.version == 1
    assert serde.unpack_entry(written.data) == serde.Envelope(
        v=TODO.version,
        n=TODO.name,
        f=TODO.schema.fingerprint,
        d=TODO.schema.pack([["milk"]]),
    )


async def test_key_prefix_replaces_the_whole_namespace_head() -> None:
    store = memory.MemoryStore()

    raw = await sent(backend_for(store, key_prefix="modbot"), TodoList())

    located = anchor.StoreAnchor.parse(raw)
    assert located is not None
    assert await store.get(f"modbot:1:{located.token}") is not None


async def test_builds_before_any_click_fetch_the_application_id_once() -> None:
    client = rest()
    namespace = stored.KeyNamespace(client)

    keys = await asyncio.gather(namespace.for_build("aa"), namespace.for_build("bb"))

    assert keys == [f"risa:{APP_ID}:1:aa", f"risa:{APP_ID}:1:bb"]
    client.fetch_application.assert_awaited_once()


async def test_a_click_teaches_the_namespace_the_application_id() -> None:
    client = rest()
    namespace = stored.KeyNamespace(client)

    namespace.for_click("aa", interaction())
    await namespace.for_build("bb")

    client.fetch_application.assert_not_awaited()


async def test_a_click_hydrates_the_view_from_the_store() -> None:
    owner = backend_for(memory.MemoryStore())
    raw = await sent(owner, TodoList(items=["milk", "eggs"]))

    _, todo = await click(owner, raw)

    assert todo.items == ["milk", "eggs"]


async def test_stage_commits_and_keeps_the_component_ids_stable() -> None:
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)

    todo.items.append("milk")
    assert await session.stage(todo) == raw
    todo.items.append("eggs")
    assert await session.stage(todo) == raw

    assert await items_in(store, raw) == ["milk", "eggs"]
    written = await store.get_versioned(key_of(raw))
    assert written is not None
    assert written.version == 3


async def test_an_untouched_click_slides_the_ttl() -> None:
    clock = Clock()
    store = memory.MemoryStore(clock=clock)
    owner = backend_for(store)
    raw = await sent(owner, TodoList(items=["milk"]))

    clock.now = 50.0
    session, todo = await click(owner, raw)
    assert await session.settle(todo) is False
    await session.close()
    clock.now = 100.0

    assert await items_in(store, raw) == ["milk"]


async def test_a_mutation_without_rerender_is_reported_dirty_and_left_to_dispatch() -> None:
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)

    todo.items.append("milk")

    assert await session.settle(todo) is True
    assert await items_in(store, raw) == []


async def test_a_lost_race_adopts_the_winner_and_raises() -> None:
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)
    await another_process_writes(store, raw, TodoList(items=["theirs"]))

    todo.items.append("mine")
    with pytest.raises(risa.StateConflictError) as exc_info:
        await session.stage(todo)

    assert (exc_info.value.expected, exc_info.value.found) == (1, 2)
    assert todo.items == ["theirs"]


async def test_after_a_lost_race_the_handler_can_commit_on_top_of_the_winner() -> None:
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)
    await another_process_writes(store, raw, TodoList(items=["theirs"]))
    todo.items.append("mine")
    with pytest.raises(risa.StateConflictError):
        await session.stage(todo)

    todo.items.append("mine")
    await session.stage(todo)

    assert await items_in(store, raw) == ["theirs", "mine"]


async def test_a_record_gone_under_the_handler_raises_and_detaches() -> None:
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)
    await store.delete(key_of(raw))

    todo.items.append("milk")
    with pytest.raises(risa.StateNotFoundError) as first:
        await session.stage(todo)
    with pytest.raises(risa.StateNotFoundError) as second:
        await session.stage(todo)

    assert first.value is not second.value
    assert await session.settle(todo) is False
    assert await items_in(store, raw) is None


async def test_a_change_after_the_session_was_detached_is_logged_as_discarded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)
    await store.delete(key_of(raw))
    todo.items.append("milk")
    with pytest.raises(risa.StateNotFoundError):
        await session.stage(todo)

    assert await session.settle(todo) is False
    assert any("discarded" in line for line in records_at(caplog, logging.DEBUG))
    assert not records_at(caplog, logging.WARNING)


async def test_an_unchanged_rerender_writes_nothing_and_slides_the_ttl() -> None:
    clock = Clock()
    store = memory.MemoryStore(clock=clock)
    owner = backend_for(store)
    raw = await sent(owner, TodoList(items=["milk"]))

    clock.now = 50.0
    session, todo = await click(owner, raw)
    assert await session.stage(todo) == raw
    clock.now = 100.0

    written = await store.get_versioned(key_of(raw))
    assert written is not None
    assert written.version == 1


async def test_an_unchanged_rerender_raises_no_conflict_after_another_write() -> None:
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)
    await another_process_writes(store, raw, TodoList(items=["theirs"]))

    assert await session.stage(todo) == raw
    assert await items_in(store, raw) == ["theirs"]


async def test_an_unreadable_winner_raises_without_adopting() -> None:
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    session, todo = await click(owner, raw)
    await store.put(key_of(raw), b"\xc1", ttl=TTL)

    todo.items.append("mine")
    with pytest.raises(risa.StateConflictError) as exc_info:
        await session.stage(todo)

    assert exc_info.value.found == 2
    assert todo.items == ["mine"]
    assert await session.settle(todo) is False


async def test_an_expired_record_reads_as_missing_without_a_log_of_its_own(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    clock = Clock()
    owner = backend_for(memory.MemoryStore(clock=clock))
    raw = await sent(owner, TodoList())
    clock.now = TTL + 1

    session = await opened(owner, codecs.BASE85.encode(raw))

    assert await session.restore() is backend.Unreadably.MISSING
    assert not records_at(caplog, logging.WARNING)
    assert not records_at(caplog, logging.ERROR)


async def test_an_entry_of_another_view_reads_as_forged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    await store.put(key_of(raw), entry_of(TodoList(), name="someone-else"), ttl=TTL)

    session = await opened(owner, codecs.BASE85.encode(raw))

    assert await session.restore() is backend.Unreadably.OUTDATED
    assert any("not one of its records" in line for line in records_at(caplog, logging.WARNING))


async def test_an_unknown_fingerprint_in_the_store_is_a_schema_edit(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    assert not TODO.schema.accepts("zzz")
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    await store.put(key_of(raw), entry_of(TodoList(), fingerprint="zzz"), ttl=TTL)

    session = await opened(owner, codecs.BASE85.encode(raw))

    assert await session.restore() is backend.Unreadably.OUTDATED
    assert any("no longer accepts" in line for line in records_at(caplog, logging.ERROR))


async def test_garbage_in_the_store_reads_as_a_foreign_entry(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    store = memory.MemoryStore()
    owner = backend_for(store)
    raw = await sent(owner, TodoList())
    await store.put(key_of(raw), b"\xc1", ttl=TTL)

    session = await opened(owner, codecs.BASE85.encode(raw))

    assert await session.restore() is backend.Unreadably.OUTDATED
    assert any("not one of its records" in line for line in records_at(caplog, logging.WARNING))


@pytest.mark.parametrize(
    ("fragment", "index"),
    [
        (codecs.BASE85.encode(anchor.MessageAnchor(TODO.schema.fingerprint, 0, b"\x91\x90").pack()), 0),
        (codecs.BASE85.encode(b"m" + bytes(30)), 0),
        ("", 0),
        ("", 3),
        (codecs.BASE85.encode(anchor.StoreAnchor.mint().pack()), 2),
    ],
)
async def test_a_component_without_a_store_key_is_a_placement_switch(
    fragment: str,
    index: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")

    session = await opened(backend_for(memory.MemoryStore()), fragment, index=index)

    assert await session.restore() is backend.Unreadably.OUTDATED
    assert any("placement was switched" in line for line in records_at(caplog, logging.ERROR))


async def test_an_unreadable_component_reads_as_forged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")

    session = await opened(backend_for(memory.MemoryStore()), "x")

    assert await session.restore() is backend.Unreadably.OUTDATED
    assert any("no readable store key" in line for line in records_at(caplog, logging.WARNING))


async def test_a_failing_store_is_unavailable_never_missing() -> None:
    owner = backend_for(FlakyStore())
    raw = await sent(owner, TodoList())

    session = await opened(owner, codecs.BASE85.encode(raw))
    with pytest.raises(risa.StoreUnavailableError) as exc_info:
        await session.restore()

    assert exc_info.value.key == key_of(raw)
    assert "ConnectionError" in exc_info.value.reason


async def test_a_failing_lock_is_unavailable() -> None:
    owner = backend_for(LocklessStore())
    raw = await sent(owner, TodoList())

    with pytest.raises(risa.StoreUnavailableError) as exc_info:
        await opened(owner, codecs.BASE85.encode(raw))

    assert "OSError" in exc_info.value.reason


async def test_a_lock_timeout_passes_through_as_itself() -> None:
    owner = backend_for(memory.MemoryStore(), lock_timeout=0.01)
    raw = await sent(owner, TodoList())
    holder = await opened(owner, codecs.BASE85.encode(raw))

    with pytest.raises(risa.LockTimeoutError):
        await opened(owner, codecs.BASE85.encode(raw))

    await holder.close()


async def test_settle_before_a_restore_is_a_bug() -> None:
    owner = backend_for(memory.MemoryStore())
    raw = await sent(owner, TodoList())
    session = await opened(owner, codecs.BASE85.encode(raw))

    with pytest.raises(RuntimeError, match="restored"):
        await session.settle(TodoList())


async def test_commit_first_takes_only_a_minted_anchor() -> None:
    owner = backend_for(memory.MemoryStore())

    with pytest.raises(TypeError, match="first_anchor"):
        await owner.commit_first(meta=TODO, view=TodoList(), anchor=b"not an anchor")


def test_a_message_view_has_no_stored_session() -> None:
    with pytest.raises(TypeError, match="message"):
        backend_for(memory.MemoryStore()).session(
            meta=POLL, component=clicked("", meta=POLL), interaction=interaction()
        )
