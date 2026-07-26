# Copyright (c) 2025-present Rayakame
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
"""The client that wires risa into a hikari bot and routes interactions."""

from __future__ import annotations

import abc
import asyncio
import logging
import types
import typing

import hikari
import linkd

from risa import context
from risa import di as di_
from risa import errors
from risa import view
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry
from risa.state import backend as backend_
from risa.state import message as message_
from risa.state import placement as placement_
from risa.state import stateless as stateless_
from risa.state import store as store_
from risa.state import stored as stored_
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

    Not instantiated directly: use :func:`client_from_app`, which returns the
    implementation matching how the bot receives its interactions.

    Parameters
    ----------
    rest
        The REST client interactions are answered through.
    stores
        The stores views may name in ``state=risa.InStore(store=...)``, by that
        name. Defaults to a single ``"default"``
        :class:`~risa.state.store.MemoryStore`, which is enough for a bot running
        as a single process and enough for nothing else. Views that keep their
        state in their message -- the default placement -- use none of this.
    use_global
        When ``True``, views registered process-wide by ``@risa.register`` are
        routed in addition to those added to this client. When ``False`` (the
        default), only views added via :meth:`add_view` are routed.
    di
        An existing dependency injection manager to share. A new one is created
        when omitted.
    register_app_dependencies
        Whether the bot's own types are registered against the manager. Pass
        ``False`` when sharing a manager whose owner has registered them already.
    """

    __slots__ = (
        "_di",
        "_improvised_store",
        "_message",
        "_registry",
        "_stateless",
        "_stored",
        "_stores",
        "_use_global",
    )

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        *,
        stores: collections.abc.Mapping[str, store_.Store] | None = None,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        self._di = di if di is not None else linkd.DependencyInjectionManager()
        self._registry = registry.Registry()
        self._use_global = use_global

        self._improvised_store = stores is None
        self._stores: dict[str, store_.Store] = (
            dict(stores) if stores is not None else {placement_.DEFAULT_STORE: store_.MemoryStore()}
        )
        self._stateless = stateless_.StatelessBackend()
        self._message = message_.MessageBackend()
        self._stored = stored_.StoredBackend(self._stores)

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

    @property
    def stores(self) -> collections.abc.Mapping[str, store_.Store]:
        """The stores this client's views may name, by name."""
        return types.MappingProxyType(self._stores)

    def _register_dependency[T](self, dependency_type: type[T], value: T) -> None:
        """Register a dependency against the default scope.

        A manager that has already opened a container can no longer be registered
        against. That is recoverable, since whoever opened it first has usually
        registered the same objects, so it is logged rather than raised -- but it
        does mean anything asking for this type resolves against their value, or
        fails if they never registered it.

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

    def add_view[T: view.View](self, cls: type[T]) -> type[T]:
        """Add a view to this client so its components are routed.

        Only affects this client. Views are always registered process-wide by
        ``@risa.register`` as well, but a client ignores that registry unless it
        was created with ``use_global=True``.

        Returns the class unchanged, so it also works as a decorator::

            @client.add_view
            @risa.register(name="poll", version=1)
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
        DuplicateViewError
            If a different view is already registered on this client under the
            same cookie.
        NotAViewError
            If the class subclasses :class:`~risa.view.View` but was never
            decorated with ``@risa.register``.
        ViewDeclarationError
            If the view keeps its state in a store this client was not given.
            Deliberately raised here rather than when somebody clicks: a
            misconfigured store is a startup mistake, and a dead button is a
            terrible way to be told about one.
        """
        view_meta = getattr(cls, constants.VIEW_META, None)
        if not isinstance(view_meta, registry.ViewMeta):
            raise errors.NotAViewError(cls.__name__)

        self._check_stores(view_meta)
        placement = view_meta.placement
        if self._improvised_store and isinstance(placement, placement_.InStore):
            _LOGGER.warning(
                "view %r keeps its state in a store, but this client was not given one, so it is using"
                " an in-memory store that empties on restart. That forfeits the very thing a store is"
                " for; pass stores={...} to the client, or place the state with state=risa.InMessage().",
                view_meta.name,
            )

        self._registry.register(view_meta)
        return cls

    def _check_stores(self, meta: registry.ViewMeta) -> None:
        """Confirm this client has the store a view's placement names.

        Raises
        ------
        ViewDeclarationError
            If the view names a store this client was never given.
        """
        if isinstance(meta.placement, placement_.InStore):
            self._stored.store_for(meta)

    def _backend_for(self, meta: registry.ViewMeta) -> backend_.StateBackend:
        """Return the backend that answers for where a view keeps its state.

        Returns
        -------
        backend_.StateBackend
            The backend for this view's placement, or the one that keeps
            nothing when the view has no durable state to place.
        """
        if meta.stateless:
            return self._stateless
        if isinstance(meta.placement, placement_.InStore):
            return self._stored
        return self._message

    async def build(self, view_: view.View) -> collections.abc.Sequence[hikari.api.ComponentBuilder]:
        """Render a view into the builders a message is sent with.

        Because a message carrying Components V2 may not also carry ``content``
        or ``embeds``, what comes back is the whole message body::

            await channel.send(components=await client.build(Poll(question="Ship it?")))

        Where the view's state ends up is its registered placement's business,
        and this asks that placement for the anchor its components should carry:
        the state itself for a view that rides its own message, a freshly minted
        key for one that lives in a store. Anything that has to be written is
        written *after* the tree builds, so a view that cannot be rendered
        leaves nothing behind.

        Parameters
        ----------
        view_
            The view to render.

        Returns
        -------
        collections.abc.Sequence[hikari.api.ComponentBuilder]
            The top-level builders, ready to pass to ``components=``.

        Raises
        ------
        NotAViewError
            If the view's class was never decorated with ``@risa.register``.
        ViewDeclarationError
            If the view keeps its state in a store this client was not given.
        LayoutError
            If a component routes to a handler the view does not have.
        StateOverflowError
            If the view's state does not fit the components it renders.
        CustomIdOverflowError
            If an encoded ``custom_id`` would exceed Discord's length limit.
        """
        meta = getattr(type(view_), constants.VIEW_META, None)
        if not isinstance(meta, registry.ViewMeta):
            raise errors.NotAViewError(type(view_).__name__)

        if self._resolve(meta.cookie) is None:
            _LOGGER.warning(
                "building view %r, which this client cannot route: every component it renders will be"
                " ignored when clicked. Pass it to add_view, or build the client with use_global=True.",
                meta.name,
            )
        self._check_stores(meta)

        async with self._di.enter_context(di_.Contexts.DEFAULT):
            await view.refill(view_)

        backend = self._backend_for(meta)
        anchor = backend.first_anchor(meta=meta, view=view_)
        builders = build_.build(view_.render(), meta=meta, anchor=anchor)
        await backend.commit_first(meta=meta, view=view_, anchor=anchor)
        return builders

    def _resolve(self, cookie: str) -> registry.ViewMeta | None:
        meta = self._registry.get(cookie)
        if meta is None and self._use_global:
            meta = registry.global_registry().get(cookie)
        return meta

    async def _process_component_interaction(
        self,
        interaction: hikari.ComponentInteraction,
        ctx: context.ComponentContext,
        state: context.DispatchState,
    ) -> None:
        """Route one component interaction and settle its watchdog either way.

        A dispatch that finishes cleanly without responding gets the deferred
        acknowledgement issued immediately; one that raises gets the watchdog
        cancelled instead, so the failure stays visible rather than being
        acked into silence. The acknowledgement gate opens in both cases,
        which is what lets a REST listener stop waiting.
        """
        try:
            await self._route_component_interaction(interaction, ctx, state)
        except Exception:
            await context.conclude_dispatch(state, interaction, failed=True)
            raise
        else:
            await context.conclude_dispatch(state, interaction, failed=False)

    async def _route_component_interaction(
        self,
        interaction: hikari.ComponentInteraction,
        ctx: context.ComponentContext,
        state: context.DispatchState,
    ) -> None:
        custom_id = codec.CustomID.parse(interaction.custom_id)
        if custom_id is None:
            return

        meta = self._resolve(custom_id.raw_cookie)
        if meta is None:
            _LOGGER.warning(
                "ignoring interaction %s: no view is registered under cookie %r. The component predates a"
                " change to its view's name or version, or its view was never added to this client.",
                interaction.id,
                custom_id.raw_cookie,
            )
            return

        record = meta.handlers.get(custom_id.handler)
        autodefer = meta.defer if record is None or record.defer is None else record.defer
        context.prepare_dispatch(state, interaction, meta=meta, autodefer=autodefer)

        async with (
            self._di.enter_context(di_.Contexts.DEFAULT),
            self._di.enter_context(di_.Contexts.COMPONENT) as container,
        ):
            container.add_value(context.ComponentContext, ctx)

            if record is None:
                _LOGGER.log(
                    logging.DEBUG if meta.handles_outdated else logging.WARNING,
                    "interaction %s: view %r has no handler under token %r. The component predates a change"
                    " to its handler's id or version; routing to on_outdated.",
                    interaction.id,
                    meta.name,
                    custom_id.handler,
                )
                await meta.cls.on_outdated(ctx)
                return

            try:
                args = record.signature.decode(
                    custom_id.args,
                    view_name=meta.name,
                    handler_id=record.handler_id,
                    version=record.version,
                )
            except errors.SignatureMismatchError:
                _LOGGER.exception(
                    "interaction %s cannot be dispatched; routing to on_outdated. Declare the old signature"
                    " under this handler version and move the new one to a bumped version.",
                    interaction.id,
                )
                await meta.cls.on_outdated(ctx)
                return

            await self._dispatch(meta=meta, record=record, ctx=ctx, state=state, component=custom_id, args=args)

    async def _dispatch(  # ruff:ignore[too-many-arguments]
        self,
        *,
        meta: registry.ViewMeta,
        record: registry.HandlerRecord,
        ctx: context.ComponentContext,
        state: context.DispatchState,
        component: codec.CustomID,
        args: tuple[object, ...],
    ) -> None:
        """Rebuild the view this component belongs to, run its handler, and commit.

        One shape for every placement. The session opened here is the only thing
        that knows where the state actually is; everything around it -- reading,
        running, committing, and answering when there is nothing to read -- is
        written once and means the same under all of them.

        The lock the session takes -- whose reach is
        :meth:`~risa.state.store.Store.lock`'s to explain -- is taken only once
        dispatch knows it has a handler to run: a component that outlived its
        handler never touches state, and so must not make anybody wait for it.

        Parameters
        ----------
        meta
            The view the interaction resolved to.
        record
            The handler to run.
        ctx
            The interaction being answered.
        state
            The dispatch state behind ``ctx``.
        component
            The clicked component, decoded.
        args
            The component's decoded wire arguments, passed after ``ctx``.
        """
        session = self._backend_for(meta).session(meta=meta, component=component, interaction=ctx.interaction)
        async with session:
            outcome = await session.restore()
            if not isinstance(outcome, backend_.Restored):
                await context.answer_unreadable(ctx, meta, outcome)
                return

            instance = outcome.view
            context.supply_view(state, instance, session)
            await view.refill(instance)
            await record.callback(instance, ctx, *args)
            await context.settle_state(ctx, state)


class GatewayEnabledClient(Client):
    """Client for an application that receives interactions as gateway events.

    Built by :func:`client_from_app`; not instantiated directly.
    """

    __slots__ = ("_app",)

    def __init__(
        self,
        app: GatewayClientAppT,
        *,
        stores: collections.abc.Mapping[str, store_.Store] | None = None,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        super().__init__(
            app.rest,
            stores=stores,
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
        state = context.DispatchState()
        ctx = context.ComponentContext(self, event.interaction, state)
        await self._process_component_interaction(event.interaction, ctx, state)


class RestEnabledClient(Client):
    """Client for an application that receives interactions on a server.

    Built by :func:`client_from_app`; not instantiated directly.
    """

    __slots__ = ("_app",)

    def __init__(
        self,
        app: RestClientAppT,
        *,
        stores: collections.abc.Mapping[str, store_.Store] | None = None,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        super().__init__(
            app.rest,
            stores=stores,
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
        """Answer Discord's webhook once dispatch has responded out-of-band.

        Dispatch runs as a task and issues its responses through the same REST
        calls the gateway transport uses; this listener only waits for the
        first of them (the auto-defer watchdog guarantees one within its
        delay) and then yields ``None``, which hikari turns into a
        ``204 No Content`` -- "already handled". The dispatch task is awaited
        after the yield, inside the background task hikari keeps for
        generator listeners, so a handler is never cut short by the webhook
        being answered.
        """
        state = context.DispatchState()
        ctx = context.ComponentContext(self, interaction, state)
        task = asyncio.create_task(self._process_component_interaction(interaction, ctx, state))

        acknowledged = True
        try:
            async with asyncio.timeout(constants.REST_ACK_TIMEOUT):
                await state.acknowledged.wait()
        except TimeoutError:
            acknowledged = False
        if not acknowledged:
            _LOGGER.error(
                "no response was issued for interaction %s within %.0f seconds; Discord will show the"
                " click as failed. The handler was not cancelled and is still running.",
                interaction.id,
                constants.REST_ACK_TIMEOUT,
            )
        yield

        try:
            await task
        except Exception:
            _LOGGER.exception("dispatch for interaction %s raised", interaction.id)


@typing.overload
def client_from_app(
    app: GatewayClientAppT,
    *,
    stores: collections.abc.Mapping[str, store_.Store] | None = ...,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
) -> GatewayEnabledClient: ...


@typing.overload
def client_from_app(
    app: RestClientAppT,
    *,
    stores: collections.abc.Mapping[str, store_.Store] | None = ...,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
) -> RestEnabledClient: ...


def client_from_app(
    app: GatewayClientAppT | RestClientAppT,
    *,
    stores: collections.abc.Mapping[str, store_.Store] | None = None,
    use_global: bool = False,
    di: linkd.DependencyInjectionManager | None = None,
) -> Client:
    """Build the client implementation matching how a bot receives interactions.

    Parameters
    ----------
    app
        The bot to attach to.
    stores
        The stores views may name in ``state=risa.InStore(store=...)``, by that
        name. Defaults to a single ``"default"``
        :class:`~risa.state.store.MemoryStore`, which is enough for a bot running
        as a single process and enough for nothing else.
    use_global
        Whether views registered process-wide by ``@risa.register`` are routed in
        addition to those added to this client.
    di
        An existing dependency injection manager to share. A new one is created
        when omitted.

    Returns
    -------
    Client
        A :class:`GatewayEnabledClient` for an application with an event manager,
        otherwise a :class:`RestEnabledClient`.
    """
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(app, stores=stores, use_global=use_global, di=di)
    return RestEnabledClient(app, stores=stores, use_global=use_global, di=di)


def client_from_lightbulb(
    lightbulb_client: LightbulbClient,
    *,
    stores: collections.abc.Mapping[str, store_.Store] | None = None,
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
    stores
        The stores views may name in ``state=risa.InStore(store=...)``, by that
        name. Defaults to a single ``"default"``
        :class:`~risa.state.store.MemoryStore`, which is enough for a bot running
        as a single process and enough for nothing else.
    use_global
        Whether views registered process-wide by ``@risa.register`` are routed in
        addition to those added to this client.

    Returns
    -------
    Client
        A :class:`GatewayEnabledClient` for an application with an event manager,
        otherwise a :class:`RestEnabledClient`.

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
            stores=stores,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
        )
    if isinstance(app, RestClientAppT):
        return RestEnabledClient(
            app,
            stores=stores,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
        )

    msg = "the lightbulb client's app has neither an event manager nor an interaction server"
    raise TypeError(msg)
