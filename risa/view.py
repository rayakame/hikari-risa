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
"""Views, their handlers, and how they are registered."""

from __future__ import annotations

import enum
import inspect
import typing

import linkd
import msgspec

from risa import binding as binding_
from risa import codecs
from risa import errors
from risa.internal import codec
from risa.internal import registry
from risa.state import placement as placement_
from risa.state import schema as schema_

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import context
    from risa import ui

__all__ = (
    "AutoDefer",
    "HandlerFunction",
    "HandlerMethod",
    "View",
    "handler",
    "register",
)

type HandlerFunction[V: View, **P] = collections.abc.Callable[
    typing.Concatenate[V, context.ComponentContext, P],
    collections.abc.Awaitable[None],
]


class AutoDefer(enum.StrEnum):
    """How risa answers a click whose handler has not answered in time.

    Discord fails an interaction that gets no answer within three seconds. With
    auto-defer, risa acknowledges the click in the handler's place after the client's
    `auto_defer_delay`, and the handler carries on. Set it on the client, on a view with
    `@risa.register(defer=...)`, or on a handler with `@risa.handler(defer=...)`; the
    most specific setting wins.

    Attributes
    ----------
    OFF
        Never answer in the handler's place. The default.
    UPDATE
        Acknowledge silently, for a handler that answers with `ctx.rerender()` or
        `ctx.edit(...)`.
    THINKING
        Show a "thinking..." message, for a handler that answers with `ctx.respond(...)`,
        which then takes its place.
    THINKING_EPHEMERAL
        The same, visible only to the user who clicked.
    """

    OFF = enum.auto()
    UPDATE = enum.auto()
    THINKING = enum.auto()
    THINKING_EPHEMERAL = enum.auto()


class HandlerMethod[V: View, **P]:
    """A view method made a handler by `@risa.handler`.

    Accessed on a view instance, as `self.vote`, it is the handler to put on a component,
    bare or through `risa.bind`. It cannot be called. Accessed on the class, as
    `Poll.vote`, it is this object, and `Poll.vote.func` is the plain function, for one
    handler that delegates to another.
    """

    __slots__ = ("_defer", "_func", "_handler_id", "_owner", "_signature", "_token", "_version")

    def __init__(
        self,
        func: collections.abc.Callable[..., collections.abc.Awaitable[None]],
        *,
        handler_id: str,
        version: int,
        defer: AutoDefer | None = None,
    ) -> None:
        self._func = func
        self._handler_id = handler_id
        self._version = version
        self._token = codec.make_handler_token(handler_id, version)
        self._defer = defer
        self._signature: codec.HandlerSignature | None = None
        self._owner: type[View] | None = None

    @property
    def owner(self) -> type[View] | None:
        """The view class the handler is defined on."""
        return self._owner

    @property
    def signature(self) -> codec.HandlerSignature:
        """The handler's wire parameters, resolved on first use."""
        if self._signature is None:
            self._signature = codec.resolve_signature(self._func)
        return self._signature

    @property
    def func(self) -> collections.abc.Callable[..., collections.abc.Awaitable[None]]:
        """The decorated function."""
        return self._func

    @property
    def handler_id(self) -> str:
        """The id the handler routes under."""
        return self._handler_id

    @property
    def version(self) -> int:
        """The handler's version."""
        return self._version

    @property
    def token(self) -> str:
        """The routing token, a hash of the id and the version."""
        return self._token

    @property
    def defer(self) -> AutoDefer | None:
        """The handler's own `AutoDefer`, or `None` to use the view's."""
        return self._defer

    @typing.overload
    def __get__(self, instance: None, owner: type[V]) -> HandlerMethod[V, P]: ...

    @typing.overload
    def __get__(self, instance: V, owner: type[V]) -> binding_.BindTarget[P]: ...

    def __get__(self, instance: V | None, owner: type[V]) -> HandlerMethod[V, P] | binding_.BindTarget[P]:
        if instance is None:
            return self
        return typing.cast(
            "binding_.BindTarget[P]",
            binding_.HandlerBinder(
                handler_id=self._handler_id,
                version=self._version,
                token=self._token,
                signature=self.signature,
                func_name=self._func.__qualname__,
                owner=self.owner,
            ),
        )

    def __set_name__(self, owner: type[View], _name: str) -> None:
        self._owner = owner


class _HandlerDecorator(typing.Protocol):
    def __call__[V: View, **P](self, func: HandlerFunction[V, P], /) -> HandlerMethod[V, P]: ...


@typing.overload
def handler[V: View, **P](func: HandlerFunction[V, P], /) -> HandlerMethod[V, P]: ...


@typing.overload
def handler(
    *,
    handler_id: str | None = ...,
    version: int = ...,
    defer: AutoDefer | None = ...,
) -> _HandlerDecorator: ...


def handler(
    func: collections.abc.Callable[..., collections.abc.Awaitable[None]] | None = None,
    /,
    *,
    handler_id: str | None = None,
    version: int = 1,
    defer: AutoDefer | None = None,
) -> object:
    """Make a view method a handler that components can route to.

    Use it bare or with options. A handler takes the view and a `ComponentContext`, then
    its wire parameters, then anything to inject:

    ```python
    @risa.handler(defer=risa.AutoDefer.UPDATE)
    async def vote(
        self, ctx: risa.ComponentContext, option: int, db: Database
    ) -> None: ...
    ```

    Wire parameters come first, are positional, and are annotated `bool`, `int`, `str`,
    `hikari.Snowflake` or an `enum.Enum` of `int` or `str` values. They receive the
    values `risa.bind` put on the component. Every parameter after them is injected
    through linkd, so a hikari enum such as `hikari.ChannelType`, which is not an
    `enum.Enum`, is injected too; to carry one on the wire, bind its value as an `int`.
    A handler returns `None`.

    Parameters
    ----------
    handler_id
        The id the handler routes under, the method's name by default. Pin it before
        renaming the method, so components already sent keep working.
    version
        Bump it when the wire parameters change. Components sent before keep routing to a
        method that still has the old version; once none does, they go to
        `View.on_outdated`.
    defer
        The handler's `AutoDefer`, overriding the view's.

    Returns
    -------
    HandlerMethod | Callable[[HandlerFunction], HandlerMethod]
        The handler, or a decorator that makes one when options are given.
    """

    def decorate(f: collections.abc.Callable[..., collections.abc.Awaitable[None]]) -> HandlerMethod[View, ...]:
        return HandlerMethod(
            f,
            handler_id=handler_id if handler_id is not None else f.__name__,
            version=version,
            defer=defer,
        )

    return decorate(func) if func is not None else decorate


class View(msgspec.Struct):
    """Base class of a view: a message's layout, its state, and the handlers behind it.

    A view is a `msgspec.Struct`, and its annotated fields are its durable state, carried
    from click to click. Mark a field `risa.Prop[...]` to rebuild it fresh on every click
    instead, typically in `load`. Register the class with `@risa.register`, return its
    layout from `render`, and send it with `client.build`:

    ```python
    @risa.register(name="counter")
    class Counter(risa.View):
        count: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.add, label=f"Clicked {self.count} times"))

        @risa.handler
        async def add(self, ctx: risa.ComponentContext) -> None:
            self.count += 1
            await ctx.rerender()
    ```

    Each click runs its handler as a transaction on the latest saved state of its view:
    clicks on one view queue up within a process, and across processes the first write
    wins. A handler that changes the state and does not rerender still saves it when it
    returns; one that raises saves nothing.
    """

    def render(self) -> ui.Layout:
        """Return the view's layout, drawn from its current state.

        It runs for the first send and for every rerender. Keep it synchronous and free of
        I/O; fetch what it shows in `load`.
        """
        raise NotImplementedError

    async def load(self) -> None:
        """Fill the view's props before it renders.

        risa awaits it before the first send and on every click, after restoring the durable
        state and before the handler runs. Its parameters are injected, as a handler's are.
        """

    @classmethod
    async def on_outdated(cls, ctx: context.ComponentContext) -> None:
        """Answer a click on a component this view can no longer route.

        The component's handler was retired by a version bump, its wire parameters
        changed without one, or its state was written by durable fields the view no
        longer has. The default does nothing, so the click fails on the user's side, and
        risa logs it. Override it to answer the user, or to replace the dead message with
        `ctx.edit(...)`. Its parameters are injected, as a handler's are, and `ctx` has
        no view state.

        To answer clicks on one retired handler differently, keep a method with its id,
        version and wire parameters whose body says the component is outdated. It routes
        like any handler, and nothing is logged.
        """

    @classmethod
    async def on_state_missing(cls, ctx: context.ComponentContext) -> None:
        """Answer a click on an `InStore` view whose state is gone.

        The record expired or was deleted. The default does nothing, so the click fails on
        the user's side, and risa logs a warning. Override it to answer the user, or to
        replace the message with `ctx.edit(...)`. Its parameters are injected, as a
        handler's are, and `ctx` has no view state.
        """


def _inject(func: collections.abc.Callable[..., collections.abc.Awaitable[None]]) -> registry.DispatchCallback:
    return typing.cast("registry.DispatchCallback", linkd.inject(func))


def _injected_outdated(cls: type[View]) -> registry.HookCallback | None:
    if cls.on_outdated.__func__ is View.on_outdated.__func__:
        return None
    return typing.cast("registry.HookCallback", linkd.inject(cls.on_outdated))


def _injected_state_missing(cls: type[View]) -> registry.HookCallback | None:
    if cls.on_state_missing.__func__ is View.on_state_missing.__func__:
        return None
    return typing.cast("registry.HookCallback", linkd.inject(cls.on_state_missing))


def _injected_load(cls: type[View]) -> registry.LoadCallback | None:
    if cls.load is View.load:
        return None
    return typing.cast("registry.LoadCallback", linkd.inject(cls.load))


def _check_placement(view_name: str, *, state: placement_.StatePlacement, schema: schema_.StateSchema) -> None:
    if isinstance(state, placement_.InStore) and not schema.durable:
        reason = (
            "state=risa.InStore(...) was given, but every field is a risa.Prop, so there is nothing"
            " to store; drop the argument, or drop the marker from the fields that should persist"
        )
        raise errors.ViewDeclarationError(view_name, reason)
    try:
        codecs.register(state.codec)
    except ValueError as exc:
        raise errors.ViewDeclarationError(view_name, str(exc)) from exc
    codecs.ensure_available(state.codec)


def register[T: View](
    *,
    name: str,
    version: int = 1,
    defer: AutoDefer | None = None,
    state: placement_.StatePlacement = placement_.DEFAULT_PLACEMENT,
) -> typing.Callable[[type[T]], type[T]]:
    """Register a view class so its components can be routed.

    ```python
    @risa.register(name="poll", state=risa.InStore(ttl=30 * 86400))
    class Poll(risa.View): ...
    ```

    A client then routes it once it is passed to `client.add_view`. The decorator raises
    `ViewDeclarationError` for a class risa cannot serve, such as a blank name, a prop
    without a default, or an `InStore` view without durable fields.

    Parameters
    ----------
    name
        The name the view routes under. Renaming it retires every component already sent.
    version
        The schema version of the durable state. Appending fields that have defaults needs
        no bump; any other change to the durable fields does, and components sent before
        the bump no longer route to the view.
    defer
        The view's `AutoDefer`, for handlers that set none.
    state
        Where the durable state lives: `InMessage()`, the default, or `InStore(...)`.

    Returns
    -------
    Callable[[type[T]], type[T]]
        The decorator. It returns the class unchanged.
    """

    def decorate(cls: type[T]) -> type[T]:
        if not name.strip():
            reason = "a view needs a name to be routable, and it must not be blank"
            raise errors.ViewDeclarationError(cls.__name__, reason)
        if version < 1:
            reason = f"version must be 1 or greater, got {version}"
            raise errors.ViewDeclarationError(name, reason)

        schema = schema_.StateSchema.of(cls, view_name=name)
        _check_placement(name, state=state, schema=schema)

        handlers: dict[str, registry.HandlerRecord] = {}
        for _, member in inspect.getmembers(cls):
            if not isinstance(member, HandlerMethod):
                continue
            if (existing := handlers.get(member.token)) is not None:
                raise errors.DuplicateHandlerError(
                    name,
                    member.token,
                    first_id=existing.handler_id,
                    first_version=existing.version,
                    second_id=member.handler_id,
                    second_version=member.version,
                )
            resolved_defer = member.defer if member.defer is not None else defer
            handlers[member.token] = registry.HandlerRecord(
                callback=_inject(member.func),
                handler_id=member.handler_id,
                version=member.version,
                defer=resolved_defer,
                signature=member.signature,
            )

        meta = registry.ViewMeta(
            cls=cls,
            name=name,
            version=version,
            schema=schema,
            state=state,
            handlers=handlers,
            outdated=_injected_outdated(cls),
            state_missing=_injected_state_missing(cls),
            loader=_injected_load(cls),
        )
        registry.global_registry().register(meta)
        registry.stamp(cls, meta)
        return cls

    return decorate
