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
import typing

import msgspec

from risa import errors
from risa.internal import codec

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import view as view_

__all__ = (
    "BindTarget",
    "Binding",
    "HandlerBinder",
    "Handlerish",
    "bind",
    "resolve",
)

type BindTarget[**P] = collections.abc.Callable[P, collections.abc.Awaitable[None]]

type Handlerish = (
    collections.abc.Callable[..., collections.abc.Awaitable[None]]
    | Binding
    | functools.partial[collections.abc.Awaitable[None]]
)

bind: typing.Final = functools.partial


class Binding(msgspec.Struct, frozen=True):
    handler_id: str
    version: int
    token: str
    payload: str
    owner: type[view_.View] | None = None


class HandlerBinder:
    __slots__ = ("_func_name", "_handler_id", "_owner", "_signature", "_token", "_version")

    def __init__(
        self,
        *,
        handler_id: str,
        version: int,
        token: str,
        signature: codec.HandlerSignature,
        func_name: str,
        owner: type[view_.View] | None,
    ) -> None:
        self._handler_id = handler_id
        self._version = version
        self._token = token
        self._signature = signature
        self._func_name = func_name
        self._owner = owner

    def __call__(self, *_args: object, **_kwargs: object) -> typing.NoReturn:
        raise errors.HandlerNotCallableError(self._func_name)

    def bind(self, *args: object, **kwargs: object) -> Binding:
        names = list(self._signature.converters)
        if len(args) > len(names):
            raise errors.ArgBindError(
                self._func_name,
                None,
                f"received {len(args)} wire arguments, but the handler declares {len(names)}",
            )
        filled: dict[str, object] = dict(zip(names, args, strict=False))
        for name, value in kwargs.items():
            if name not in self._signature.converters:
                raise errors.ArgBindError(self._func_name, name, "is not a wire parameter of this handler")
            if name in filled:
                raise errors.ArgBindError(self._func_name, name, "was supplied both positionally and by keyword")
            filled[name] = value

        k = len(names)
        for index, name in enumerate(names):
            if name not in filled:
                k = index
                break

        if any(name in filled for name in names[k:]):
            raise errors.ArgBindError(self._func_name, names[k], "was not supplied, but a later wire parameter was")
        if k < self._signature.required:
            raise errors.ArgBindError(self._func_name, names[k], "is required but was not supplied")

        parts = self._encode_frames(names[:k], filled)
        payload = self._signature.fingerprint + codec.pack_frames(parts) if names else ""
        return Binding(
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
                raise errors.ArgBindError(self._func_name, name, str(exc)) from exc
            if len(encoded) > codec.MAX_FRAME_LENGTH:
                reason = (
                    f"encodes to {len(encoded)} characters, "
                    f"which exceeds the {codec.MAX_FRAME_LENGTH}-character frame limit"
                )
                raise errors.ArgBindError(self._func_name, name, reason)
            parts.append(encoded)
        return parts


def resolve(handler: Handlerish) -> Binding:
    if isinstance(handler, Binding):
        return handler
    if isinstance(handler, functools.partial):
        inner = handler.func
        if not isinstance(inner, HandlerBinder):
            raise errors.NotAHandlerError(type(inner).__name__)
        return inner.bind(*handler.args, **handler.keywords)
    if isinstance(handler, HandlerBinder):
        return handler.bind()
    raise errors.NotAHandlerError(type(handler).__name__)
