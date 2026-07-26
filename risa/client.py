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
import typing

import hikari
import linkd

from risa import context
from risa import di as di_
from risa import errors
from risa import view
from risa.internal import anchor as anchor_
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry
from risa.state import serde
from risa.state import store as store_
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
    store
        Where view state is kept between interactions. Defaults to a
        :class:`~risa.state.store.MemoryStore`, which is enough for a bot running
        as a single process and enough for nothing else.
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

    __slots__ = ("_di", "_registry", "_store", "_use_global")

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        *,
        store: store_.Store | None = None,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        self._di = di if di is not None else linkd.DependencyInjectionManager()
        self._registry = registry.Registry()
        self._store = store if store is not None else store_.MemoryStore()
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

    @property
    def store(self) -> store_.Store:
        """Where this client keeps view state between interactions."""
        return self._store

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
        """
        view_meta = getattr(cls, constants.VIEW_META, None)
        if not isinstance(view_meta, registry.ViewMeta):
            raise errors.NotAViewError(cls.__name__)
        self._registry.register(view_meta)
        return cls

    async def build(self, view_: view.View) -> collections.abc.Sequence[hikari.api.ComponentBuilder]:
        """Render a view into the builders a message is sent with.

        Because a message carrying Components V2 may not also carry ``content``
        or ``embeds``, what comes back is the whole message body::

            await channel.send(components=await client.build(Poll(question="Ship it?")))

        A view holding state has it written to the store here, under a fresh
        random key that every component it renders carries. That write is what
        makes this a coroutine: it is also the point at which the view stops
        being a local object and becomes something any process sharing the store
        can rebuild.

        The state is written only once the tree has built, so a view that cannot
        be rendered leaves nothing behind.

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
        LayoutError
            If a component routes to a handler the view does not have.
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

        async with self._di.enter_context(di_.Contexts.DEFAULT):
            await self._load(view_)

        if meta.stateless:
            return build_.build(view_.render(), meta=meta)

        state_key = anchor_.make_state_key()
        builders = build_.build(view_.render(), meta=meta, anchor=anchor_.StoreAnchor(key=state_key).encode())
        await self._store.put(state_key, serde.dumps(view_, meta=meta), ttl=meta.ttl)
        return builders

    @staticmethod
    async def _load(view_: view.View) -> None:
        """Let a view fill in whatever risa does not persist for it.

        Awaited before every render, on the send that creates a message and on
        every dispatch that redraws it, so that a view's props hold the same
        kind of thing in both. A view that does not override the hook pays
        nothing.

        The caller is responsible for having opened the dependency injection
        containers the hook resolves its parameters against.

        Parameters
        ----------
        view_
            The view about to be rendered.
        """
        if type(view_).load is view.View.load:
            return
        await view_.load()

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
        args_payload = custom_id.args
        parsed = anchor_.parse(custom_id.fragment)
        if custom_id.fragment and not isinstance(parsed, anchor_.StoreAnchor):
            _LOGGER.error(
                "interaction %s: view %r expects its state in a store, but the component carries a"
                " different kind of anchor. Its placement changed without a version bump; routing to"
                " on_outdated.",
                interaction.id,
                meta.name,
            )
            await meta.cls.on_outdated(ctx)
            return
        state_key = parsed.key if isinstance(parsed, anchor_.StoreAnchor) else ""

        autodefer = meta.defer if record is None or record.defer is None else record.defer
        context.prepare_dispatch(state, interaction, meta=meta, state_key=state_key or None, autodefer=autodefer)

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
                    args_payload,
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

            if meta.stateless:
                instance = meta.cls()
                context.supply_view(state, instance)
                await self._load(instance)
                await record.callback(instance, ctx, *args)
            else:
                await self._dispatch_stateful(meta, record.callback, ctx, state, state_key, args)

    async def _dispatch_stateful(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]
        self,
        meta: registry.ViewMeta,
        callback: registry.Handler,
        ctx: context.ComponentContext,
        state: context.DispatchState,
        state_key: str,
        args: tuple[object, ...],
    ) -> None:
        """Rebuild a view from the store, run a handler on it, and write it back.

        Whether the handler changed anything is computed, not declared: the
        view is re-encoded afterwards and compared with the loaded bytes. An
        unchanged view skips the write -- a sliding TTL is refreshed with a
        ``touch`` instead -- so a read-only handler costs no store write.

        The lock is held across the whole sequence rather than around the write
        alone, because two people pressing the same button otherwise read the
        same state and each write their own successor to it, losing one of the
        two changes. It costs a slow handler the ability to run concurrently
        with another click on the same view, which is the point.

        The version check is not made redundant by that lock. A lock held in this
        process means nothing to another one, so for any store shared between
        processes the check is what actually rejects a write computed from state
        somebody else has since replaced.

        Parameters
        ----------
        meta
            The view the interaction resolved to.
        callback
            The handler to run, taking the rebuilt view and ``ctx``.
        ctx
            The interaction being answered.
        state_key
            Key the view's state should be under, taken from the ``custom_id``.
        args
            The component's decoded wire arguments, passed to the handler after
            ``ctx``.

        Raises
        ------
        StateConflictError
            If the state was replaced while the handler ran. Not retried: the
            handler has already had its chance to respond, and running it twice
            would answer the interaction twice.
        """
        if not state_key:
            _LOGGER.warning(
                "ignoring interaction %s: view %r holds state, but the component carries no key for it."
                " The component was rendered before the view declared any fields.",
                ctx.interaction.id,
                meta.name,
            )
            await meta.cls.on_state_missing(ctx)
            return

        async with self._store.lock(state_key):
            versioned = await self._store.get_versioned(state_key)
            if versioned is None:
                await meta.cls.on_state_missing(ctx)
                return

            raw, version = versioned
            try:
                view_ = serde.loads(raw, meta=meta)
            except errors.SerializationError:
                _LOGGER.exception(
                    "could not rebuild view %r from the state under key %r. Its fields were changed"
                    " without bumping the schema version on @risa.register.",
                    meta.name,
                    state_key,
                )
                await meta.cls.on_state_missing(ctx)
                return

            context.supply_view(state, view_)
            await self._load(view_)
            await callback(view_, ctx, *args)

            encoded = serde.dumps(view_, meta=meta)
            if encoded == raw:
                if meta.ttl is not None:
                    await self._store.touch(state_key, ttl=meta.ttl)
                return

            written = await self._store.put_if_version(
                state_key,
                encoded,
                expected=version,
                ttl=meta.ttl,
            )
            if not written:
                raise errors.StateConflictError(state_key)


class GatewayEnabledClient(Client):
    """Client for an application that receives interactions as gateway events.

    Built by :func:`client_from_app`; not instantiated directly.
    """

    __slots__ = ("_app",)

    def __init__(
        self,
        app: GatewayClientAppT,
        *,
        store: store_.Store | None = None,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        super().__init__(
            app.rest,
            store=store,
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
        store: store_.Store | None = None,
        use_global: bool = False,
        di: linkd.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
    ) -> None:
        super().__init__(
            app.rest,
            store=store,
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
    store: store_.Store | None = ...,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
) -> GatewayEnabledClient: ...


@typing.overload
def client_from_app(
    app: RestClientAppT,
    *,
    store: store_.Store | None = ...,
    use_global: bool = ...,
    di: linkd.DependencyInjectionManager | None = ...,
) -> RestEnabledClient: ...


def client_from_app(
    app: GatewayClientAppT | RestClientAppT,
    *,
    store: store_.Store | None = None,
    use_global: bool = False,
    di: linkd.DependencyInjectionManager | None = None,
) -> Client:
    """Build the client implementation matching how a bot receives interactions.

    Parameters
    ----------
    app
        The bot to attach to.
    store
        Where view state is kept between interactions. Defaults to a
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
        return GatewayEnabledClient(app, store=store, use_global=use_global, di=di)
    return RestEnabledClient(app, store=store, use_global=use_global, di=di)


def client_from_lightbulb(
    lightbulb_client: LightbulbClient,
    *,
    store: store_.Store | None = None,
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
    store
        Where view state is kept between interactions. Defaults to a
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
            store=store,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
        )
    if isinstance(app, RestClientAppT):
        return RestEnabledClient(
            app,
            store=store,
            use_global=use_global,
            di=lightbulb_client.di,
            register_app_dependencies=False,
        )

    msg = "the lightbulb client's app has neither an event manager nor an interaction server"
    raise TypeError(msg)
