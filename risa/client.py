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
from risa import dispatch as dispatch_
from risa import ui
from risa import view as view_
from risa.internal import constants
from risa.internal import registry

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


class Client(abc.ABC):
    __slots__ = ("_auto_defer", "_auto_defer_delay", "_di", "_dispatcher", "_registry", "_rest", "_use_global")

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
        self._dispatcher = dispatch_.Dispatcher(
            rest,
            self._di,
            self._resolve,
            auto_defer=auto_defer,
            auto_defer_delay=auto_defer_delay,
        )

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
        return ui.Rendered(ui.build(view.render(), registry.require_meta(view)))

    def add_view[T: view_.View](self, cls: type[T]) -> type[T]:
        meta = registry.require_meta(cls)
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

    def _resolve(self, cookie: str) -> registry.ViewMeta | None:
        meta = self._registry.get(cookie)
        if meta is None and self._use_global:
            meta = registry.global_registry().get(cookie)
        return meta

    async def _process_interaction(
        self,
        interaction: hikari.ComponentInteraction,
        gate: context.ResponseGate | None = None,
    ) -> None:
        await self._dispatcher.process(interaction, gate)


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
        gate = context.ResponseGate()
        task = asyncio.create_task(self._process_interaction(interaction, gate))
        try:
            await asyncio.wait_for(gate.acknowledged.wait(), timeout=constants.INTERACTION_WINDOW)
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
        if gate.adopted and not gate.responded:
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
