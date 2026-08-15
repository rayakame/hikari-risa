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
import functools
import inspect
import typing

import linkd
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
    "AutoDefer",
    "BoundHandler",
    "BoundHandlerMethod",
    "HandlerFunction",
    "HandlerMethod",
    "View",
    "bind",
    "handler",
    "register",
)

type HandlerFunction[V: View, **P] = collections.abc.Callable[
    typing.Concatenate[V, context.ComponentContext, P],
    collections.abc.Awaitable[None],
]

bind: typing.Final = functools.partial


class AutoDefer(enum.StrEnum):
    OFF = enum.auto()
    UPDATE = enum.auto()
    THINKING = enum.auto()
    THINKING_EPHEMERAL = enum.auto()


class BoundHandler(msgspec.Struct, frozen=True):
    handler_id: str
    version: int
    token: str
    payload: str
    owner: type[View] | None = None


class BoundHandlerMethod:
    __slots__ = ("_callback_name", "_handler_id", "_owner", "_signature", "_token", "_version")

    def __init__(
        self,
        *,
        handler_id: str,
        version: int,
        token: str,
        signature: codec.HandlerSignature,
        callback_name: str,
        owner: type[View] | None,
    ) -> None:
        self._handler_id = handler_id
        self._version = version
        self._token = token
        self._signature = signature
        self._callback_name = callback_name
        self._owner = owner

    def __call__(self, *_args: object, **_kwargs: object) -> typing.NoReturn:
        raise errors.HandlerNotCallableError(self._callback_name)

    def bind(self, *args: object, **kwargs: object) -> BoundHandler:
        names = list(self._signature.converters)
        if len(args) > len(names):
            raise errors.ArgBindError(
                self._callback_name,
                None,
                f"received {len(args)} wire arguments, but the handler declares {len(names)}",
            )
        filled: dict[str, object] = dict(zip(names, args, strict=False))
        for name, value in kwargs.items():
            if name not in self._signature.converters:
                raise errors.ArgBindError(self._callback_name, name, "is not a wire parameter of this handler")
            if name in filled:
                raise errors.ArgBindError(self._callback_name, name, "was supplied both positionally and by keyword")
            filled[name] = value

        k = len(names)
        for index, name in enumerate(names):
            if name not in filled:
                k = index
                break

        if any(name in filled for name in names[k:]):
            raise errors.ArgBindError(self._callback_name, names[k], "was not supplied, but a later wire parameter was")
        if k < self._signature.required:
            raise errors.ArgBindError(self._callback_name, names[k], "is required but was not supplied")

        parts = self._encode_frames(names[:k], filled)
        payload = self._signature.fingerprint + codec.pack_frames(parts) if names else ""
        return BoundHandler(
            handler_id=self._handler_id,
            version=self._version,
            token=self._token,
            payload=payload,
            owner=self._owner,
        )

    def _encode_frames(self, names: collections.abc.Sequence[str], filled: dict[str, object]) -> list[str]:
        parts: list[str] = []
        for name in names:
            try:
                encoded = self._signature.converters[name].encode(filled[name])
            except (TypeError, ValueError) as exc:
                raise errors.ArgBindError(self._callback_name, name, str(exc)) from exc
            if len(encoded) > codec.MAX_FRAME_LENGTH:
                reason = (
                    f"encodes to {len(encoded)} characters, "
                    f"which exceeds the {codec.MAX_FRAME_LENGTH}-character frame limit"
                )
                raise errors.ArgBindError(self._callback_name, name, reason)
            parts.append(encoded)
        return parts


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
    def __get__(self, instance: V, owner: type[V]) -> collections.abc.Callable[P, collections.abc.Awaitable[None]]: ...

    def __get__(
        self, instance: V | None, owner: type[V]
    ) -> HandlerMethod[V, P] | collections.abc.Callable[P, collections.abc.Awaitable[None]]:
        if instance is None:
            return self
        return typing.cast(
            "collections.abc.Callable[P, collections.abc.Awaitable[None]]",
            BoundHandlerMethod(
                handler_id=self._handler_id,
                version=self._version,
                token=self._token,
                signature=self.signature,
                callback_name=self._func.__qualname__,
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
    __risa_view_meta__: typing.ClassVar[registry.ViewMeta]

    def render(self) -> ui.Layout:
        raise NotImplementedError

    @classmethod
    async def on_outdated(cls, ctx: context.ComponentContext) -> None:
        """Answer a click on a component this view can no longer route."""


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
                callback=typing.cast(
                    "collections.abc.Callable[..., collections.abc.Awaitable[None]]",
                    linkd.inject(member.func),
                ),
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
            handles_outdated=cls.on_outdated.__func__ is not View.on_outdated.__func__,
        )
        registry.global_registry().register(meta)
        setattr(cls, constants.VIEW_META, meta)
        return cls

    return decorate
