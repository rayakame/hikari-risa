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

import abc
import asyncio
import logging
import typing

import hikari
import linkd
import msgspec

from risa import context
from risa import di as di_
from risa import errors
from risa import ui
from risa import view as view_
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry
from risa.ui import build as build_

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "Client",
    "GatewayClientAppT",
    "GatewayEnabledClient",
    "LightbulbClient",
    "RestClientAppT",
    "RestEnabledClient",
    "client_from_app",
    "client_from_lightbulb",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.client")


@typing.runtime_checkable
class GatewayClientAppT(hikari.EventManagerAware, hikari.RESTAware, typing.Protocol): ...


@typing.runtime_checkable
class RestClientAppT(hikari.InteractionServerAware, hikari.RESTAware, typing.Protocol): ...


@typing.runtime_checkable
class LightbulbClient(typing.Protocol):
    @property
    def app(self) -> hikari.RESTAware: ...

    @property
    def di(self) -> linkd.DependencyInjectionManager: ...


class _LightbulbOptions(typing.TypedDict, total=False):
    use_global: bool
    auto_defer: view_.AutoDefer
    auto_defer_delay: float


class _ClientOptions(_LightbulbOptions, total=False):
    di: linkd.DependencyInjectionManager | None


class _Route(msgspec.Struct, frozen=True):
    meta: registry.ViewMeta
    handler: registry.HandlerRecord | None = None
    decoded: collections.abc.Sequence[object] = ()


class Client(abc.ABC):
    __slots__ = ("_auto_defer", "_auto_defer_delay", "_di", "_registry", "_rest", "_use_global")

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        *,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
        auto_defer: view_.AutoDefer = view_.AutoDefer.OFF,
        auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
    ) -> None:
        self._di = di if di is not None else linkd.DependencyInjectionManager()
        self._registry = registry.Registry()
        self._rest = rest
        self._use_global = use_global
        self._auto_defer = auto_defer
        self._auto_defer_delay = auto_defer_delay

        if Client in self._di.registry_for(di_.Contexts.DEFAULT):
            _LOGGER.warning(
                "another risa client is already registered on this dependency manager;"
                " `client: risa.Client` injections will resolve to this newest one",
            )
        self._register_dependency(Client, self)
        if register_app_dependencies:
            self._register_dependency(hikari.api.RESTClient, rest)

    @property
    @abc.abstractmethod
    def app(self) -> hikari.RESTAware: ...

    @property
    def di(self) -> linkd.DependencyInjectionManager:
        return self._di

    @property
    def rest(self) -> hikari.api.RESTClient:
        return self._rest

    async def build(self, view: view_.View) -> ui.Rendered:  # ruff:ignore[no-self-use]
        meta = getattr(type(view), constants.VIEW_META, None)
        if not isinstance(meta, registry.ViewMeta):
            raise errors.NotAViewError(type(view).__name__)
        return ui.Rendered(build_(view.render(), meta))

    def add_view[T: view_.View](self, cls: type[T]) -> type[T]:
        meta = getattr(cls, constants.VIEW_META, None)
        if not isinstance(meta, registry.ViewMeta):
            raise errors.NotAViewError(cls.__name__)
        _LOGGER.debug("registering view %s to client", meta.name)
        self._registry.register(meta)
        return cls

    def _register_dependency[T](self, dependency_type: type[T], value: T) -> None:
        try:
            self._di.registry_for(di_.Contexts.DEFAULT).register_value(dependency_type, value)
        except linkd.RegistryFrozenException:
            _LOGGER.warning(
                "could not register %s: the dependency manager is already in use. Build the client before"
                " the manager opens its first container, or pass register_app_dependencies=False if its"
                " owner registers these already.",
                dependency_type,
            )

    def _resolve(self, key: str) -> registry.ViewMeta | None:
        meta = self._registry.get(key)
        if meta is None and self._use_global:
            meta = registry.global_registry().get(key)
        return meta

    async def _process_interaction(
        self,
        interaction: hikari.ComponentInteraction,
        state: context.DispatchState | None = None,
    ) -> None:
        if state is None:
            state = context.DispatchState()
        try:
            await self._dispatch(interaction, state)
        finally:
            state.acknowledged.set()

    def _route(
        self,
        interaction: hikari.ComponentInteraction,
    ) -> _Route | None:
        custom_id = codec.parse_custom_id(interaction.custom_id)
        if custom_id is None:
            return None

        meta = self._resolve(custom_id.cookie)
        if meta is None:
            _LOGGER.debug(
                "interaction %s carries a risa custom_id with cookie %r, but this client has no view for it",
                interaction.id,
                custom_id.cookie,
            )
            return None
        handler = meta.handlers.get(custom_id.handler)
        if handler is None:
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

        mismatch = Client._fingerprint_mismatch(custom_id, meta, handler)
        if mismatch is not None:
            _LOGGER.error("interaction %s: %s", interaction.id, mismatch)  # ERROR always
            return _Route(meta=meta)

        decoded = self._decode_args(custom_id, meta, handler, interaction.id)
        if decoded is None:
            return None
        return _Route(meta=meta, handler=handler, decoded=decoded)

    async def _dispatch(
        self,
        interaction: hikari.ComponentInteraction,
        state: context.DispatchState,
    ) -> None:
        route = self._route(interaction)
        if route is None:
            return
        state.adopted = True
        if route.handler is None:
            await self._run_outdated(interaction, route, state)
            return

        never_ran = f"so handler {route.handler.handler_id!r} (version {route.handler.version}) never ran"
        made = self._make_context(interaction, route.meta, state, never_ran)
        if made is None:
            return
        await self._run_handler(interaction, route, state, made)

    async def _run_handler(
        self,
        interaction: hikari.ComponentInteraction,
        route: _Route,
        state: context.DispatchState,
        made: tuple[view_.View, context.ComponentContext],
    ) -> None:
        if route.handler is None:
            return
        view, ctx = made
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
            await self._stop_auto_defer_task(watchdog, state)
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
            await self._stop_auto_defer_task(watchdog, state)
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

    def _make_context(
        self,
        interaction: hikari.ComponentInteraction,
        meta: registry.ViewMeta,
        state: context.DispatchState,
        detail: str,
    ) -> tuple[view_.View, context.ComponentContext] | None:
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
        return view, context.ComponentContext(interaction, rest=self._rest, state=state, view=view, meta=meta)

    async def _run_outdated(
        self,
        interaction: hikari.ComponentInteraction,
        route: _Route,
        state: context.DispatchState,
    ) -> None:
        if not route.meta.handles_outdated:
            return
        made = self._make_context(interaction, route.meta, state, "so its on_outdated hook never ran")
        if made is None:
            return
        _view, ctx = made
        try:
            async with (
                self._di.enter_context(di_.Contexts.DEFAULT),
                self._di.enter_context(di_.Contexts.COMPONENT) as container,
            ):
                container.add_value(context.ComponentContext, ctx)
                container.add_value(hikari.ComponentInteraction, interaction)
                await route.meta.cls.on_outdated(ctx)
        except Exception:
            _LOGGER.exception(
                "on_outdated of view %s failed while answering interaction %s",
                route.meta.name,
                interaction.id,
            )

    async def _auto_defer_task(self, ctx: context.ComponentContext, defer: view_.AutoDefer) -> None:
        await asyncio.sleep(self._auto_defer_delay)
        if defer is view_.AutoDefer.OFF:
            return
        thinking = defer in {view_.AutoDefer.THINKING, view_.AutoDefer.THINKING_EPHEMERAL}
        ephemeral = defer is view_.AutoDefer.THINKING_EPHEMERAL
        try:
            await ctx.acknowledge(thinking=thinking, ephemeral=ephemeral)
        except Exception:
            _LOGGER.exception("auto-defer failed to acknowledge interaction %s", ctx.interaction.id)

    @staticmethod
    async def _stop_auto_defer_task(task: asyncio.Task[None] | None, state: context.DispatchState) -> None:
        if task is None:
            return
        async with state.lock:
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("the auto-defer watchdog raised while being stood down")

    @staticmethod
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

    @staticmethod
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

        return Client._decode_frames(frames, meta, record, interaction_id)

    @staticmethod
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


class GatewayEnabledClient(Client):
    __slots__ = ("_app",)

    def __init__(
        self,
        app: GatewayClientAppT,
        *,
        register_app_dependencies: bool = True,
        **options: typing.Unpack[_ClientOptions],
    ) -> None:
        super().__init__(app.rest, register_app_dependencies=register_app_dependencies, **options)
        self._app = app

        if register_app_dependencies:
            self._register_dependency(hikari.api.EventManager, app.event_manager)
            if isinstance(app, hikari.GatewayBot):
                self._register_dependency(hikari.GatewayBot, app)

        app.event_manager.subscribe(hikari.ComponentInteractionCreateEvent, self._on_interaction_create)

    @property
    @typing.override
    def app(self) -> GatewayClientAppT:
        return self._app

    async def _on_interaction_create(self, event: hikari.ComponentInteractionCreateEvent) -> None:
        await self._process_interaction(event.interaction)


class RestEnabledClient(Client):
    __slots__ = ("_app",)

    def __init__(
        self,
        app: RestClientAppT,
        *,
        register_app_dependencies: bool = True,
        **options: typing.Unpack[_ClientOptions],
    ) -> None:
        super().__init__(app.rest, register_app_dependencies=register_app_dependencies, **options)
        self._app = app

        if register_app_dependencies:
            self._register_dependency(hikari.api.InteractionServer, app.interaction_server)
            if isinstance(app, hikari.RESTBot):
                self._register_dependency(hikari.RESTBot, app)

        app.interaction_server.set_listener(hikari.ComponentInteraction, self._on_interaction)

    @property
    @typing.override
    def app(self) -> RestClientAppT:
        return self._app

    async def _on_interaction(
        self,
        interaction: hikari.ComponentInteraction,
    ) -> collections.abc.AsyncGenerator[None, None]:
        state = context.DispatchState()
        task = asyncio.create_task(self._process_interaction(interaction, state))
        try:
            await asyncio.wait_for(state.acknowledged.wait(), timeout=constants.INTERACTION_WINDOW)
        except TimeoutError:
            _LOGGER.error(  # ruff:ignore[error-instead-of-exception]
                "interaction %s received no initial response within %.1fs on the REST transport; the"
                " interaction is lost - respond, defer, or enable auto_defer. The handler was not"
                " cancelled and continues in the background.",
                interaction.id,
                constants.INTERACTION_WINDOW,
            )
        yield
        await task
        if state.adopted and not state.responded:
            _LOGGER.debug(
                "interaction %s was answered with 204 and no response was sent through risa;"
                " Discord shows it as failed unless the handler answered it another way",
                interaction.id,
            )


@typing.overload
def client_from_app(
    app: GatewayClientAppT,
    **options: typing.Unpack[_ClientOptions],
) -> GatewayEnabledClient: ...


@typing.overload
def client_from_app(
    app: RestClientAppT,
    **options: typing.Unpack[_ClientOptions],
) -> RestEnabledClient: ...


def client_from_app(
    app: GatewayClientAppT | RestClientAppT,
    **options: typing.Unpack[_ClientOptions],
) -> Client:
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(app, **options)
    return RestEnabledClient(app, **options)


def client_from_lightbulb(
    lightbulb_client: LightbulbClient,
    **options: typing.Unpack[_LightbulbOptions],
) -> Client:
    app = lightbulb_client.app
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(app, di=lightbulb_client.di, register_app_dependencies=False, **options)
    if isinstance(app, RestClientAppT):
        return RestEnabledClient(app, di=lightbulb_client.di, register_app_dependencies=False, **options)

    msg = "the lightbulb client's app has neither an event manager nor an interaction server"
    raise TypeError(msg)
