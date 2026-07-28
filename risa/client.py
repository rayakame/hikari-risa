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
"""The client that wires risa into a hikari bot.

One client owns three things: the bot it answers for, the dependency injection
manager its callbacks resolve against, and the set of views it will route to.
It is subclassed per transport, because a gateway bot receives interactions as
events while a REST bot receives them as requests it must answer -- and that is
the only difference between the two, which is why everything else lives on the
base.

Not instantiated directly. :func:`client_from_app` picks the implementation
matching how the bot is wired, and :func:`client_from_lightbulb` additionally
shares lightbulb's dependency manager so a dependency registered once is
visible to commands and components alike.
"""

from __future__ import annotations

import abc
import logging
import typing

import hikari
import linkd

from risa import di as di_
from risa import errors
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
class GatewayClientAppT(hikari.EventManagerAware, hikari.RESTAware, typing.Protocol):
    """Protocol indicating an application delivers interactions as gateway events."""


@typing.runtime_checkable
class RestClientAppT(hikari.InteractionServerAware, hikari.RESTAware, typing.Protocol):
    """Protocol indicating an application delivers interactions to a server."""


@typing.runtime_checkable
class LightbulbClient(typing.Protocol):
    """The parts of a lightbulb client :func:`client_from_lightbulb` needs.

    Declared structurally so that risa does not depend on lightbulb; any object
    exposing a bot and a dependency injection manager satisfies it.
    """

    @property
    def app(self) -> hikari.RESTAware:
        """The bot the client is attached to."""
        ...

    @property
    def di(self) -> linkd.DependencyInjectionManager:
        """The client's dependency injection manager."""
        ...


class Client(abc.ABC):
    """Routes component interactions to registered views.

    Parameters
    ----------
    rest
        The REST client interactions are answered through.
    use_global
        When ``True``, views registered process-wide by ``@risa.register`` are
        routed in addition to those added to this client. When ``False`` (the
        default), only views added via :meth:`add_view` are routed, which is
        what lets two clients in one process answer for different views.
    di
        An existing dependency injection manager to share. A new one is created
        when omitted.
    register_app_dependencies
        Whether the bot's own types are registered against the manager. Pass
        ``False`` when sharing a manager whose owner has registered them already.
    """

    __slots__ = ("_di", "_registry", "_use_global")

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        *,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        self._di = di if di is not None else linkd.DependencyInjectionManager()
        self._registry = registry.Registry()
        self._use_global = use_global

        if register_app_dependencies:
            self._register_dependency(hikari.api.RESTClient, rest)

    @property
    @abc.abstractmethod
    def app(self) -> hikari.RESTAware:
        """The application this client was created from."""

    @property
    def di(self) -> linkd.DependencyInjectionManager:
        """The dependency injection manager this client dispatches against."""
        return self._di

    def add_view[T: view_.View](self, cls: type[T]) -> type[T]:
        """Add a view to this client so its components are routed.

        Only affects this client. Views are always registered process-wide by
        ``@risa.register`` as well, but a client ignores that registry unless it
        was created with ``use_global=True``.

        Returns the class unchanged, so it also works as a decorator::

            @client.add_view
            @risa.register(name="poll")
            class Poll(risa.View): ...

        Parameters
        ----------
        cls
            A view class, already decorated with ``@risa.register``.

        Returns
        -------
        type[T]
            The class it was given, unchanged.

        Raises
        ------
        NotAViewError
            If the class was never decorated with ``@risa.register``.
        DuplicateViewError
            If a different view is already added to this client under the same
            name and version.
        """
        meta = getattr(cls, constants.VIEW_META, None)
        if not isinstance(meta, registry.ViewMeta):
            raise errors.NotAViewError(cls.__name__)
        _LOGGER.info("registering view %s to client", meta.name)
        self._registry.register(meta)
        return cls

    def _register_dependency[T](self, dependency_type: type[T], value: T) -> None:
        """Register a dependency against the default scope.

        A manager that has already opened a container can no longer be
        registered against. That is recoverable, since whoever opened it first
        has usually registered the same objects, so it is logged rather than
        raised -- but it does mean anything asking for this type resolves
        against their value, or fails if they never registered it.

        Parameters
        ----------
        dependency_type
            The type to register under, which ``value`` must satisfy.
        value
            The dependency itself.
        """
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
        """Return the view registered under ``key``, if this client routes it.

        Returns
        -------
        registry.ViewMeta | None
            The view, or ``None`` when this client has no view under that key.
        """
        meta = self._registry.get(key)
        if meta is None and self._use_global:
            meta = registry.global_registry().get(key)
        return meta

    async def _process_interaction(  # ruff:ignore[no-self-use]
        self,
        interaction: hikari.ComponentInteraction,
    ) -> None:
        """Answer one component interaction.

        Both transports funnel into this, so everything above it -- decoding
        what the component names, finding the view, running the handler and
        responding -- is written once and works on either.

        Nothing is routed yet: the client can be attached to a bot and told
        about views, but a click currently reaches here and stops. The
        suppression above goes away with the first line that reaches for
        ``self``.

        Parameters
        ----------
        interaction
            The interaction to answer.
        """
        _LOGGER.debug(
            "interaction %s carried custom_id %r, which this client cannot route yet",
            interaction.id,
            interaction.custom_id,
        )


class GatewayEnabledClient(Client):
    """Client for an application that receives interactions as gateway events.

    Built by :func:`client_from_app`; not instantiated directly.
    """

    __slots__ = ("_app",)

    def __init__(
        self,
        app: GatewayClientAppT,
        *,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        super().__init__(
            app.rest,
            use_global=use_global,
            di=di,
            register_app_dependencies=register_app_dependencies,
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
        """The application this client was created from."""
        return self._app

    async def _on_interaction_create(self, event: hikari.ComponentInteractionCreateEvent) -> None:
        """Answer an interaction hikari delivered as an event."""
        await self._process_interaction(event.interaction)


class RestEnabledClient(Client):
    """Client for an application that receives interactions on a server.

    Built by :func:`client_from_app`; not instantiated directly.
    """

    __slots__ = ("_app",)

    def __init__(
        self,
        app: RestClientAppT,
        *,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        super().__init__(
            app.rest,
            use_global=use_global,
            di=di,
            register_app_dependencies=register_app_dependencies,
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
        """The application this client was created from."""
        return self._app

    async def _on_interaction(
        self,
        interaction: hikari.ComponentInteraction,
    ) -> collections.abc.AsyncGenerator[None, None]:
        """Answer Discord's webhook once the interaction has been handled.

        Yielding ``None`` is what hikari turns into a ``204 No Content`` --
        "already handled" -- so the yield has to come after whatever answers the
        interaction out of band. Handling currently runs to completion first,
        which holds Discord's request open for its duration; once responses
        exist this should instead yield as soon as one has been issued.
        """
        await self._process_interaction(interaction)
        yield


@typing.overload
def client_from_app(
    app: GatewayClientAppT,
    *,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
) -> GatewayEnabledClient: ...


@typing.overload
def client_from_app(
    app: RestClientAppT,
    *,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
) -> RestEnabledClient: ...


def client_from_app(
    app: GatewayClientAppT | RestClientAppT,
    *,
    use_global: bool = False,
    di: linkd.DependencyInjectionManager | None = None,
) -> Client:
    """Build the client implementation matching how a bot receives interactions.

    Parameters
    ----------
    app
        The bot to attach to.
    use_global
        Whether views registered process-wide by ``@risa.register`` are routed
        in addition to those added to this client.
    di
        An existing dependency injection manager to share. A new one is created
        when omitted.

    Returns
    -------
    Client
        A :class:`GatewayEnabledClient` for an application with an event
        manager, otherwise a :class:`RestEnabledClient`.
    """
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(app, use_global=use_global, di=di)
    return RestEnabledClient(app, use_global=use_global, di=di)


def client_from_lightbulb(
    lightbulb_client: LightbulbClient,
    *,
    use_global: bool = False,
) -> Client:
    """Build a client that shares a lightbulb client's bot and dependencies.

    The bot and the dependency injection manager are both taken from the
    lightbulb client, so a dependency registered once is visible to commands and
    to component handlers alike. lightbulb has already registered the bot's own
    types against the shared manager, so risa does not register them again.

    Parameters
    ----------
    lightbulb_client
        The lightbulb client to share with.
    use_global
        Whether views registered process-wide by ``@risa.register`` are routed
        in addition to those added to this client.

    Returns
    -------
    Client
        A :class:`GatewayEnabledClient` for an application with an event
        manager, otherwise a :class:`RestEnabledClient`.

    Raises
    ------
    TypeError
        If the lightbulb client is attached to an application that delivers
        interactions by neither mechanism.
    """
    app = lightbulb_client.app
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(
            app,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
        )
    if isinstance(app, RestClientAppT):
        return RestEnabledClient(
            app,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
        )

    msg = "the lightbulb client's app has neither an event manager nor an interaction server"
    raise TypeError(msg)
