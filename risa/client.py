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

from risa import context
from risa import di as di_
from risa import errors
from risa import view as view_
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry
from risa.ui import build as build_

if typing.TYPE_CHECKING:
    import collections.abc

    from hikari.api import special_endpoints

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


class Client(abc.ABC):
    __slots__ = ("_auto_defer", "_auto_defer_delay", "_di", "_registry", "_use_global")

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        *,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
        auto_defer: view_.AutoDefer = view_.AutoDefer.UPDATE,
        auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
    ) -> None:
        self._di = di if di is not None else linkd.DependencyInjectionManager()
        self._registry = registry.Registry()
        self._use_global = use_global
        self._auto_defer = auto_defer
        self._auto_defer_delay = auto_defer_delay

        if register_app_dependencies:
            self._register_dependency(hikari.api.RESTClient, rest)

    @property
    @abc.abstractmethod
    def app(self) -> hikari.RESTAware: ...

    @property
    def di(self) -> linkd.DependencyInjectionManager:
        return self._di

    async def build(self, view: view_.View) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:  # ruff:ignore[no-self-use]
        meta = getattr(type(view), constants.VIEW_META, None)
        if not isinstance(meta, registry.ViewMeta):
            raise errors.NotAViewError(type(view).__name__)
        return build_(view.render(), meta)

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
    ) -> None:
        custom_id = codec.parse_custom_id(interaction.custom_id)
        if custom_id is None:
            return

        meta = self._resolve(custom_id.cookie)
        if meta is None:
            _LOGGER.debug(
                "interaction %s carries a risa custom_id with cookie %r, but this client has no view for it",
                interaction.id,
                custom_id.cookie,
            )
            return
        handler = meta.handlers.get(custom_id.handler)
        if handler is None:
            _LOGGER.warning(
                "interaction %s routes to view %s (version %d), but no handler answers to token %r."
                " A live component may predate a version bump that retired its handler.",
                interaction.id,
                meta.name,
                meta.version,
                custom_id.handler,
            )
            return

        resolved_defer = handler.defer
        if resolved_defer is None:
            resolved_defer = self._auto_defer
        view = meta.cls()
        state = context.DispatchState()
        ctx = context.ComponentContext(interaction, state)
        watchdog: asyncio.Task[None] | None = None
        if resolved_defer is not view_.AutoDefer.OFF:
            watchdog = asyncio.create_task(self._auto_defer_task(ctx, resolved_defer))
        try:
            await handler.callback(view, ctx)
        except Exception:
            await self._stop_auto_defer_task(watchdog, state)
            _LOGGER.exception(
                "handler %r (version %d) of view %s raised while answering interaction %s",
                handler.handler_id,
                handler.version,
                meta.name,
                interaction.id,
            )
        else:
            await self._stop_auto_defer_task(watchdog, state)
            await ctx.acknowledge()

    async def _auto_defer_task(self, ctx: context.ComponentContext, defer: view_.AutoDefer) -> None:
        await asyncio.sleep(self._auto_defer_delay)
        match defer:
            case view_.AutoDefer.UPDATE:
                await ctx.acknowledge()
            case view_.AutoDefer.THINKING:
                await ctx.acknowledge(thinking=True)
            case view_.AutoDefer.THINKING_EPHEMERAL:
                await ctx.acknowledge(thinking=True, ephemeral=True)
            case view_.AutoDefer.OFF:
                return

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


class GatewayEnabledClient(Client):
    __slots__ = ("_app",)

    def __init__(
        self,
        app: GatewayClientAppT,
        *,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
        auto_defer: view_.AutoDefer = view_.AutoDefer.UPDATE,
        auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
    ) -> None:
        super().__init__(
            app.rest,
            use_global=use_global,
            di=di,
            register_app_dependencies=register_app_dependencies,
            auto_defer=auto_defer,
            auto_defer_delay=auto_defer_delay,
        )
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
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
        auto_defer: view_.AutoDefer = view_.AutoDefer.UPDATE,
        auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
    ) -> None:
        super().__init__(
            app.rest,
            use_global=use_global,
            di=di,
            register_app_dependencies=register_app_dependencies,
            auto_defer=auto_defer,
            auto_defer_delay=auto_defer_delay,
        )
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
        await self._process_interaction(interaction)
        yield


@typing.overload
def client_from_app(
    app: GatewayClientAppT,
    *,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
    auto_defer: view_.AutoDefer = ...,
    auto_defer_delay: float = ...,
) -> GatewayEnabledClient: ...


@typing.overload
def client_from_app(
    app: RestClientAppT,
    *,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
    auto_defer: view_.AutoDefer = ...,
    auto_defer_delay: float = ...,
) -> RestEnabledClient: ...


def client_from_app(
    app: GatewayClientAppT | RestClientAppT,
    *,
    use_global: bool = False,
    di: linkd.DependencyInjectionManager | None = None,
    auto_defer: view_.AutoDefer = view_.AutoDefer.UPDATE,
    auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
) -> Client:
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(
            app, use_global=use_global, di=di, auto_defer=auto_defer, auto_defer_delay=auto_defer_delay
        )
    return RestEnabledClient(
        app, use_global=use_global, di=di, auto_defer=auto_defer, auto_defer_delay=auto_defer_delay
    )


def client_from_lightbulb(
    lightbulb_client: LightbulbClient,
    *,
    use_global: bool = False,
    auto_defer: view_.AutoDefer = view_.AutoDefer.UPDATE,
    auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
) -> Client:
    app = lightbulb_client.app
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(
            app,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
            auto_defer=auto_defer,
            auto_defer_delay=auto_defer_delay,
        )
    if isinstance(app, RestClientAppT):
        return RestEnabledClient(
            app,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
            auto_defer=auto_defer,
            auto_defer_delay=auto_defer_delay,
        )

    msg = "the lightbulb client's app has neither an event manager nor an interaction server"
    raise TypeError(msg)
