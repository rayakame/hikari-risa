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

import enum
import inspect
import typing

import linkd
import msgspec

from risa import binding as binding_
from risa import errors
from risa.internal import codec
from risa.internal import registry

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
    OFF = enum.auto()
    UPDATE = enum.auto()
    THINKING = enum.auto()
    THINKING_EPHEMERAL = enum.auto()


class HandlerMethod[V: View, **P]:
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
        return self._owner

    @property
    def signature(self) -> codec.HandlerSignature:
        if self._signature is None:
            self._signature = codec.resolve_signature(self._func)
        return self._signature

    @property
    def func(self) -> collections.abc.Callable[..., collections.abc.Awaitable[None]]:
        return self._func

    @property
    def handler_id(self) -> str:
        return self._handler_id

    @property
    def version(self) -> int:
        return self._version

    @property
    def token(self) -> str:
        return self._token

    @property
    def defer(self) -> AutoDefer | None:
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
    def decorate(f: collections.abc.Callable[..., collections.abc.Awaitable[None]]) -> HandlerMethod[View, ...]:
        return HandlerMethod(
            f,
            handler_id=handler_id if handler_id is not None else f.__name__,
            version=version,
            defer=defer,
        )

    return decorate(func) if func is not None else decorate


class View(msgspec.Struct):
    def render(self) -> ui.Layout:
        raise NotImplementedError

    @classmethod
    async def on_outdated(cls, ctx: context.ComponentContext) -> None:
        """Answer a click on a component this view can no longer route."""


def _inject(func: collections.abc.Callable[..., collections.abc.Awaitable[None]]) -> registry.DispatchCallback:
    return typing.cast("registry.DispatchCallback", linkd.inject(func))


def _injected_outdated(cls: type[View]) -> registry.OutdatedCallback | None:
    if cls.on_outdated.__func__ is View.on_outdated.__func__:
        return None
    return typing.cast("registry.OutdatedCallback", linkd.inject(cls.on_outdated))


def register[T: View](
    *, name: str, version: int = 1, defer: AutoDefer | None = None
) -> typing.Callable[[type[T]], type[T]]:
    def decorate(cls: type[T]) -> type[T]:
        if not name.strip():
            reason = "a view needs a name to be routable, and it must not be blank"
            raise errors.ViewDeclarationError(cls.__name__, reason)
        if version < 1:
            reason = f"version must be 1 or greater, got {version}"
            raise errors.ViewDeclarationError(name, reason)

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
            handlers=handlers,
            outdated=_injected_outdated(cls),
        )
        registry.global_registry().register(meta)
        registry.stamp(cls, meta)
        return cls

    return decorate
