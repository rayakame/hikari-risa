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

import functools
import inspect
import typing

import msgspec

from risa import errors
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry

if typing.TYPE_CHECKING:
    import collections.abc

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

type HandlerFunction[V: View, **P] = collections.abc.Callable[
    typing.Concatenate[V, context.ComponentContext, P],
    collections.abc.Awaitable[None],
]


class BoundHandler(msgspec.Struct, frozen=True):
    handler_id: str
    version: int
    token: str
    payload: str = ""


class BoundHandlerMethod:
    __slots__ = ("_call", "_handler_id", "_token", "_version")

    def __init__(
        self,
        call: collections.abc.Callable[..., collections.abc.Awaitable[None]],
        *,
        handler_id: str,
        version: int,
        token: str,
    ) -> None:
        self._call = call
        self._handler_id = handler_id
        self._version = version
        self._token = token

    def __call__(self, ctx: context.ComponentContext, /) -> collections.abc.Awaitable[None]:
        return self._call(ctx)

    def bind(self) -> BoundHandler:
        return BoundHandler(handler_id=self._handler_id, version=self._version, token=self._token)


@typing.runtime_checkable
class ZeroArgHandler(typing.Protocol):
    def bind(self) -> BoundHandler: ...


class HandlerMethod:
    __slots__ = ("_func", "_handler_id", "_token", "_version")

    def __init__(
        self,
        func: collections.abc.Callable[..., collections.abc.Awaitable[None]],
        *,
        handler_id: str,
        version: int,
    ) -> None:
        self._func = func
        self._handler_id = handler_id
        self._version = version
        self._token = codec.make_handler_token(handler_id, version)

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

    @typing.overload
    def __get__(self, instance: None, owner: type[View]) -> HandlerMethod: ...

    @typing.overload
    def __get__(self, instance: View, owner: type[View]) -> BoundHandlerMethod: ...

    def __get__(self, instance: View | None, owner: type[View]) -> HandlerMethod | BoundHandlerMethod:
        if instance is None:
            return self
        return BoundHandlerMethod(
            functools.partial(self._func, instance),
            handler_id=self._handler_id,
            version=self._version,
            token=self._token,
        )


class _HandlerDecorator(typing.Protocol):
    def __call__[V: View, **P](self, func: HandlerFunction[V, P], /) -> HandlerMethod: ...


@typing.overload
def handler[V: View, **P](func: HandlerFunction[V, P], /) -> HandlerMethod: ...


@typing.overload
def handler(*, handler_id: str | None = ..., version: int = ...) -> _HandlerDecorator: ...


def handler(
    func: collections.abc.Callable[..., collections.abc.Awaitable[None]] | None = None,
    /,
    *,
    handler_id: str | None = None,
    version: int = 1,
) -> HandlerMethod | _HandlerDecorator:
    def decorate(f: collections.abc.Callable[..., collections.abc.Awaitable[None]]) -> HandlerMethod:
        return HandlerMethod(f, handler_id=handler_id if handler_id is not None else f.__name__, version=version)

    return decorate(func) if func is not None else decorate


class View(msgspec.Struct):
    __risa_view_meta__: typing.ClassVar[registry.ViewMeta]

    def render(self) -> ui.Layout:
        raise NotImplementedError


def register[T: View](*, name: str, version: int = 1) -> typing.Callable[[type[T]], type[T]]:
    def decorate(cls: type[T]) -> type[T]:
        if not name.strip():
            reason = "a view needs a name to be routable, and it must not be blank"
            raise errors.ViewDeclarationError(cls.__name__, reason)
        if version < 1:
            reason = f"version must be 1 or greater, got {version}"
            raise errors.ViewDeclarationError(name, reason)

        handlers: dict[str, registry.HandlerRecord] = {}
        for _, method in inspect.getmembers(cls, lambda m: isinstance(m, HandlerMethod)):
            if (existing := handlers.get(method.token)) is not None:
                raise errors.DuplicateHandlerError(
                    name,
                    method.token,
                    first_id=existing.handler_id,
                    first_version=existing.version,
                    second_id=method.handler_id,
                    second_version=method.version,
                )
            handlers[method.token] = registry.HandlerRecord(
                callback=method.func,
                handler_id=method.handler_id,
                version=method.version,
            )

        meta = registry.ViewMeta(cls=cls, name=name, version=version, handlers=handlers)
        registry.global_registry().register(meta)
        setattr(cls, constants.VIEW_META, meta)
        return cls

    return decorate
