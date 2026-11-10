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
"""The client that routes clicks to views, and the functions that make one."""

from __future__ import annotations

import abc
import asyncio
import collections.abc
import logging
import typing

import hikari
import linkd
import linkd.solver

from risa import context
from risa import di as di_
from risa import dispatch as dispatch_
from risa import errors
from risa import hooks as hooks_
from risa import view as view_
from risa.internal import constants
from risa.internal import registry
from risa.state import message
from risa.state import placement as placement_
from risa.state import stateless
from risa.state import stored
from risa.store import memory
from risa.ui import build as build_

if typing.TYPE_CHECKING:
    from hikari.api import interaction_server as interaction_server_

    from risa.state import backend as backend_
    from risa.store import protocol

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

type _ComponentResponse = (
    hikari.api.InteractionDeferredBuilder | hikari.api.InteractionMessageBuilder | hikari.api.InteractionModalBuilder
)
type _ComponentListener = interaction_server_.ListenerT[hikari.ComponentInteraction, _ComponentResponse]


class _ErrorHandlerDecorator(typing.Protocol):
    def __call__[T: hooks_.ErrorHandler](self, func: T, /) -> T: ...


@typing.runtime_checkable
class GatewayClientAppT(hikari.EventManagerAware, hikari.RESTAware, typing.Protocol):
    """A gateway app, with an event manager and a REST client, such as `hikari.GatewayBot`."""


@typing.runtime_checkable
class RestClientAppT(hikari.InteractionServerAware, hikari.RESTAware, typing.Protocol):
    """A REST app, with an interaction server and a REST client, such as `hikari.RESTBot`."""


@typing.runtime_checkable
class LightbulbClient(typing.Protocol):
    """The part of a lightbulb client risa uses."""

    @property
    def app(self) -> hikari.RESTAware:
        """The hikari app the lightbulb client runs on."""
        ...

    @property
    def di(self) -> linkd.solver.DependencyInjectionManager:
        """The lightbulb client's dependency manager."""
        ...


class _LightbulbOptions(typing.TypedDict, total=False):
    use_global: bool
    auto_defer: view_.AutoDefer
    auto_defer_delay: float
    store: protocol.Store | None
    stores: collections.abc.Mapping[str, protocol.Store]
    key_prefix: str | None
    hooks: collections.abc.Sequence[hooks_.ExecutionHook[view_.View]]
    execution_step_order: collections.abc.Sequence[hooks_.ExecutionStep]


class _ClientOptions(_LightbulbOptions, total=False):
    di: linkd.solver.DependencyInjectionManager | None


class Client(abc.ABC):
    """Routes clicks on registered views to their handlers.

    Make one with `client_from_app` or `client_from_lightbulb`, add views with
    `add_view`, and send a view with `build`. A client answers only `custom_id`s risa
    wrote, so it can share a bot with other component handlers.

    Parameters
    ----------
    rest
        The REST client risa answers interactions through.
    use_global
        Also route views registered with `@risa.register` but never added to this client.
    di
        The linkd manager handlers are injected from; a new one by default.
    register_app_dependencies
        Register the app, its REST client, and its event manager or interaction server
        for injection.
    auto_defer
        The `AutoDefer` of views and handlers that set none, `OFF` by default.
    auto_defer_delay
        Seconds after which auto-defer answers a click in its handler's place.
    store
        The store `InStore` views use unless they name another.
    stores
        Further stores, by the name `InStore(store=...)` refers to them by.
    key_prefix
        The prefix of every store key, `risa:{application id}` by default. Set it to
        fit a Redis ACL pattern, or to separate bots that share one store.
    hooks
        Hooks that run for clicks on every view, before the view's and the handler's in
        each step. They take no view, or `View` itself.
    execution_step_order
        The steps of a click in the order they run, `DEFAULT_EXECUTION_STEP_ORDER` by
        default. It holds `ExecutionSteps.INVOKE` once, and every other step at most once.

    Raises
    ------
    ValueError
        If `key_prefix` is blank, the default store is given both as `store` and in
        `stores`, or `execution_step_order` lacks `INVOKE` or holds a step twice.
    """

    __slots__ = (
        "_auto_defer",
        "_auto_defer_delay",
        "_di",
        "_dispatcher",
        "_global_checked",
        "_hooks",
        "_in_message",
        "_memory_warned",
        "_order",
        "_registry",
        "_rest",
        "_stateless",
        "_stored",
        "_unanswered_warned",
        "_use_global",
    )

    def __init__(  # ruff:ignore[too-many-arguments]
        self,
        rest: hikari.api.RESTClient,
        *,
        use_global: bool = False,
        di: linkd.solver.DependencyInjectionManager | None = None,
        register_app_dependencies: bool = True,
        auto_defer: view_.AutoDefer = view_.AutoDefer.OFF,
        auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
        store: protocol.Store | None = None,
        stores: collections.abc.Mapping[str, protocol.Store] | None = None,
        key_prefix: str | None = None,
        hooks: collections.abc.Sequence[hooks_.ExecutionHook[view_.View]] = (),
        execution_step_order: collections.abc.Sequence[hooks_.ExecutionStep] = hooks_.DEFAULT_EXECUTION_STEP_ORDER,
    ) -> None:
        if key_prefix is not None and not key_prefix.strip():
            msg = "key_prefix must not be blank; leave it out to namespace store keys by the application id"
            raise ValueError(msg)
        if hooks_.ExecutionSteps.INVOKE not in execution_step_order:
            msg = "execution_step_order must hold ExecutionSteps.INVOKE, the step that runs the handler"
            raise ValueError(msg)
        if len(set(execution_step_order)) != len(execution_step_order):
            msg = "execution_step_order must not hold a step twice"
            raise ValueError(msg)
        self._order = tuple(execution_step_order)
        self._hooks = tuple(hooks)
        for hook in self._hooks:
            self._warn_if_unordered(hook, "the client")
        self._di = di if di is not None else linkd.DependencyInjectionManager()
        self._registry = registry.Registry()
        self._rest = rest
        self._use_global = use_global
        self._auto_defer = auto_defer
        self._auto_defer_delay = auto_defer_delay
        namespace = stored.KeyNamespace(rest, key_prefix=key_prefix)
        self._stored = {
            name: stored.StoredBackend(instance, namespace=namespace)
            for name, instance in _store_slots(store, stores or {}).items()
        }
        self._in_message = message.MessageBackend()
        self._stateless = stateless.StatelessBackend()
        self._memory_warned: set[str] = set()
        self._unanswered_warned: set[str] = set()
        self._global_checked: set[str] = set()

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
            self._require_backend,
            auto_defer=auto_defer,
            auto_defer_delay=auto_defer_delay,
            build=self._build_over,
            hooks=self._hooks,
            order=self._order,
        )

    @property
    @abc.abstractmethod
    def app(self) -> hikari.RESTAware:
        """The hikari app the client receives interactions from."""

    @property
    def di(self) -> linkd.solver.DependencyInjectionManager:
        """The dependency manager handlers are injected from."""
        return self._di

    @property
    def rest(self) -> hikari.api.RESTClient:
        """The REST client risa answers interactions through."""
        return self._rest

    async def build(self, view: view_.View) -> build_.Rendered:
        """Render `view` into the components of a new message, ready to send.

        ```python
        await ctx.respond(**await client.build(Poll(question="Ship it?")))
        ```

        It runs the view's `load`, renders it, and writes an `InStore` view's first record.
        The result spreads into any hikari or lightbulb call that takes `components=`. If
        that send fails, the record stays behind until its TTL reaps it, or for good under
        `InStore(ttl=None)`.

        Parameters
        ----------
        view
            The view to render, an instance of a registered class.

        Returns
        -------
        :
            The components, as a mapping that spreads into a send.

        Raises
        ------
        NotAViewError
            If the view's class was never registered.
        ViewDeclarationError
            If the view keeps its state in a store this client does not have.
        StateOverflowError
            If an `InMessage` view's state does not fit its components.
        """
        meta = registry.require_meta(view)
        if meta.loader is not None:
            async with self._di.enter_context(di_.Contexts.DEFAULT):
                await meta.loader(view)
        plan = build_.plan(view.render(), meta)
        if not plan.interactive:
            return build_.Rendered(plan.emit(b""))
        self._warn_if_unanswered(meta)
        state = self._require_backend(meta)
        anchor = state.first_anchor(meta=meta, view=view)
        builders = plan.emit(anchor)
        await state.commit_first(meta=meta, view=view, anchor=anchor)
        return build_.Rendered(builders)

    @typing.overload
    def error_handler[T: hooks_.ErrorHandler](self, func: T, /, *, priority: int = 0) -> T: ...

    @typing.overload
    def error_handler(self, /, *, priority: int = 0) -> _ErrorHandlerDecorator: ...

    def error_handler(
        self,
        func: hooks_.ErrorHandler | None = None,
        /,
        *,
        priority: int = 0,
    ) -> hooks_.ErrorHandler | _ErrorHandlerDecorator:
        """Register a function that answers clicks that failed.

        ```python
        @client.error_handler
        async def on_error(
            pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext
        ) -> bool:
            if pipeline.find(risa.StoreUnavailableError) is not None:
                await ctx.respond("Try again in a moment.", ephemeral=True)
                return True
            return False
        ```

        It runs once a click is over, if anything in it failed: a hook, `load`, the handler,
        `View.on_outdated` or `View.on_state_missing`, or reaching the view's state before
        them. `pipeline.failures` lists what failed, in order. It returns `True` once it has
        dealt with the click, and risa then logs nothing. Otherwise the next one runs, and if
        none returns `True`, risa logs the failures. One that raises is logged and counts as
        `False`. Its parameters after `ctx` are injected, as a handler's are. `ctx` has no view
        state, so a half-changed view is never saved. It runs without auto-defer, and
        Discord's three seconds count from the click, so a failure that comes late, such as a
        lock timeout, may leave it no time unless auto-defer answered first.

        Parameters
        ----------
        func
            The function to register. Left out, the call takes `priority` and returns the
            decorator.
        priority
            Higher runs first; functions of one priority run in the order they were
            registered.

        Returns
        -------
        :
            The function, unchanged, or the decorator that registers one.
        """

        def register[T: hooks_.ErrorHandler](handler: T) -> T:
            self._dispatcher.add_error_handler(handler, priority=priority)
            return handler

        return register(func) if func is not None else register

    def add_view[T: view_.View](self, cls: type[T]) -> type[T]:
        """Route clicks on the components of `cls` through this client.

        Parameters
        ----------
        cls
            The view class, already registered with `@risa.register`.

        Returns
        -------
        :
            The class, so that this also works as a decorator above `@risa.register`.

        Raises
        ------
        NotAViewError
            If the class was never registered.
        ViewDeclarationError
            If the view keeps its state in a store this client does not have.
        HookDeclarationError
            If a hook of the client, the view or a handler takes a view of another class, or
            its annotations cannot be resolved.
        """
        meta = registry.require_meta(cls)
        self._check_store(meta)
        self._check_hooks(meta)
        _LOGGER.debug("registering view %s to client", meta.name)
        self._registry.register(meta)
        return cls

    async def _build_over(self, view: view_.View, message_id: hikari.Snowflake) -> build_.Rendered:
        rendered = await self.build(view)
        # The new state starts at sequence 0, so a later commit remembered for the message would win over it.
        self._in_message.forget(message_id)
        return rendered

    def _warn_if_unanswered(self, meta: registry.ViewMeta) -> None:
        if self._resolve(meta.cookie) is None and meta.cookie not in self._unanswered_warned:
            self._unanswered_warned.add(meta.cookie)
            _LOGGER.warning(
                "view %s was built, but this client does not answer its clicks: pass the class to"
                " client.add_view, or make the client with use_global=True",
                meta.name,
            )

    def _check_hooks(self, meta: registry.ViewMeta) -> None:
        for hook in (*self._hooks, *_own_hooks(meta)):
            view_type = hook.view_type
            if view_type is not None and not issubclass(meta.cls, view_type):
                reason = (
                    f"it takes a {view_type.__name__}, but runs for view {meta.name!r}, a {meta.cls.__name__};"
                    " attach it to that view or its handlers, or annotate its view parameter with a base of both"
                )
                raise errors.HookDeclarationError(hook.name, reason)
        self._warn_of_unordered(meta)

    def _warn_of_unordered(self, meta: registry.ViewMeta) -> None:
        for hook in _own_hooks(meta):
            self._warn_if_unordered(hook, f"view {meta.name}")

    def _warn_if_unordered(self, hook: hooks_.AnyHook, owner: str) -> None:
        if hook.step not in self._order:
            _LOGGER.warning(
                "hook %r of %s runs in step %s, which this client's execution_step_order leaves out, so it never runs",
                hook.name,
                owner,
                hook.step.name,
            )

    def _check_store(self, meta: registry.ViewMeta) -> None:
        state = meta.state
        if not isinstance(state, placement_.InStore):
            return
        slot = self._stored.get(state.store)
        if slot is None:
            raise errors.ViewDeclarationError(meta.name, _missing_store(state.store))
        if isinstance(slot.store, memory.MemoryStore) and slot.store.warn and state.store not in self._memory_warned:
            self._memory_warned.add(state.store)
            _LOGGER.warning(
                "view %s keeps its state in store %r, which is a MemoryStore: the state lives in this"
                " process only, so it is lost on every restart, and on a REST bot with more than one"
                " process most clicks will not find it. Pass MemoryStore(warn=False) once this is intended",
                meta.name,
                state.store,
            )

    def _require_backend(self, meta: registry.ViewMeta) -> backend_.StateBackend:
        found = self._backend_for(meta)
        if found is None:
            name = meta.state.store if isinstance(meta.state, placement_.InStore) else placement_.DEFAULT_STORE
            raise errors.ViewDeclarationError(meta.name, _missing_store(name))
        return found

    def _backend_for(self, meta: registry.ViewMeta) -> backend_.StateBackend | None:
        if not meta.schema.durable:
            return self._stateless
        if isinstance(meta.state, placement_.InStore):
            return self._stored.get(meta.state.store)
        return self._in_message

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
            if meta is not None and meta.cookie not in self._global_checked:
                self._global_checked.add(meta.cookie)
                self._warn_of_unordered(meta)
        return meta

    async def _process_interaction(
        self,
        interaction: hikari.ComponentInteraction,
        gate: context.ResponseGate | None = None,
    ) -> None:
        await self._dispatcher.process(interaction, gate)


class GatewayEnabledClient(Client):
    """A client that receives clicks as gateway events. `client_from_app` or `client_from_lightbulb` makes one."""

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
        """The gateway app the client receives interactions from."""
        return self._app

    async def _on_interaction_create(self, event: hikari.ComponentInteractionCreateEvent) -> None:
        await self._process_interaction(event.interaction)


class RestEnabledClient(Client):
    """A client that receives clicks from hikari's interaction server.

    The interaction server has a single component listener. The client takes it over and
    passes every interaction it does not handle on to the listener it replaced, and it
    logs a warning naming that listener unless `warn_shared_listener=False`.
    `client_from_app` or `client_from_lightbulb` makes one.
    """

    __slots__ = ("_app", "_replaced")

    def __init__(
        self,
        app: RestClientAppT,
        *,
        register_app_dependencies: bool = True,
        warn_shared_listener: bool = True,
        **options: typing.Unpack[_ClientOptions],
    ) -> None:
        super().__init__(app.rest, register_app_dependencies=register_app_dependencies, **options)
        self._app = app

        if register_app_dependencies:
            self._register_dependency(hikari.api.InteractionServer, app.interaction_server)
            if isinstance(app, hikari.RESTBot):
                self._register_dependency(hikari.RESTBot, app)

        self._replaced = typing.cast(
            "_ComponentListener | None",
            app.interaction_server.get_listener(hikari.ComponentInteraction),
        )
        if self._replaced is not None and warn_shared_listener:
            _LOGGER.warning(
                "the interaction server already had a component listener, %r; risa now receives every"
                " component interaction first and passes each one it does not handle on to that listener."
                " Pass warn_shared_listener=False to the risa client once this is intended",
                self._replaced,
            )
        app.interaction_server.set_listener(hikari.ComponentInteraction, self._on_interaction, replace=True)

    @property
    @typing.override
    def app(self) -> RestClientAppT:
        """The REST app the client receives interactions from."""
        return self._app

    async def _on_interaction(
        self,
        interaction: hikari.ComponentInteraction,
    ) -> collections.abc.AsyncGenerator[_ComponentResponse | None, None]:
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
        if not gate.adopted and self._replaced is not None:
            await task
            call = self._replaced(interaction)
            if isinstance(call, collections.abc.AsyncGenerator):
                async for response in call:
                    yield response
            else:
                yield await call
            return
        yield None
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
    *,
    warn_shared_listener: bool = True,
    **options: typing.Unpack[_ClientOptions],
) -> RestEnabledClient: ...


def client_from_app(
    app: GatewayClientAppT | RestClientAppT,
    *,
    warn_shared_listener: bool = True,
    **options: typing.Unpack[_ClientOptions],
) -> Client:
    """Make a client for a hikari app, gateway or REST.

    ```python
    bot = hikari.GatewayBot(TOKEN)
    client = risa.client_from_app(bot, store=risa.MemoryStore())
    ```

    Parameters
    ----------
    app
        The app to receive interactions from.
    warn_shared_listener
        REST only: warn when the client takes over a component listener another library
        set.
    **options
        The options of `Client`.

    Returns
    -------
    :
        A `GatewayEnabledClient` or a `RestEnabledClient`, depending on the app.
    """
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(app, **options)
    return RestEnabledClient(app, warn_shared_listener=warn_shared_listener, **options)


def client_from_lightbulb(
    lightbulb_client: LightbulbClient,
    *,
    warn_shared_listener: bool = True,
    **options: typing.Unpack[_LightbulbOptions],
) -> Client:
    """Make a client that shares a lightbulb client's app and dependency manager.

    Handlers can then be injected with anything registered with lightbulb. The app's own
    dependencies are left to lightbulb, which registers them already.

    Parameters
    ----------
    lightbulb_client
        The lightbulb client to share.
    warn_shared_listener
        REST only: warn when the client takes over lightbulb's component listener.
    **options
        The options of `Client`, apart from `di`.

    Returns
    -------
    :
        A `GatewayEnabledClient` or a `RestEnabledClient`, depending on the app.

    Raises
    ------
    TypeError
        If the lightbulb client's app is neither a gateway nor a REST app.
    """
    app = lightbulb_client.app
    if isinstance(app, GatewayClientAppT):
        return GatewayEnabledClient(app, di=lightbulb_client.di, register_app_dependencies=False, **options)
    if isinstance(app, RestClientAppT):
        return RestEnabledClient(
            app,
            di=lightbulb_client.di,
            register_app_dependencies=False,
            warn_shared_listener=warn_shared_listener,
            **options,
        )

    msg = "the lightbulb client's app has neither an event manager nor an interaction server"
    raise TypeError(msg)


def _store_slots(
    store: protocol.Store | None,
    stores: collections.abc.Mapping[str, protocol.Store],
) -> dict[str, protocol.Store]:
    if store is None:
        return dict(stores)
    if placement_.DEFAULT_STORE in stores:
        msg = f"the {placement_.DEFAULT_STORE!r} store was given twice, as store= and in stores=; pass it one way"
        raise ValueError(msg)
    return {placement_.DEFAULT_STORE: store, **stores}


def _missing_store(name: str) -> str:
    if name == placement_.DEFAULT_STORE:
        return (
            "it keeps its state in the default store, but this client has none; pass store=... to the"
            " client - store=risa.MemoryStore() needs nothing installed"
        )
    return (
        f"it keeps its state in store {name!r}, but this client has no store of that name; pass"
        f" stores={{{name!r}: ...}} to the client"
    )


def _own_hooks(meta: registry.ViewMeta) -> list[hooks_.AnyHook]:
    return [*meta.hooks, *(hook for record in meta.handlers.values() for hook in record.hooks)]
