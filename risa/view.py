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
"""Declaring views and the handlers their components route to.

Handlers are ordinary methods marked with :func:`handler`. The mark wraps the
method in a descriptor recording a durable identity -- an id and a version --
and accessing it on a view instance yields a bound form whose ``bind()`` bakes
per-component arguments into the ``custom_id``. The view decorator later
collects every marked method into the routing table the dispatcher resolves
callbacks against.
"""

from __future__ import annotations

import abc
import collections.abc
import inspect
import logging
import typing

import linkd
import msgspec

from risa import errors
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry

if typing.TYPE_CHECKING:
    from risa import context
    from risa import ui

__all__ = (
    "BoundHandler",
    "BoundHandlerMethod",
    "HandlerFunction",
    "HandlerMethod",
    "View",
    "ZeroArgHandler",
    "handler",
    "register",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.view")

type HandlerFunction[V: View, **P] = collections.abc.Callable[
    typing.Concatenate[V, context.ComponentContext, P],
    collections.abc.Awaitable[None],
]
"""The shape of a method :func:`handler` accepts.

The parameters after the context are the handler's own: a leading block of
converter-typed wire parameters filled from the ``custom_id``, then anything
dependency injection should resolve.
"""


class BoundHandler(msgspec.Struct, frozen=True):
    """A handler identity with its wire arguments already encoded.

    What :meth:`BoundHandlerMethod.bind` returns and what a component is
    ultimately built from. Encoding has already happened, so holding one of
    these cannot fail later than the ``render()`` that produced it.

    Attributes
    ----------
    handler_id
        The handler's durable id.
    version
        The handler's version.
    token
        The encoded token the handler routes under.
    payload
        The encoded args payload: fingerprint and frames, or ``""`` when the
        handler takes no wire arguments.
    """

    handler_id: str
    version: int
    token: str
    payload: str


class BoundHandlerMethod[**P]:
    """A handler as accessed on a view instance.

    Awaitable like the plain method it wraps, so one handler version can
    delegate to another, and the carrier of :meth:`bind`, which is how a
    component gets per-component arguments.
    """

    __slots__ = ("_call", "_handler_id", "_signature", "_token", "_version")

    def __init__(
        self,
        call: collections.abc.Callable[
            typing.Concatenate[context.ComponentContext, P],
            collections.abc.Awaitable[None],
        ],
        *,
        handler_id: str,
        version: int,
        token: str,
        signature: collections.abc.Callable[[], codec.HandlerSignature],
    ) -> None:
        self._call = call
        self._handler_id = handler_id
        self._version = version
        self._token = token
        self._signature = signature

    def __call__(
        self,
        ctx: context.ComponentContext,
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> collections.abc.Awaitable[None]:
        """Invoke the handler directly, as one version delegating to another does.

        Parameters
        ----------
        ctx
            The interaction being answered.
        *args
            The handler's own positional arguments.
        **kwargs
            The handler's own keyword arguments.

        Returns
        -------
        collections.abc.Awaitable[None]
            The handler's own coroutine.
        """
        return self._call(ctx, *args, **kwargs)

    def bind(self, *args: P.args, **kwargs: P.kwargs) -> BoundHandler:
        """Bake arguments for this handler into a component identity.

        The bound values are encoded immediately, so a value that does not fit
        fails at the ``render()`` call site rather than when the component is
        clicked. Keywords are normalised to positions; together with any
        positional values they must cover a contiguous prefix of the wire
        parameters, and every omitted parameter must have a default, which
        dispatch later lets apply.

        Returns
        -------
        BoundHandler
            The identity a component routes presses through, args included.

        Raises
        ------
        ArgBindError
            If the arguments do not fit the handler's wire parameters.
        HandlerSignatureError
            If the handler's signature cannot be used for wire arguments.
        """
        signature = self._signature()
        specs = signature.args
        if len(args) > len(specs):
            reason = f"handler takes at most {len(specs)} wire arguments, got {len(args)}"
            raise errors.ArgBindError(self._handler_id, reason)

        values: list[object] = list(args)
        if kwargs:
            remaining = dict(kwargs)
            for index in range(len(args), len(specs)):
                name = specs[index].name
                if name not in remaining:
                    break
                values.append(remaining.pop(name))
            for name in remaining:
                position = next((index for index, spec in enumerate(specs) if spec.name == name), None)
                if position is None:
                    reason = f"{name!r} is not a wire parameter of this handler"
                elif position < len(args):
                    reason = f"got multiple values for wire parameter {name!r}"
                else:
                    reason = f"cannot bind {name!r} without also binding the wire parameters before it"
                raise errors.ArgBindError(self._handler_id, reason)

        payload = codec.encode_args(values, signature, handler_id=self._handler_id)
        return BoundHandler(
            handler_id=self._handler_id,
            version=self._version,
            token=self._token,
            payload=payload,
        )


@typing.runtime_checkable
class ZeroArgHandler(typing.Protocol):
    """A handler that can sit on a component without an explicit ``bind()``.

    Structurally, anything whose ``bind`` is callable with no arguments --
    which is precisely a handler whose wire parameters are all defaulted or
    absent. A handler with required wire parameters does not match, so passing
    one to a component without binding is a static error, not a click that
    fails later.
    """

    def bind(self) -> BoundHandler:
        """Encode an empty binding.

        Returns
        -------
        BoundHandler
            The identity a component routes presses through.
        """
        ...


class HandlerMethod[V: View, **P]:
    """The descriptor :func:`handler` wraps a component callback in.

    On the class it evaluates to itself, which is how ``@risa.register``
    collects it; on an instance it evaluates to a :class:`BoundHandlerMethod`,
    which is what ``render()`` builds components from.
    """

    __slots__ = ("_callback", "_fn", "_handler_id", "_signature", "_token", "_version")

    def __init__(self, fn: HandlerFunction[V, P], *, handler_id: str, version: int) -> None:
        self._fn = fn
        self._callback: registry.Handler = typing.cast("registry.Handler", linkd.inject(fn))
        self._handler_id = handler_id
        self._version = version
        self._token = codec.make_handler_token(handler_id, version)
        self._signature: codec.HandlerSignature | None = None

    @property
    def handler_id(self) -> str:
        """The handler's durable id."""
        return self._handler_id

    @property
    def version(self) -> int:
        """The handler's version."""
        return self._version

    @property
    def token(self) -> str:
        """The encoded token the handler routes under."""
        return self._token

    @property
    def callback(self) -> registry.Handler:
        """The callback dispatch runs, wrapped for dependency injection."""
        return self._callback

    def signature(self) -> codec.HandlerSignature:
        """Resolve and cache the handler's wire signature.

        Returns
        -------
        codec.HandlerSignature
            The wire parameters and their fingerprint.

        Raises
        ------
        HandlerSignatureError
            If the signature cannot be used for wire arguments.
        """
        if self._signature is None:
            self._signature = _resolve_signature(self._fn, handler_id=self._handler_id)
        return self._signature

    @typing.overload
    def __get__(self, obj: None, objtype: type[V]) -> HandlerMethod[V, P]: ...

    @typing.overload
    def __get__(self, obj: V, objtype: type[V] | None = ...) -> BoundHandlerMethod[P]: ...

    def __get__(self, obj: V | None, objtype: type[V] | None = None) -> HandlerMethod[V, P] | BoundHandlerMethod[P]:
        """Evaluate to the descriptor on the class, or its bound form on an instance.

        Returns
        -------
        HandlerMethod[V, P] | BoundHandlerMethod[P]
            The descriptor itself when accessed on the class, otherwise the
            handler bound to ``obj``.
        """
        if obj is None:
            return self

        def call(
            ctx: context.ComponentContext,
            /,
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> collections.abc.Awaitable[None]:
            return self._callback(obj, ctx, *args, **kwargs)

        return BoundHandlerMethod(
            call,
            handler_id=self._handler_id,
            version=self._version,
            token=self._token,
            signature=self.signature,
        )


def _resolve_signature(fn: collections.abc.Callable[..., object], *, handler_id: str) -> codec.HandlerSignature:
    """Classify a handler's parameters into wire arguments and the rest.

    Wire parameters are the contiguous block of converter-typed parameters
    directly after ``ctx``. The first parameter that has no converter -- or
    that defaults to ``linkd.INJECTED``, or is not positional -- ends the
    block; everything after it is left to dependency injection.

    Returns
    -------
    codec.HandlerSignature
        The wire parameters and their fingerprint.

    Raises
    ------
    HandlerSignatureError
        If an annotation cannot be resolved at runtime, the parameters before
        the wire block are missing, or a wire parameter follows a non-wire one.
    """
    try:
        hints = typing.get_type_hints(fn)
    except NameError as exc:
        reason = f"annotation {exc.name!r} is not resolvable at runtime; handler annotations need real imports"
        raise errors.HandlerSignatureError(handler_id, reason) from exc

    parameters = list(inspect.signature(fn).parameters.values())
    if len(parameters) < 2:  # ruff:ignore[magic-value-comparison]
        reason = "a handler takes at least (self, ctx)"
        raise errors.HandlerSignatureError(handler_id, reason)

    positional = {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    specs: list[codec.ArgSpec] = []
    wire_ended = False
    for parameter in parameters[2:]:
        converter = None
        if parameter.kind in positional and parameter.default is not linkd.INJECTED:
            converter = codec.resolve_converter(hints.get(parameter.name))
        if converter is None:
            wire_ended = True
            continue
        if wire_ended:
            reason = (
                f"wire parameter {parameter.name!r} follows a non-wire parameter;"
                " wire parameters must form a contiguous block directly after ctx"
            )
            raise errors.HandlerSignatureError(handler_id, reason)
        specs.append(
            codec.ArgSpec(
                name=parameter.name,
                converter=converter,
                required=parameter.default is inspect.Parameter.empty,
            ),
        )

    fingerprint = codec.make_signature_fingerprint([spec.converter.type_id for spec in specs]) if specs else ""
    return codec.HandlerSignature(args=tuple(specs), fingerprint=fingerprint)


class View(msgspec.Struct):
    """Base class every view inherits from.

    Subclassing declares the metadata attribute that ``@risa.register`` later
    fills in, which is what lets the dispatcher and :meth:`Client.add_view
    <risa.client.Client.add_view>` be statically typed against views rather than
    against bare classes.

    Being a :class:`msgspec.Struct`, a subclass's annotated attributes become its
    persisted state: they are what gets serialised into the store and what the
    view is rebuilt from when an interaction arrives.
    """

    __risa_view_meta__: typing.ClassVar[registry.ViewMeta]

    @abc.abstractmethod
    def render(self) -> ui.Layout:
        """Describe what the message should look like right now.

        Called whenever the view is built or rebuilt, and read only from the
        view's own state, so that the same state always produces the same
        message. It must not do any I/O: it runs inside the window Discord
        allows for a response, and runs again on every re-render.

        Returns
        -------
        ui.Layout
            The tree to render, as one top-level component or several.
        """

    @classmethod
    async def on_state_missing(cls, ctx: context.ComponentContext) -> None:
        """Answer a click on a component whose state is no longer stored.

        A class method rather than an instance one, because the reason it is
        called is precisely that there was nothing to rebuild an instance from.

        Four different things arrive here and are indistinguishable: the entry
        expired, it was evicted to keep the store within its bounds, the store
        was emptied, or something deleted it deliberately to retire the view.
        All of them mean the same thing to whoever clicked, so they are not
        worth telling apart.

        Overriding this is how a view says what it wants said. The default only
        logs, because the interaction is left unanswered otherwise, and Discord
        shows the user an error once the response window closes.

        Parameters
        ----------
        ctx
            The interaction that found nothing.
        """
        _LOGGER.debug(
            "state for view %r is gone; interaction %s went unanswered. Override on_state_missing to say so.",
            cls.__name__,
            ctx.interaction.id,
        )

    @classmethod
    async def on_outdated(cls, ctx: context.ComponentContext) -> None:
        """Answer a click on a component that outlived its handler.

        Reached when the component's view still resolves but its handler does
        not: the handler's id or version changed, or its signature was edited
        in place. Either way the click cannot be dispatched, and this hook is
        where the view says so to whoever clicked.

        Overriding it also acknowledges that components are retired on
        purpose, which is what downgrades the client's token-miss log from a
        warning to debug. The default only logs, because the interaction is
        left unanswered otherwise.

        Parameters
        ----------
        ctx
            The interaction that could not be dispatched.
        """
        _LOGGER.debug(
            "a component of view %r outlived its handler; interaction %s went unanswered."
            " Override on_outdated to say so.",
            cls.__name__,
            ctx.interaction.id,
        )


@typing.overload
def handler[V: View, **P](fn: HandlerFunction[V, P], /) -> HandlerMethod[V, P]: ...


@typing.overload
def handler[V: View, **P](
    *,
    handler_id: str | None = ...,
    version: int = ...,
) -> collections.abc.Callable[[HandlerFunction[V, P]], HandlerMethod[V, P]]: ...


def handler[V: View, **P](
    fn: HandlerFunction[V, P] | None = None,
    /,
    *,
    handler_id: str | None = None,
    version: int = 1,
) -> HandlerMethod[V, P] | collections.abc.Callable[[HandlerFunction[V, P]], HandlerMethod[V, P]]:
    """Mark a method as a component callback.

    Usable bare, taking its identity from the method name::

        @risa.handler
        async def vote(self, ctx: risa.ComponentContext) -> None: ...

    or called, pinning the identity so the method can be renamed freely, and
    optionally versioning it::

        @risa.handler(handler_id="vote", version=2)
        async def cast_vote(self, ctx: risa.ComponentContext) -> None: ...

    The id and version together are a durable wire identity: they are hashed
    into the ``custom_id`` of every component routing here, so components
    already delivered to Discord keep working across a restart. Renaming the
    *method* is safe whenever the id is pinned; bumping the *version* retires
    components carrying the old one -- or keeps them alive, if a method is left
    registered under the old version. Two methods may share an id at different
    versions, which is how an old signature keeps being served while new
    renders bind the new one.

    Parameters after ``ctx`` whose annotations have converters (``int``,
    ``str``, ``bool``, enums, snowflakes) are wire parameters, filled from the
    ``custom_id`` via :meth:`BoundHandlerMethod.bind`; the first parameter
    without one ends that block, and the rest is resolved by dependency
    injection when the handler runs.

    Parameters
    ----------
    fn
        The callback to mark, when used bare. Omitted when passing options.
    handler_id
        Stable identity for the callback. Defaults to the method's name.
    version
        Version of the callback's wire identity. Defaults to ``1``.

    Returns
    -------
    HandlerMethod[V, P] | collections.abc.Callable[[HandlerFunction[V, P]], HandlerMethod[V, P]]
        The wrapped callback when used bare, otherwise a decorator that wraps
        one.
    """

    def decorate(f: HandlerFunction[V, P]) -> HandlerMethod[V, P]:
        return HandlerMethod(f, handler_id=handler_id or f.__name__, version=version)

    if fn is not None:
        return decorate(fn)
    return decorate


def register[T: View](
    *,
    name: str,
    version: int = 1,
    ttl: float | None = None,
    persist: bool = True,
) -> collections.abc.Callable[[type[T]], type[T]]:
    """Declare a view and make its components routable.

    Collects every method marked with :func:`handler` into a routing table,
    derives the view's cookie from ``name`` and ``version``, and registers the
    result in the process-wide registry::

        @risa.register(name="poll", version=1)
        class Poll(risa.View):
            question: str

            @risa.handler
            async def vote(self, ctx: risa.ComponentContext) -> None: ...

    The name is required rather than taken from the class name, because it is
    half of a durable identity: it is hashed with the version into the cookie
    carried by every component this view renders. Renaming the *class* is
    therefore free, while changing the *name* retires components already sent.

    Bumping ``version`` deliberately changes the cookie, so components rendered
    by an older schema stop resolving instead of being decoded into a shape they
    no longer match.

    A view that declares no fields holds no state. Nothing is written to the
    store for one, and its components carry no key, which leaves the whole
    payload free for arguments. ``persist=False`` extends that mode to views
    *with* fields, turning them into render-only props.

    Registration is unconditional and process-wide. A
    :class:`~risa.client.Client` only consults that registry when it is created
    with ``use_global=True``; otherwise pass the view to
    :meth:`Client.add_view <risa.client.Client.add_view>`.

    Parameters
    ----------
    name
        Durable identity for the view. Must be unique per schema version.
    version
        Schema version of the view's state. Defaults to ``1``.
    ttl
        Seconds this view's state survives without being interacted with. The
        clock is reset on every interaction, so a view in use never expires and
        one that was abandoned eventually does. Defaults to ``None``, which
        keeps state until it is deleted or evicted -- a component that stops
        working after an interval nobody chose is worse than a store that grows
        somewhere its size can be seen.
    persist
        Whether the view's fields are persisted state. ``True`` (the default)
        writes them to the store on every build so an interaction can rebuild
        the view. ``False`` declares a *props-only* view: fields are render
        inputs supplied by whoever constructs it, nothing is ever stored, and
        dispatch rebuilds the view from its defaults -- so every field must
        have one, and a handler must refill whatever its redraw renders from.
        For views that are a pure function of external data plus wire args,
        such as a leaderboard rendered from the bot's own database.

    Returns
    -------
    collections.abc.Callable[[type[T]], type[T]]
        A decorator that registers the class and returns it unchanged.

    Raises
    ------
    DuplicateHandlerError
        If two of the view's handlers map to the same token. Raised when the
        returned decorator is applied.
    DuplicateViewError
        If a different view is already registered under the same cookie. Raised
        when the returned decorator is applied.
    HandlerSignatureError
        If a handler's signature cannot be used for wire arguments. Raised when
        the returned decorator is applied.
    ViewDeclarationError
        If ``persist=False`` is combined with a ``ttl`` or with a field that
        has no default. Raised when the returned decorator is applied.
    """

    def decorate(cls: type[T]) -> type[T]:
        fields = msgspec.structs.fields(cls)
        if not persist:
            if ttl is not None:
                reason = "persist=False stores nothing, so ttl has nothing to apply to"
                raise errors.ViewDeclarationError(name, reason)
            required = [field.name for field in fields if field.required]
            if required:
                names = ", ".join(repr(field_name) for field_name in required)
                reason = f"persist=False rebuilds the view from defaults on dispatch, but {names} have none"
                raise errors.ViewDeclarationError(name, reason)

        handlers: dict[str, registry.HandlerRecord] = {}

        for _, member in inspect.getmembers(cls):
            if not isinstance(member, HandlerMethod):
                continue
            signature = member.signature()
            existing = handlers.get(member.token)
            if existing is not None:
                raise errors.DuplicateHandlerError(
                    name,
                    member.handler_id,
                    member.version,
                    existing.handler_id,
                    existing.version,
                    member.token,
                )
            handlers[member.token] = registry.HandlerRecord(
                callback=member.callback,
                handler_id=member.handler_id,
                version=member.version,
                signature=signature,
            )

        outdated_definer = next(klass for klass in cls.__mro__ if "on_outdated" in vars(klass))
        meta = registry.ViewMeta(
            cls=cls,
            name=name,
            version=version,
            cookie=codec.make_cookie(name, version),
            handlers=handlers,
            stateless=not persist or not fields,
            handles_outdated=outdated_definer is not View,
            ttl=ttl,
        )
        setattr(cls, constants.VIEW_META, meta)
        registry.global_registry().register(meta)
        return cls

    return decorate
