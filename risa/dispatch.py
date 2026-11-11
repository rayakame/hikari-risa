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

route → one dependency scope and pipeline for the click → state session `open` → `restore`
→ the execution steps, where INVOKE is `load()`, the handler and the end-of-dispatch commit,
or `on_outdated` / `on_state_missing` for a click without a view → the error handlers if
anything failed → `close` → acknowledge. The auto-defer watchdog runs beside it from the
moment the click is routed.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import functools
import logging
import operator
import typing

import hikari
import linkd
import msgspec

from risa import context
from risa import di as di_
from risa import errors
from risa import hooks as hooks_
from risa import view as view_
from risa.internal import codec
from risa.internal import constants
from risa.state import backend

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import codecs
    from risa.internal import registry
    from risa.ui import build as build_

__all__ = ("Dispatcher",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.dispatch")

# What keeps a click from reaching its state without a bug: logged as one line, without a traceback.
_ENVIRONMENT_ERRORS: typing.Final = (
    errors.StoreUnavailableError,
    errors.LockTimeoutError,
    errors.CodecUnavailableError,
    errors.ViewDeclarationError,
)
_STAGE_NAMES: typing.Final[collections.abc.Mapping[hooks_.FailureStage, str]] = {
    hooks_.FailureStage.HANDLER: "the handler",
    hooks_.FailureStage.LOAD: "load()",
    hooks_.FailureStage.ON_OUTDATED: "on_outdated",
    hooks_.FailureStage.ON_STATE_MISSING: "on_state_missing",
    hooks_.FailureStage.STATE: "reaching its state",
}

type BackendResolver = collections.abc.Callable[[registry.ViewMeta], backend.StateBackend]
type _InjectedErrorHandler = collections.abc.Callable[
    [hooks_.ExecutionPipeline, context.ComponentContext],
    collections.abc.Awaitable[bool],
]
type _Invoke = collections.abc.Callable[[], collections.abc.Awaitable[None]]
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


class _Click(msgspec.Struct):
    """One routed click.

    `failures` is the list the click's pipeline shows. `invoked` says whether the handler
    returned without raising, and `saved` whether it left the state saved, so a change made
    after it can be told apart.
    """

    interaction: hikari.ComponentInteraction
    meta: registry.ViewMeta
    component: codec.CustomID
    record: registry.HandlerRecord | None
    decoded: collections.abc.Sequence[object]
    ctx: context.ComponentContext
    slot: context.ViewSlot
    gate: context.ResponseGate
    failures: list[hooks_.Failure]
    pipeline: hooks_.ExecutionPipeline
    invoked: bool = False
    saved: bool = False


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
    hooks
        The client's hooks.
    order
        The execution steps, in the order they run.
    """

    __slots__ = (
        "_auto_defer",
        "_auto_defer_delay",
        "_backend_for",
        "_build",
        "_di",
        "_error_handlers",
        "_hooks",
        "_order",
        "_resolve_view",
        "_rest",
    )

    def __init__(  # ruff:ignore[too-many-arguments]
        self,
        rest: hikari.api.RESTClient,
        di: linkd.DependencyInjectionManager,
        resolve_view: collections.abc.Callable[[str], registry.ViewMeta | None],
        backend_for: BackendResolver,
        *,
        auto_defer: view_.AutoDefer,
        auto_defer_delay: float,
        build: ViewBuilder,
        hooks: collections.abc.Sequence[hooks_.ExecutionHook[view_.View]],
        order: collections.abc.Sequence[hooks_.ExecutionStep],
    ) -> None:
        self._rest = rest
        self._di = di
        self._resolve_view = resolve_view
        self._backend_for = backend_for
        self._auto_defer = auto_defer
        self._auto_defer_delay = auto_defer_delay
        self._build = build
        self._hooks = tuple(hooks)
        self._order = tuple(order)
        self._error_handlers: list[tuple[int, int, _InjectedErrorHandler]] = []

    def add_error_handler(self, handler: hooks_.ErrorHandler, *, priority: int) -> None:
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
        failures: list[hooks_.Failure] = []
        click = _Click(
            interaction=interaction,
            meta=route.meta,
            component=route.component,
            record=route.handler,
            decoded=route.decoded,
            ctx=context.ComponentContext(
                interaction,
                rest=self._rest,
                meta=route.meta,
                build=self._build,
                gate=gate,
                slot=slot,
            ),
            slot=slot,
            gate=gate,
            failures=failures,
            pipeline=hooks_.ExecutionPipeline(failures),
        )
        try:
            async with self._scope(click):
                await self._run(click, route.unavailable)
        except Exception:
            _LOGGER.exception("risa failed while answering interaction %s on view %s", interaction.id, route.meta.name)

    async def _run(self, click: _Click, unavailable: errors.CodecUnavailableError | None) -> None:
        if unavailable is not None:
            _fail(click, hooks_.Failure(unavailable, hooks_.FailureStage.STATE))
            await self._report(click)
        elif click.record is None:
            async with self._watchdog(click, click.meta.defer) as watchdog:
                await self._run_steps(click, None, functools.partial(_fall_back, click, watchdog, outdated=True))
                await _stand_down(watchdog, click.gate)
                await self._report(click)
        else:
            await self._run_handler(click, click.record)

    async def _run_handler(self, click: _Click, record: registry.HandlerRecord) -> None:
        try:
            state = self._backend_for(click.meta)
        except errors.ViewDeclarationError as missing:
            _fail(click, hooks_.Failure(missing, hooks_.FailureStage.STATE))
            await self._report(click)
            return
        async with self._watchdog(click, record.defer) as watchdog:
            if await self._transact(state, click, record, watchdog) and watchdog is not None:
                await _stand_down(watchdog, click.gate)
                await _acknowledge(click)

    async def _transact(
        self,
        state: backend.StateBackend,
        click: _Click,
        record: registry.HandlerRecord,
        watchdog: asyncio.Task[None] | None,
    ) -> bool:
        """Run a routed click inside its state session, and report whether its handler ran through.

        The error handlers run before the session closes, and the session is closed whatever
        happens.

        Returns
        -------
        bool
            Whether the handler returned without raising, so the click is owed an
            acknowledgement even if a hook after it failed.
        """
        session = state.session(meta=click.meta, component=click.component, interaction=click.interaction)
        try:
            return await self._within(click, record, session, watchdog)
        finally:
            await _release(click, session)
            click.slot.view = None
            click.slot.session = None

    async def _within(
        self,
        click: _Click,
        record: registry.HandlerRecord,
        session: backend.StateSession,
        watchdog: asyncio.Task[None] | None,
    ) -> bool:
        restored = await _reach(click, session)
        if isinstance(restored, backend.Restored):
            click.slot.view = restored.view
            click.slot.session = session
            invoke = functools.partial(_invoke, click, record, session, restored.view)
            await self._run_steps(click, restored.view, invoke)
            await _warn_if_unsaved(click, session, restored.view)
        elif restored is not None:
            outdated = restored is backend.Unreadably.OUTDATED
            if not outdated:
                _log_state_missing(click)
            await self._run_steps(click, None, functools.partial(_fall_back, click, watchdog, outdated=outdated))
        if click.failures:
            await _stand_down(watchdog, click.gate)
            await self._report(click)
        return click.invoked

    async def _run_steps(self, click: _Click, view: view_.View | None, invoke: _Invoke) -> None:
        own = click.record.hooks if click.record is not None else ()
        attached = (*self._hooks, *click.meta.hooks, *own)
        for step in self._order:
            if step == hooks_.ExecutionSteps.INVOKE:
                if not click.failures:
                    await invoke()
                continue
            for hook in attached:
                if hook.step == step and not (hook.skip_when_failed and click.failures):
                    await _guard(
                        click, hooks_.FailureStage.HOOK, functools.partial(_call_hook, click, hook, view), hook
                    )

    async def _report(self, click: _Click) -> None:
        """Run the error handlers on a click that failed, and log it if none dealt with it."""
        if not click.failures:
            return
        for _, _, handler in tuple(self._error_handlers):
            try:
                if await handler(click.pipeline, click.ctx):
                    return
            except Exception:
                _LOGGER.exception("an error handler failed while handling interaction %s", click.interaction.id)
        _log_unhandled(click)

    @contextlib.asynccontextmanager
    async def _watchdog(
        self,
        click: _Click,
        defer: view_.AutoDefer | None,
    ) -> collections.abc.AsyncGenerator[asyncio.Task[None] | None]:
        resolved = defer if defer is not None else self._auto_defer
        task = (
            None if resolved is view_.AutoDefer.OFF else asyncio.create_task(self._auto_defer_task(click.ctx, resolved))
        )
        try:
            yield task
        finally:
            if task is not None:
                task.cancel()

    @contextlib.asynccontextmanager
    async def _scope(self, click: _Click) -> collections.abc.AsyncGenerator[None]:
        async with (
            self._di.enter_context(di_.Contexts.DEFAULT),
            self._di.enter_context(di_.Contexts.COMPONENT) as container,
        ):
            container.add_value(context.ComponentContext, click.ctx)
            container.add_value(hikari.ComponentInteraction, click.interaction)
            container.add_value(hooks_.ExecutionPipeline, click.pipeline)
            yield

    async def _auto_defer_task(self, ctx: context.ComponentContext, defer: view_.AutoDefer) -> None:
        await asyncio.sleep(self._auto_defer_delay)
        thinking = defer in {view_.AutoDefer.THINKING, view_.AutoDefer.THINKING_EPHEMERAL}
        ephemeral = defer is view_.AutoDefer.THINKING_EPHEMERAL
        try:
            await ctx.acknowledge(thinking=thinking, ephemeral=ephemeral)
        except Exception:
            _LOGGER.exception("auto-defer failed to acknowledge interaction %s", ctx.interaction.id)


def _fail(click: _Click, failure: hooks_.Failure) -> None:
    """Record a failure; from the first one on, the context has no view, so nothing half-changed is saved."""
    click.failures.append(failure)
    click.slot.view = None
    click.slot.session = None


async def _guard(
    click: _Click,
    stage: hooks_.FailureStage,
    call: collections.abc.Callable[[], collections.abc.Awaitable[object]],
    hook: hooks_.AnyHook | None = None,
) -> bool:
    """Await `call`, record what it raises as a failure of the click, and report whether it ran through.

    Returns
    -------
    bool
        Whether `call` returned without raising.
    """
    try:
        await call()
    except Exception as exc:  # ruff:ignore[blind-except]
        _fail(click, hooks_.Failure(exc, stage, hook))
        return False
    return True


async def _reach(click: _Click, session: backend.StateSession) -> backend.Restored | backend.Unreadably | None:
    try:
        await session.open()
        return await session.restore()
    except Exception as exc:  # ruff:ignore[blind-except]
        _fail(click, hooks_.Failure(exc, hooks_.FailureStage.STATE))
        return None


def _log_state_missing(click: _Click) -> None:
    _LOGGER.log(
        logging.DEBUG if click.meta.handles_state_missing else logging.WARNING,
        "interaction %s routes to view %s, but its stored state is gone - expired, evicted or"
        " deleted; override on_state_missing to answer the user",
        click.interaction.id,
        click.meta.name,
    )


async def _call_hook(click: _Click, hook: hooks_.AnyHook, view: view_.View | None) -> None:
    """Run a hook, unless it takes a view and the click has none.

    Raises
    ------
    HookDeclarationError
        If the hook's annotations cannot be resolved, or it takes a view of another class,
        which `Client.add_view` catches first for every view added to a client.
    """
    view_type = hook.view_type
    if view_type is not None and view is None:
        return
    if view_type is not None and not isinstance(view, view_type):
        reason = f"it takes a {view_type.__name__}, but ran for view {click.meta.name!r}"
        raise errors.HookDeclarationError(hook.name, reason)
    await typing.cast("hooks_.ExecutionHook[view_.View]", hook)(click.pipeline, click.ctx, view)


async def _invoke(
    click: _Click,
    record: registry.HandlerRecord,
    session: backend.StateSession,
    view: view_.View,
) -> None:
    loader = click.meta.loader
    if loader is not None and not await _guard(click, hooks_.FailureStage.LOAD, functools.partial(loader, view)):
        return
    handle = functools.partial(record.callback, view, click.ctx, *click.decoded)
    if await _guard(click, hooks_.FailureStage.HANDLER, handle):
        click.invoked = True
        click.saved = click.slot.view is None or await _commit_leftovers(click, session, view)


async def _fall_back(click: _Click, watchdog: asyncio.Task[None] | None, *, outdated: bool) -> None:
    await _stand_down(watchdog, click.gate)
    callback = click.meta.outdated if outdated else click.meta.state_missing
    stage = hooks_.FailureStage.ON_OUTDATED if outdated else hooks_.FailureStage.ON_STATE_MISSING
    if callback is not None:
        await _guard(click, stage, functools.partial(callback, click.ctx))


async def _warn_if_unsaved(click: _Click, session: backend.StateSession, view: view_.View) -> None:
    if not click.saved or click.slot.view is None:
        return
    try:
        dirty = await session.settle(view)
    except Exception:
        _LOGGER.exception(
            "settling the state of view %s after the hooks of interaction %s failed",
            click.meta.name,
            click.interaction.id,
        )
        return
    if dirty:
        _LOGGER.warning(
            "interaction %s on view %s: a hook after the handler changed durable state without"
            " ctx.rerender(), so the change was not saved",
            click.interaction.id,
            click.meta.name,
        )


def _describe(click: _Click, failure: hooks_.Failure) -> str:
    if failure.hook is not None:
        return f"hook {failure.hook.name!r} ({failure.hook.step.name})"
    if failure.stage is hooks_.FailureStage.HANDLER and click.record is not None:
        return f"handler {click.record.handler_id!r} (version {click.record.version})"
    return _STAGE_NAMES[failure.stage]


def _log_unhandled(click: _Click) -> None:
    first = click.failures[0]
    if first.stage is hooks_.FailureStage.STATE and isinstance(first.error, _ENVIRONMENT_ERRORS):
        _LOGGER.error(
            "interaction %s on view %s could not be answered: %s", click.interaction.id, click.meta.name, first.error
        )
        return
    where = [_describe(click, failure) for failure in click.failures]
    _LOGGER.error(
        "interaction %s on view %s failed in %s",
        click.interaction.id,
        click.meta.name,
        where[0] if len(where) == 1 else f"{', '.join(where[:-1])} and {where[-1]}",
        exc_info=ExceptionGroup(
            f"interaction {click.interaction.id} failed",
            [failure.error for failure in click.failures],
        ),
    )


def _log_caught_overflow(click: _Click) -> None:
    _LOGGER.debug(
        "interaction %s on view %s left a state that already overflowed in rerender() or edit(), which"
        " the handler caught; it could not be saved when the handler returned either",
        click.interaction.id,
        click.meta.name,
    )


async def _commit_leftovers(click: _Click, session: backend.StateSession, view: view_.View) -> bool:
    """Save durable changes the handler made without rerendering, and report whether the state is saved now.

    The commit is a plain `rerender()`. Nobody is left to raise to, so a failure is
    logged. A lost race discards the change. An `InStore` commit that fails after the
    store accepted it, while editing the message, leaves the state saved and only the
    message behind, which the next click redraws. A state that overflowed earlier in the
    click, which the handler caught, is still tried, since `render()` may hold more than the
    layout that overflowed; if it fails again, only a DEBUG line is logged.

    Returns
    -------
    bool
        Whether the state was already saved or is saved now.
    """
    try:
        dirty = await session.settle(view)
    except Exception:
        _LOGGER.exception(
            "settling the state of view %s after interaction %s failed",
            click.meta.name,
            click.interaction.id,
        )
        return False
    if not dirty:
        return True
    caught = bool(click.slot.overflowed) and click.meta.schema.pack_view(view) in click.slot.overflowed
    try:
        await click.ctx.rerender()
    except errors.EphemeralOriginError:
        if caught:
            _log_caught_overflow(click)
            return False
        _LOGGER.warning(
            "interaction %s on view %s left durable state unsaved, but the handler had already"
            " answered and the origin message is ephemeral, so the change could"
            " not be written; call ctx.rerender() before ctx.respond()",
            click.interaction.id,
            click.meta.name,
        )
    except (errors.StateConflictError, errors.StateNotFoundError, errors.StateUnreadableError) as exc:
        _LOGGER.warning(
            "interaction %s on view %s left durable state unsaved, and committing it at the end of"
            " the dispatch failed: %s; the change was discarded - call"
            " ctx.rerender() in the handler to handle conflicts yourself",
            click.interaction.id,
            click.meta.name,
            exc,
        )
    except Exception as exc:
        if caught and isinstance(exc, errors.StateOverflowError):
            _log_caught_overflow(click)
            return False
        _LOGGER.exception(
            "interaction %s on view %s left durable state unsaved, and committing it at the end of the dispatch failed",
            click.interaction.id,
            click.meta.name,
        )
    else:
        return True
    return False


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
