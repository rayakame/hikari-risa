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
import functools
import logging
import typing

import msgspec

from risa import errors
from risa.internal import codec
from risa.state import anchor as anchor_
from risa.state import backend
from risa.state import placement as placement_
from risa.state import serde
from risa.store import protocol

if typing.TYPE_CHECKING:
    import collections.abc

    import hikari

    from risa import view as view_
    from risa.internal import registry

__all__ = (
    "KEY_LAYOUT",
    "KeyNamespace",
    "StoredBackend",
    "StoredSession",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.state.stored")

KEY_LAYOUT: typing.Final[str] = "1"


class _Target(msgspec.Struct, frozen=True):
    key: str
    located: anchor_.StoreAnchor


class KeyNamespace:
    __slots__ = ("_application_id", "_fetching", "_key_prefix", "_rest")

    def __init__(self, rest: hikari.api.RESTClient, *, key_prefix: str | None = None) -> None:
        self._rest = rest
        self._key_prefix = key_prefix
        self._application_id: hikari.Snowflake | None = None
        self._fetching = asyncio.Lock()

    def for_click(self, token: str, interaction: hikari.ComponentInteraction) -> str:
        if self._key_prefix is not None:
            return _compose(self._key_prefix, token)
        self._application_id = interaction.application_id
        return _compose(f"risa:{interaction.application_id}", token)

    async def for_build(self, token: str) -> str:
        if self._key_prefix is not None:
            return _compose(self._key_prefix, token)
        return _compose(f"risa:{await self._application()}", token)

    async def _application(self) -> hikari.Snowflake:
        if self._application_id is not None:
            return self._application_id
        async with self._fetching:
            if self._application_id is None:
                self._application_id = (await self._rest.fetch_application()).id
            return self._application_id


class StoredSession(backend.StateSession):
    __slots__ = (
        "_backend",
        "_baseline",
        "_component",
        "_detached",
        "_held",
        "_interaction",
        "_meta",
        "_placement",
        "_refreshed",
        "_target",
        "_version",
    )

    def __init__(
        self,
        owner: StoredBackend,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> None:
        self._backend = owner
        self._meta = meta
        self._placement = _placement_of(meta)
        self._component = component
        self._interaction = interaction
        self._held = contextlib.AsyncExitStack()
        self._target: _Target | None = None
        self._version = 0
        self._baseline: bytes | None = None
        self._detached: collections.abc.Callable[[], errors.StateError] | None = None
        self._refreshed = False

    @typing.override
    async def open(self) -> None:
        located = self._locate()
        if located is None:
            return
        key = self._backend.namespace.for_click(located.token, self._interaction)
        self._target = _Target(key=key, located=located)
        store = self._backend.store
        timeout = self._backend.lock_timeout
        await _guarded(key, lambda: self._held.enter_async_context(store.lock(key, timeout=timeout)))

    @typing.override
    async def close(self) -> None:
        if self._target is None:
            await self._held.aclose()
            return
        await _guarded(self._target.key, self._held.aclose)

    @typing.override
    async def restore(self) -> backend.Restored | backend.Unreadably:
        if self._target is None:
            return backend.Unreadably.OUTDATED
        key = self._target.key
        store = self._backend.store
        entry = await _guarded(key, lambda: store.get_versioned(key))
        if entry is None:
            return backend.Unreadably.MISSING
        values = self._unpack(key, entry.data)
        if values is None:
            return backend.Unreadably.OUTDATED
        view = backend.hydrate(self._meta, values)
        self._version = entry.version
        self._baseline = self._meta.schema.pack_view(view)
        return backend.Restored(view)

    @typing.override
    async def stage(self, view: view_.View) -> bytes:
        target = self._restored()
        if self._detached is not None:
            raise self._detached()
        packed = self._meta.schema.pack_view(view)
        if packed == self._baseline:
            await self._touch(target.key)
        elif not await self._commit(target.key, packed):
            raise await self._lost(target.key, view)
        return target.located.pack()

    @typing.override
    async def published(self, view: view_.View) -> None:
        pass

    @typing.override
    async def settle(self, view: view_.View) -> bool:
        key = self._restored().key
        dirty = self._meta.schema.pack_view(view) != self._baseline
        if self._detached is not None:
            if dirty:
                _LOGGER.debug(
                    "interaction %s on view %s changed durable state after its session was detached"
                    " (%s); the change was discarded",
                    self._interaction.id,
                    self._meta.name,
                    self._detached(),
                )
            return False
        if not dirty and not self._refreshed:
            await self._touch(key)
        return dirty

    def _locate(self) -> anchor_.StoreAnchor | None:
        found = codec.decode_text(self._component.fragment, self._component.codec)
        if isinstance(found, errors.CodecUnavailableError):
            raise found
        if found is not None and (self._component.fragment_index != 0 or found[:1] in {b"", anchor_.PLACEMENT_MESSAGE}):
            _LOGGER.error(
                "interaction %s routes to view %s, which keeps its state in a store, but the component"
                " carries no store key: the placement was switched without bumping version=%d; bump"
                " the version to retire these components",
                self._interaction.id,
                self._meta.name,
                self._meta.version,
            )
            return None
        parsed = None if found is None else anchor_.parse(found)
        if not isinstance(parsed, anchor_.StoreAnchor):
            _LOGGER.warning(
                "interaction %s routes to view %s, but its component carries no readable store key;"
                " the component may be forged or corrupted",
                self._interaction.id,
                self._meta.name,
            )
            return None
        return parsed

    def _unpack(self, key: str, data: bytes) -> collections.abc.Sequence[object] | None:
        envelope = serde.unpack_entry(data)
        if envelope is None or envelope.n != self._meta.name or envelope.v != self._meta.version:
            _LOGGER.warning(
                "interaction %s routes to view %s (version %d), but the entry under %r is not one of its"
                " records; the key may be forged, or the entry written by something other than risa",
                self._interaction.id,
                self._meta.name,
                self._meta.version,
                key,
            )
            return None
        schema = self._meta.schema
        if not schema.accepts(envelope.f):
            _LOGGER.error(
                "interaction %s routes to view %s, but the entry under %r was written by durable fields"
                " this code no longer accepts: a field was removed, reordered, retyped, or added without"
                " a default, without bumping version=%d; bump the version to retire these components",
                self._interaction.id,
                self._meta.name,
                key,
                self._meta.version,
            )
            return None
        values = schema.unpack(envelope.f, envelope.d)
        if values is None:
            _LOGGER.warning(
                "interaction %s routes to view %s, but the entry under %r does not decode under its own"
                " fingerprint; the entry is corrupted",
                self._interaction.id,
                self._meta.name,
                key,
            )
        return values

    def _restored(self) -> _Target:
        if self._target is None or self._baseline is None:
            msg = "the session has no restored state to commit against"
            raise RuntimeError(msg)
        return self._target

    async def _commit(self, key: str, packed: bytes) -> bool:
        store = self._backend.store
        entry = _entry(self._meta, packed)
        expected = self._version
        ttl = self._placement.ttl
        written = await _guarded(key, lambda: store.put_if_version(key, entry, expected=expected, ttl=ttl))
        if written is None:
            return False
        self._version = written
        self._baseline = packed
        self._refreshed = True
        return True

    async def _lost(self, key: str, view: view_.View) -> errors.StateError:
        store = self._backend.store
        expected = self._version
        entry = await _guarded(key, lambda: store.get_versioned(key))
        if entry is None:
            self._detached = functools.partial(errors.StateNotFoundError, key)
            return self._detached()
        values = self._unpack(key, entry.data)
        if values is None:
            self._detached = functools.partial(errors.StateConflictError, key, expected=expected, found=entry.version)
            return self._detached()
        self._meta.schema.adopt(view, backend.hydrate(self._meta, values))
        self._version = entry.version
        self._baseline = self._meta.schema.pack_view(view)
        return errors.StateConflictError(key, expected=expected, found=entry.version)

    async def _touch(self, key: str) -> None:
        ttl = self._placement.ttl
        if ttl is None:
            return
        store = self._backend.store
        await _guarded(key, lambda: store.touch(key, ttl=ttl))
        self._refreshed = True


class StoredBackend(backend.StateBackend):
    __slots__ = ("_lock_timeout", "_namespace", "_store")

    def __init__(
        self,
        store: protocol.Store,
        *,
        namespace: KeyNamespace,
        lock_timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> None:
        self._store = store
        self._namespace = namespace
        self._lock_timeout = lock_timeout

    @property
    def store(self) -> protocol.Store:
        return self._store

    @property
    def namespace(self) -> KeyNamespace:
        return self._namespace

    @property
    def lock_timeout(self) -> float:
        return self._lock_timeout

    @typing.override
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> StoredSession:
        return StoredSession(self, meta=meta, component=component, interaction=interaction)

    @typing.override
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> bytes:
        return anchor_.StoreAnchor.mint().pack()

    @typing.override
    async def commit_first(self, *, meta: registry.ViewMeta, view: view_.View, anchor: bytes) -> None:
        located = anchor_.parse(anchor)
        if not isinstance(located, anchor_.StoreAnchor):
            msg = "commit_first() takes the anchor first_anchor() minted"
            raise TypeError(msg)
        key = await self._namespace.for_build(located.token)
        entry = _entry(meta, meta.schema.pack_view(view))
        ttl = _placement_of(meta).ttl
        await _guarded(key, lambda: self._store.put(key, entry, ttl=ttl))


def _compose(prefix: str, token: str) -> str:
    return f"{prefix}:{KEY_LAYOUT}:{token}"


def _placement_of(meta: registry.ViewMeta) -> placement_.InStore:
    if not isinstance(meta.state, placement_.InStore):
        msg = f"view {meta.name!r} keeps its state in the message, not in a store"
        raise TypeError(msg)
    return meta.state


def _entry(meta: registry.ViewMeta, packed: bytes) -> bytes:
    return serde.pack_entry(serde.Envelope(v=meta.version, n=meta.name, f=meta.schema.fingerprint, d=packed))


async def _guarded[T](key: str, call: collections.abc.Callable[[], collections.abc.Awaitable[T]]) -> T:
    try:
        return await call()
    except (errors.LockTimeoutError, errors.StoreUnavailableError):
        raise
    except Exception as exc:
        raise errors.StoreUnavailableError(key, f"{type(exc).__name__}: {exc}") from exc
