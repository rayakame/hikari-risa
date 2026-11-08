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
"""The path of one click, from `custom_id` to acknowledgement.

route → state session `open` → `restore` (or `on_state_missing` / `on_outdated`) →
`load()` → handler → the end-of-dispatch commit → `close` → acknowledge. The
auto-defer watchdog runs beside it from the moment the click is routed.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import operator
import typing

import hikari
import linkd
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

    from risa import codecs
    from risa.internal import registry
    from risa.ui import build as build_

__all__ = ("Dispatcher",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.dispatch")

type BackendResolver = collections.abc.Callable[[registry.ViewMeta], backend.StateBackend]
type _InjectedErrorHandler = collections.abc.Callable[[context.ClickFailure], collections.abc.Awaitable[bool]]
type ViewBuilder = collections.abc.Callable[[view_.View, hikari.Snowflake], collections.abc.Awaitable[build_.Rendered]]


class _Outdated(enum.Enum):
    OUTDATED = enum.auto()


_OUTDATED: typing.Final = _Outdated.OUTDATED


class _Route(msgspec.Struct, frozen=True):
    meta: registry.ViewMeta
    component: codec.CustomID
    handler: registry.HandlerRecord | None = None
    decoded: collections.abc.Sequence[object] = ()
    unavailable: errors.CodecUnavailableError | None = None


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
    """Runs clicks through the views a client routes.

    Parameters
    ----------
    rest
        The REST client interactions are answered through.
    di
        The dependency manager handlers and hooks are injected from.
    resolve_view
        Finds the view for a cookie.
    backend_for
        Finds the state backend for a view, and raises `ViewDeclarationError` when its store
        is not configured.
    auto_defer
        The client's `AutoDefer`, for handlers and views that set none.
    auto_defer_delay
        Seconds after which the watchdog answers in a handler's place.
    build
        Builds a view for `ComponentContext.replace` over the message with the given id.
    """

    __slots__ = (
        "_auto_defer",
        "_auto_defer_delay",
        "_backend_for",
        "_build",
        "_di",
        "_error_handlers",
        "_resolve_view",
        "_rest",
    )

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        di: linkd.DependencyInjectionManager,
        resolve_view: collections.abc.Callable[[str], registry.ViewMeta | None],
        backend_for: BackendResolver,
        *,
        auto_defer: view_.AutoDefer,
        auto_defer_delay: float,
        build: ViewBuilder,
    ) -> None:
        self._rest = rest
        self._di = di
        self._resolve_view = resolve_view
        self._backend_for = backend_for
        self._auto_defer = auto_defer
        self._auto_defer_delay = auto_defer_delay
        self._build = build
        self._error_handlers: list[tuple[int, int, _InjectedErrorHandler]] = []

    def add_error_handler(self, handler: context.ErrorHandler, *, priority: int) -> None:
        """Run `handler` on failed clicks, higher `priority` first and in registration order within one."""
        injected = typing.cast("_InjectedErrorHandler", linkd.inject(handler))
        self._error_handlers.append((-priority, len(self._error_handlers), injected))
        self._error_handlers.sort(key=operator.itemgetter(0, 1))

    async def process(
        self,
        interaction: hikari.ComponentInteraction,
        gate: context.ResponseGate | None = None,
    ) -> None:
        """Dispatch one component interaction.

        The gate's `acknowledged` event is set when this returns, whatever happened, so the
        REST transport never waits out its timeout for a click risa ignored or could not
        answer.
        """
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
        """Find where a click goes, or return `None` to ignore it.

        A `custom_id` risa did not write is ignored without a word, and a cookie no view of
        this client has with a DEBUG line, so other handlers can answer the click. A known
        view with an unknown handler token, a mismatched wire signature, too few wire
        arguments or an enum value that no longer exists routes without a handler, which sends
        the click to `on_outdated`.

        Returns
        -------
        _Route | None
            The route, or `None` for a click that is not this client's.
        """
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
        return _route_to_handler(meta, custom_id, interaction)

    async def _dispatch(
        self,
        interaction: hikari.ComponentInteraction,
        gate: context.ResponseGate,
    ) -> None:
        route = self._route(interaction)
        if route is None:
            return
        if route.unavailable is not None and route.handler is not None and not self._error_handlers:
            _log_codec_unavailable(interaction.id, route.meta, route.handler, route.unavailable)
            return
        gate.adopted = True
        slot = context.ViewSlot()
        ctx = context.ComponentContext(
            interaction,
            rest=self._rest,
            meta=route.meta,
            build=self._build,
            gate=gate,
            slot=slot,
        )
        if route.unavailable is not None and route.handler is not None:
            await self._codec_unavailable(ctx, route.meta, route.handler, route.unavailable)
            return
        if route.handler is None:
            await self._run_hook(route.meta, route.meta.outdated, ctx, context.FailureStage.ON_OUTDATED)
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

    async def _codec_unavailable(
        self,
        ctx: context.ComponentContext,
        meta: registry.ViewMeta,
        record: registry.HandlerRecord,
        error: errors.CodecUnavailableError,
    ) -> None:
        if not await self._report_in_scope(ctx, error, context.FailureStage.STATE):
            _log_codec_unavailable(ctx.interaction.id, meta, record, error)

    async def _run_handler(self, click: _Click) -> None:
        try:
            state = self._backend_for(click.meta)
        except errors.ViewDeclarationError as missing:
            if not await self._report_in_scope(click.ctx, missing, context.FailureStage.STATE):
                _LOGGER.error(  # ruff:ignore[error-instead-of-exception]
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
        """Run a routed click inside its state session, and report whether it ran to the end.

        A failure to reach the state goes to the error handlers, or is logged at ERROR. A
        failure after the state was read is logged, and the session is closed whatever happens.

        Returns
        -------
        bool
            Whether the handler ran and nothing failed after it.
        """
        session = state.session(meta=click.meta, component=click.component, interaction=click.interaction)
        try:
            view = await self._reach(click, session, watchdog)
            if view is None:
                return False
            return await self._within(click, session, view, watchdog)
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
            click.slot.view = None
            click.slot.session = None

    async def _reach(
        self,
        click: _Click,
        session: backend.StateSession,
        watchdog: asyncio.Task[None] | None,
    ) -> view_.View | None:
        try:
            await session.open()
            return await self._hydrate(click, session, watchdog)
        except (errors.StoreUnavailableError, errors.CodecUnavailableError, errors.LockTimeoutError) as exc:
            await _stand_down(watchdog, click.gate)
            if not await self._report_in_scope(click.ctx, exc, context.FailureStage.STATE):
                _LOGGER.error(  # ruff:ignore[error-instead-of-exception]
                    "interaction %s routes to view %s, but its state could not be reached: %s",
                    click.interaction.id,
                    click.meta.name,
                    exc,
                )
            return None
        except Exception as exc:
            await _stand_down(watchdog, click.gate)
            if not await self._report_in_scope(click.ctx, exc, context.FailureStage.STATE):
                _LOGGER.exception(
                    "the state session of view %s failed while answering interaction %s",
                    click.meta.name,
                    click.interaction.id,
                )
            return None

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
            await self._run_hook(click.meta, click.meta.outdated, click.ctx, context.FailureStage.ON_OUTDATED)
            return None
        _LOGGER.log(
            logging.DEBUG if click.meta.handles_state_missing else logging.WARNING,
            "interaction %s routes to view %s, but its stored state is gone - expired, evicted or"
            " deleted; override on_state_missing to answer the user",
            click.interaction.id,
            click.meta.name,
        )
        await self._run_hook(click.meta, click.meta.state_missing, click.ctx, context.FailureStage.ON_STATE_MISSING)
        return None

    async def _within(
        self,
        click: _Click,
        session: backend.StateSession,
        view: view_.View,
        watchdog: asyncio.Task[None] | None,
    ) -> bool:
        if not await self._call(click, view, watchdog):
            await _stand_down(watchdog, click.gate)
            return False
        if click.slot.view is not None:
            await _commit_leftovers(click, session, view)
        return True

    async def _call(self, click: _Click, view: view_.View, watchdog: asyncio.Task[None] | None) -> bool:
        async with self._scope(click.ctx):
            if click.meta.loader is not None:
                try:
                    await click.meta.loader(view)
                except Exception as exc:
                    if await self._failed(click, watchdog, exc, context.FailureStage.LOAD):
                        return False
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
            except Exception as exc:
                if await self._failed(click, watchdog, exc, context.FailureStage.HANDLER):
                    return False
                _LOGGER.exception(
                    "handler %r (version %d) of view %s failed while answering interaction %s",
                    click.record.handler_id,
                    click.record.version,
                    click.meta.name,
                    click.interaction.id,
                )
                return False
        return True

    async def _failed(
        self,
        click: _Click,
        watchdog: asyncio.Task[None] | None,
        error: Exception,
        stage: context.FailureStage,
    ) -> bool:
        """Hand a failure of the bot's code to the error handlers, and report whether one took it.

        The watchdog stands down first, and the view leaves the context, so an error handler
        answers the click itself and cannot save the state the failure left behind.

        Returns
        -------
        bool
            Whether an error handler returned `True`.
        """
        if not self._error_handlers:
            return False
        await _stand_down(watchdog, click.gate)
        click.slot.view = None
        click.slot.session = None
        return await self._report(click.ctx, error, stage)

    async def _run_hook(
        self,
        meta: registry.ViewMeta,
        hook: registry.HookCallback | None,
        ctx: context.ComponentContext,
        stage: context.FailureStage,
    ) -> None:
        if hook is None:
            return
        try:
            async with self._scope(ctx):
                await self._hook_or_report(hook, ctx, stage)
        except Exception:
            _LOGGER.exception(
                "%s of view %s failed while answering interaction %s",
                stage,
                meta.name,
                ctx.interaction.id,
            )

    async def _hook_or_report(
        self,
        hook: registry.HookCallback,
        ctx: context.ComponentContext,
        stage: context.FailureStage,
    ) -> None:
        try:
            await hook(ctx)
        except Exception as exc:
            if not await self._report(ctx, exc, stage):
                raise

    async def _report_in_scope(
        self,
        ctx: context.ComponentContext,
        error: Exception,
        stage: context.FailureStage,
    ) -> bool:
        if not self._error_handlers:
            return False
        handled = False
        try:
            async with self._scope(ctx):
                handled = await self._report(ctx, error, stage)
        except Exception:
            _LOGGER.exception("the dependency scope of the error handlers of interaction %s failed", ctx.interaction.id)
        return handled

    async def _report(self, ctx: context.ComponentContext, error: Exception, stage: context.FailureStage) -> bool:
        failure = context.ClickFailure(ctx=ctx, error=error, stage=stage)
        for _, _, handler in tuple(self._error_handlers):
            try:
                if await handler(failure):
                    return True
            except Exception:
                _LOGGER.exception(
                    "an error handler failed while handling a failure in %s of interaction %s",
                    stage,
                    ctx.interaction.id,
                )
        return False

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
    """Save durable changes the handler made without rerendering.

    The commit is a plain `rerender()`. Nobody is left to raise to, so a failure is
    logged. A lost race discards the change. An `InStore` commit that fails after the
    store accepted it, while editing the message, leaves the state saved and only the
    message behind, which the next click redraws.
    """
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
    except (errors.StateConflictError, errors.StateNotFoundError, errors.StateUnreadableError) as exc:
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
    """Stop the watchdog before risa answers a click itself.

    If the watchdog has already started acknowledging, this waits for it to finish, so
    risa's own answer cannot race it.

    Raises
    ------
    CancelledError
        If the caller itself is cancelled while waiting.
    """
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


def _log_codec_unavailable(
    interaction_id: hikari.Snowflake,
    meta: registry.ViewMeta,
    record: registry.HandlerRecord,
    error: errors.CodecUnavailableError,
) -> None:
    _LOGGER.error(
        "interaction %s routes to handler %r (version %d) of view %s, but the component was"
        " written with codec %r, which needs the optional dependency %r: pip install"
        " hikari-risa[%s]",
        interaction_id,
        record.handler_id,
        record.version,
        meta.name,
        error.codec_name,
        error.extra,
        error.extra,
    )


def _route_to_handler(
    meta: registry.ViewMeta,
    custom_id: codec.CustomID,
    interaction: hikari.ComponentInteraction,
) -> _Route | None:
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
    if decoded is _OUTDATED:
        return _Route(meta=meta, component=custom_id)
    if isinstance(decoded, errors.CodecUnavailableError):
        return _Route(meta=meta, component=custom_id, handler=record, unavailable=decoded)
    return _Route(meta=meta, component=custom_id, handler=record, decoded=decoded)


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
) -> list[object] | _Outdated | errors.CodecUnavailableError | None:
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
        given = _decode_frames(frames, custom_id.codec, meta, record, interaction_id)
        if given is None or isinstance(given, errors.CodecUnavailableError):
            return given
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
        return _OUTDATED
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
) -> list[object] | _Outdated | errors.CodecUnavailableError | None:
    signature = record.signature
    decoded: list[object] = []
    outdated = False
    for (name, converter), frame in zip(signature.converters.items(), frames, strict=False):
        data = codec.decode_text(frame, wire_codec)
        if isinstance(data, errors.CodecUnavailableError):
            return data
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
            if not (isinstance(converter, codec.EnumConverter) and converter.reads(data)):
                return None
            outdated = True
            continue
        decoded.append(value)
    return _OUTDATED if outdated else decoded
