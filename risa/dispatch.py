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

import hikari
import msgspec

from risa import context
from risa import di as di_
from risa import errors
from risa import view as view_
from risa.internal import codec
from risa.internal import constants

if typing.TYPE_CHECKING:
    import collections.abc

    import linkd

    from risa.internal import registry

__all__ = ("Dispatcher",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.dispatch")


class _Route(msgspec.Struct, frozen=True):
    meta: registry.ViewMeta
    handler: registry.HandlerRecord | None = None
    decoded: collections.abc.Sequence[object] = ()


class _Prepared(msgspec.Struct, frozen=True):
    view: view_.View
    ctx: context.ComponentContext


class Dispatcher:
    __slots__ = ("_auto_defer", "_auto_defer_delay", "_di", "_resolve_view", "_rest")

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        di: linkd.DependencyInjectionManager,
        resolve_view: collections.abc.Callable[[str], registry.ViewMeta | None],
        *,
        auto_defer: view_.AutoDefer,
        auto_defer_delay: float,
    ) -> None:
        self._rest = rest
        self._di = di
        self._resolve_view = resolve_view
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
            return _Route(meta=meta)

        mismatch = _fingerprint_mismatch(custom_id, meta, record)
        if mismatch is not None:
            _LOGGER.error("interaction %s: %s", interaction.id, mismatch)  # ERROR always
            return _Route(meta=meta)

        decoded = _decode_args(custom_id, meta, record, interaction.id)
        if decoded is None:
            return None
        return _Route(meta=meta, handler=record, decoded=decoded)

    async def _dispatch(
        self,
        interaction: hikari.ComponentInteraction,
        gate: context.ResponseGate,
    ) -> None:
        route = self._route(interaction)
        if route is None:
            return
        gate.adopted = True
        if route.handler is None:
            await self._run_outdated(interaction, route, gate)
            return

        never_ran = f"so handler {route.handler.handler_id!r} (version {route.handler.version}) never ran"
        made = self._make_context(interaction, route.meta, gate, never_ran)
        if made is None:
            return
        await self._run_handler(interaction, route, gate, made)

    async def _run_handler(
        self,
        interaction: hikari.ComponentInteraction,
        route: _Route,
        gate: context.ResponseGate,
        made: _Prepared,
    ) -> None:
        if route.handler is None:
            return
        view, ctx = made.view, made.ctx
        resolved_defer = route.handler.defer
        if resolved_defer is None:
            resolved_defer = self._auto_defer
        watchdog: asyncio.Task[None] | None = None
        if resolved_defer is not view_.AutoDefer.OFF:
            watchdog = asyncio.create_task(self._auto_defer_task(ctx, resolved_defer))
        try:
            async with (
                self._di.enter_context(di_.Contexts.DEFAULT),
                self._di.enter_context(di_.Contexts.COMPONENT) as container,
            ):
                container.add_value(context.ComponentContext, ctx)
                container.add_value(hikari.ComponentInteraction, interaction)
                await route.handler.callback(view, ctx, *route.decoded)
        except Exception:
            await _stand_down(watchdog, gate)
            _LOGGER.exception(
                "handler %r (version %d) of view %s failed while answering interaction %s",
                route.handler.handler_id,
                route.handler.version,
                route.meta.name,
                interaction.id,
            )
        else:
            if resolved_defer is view_.AutoDefer.OFF:
                return
            await _stand_down(watchdog, gate)
            try:
                await ctx.acknowledge()
            except Exception:
                _LOGGER.exception(
                    "failed to acknowledge interaction %s after its handler finished; if the handler"
                    " ran longer than Discord's %.1fs window the interaction is already lost -"
                    " respond, defer, or enable auto_defer",
                    interaction.id,
                    constants.INTERACTION_WINDOW,
                )
        finally:
            if watchdog is not None:
                watchdog.cancel()

    async def _run_outdated(
        self,
        interaction: hikari.ComponentInteraction,
        route: _Route,
        gate: context.ResponseGate,
    ) -> None:
        outdated = route.meta.outdated
        if outdated is None:
            return
        made = self._make_context(interaction, route.meta, gate, "so its on_outdated hook never ran")
        if made is None:
            return
        _view, ctx = made.view, made.ctx
        try:
            async with (
                self._di.enter_context(di_.Contexts.DEFAULT),
                self._di.enter_context(di_.Contexts.COMPONENT) as container,
            ):
                container.add_value(context.ComponentContext, ctx)
                container.add_value(hikari.ComponentInteraction, interaction)
                await outdated(ctx)
        except Exception:
            _LOGGER.exception(
                "on_outdated of view %s failed while answering interaction %s",
                route.meta.name,
                interaction.id,
            )

    def _make_context(
        self,
        interaction: hikari.ComponentInteraction,
        meta: registry.ViewMeta,
        gate: context.ResponseGate,
        detail: str,
    ) -> _Prepared | None:
        try:
            view = meta.cls()
        except Exception:
            _LOGGER.exception(
                "view %s could not be constructed to answer interaction %s, %s",
                meta.name,
                interaction.id,
                detail,
            )
            return None
        return _Prepared(view, context.ComponentContext(interaction, rest=self._rest, gate=gate, view=view, meta=meta))

    async def _auto_defer_task(self, ctx: context.ComponentContext, defer: view_.AutoDefer) -> None:
        await asyncio.sleep(self._auto_defer_delay)
        thinking = defer in {view_.AutoDefer.THINKING, view_.AutoDefer.THINKING_EPHEMERAL}
        ephemeral = defer is view_.AutoDefer.THINKING_EPHEMERAL
        try:
            await ctx.acknowledge(thinking=thinking, ephemeral=ephemeral)
        except Exception:
            _LOGGER.exception("auto-defer failed to acknowledge interaction %s", ctx.interaction.id)


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

    return _decode_frames(frames, meta, record, interaction_id)


def _decode_frames(
    frames: list[str],
    meta: registry.ViewMeta,
    record: registry.HandlerRecord,
    interaction_id: hikari.Snowflake,
) -> list[object] | None:
    signature = record.signature
    decoded: list[object] = []
    for (name, converter), frame in zip(signature.converters.items(), frames, strict=False):
        try:
            value = converter.decode(frame)
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
