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
import typing

import hikari
import msgspec

from risa import context
from risa import di as di_
from risa import errors
from risa import view as view_
from risa.internal import codec
from risa.internal import constants
from risa.state import backend
from risa.state import placement

if typing.TYPE_CHECKING:
    import collections.abc

    import linkd

    from risa import codecs
    from risa.internal import registry

__all__ = ("Dispatcher",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.dispatch")

type BackendResolver = collections.abc.Callable[[registry.ViewMeta], backend.StateBackend | None]


class _Route(msgspec.Struct, frozen=True):
    meta: registry.ViewMeta
    component: codec.CustomID
    handler: registry.HandlerRecord | None = None
    decoded: collections.abc.Sequence[object] = ()


class _Click(msgspec.Struct, frozen=True):
    interaction: hikari.ComponentInteraction
    meta: registry.ViewMeta
    component: codec.CustomID
    record: registry.HandlerRecord
    decoded: collections.abc.Sequence[object]
    ctx: context.ComponentContext
    slot: context.ViewSlot
    gate: context.ResponseGate


class Dispatcher:
    __slots__ = ("_auto_defer", "_auto_defer_delay", "_backend_for", "_di", "_resolve_view", "_rest")

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        di: linkd.DependencyInjectionManager,
        resolve_view: collections.abc.Callable[[str], registry.ViewMeta | None],
        backend_for: BackendResolver,
        *,
        auto_defer: view_.AutoDefer,
        auto_defer_delay: float,
    ) -> None:
        self._rest = rest
        self._di = di
        self._resolve_view = resolve_view
        self._backend_for = backend_for
        self._auto_defer = auto_defer
        self._auto_defer_delay = auto_defer_delay

    async def process(
        self,
        interaction: hikari.ComponentInteraction,
        gate: context.ResponseGate | None = None,
    ) -> None:
        if gate is None:
            gate = context.ResponseGate()
        try:
            await self._dispatch(interaction, gate)
        finally:
            gate.acknowledged.set()

    def _route(
        self,
        interaction: hikari.ComponentInteraction,
    ) -> _Route | None:
        custom_id = codec.parse_custom_id(interaction.custom_id)
        if custom_id is None:
            return None

        meta = self._resolve_view(custom_id.cookie)
        if meta is None:
            _LOGGER.debug(
                "interaction %s carries a risa custom_id with cookie %r, but this client has no view for it",
                interaction.id,
                custom_id.cookie,
            )
            return None
        record = meta.handlers.get(custom_id.handler)
        if record is None:
            level = logging.DEBUG if meta.handles_outdated else logging.WARNING
            _LOGGER.log(
                level,
                "interaction %s routes to view %s (version %d), but no handler answers to token %r."
                " A live component may predate a version bump that retired its handler.",
                interaction.id,
                meta.name,
                meta.version,
                custom_id.handler,
            )
            return _Route(meta=meta, component=custom_id)

        mismatch = _fingerprint_mismatch(custom_id, meta, record)
        if mismatch is not None:
            _LOGGER.error("interaction %s: %s", interaction.id, mismatch)  # ERROR always
            return _Route(meta=meta, component=custom_id)

        decoded = _decode_args(custom_id, meta, record, interaction.id)
        if decoded is None:
            return None
        return _Route(meta=meta, component=custom_id, handler=record, decoded=decoded)

    async def _dispatch(
        self,
        interaction: hikari.ComponentInteraction,
        gate: context.ResponseGate,
    ) -> None:
        route = self._route(interaction)
        if route is None:
            return
        gate.adopted = True
        slot = context.ViewSlot()
        ctx = context.ComponentContext(interaction, rest=self._rest, meta=route.meta, gate=gate, slot=slot)
        if route.handler is None:
            await self._run_hook(route.meta, route.meta.outdated, ctx, "on_outdated")
            return
        click = _Click(
            interaction=interaction,
            meta=route.meta,
            component=route.component,
            record=route.handler,
            decoded=route.decoded,
            ctx=ctx,
            slot=slot,
            gate=gate,
        )
        await self._run_handler(click)

    async def _run_handler(self, click: _Click) -> None:
        state = self._backend_for(click.meta)
        if state is None:
            _LOGGER.error(
                "interaction %s routes to view %s, which keeps its state in store %r, but this client has"
                " no store of that name; pass it to the client with store= or stores=",
                click.interaction.id,
                click.meta.name,
                click.meta.state.store if isinstance(click.meta.state, placement.InStore) else None,
            )
            return
        defer = click.record.defer if click.record.defer is not None else self._auto_defer
        watchdog = (
            None if defer is view_.AutoDefer.OFF else asyncio.create_task(self._auto_defer_task(click.ctx, defer))
        )
        try:
            if await self._transact(state, click, watchdog) and defer is not view_.AutoDefer.OFF:
                await _stand_down(watchdog, click.gate)
                await _acknowledge(click)
        finally:
            if watchdog is not None:
                watchdog.cancel()

    async def _transact(
        self,
        state: backend.StateBackend,
        click: _Click,
        watchdog: asyncio.Task[None] | None,
    ) -> bool:
        session = state.session(meta=click.meta, component=click.component, interaction=click.interaction)
        try:
            await session.open()
            return await self._within(click, session, watchdog)
        except (errors.StoreUnavailableError, errors.CodecUnavailableError, errors.LockTimeoutError) as exc:
            await _stand_down(watchdog, click.gate)
            _LOGGER.error(  # ruff:ignore[error-instead-of-exception]
                "interaction %s routes to view %s, but its state could not be reached: %s",
                click.interaction.id,
                click.meta.name,
                exc,
            )
            return False
        except Exception:
            await _stand_down(watchdog, click.gate)
            _LOGGER.exception(
                "the state session of view %s failed while answering interaction %s",
                click.meta.name,
                click.interaction.id,
            )
            return False
        finally:
            await _release(click, session)

    async def _within(
        self,
        click: _Click,
        session: backend.StateSession,
        watchdog: asyncio.Task[None] | None,
    ) -> bool:
        view = await self._hydrate(click, session, watchdog)
        if view is None:
            return False
        if not await self._call(click, view):
            await _stand_down(watchdog, click.gate)
            return False
        await _commit_leftovers(click, session, view)
        return True

    async def _hydrate(
        self,
        click: _Click,
        session: backend.StateSession,
        watchdog: asyncio.Task[None] | None,
    ) -> view_.View | None:
        restored = await session.restore()
        if isinstance(restored, backend.Restored):
            click.slot.view = restored.view
            click.slot.session = session
            return restored.view
        await _stand_down(watchdog, click.gate)
        if restored is backend.Unreadably.OUTDATED:
            await self._run_hook(click.meta, click.meta.outdated, click.ctx, "on_outdated")
            return None
        _LOGGER.log(
            logging.DEBUG if click.meta.handles_state_missing else logging.WARNING,
            "interaction %s routes to view %s, but its stored state is gone - expired, evicted or"
            " deleted; override on_state_missing to answer the user",
            click.interaction.id,
            click.meta.name,
        )
        await self._run_hook(click.meta, click.meta.state_missing, click.ctx, "on_state_missing")
        return None

    async def _call(self, click: _Click, view: view_.View) -> bool:
        async with self._scope(click.ctx):
            if click.meta.loader is not None:
                try:
                    await click.meta.loader(view)
                except Exception:
                    _LOGGER.exception(
                        "load() of view %s failed while answering interaction %s, so handler %r (version %d) never ran",
                        click.meta.name,
                        click.interaction.id,
                        click.record.handler_id,
                        click.record.version,
                    )
                    return False
            try:
                await click.record.callback(view, click.ctx, *click.decoded)
            except Exception:
                _LOGGER.exception(
                    "handler %r (version %d) of view %s failed while answering interaction %s",
                    click.record.handler_id,
                    click.record.version,
                    click.meta.name,
                    click.interaction.id,
                )
                return False
        return True

    async def _run_hook(
        self,
        meta: registry.ViewMeta,
        hook: registry.HookCallback | None,
        ctx: context.ComponentContext,
        name: str,
    ) -> None:
        if hook is None:
            return
        try:
            async with self._scope(ctx):
                await hook(ctx)
        except Exception:
            _LOGGER.exception(
                "%s of view %s failed while answering interaction %s",
                name,
                meta.name,
                ctx.interaction.id,
            )

    @contextlib.asynccontextmanager
    async def _scope(self, ctx: context.ComponentContext) -> collections.abc.AsyncGenerator[None]:
        async with (
            self._di.enter_context(di_.Contexts.DEFAULT),
            self._di.enter_context(di_.Contexts.COMPONENT) as container,
        ):
            container.add_value(context.ComponentContext, ctx)
            container.add_value(hikari.ComponentInteraction, ctx.interaction)
            yield

    async def _auto_defer_task(self, ctx: context.ComponentContext, defer: view_.AutoDefer) -> None:
        await asyncio.sleep(self._auto_defer_delay)
        thinking = defer in {view_.AutoDefer.THINKING, view_.AutoDefer.THINKING_EPHEMERAL}
        ephemeral = defer is view_.AutoDefer.THINKING_EPHEMERAL
        try:
            await ctx.acknowledge(thinking=thinking, ephemeral=ephemeral)
        except Exception:
            _LOGGER.exception("auto-defer failed to acknowledge interaction %s", ctx.interaction.id)


async def _commit_leftovers(click: _Click, session: backend.StateSession, view: view_.View) -> None:
    try:
        dirty = await session.settle(view)
    except Exception:
        _LOGGER.exception(
            "settling the state of view %s after interaction %s failed",
            click.meta.name,
            click.interaction.id,
        )
        return
    if not dirty:
        return
    try:
        await click.ctx.rerender()
    except errors.EphemeralOriginError:
        _LOGGER.warning(
            "interaction %s on view %s mutated durable state without calling rerender(), but the"
            " handler had already answered and the origin message is ephemeral, so the change could"
            " not be written; call ctx.rerender() before ctx.respond()",
            click.interaction.id,
            click.meta.name,
        )
    except (errors.StateConflictError, errors.StateNotFoundError) as exc:
        _LOGGER.warning(
            "interaction %s on view %s mutated durable state without calling rerender(), and"
            " committing it at the end of the dispatch failed: %s; the change was discarded - call"
            " ctx.rerender() in the handler to handle conflicts yourself",
            click.interaction.id,
            click.meta.name,
            exc,
        )
    except Exception:
        _LOGGER.exception(
            "interaction %s on view %s mutated durable state without calling rerender(), and"
            " committing it at the end of the dispatch failed",
            click.interaction.id,
            click.meta.name,
        )


async def _release(click: _Click, session: backend.StateSession) -> None:
    try:
        await session.close()
    except Exception:
        _LOGGER.exception(
            "releasing the state of view %s after interaction %s failed",
            click.meta.name,
            click.interaction.id,
        )


async def _acknowledge(click: _Click) -> None:
    try:
        await click.ctx.acknowledge()
    except Exception:
        _LOGGER.exception(
            "failed to acknowledge interaction %s after its handler finished; if the handler"
            " ran longer than Discord's %.1fs window the interaction is already lost -"
            " respond, defer, or enable auto_defer",
            click.interaction.id,
            constants.INTERACTION_WINDOW,
        )


async def _stand_down(task: asyncio.Task[None] | None, gate: context.ResponseGate) -> None:
    if task is None:
        return
    async with gate.lock:
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
    except Exception:
        _LOGGER.exception("the auto-defer watchdog raised while being stood down")


def _fingerprint_mismatch(
    custom_id: codec.CustomID,
    meta: registry.ViewMeta,
    record: registry.HandlerRecord,
) -> errors.SignatureMismatchError | None:
    signature = record.signature
    tail = custom_id.tail

    if not signature.converters:
        if not tail:
            return None
        return errors.SignatureMismatchError(
            meta.name,
            record.handler_id,
            record.version,
            found=tail[: codec.FINGERPRINT_LENGTH],
            expected="",
        )
    if len(tail) < codec.FINGERPRINT_LENGTH:
        return errors.SignatureMismatchError(
            meta.name,
            record.handler_id,
            record.version,
            found="",
            expected=signature.fingerprint,
        )
    if tail[: codec.FINGERPRINT_LENGTH] != signature.fingerprint:
        return errors.SignatureMismatchError(
            meta.name,
            record.handler_id,
            record.version,
            found=tail[: codec.FINGERPRINT_LENGTH],
            expected=signature.fingerprint,
        )
    return None


def _decode_args(
    custom_id: codec.CustomID,
    meta: registry.ViewMeta,
    record: registry.HandlerRecord,
    interaction_id: hikari.Snowflake,
) -> list[object] | None:
    signature = record.signature
    if not signature.converters:
        return []

    frames = codec.unpack_frames(custom_id.tail[codec.FINGERPRINT_LENGTH :])
    if frames is None:
        _LOGGER.warning(
            "interaction %s routes to handler %r (version %d) of view %s, but its wire argument"
            " frames are unreadable; the component may be forged or corrupted",
            interaction_id,
            record.handler_id,
            record.version,
            meta.name,
        )
        return None

    if len(frames) < signature.required:
        _LOGGER.error(
            "interaction %s: handler %r (version %d) of view %s received %d wire argument"
            " frames but now requires %d; a parameter default was removed in place - bump the"
            " handler version to retire the old components",
            interaction_id,
            record.handler_id,
            record.version,
            meta.name,
            len(frames),
            signature.required,
        )
        return None
    if len(frames) > len(signature.converters):
        _LOGGER.warning(
            "interaction %s routes to handler %r (version %d) of view %s with %d wire argument"
            " frames, but the handler accepts at most %d; the component may be forged"
            " or corrupted",
            interaction_id,
            record.handler_id,
            record.version,
            meta.name,
            len(frames),
            len(signature.converters),
        )
        return None

    return _decode_frames(frames, custom_id.codec, meta, record, interaction_id)


def _decode_frames(
    frames: list[str],
    wire_codec: codecs.Codec,
    meta: registry.ViewMeta,
    record: registry.HandlerRecord,
    interaction_id: hikari.Snowflake,
) -> list[object] | None:
    signature = record.signature
    decoded: list[object] = []
    for (name, converter), frame in zip(signature.converters.items(), frames, strict=False):
        data = codec.decode_text(frame, wire_codec)
        if isinstance(data, errors.CodecUnavailableError):
            _LOGGER.error(
                "interaction %s routes to handler %r (version %d) of view %s, but the component was"
                " written with codec %r, which needs the optional dependency %r: pip install"
                " hikari-risa[%s]",
                interaction_id,
                record.handler_id,
                record.version,
                meta.name,
                data.codec_name,
                data.extra,
                data.extra,
            )
            return None
        if data is None:
            _LOGGER.warning(
                "interaction %s routes to handler %r (version %d) of view %s, but wire argument %r"
                " is not valid %s text; the component may be forged or corrupted",
                interaction_id,
                record.handler_id,
                record.version,
                meta.name,
                name,
                wire_codec.name,
            )
            return None
        try:
            value = converter.decode(data)
        except Exception:
            _LOGGER.warning(
                "interaction %s routes to handler %r (version %d) of view %s, but decoding wire"
                " argument %r raised; the component may be forged, or the converter cannot"
                " handle a stale value",
                interaction_id,
                record.handler_id,
                record.version,
                meta.name,
                name,
                exc_info=True,
            )
            return None
        if value is None:
            _LOGGER.warning(
                "interaction %s routes to handler %r (version %d) of view %s, but wire argument"
                " %r could not be decoded; the component may be forged, or reference a value"
                " that no longer exists",
                interaction_id,
                record.handler_id,
                record.version,
                meta.name,
                name,
            )
            return None
        decoded.append(value)
    return decoded
