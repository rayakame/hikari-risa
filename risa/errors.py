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

from risa.internal import constants

__all__ = (
    "AlreadyRespondedError",
    "ArgBindError",
    "CustomIdOverflowError",
    "DuplicateHandlerError",
    "DuplicateViewError",
    "EphemeralOriginError",
    "HandlerNotCallableError",
    "HandlerSignatureError",
    "LayoutError",
    "LockTimeoutError",
    "NotAHandlerError",
    "NotAViewError",
    "RisaError",
    "SchemaMismatchError",
    "SerializationError",
    "SignatureMismatchError",
    "StateConflictError",
    "StateError",
    "StateNotFoundError",
    "ViewDeclarationError",
)


class RisaError(Exception): ...


class HandlerSignatureError(RisaError):
    def __init__(self, callback_name: str, parameter: str, reason: str) -> None:
        self.callback_name = callback_name
        self.parameter = parameter
        self.reason = reason
        super().__init__(f"handler {callback_name!r}: parameter {parameter!r} {reason}")


class ArgBindError(RisaError):
    def __init__(self, callback_name: str, parameter: str | None, reason: str) -> None:
        self.callback_name = callback_name
        self.parameter = parameter
        self.reason = reason
        if parameter is None:
            super().__init__(f"handler {callback_name!r}: {reason}")
        else:
            super().__init__(f"handler {callback_name!r}: parameter {parameter!r} {reason}")


class SignatureMismatchError(RisaError):
    def __init__(self, view_name: str, handler_id: str, version: int, *, found: str, expected: str) -> None:
        self.view_name = view_name
        self.handler_id = handler_id
        self.version = version
        self.found = found
        self.expected = expected
        if not expected:
            detail = "a component carries wire arguments, but the handler declares no wire parameters"
        elif not found:
            detail = "a component carries no wire fingerprint, but the handler declares wire parameters"
        else:
            detail = f"a component carries wire fingerprint {found!r}, but the handler's current chain is {expected!r}"
        super().__init__(
            f"handler {handler_id!r} (version {version}) of view {view_name!r}: {detail};"
            " the signature changed in place - bump the handler version to retire the old components",
        )


class HandlerNotCallableError(RisaError):
    def __init__(self, callback_name: str) -> None:
        self.callback_name = callback_name
        super().__init__(
            f"handler {callback_name!r} is dispatched by risa, not called directly;"
            " place it on a component with risa.bind(...), or delegate through the view"
            " class's descriptor (Type.handler.func)",
        )


class EphemeralOriginError(RisaError):
    def __init__(self, view_name: str) -> None:
        self.view_name = view_name
        super().__init__(
            f"the origin message of view {view_name!r} is ephemeral and can no longer be edited:"
            " on ephemeral views, rerender() or edit() must be the initial response,"
            " or follow a silent defer",
        )


class ViewDeclarationError(RisaError):
    def __init__(self, view_name: str, reason: str) -> None:
        self.view_name = view_name
        self.reason = reason
        super().__init__(f"view {view_name!r}: {reason}")


class DuplicateViewError(RisaError):
    def __init__(self, view_name: str, existing_name: str, key: str) -> None:
        self.view_name = view_name
        self.existing_name = existing_name
        self.key = key
        super().__init__(f"view {view_name!r} collides with {existing_name!r}: both are registered under {key!r}")


class DuplicateHandlerError(RisaError):
    def __init__(
        self,
        view_name: str,
        token: str,
        *,
        first_id: str,
        first_version: int,
        second_id: str,
        second_version: int,
    ) -> None:
        self.view_name = view_name
        self.token = token
        self.first_id = first_id
        self.first_version = first_version
        self.second_id = second_id
        self.second_version = second_version
        super().__init__(
            f"view {view_name!r}: handlers {first_id!r} (version {first_version}) and "
            f"{second_id!r} (version {second_version}) both route under token {token!r}",
        )


class AlreadyRespondedError(RisaError):
    def __init__(self, attempted: str, already_sent: str) -> None:
        self.attempted = attempted
        self.already_sent = already_sent
        super().__init__(
            f"cannot {attempted}: this interaction already received its initial response ({already_sent})",
        )


class NotAHandlerError(RisaError):
    def __init__(self, type_name: str) -> None:
        self.type_name = type_name
        super().__init__(
            f"{type_name} has no handler identity to route under; pass a handler method"
            " accessed on the view instance, bare or through risa.bind(...)",
        )


class NotAViewError(RisaError):
    def __init__(self, type_name: str) -> None:
        self.type_name = type_name
        super().__init__(f"{type_name} is not a registered view; decorate it with @risa.register")


class LayoutError(RisaError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class CustomIdOverflowError(RisaError):
    def __init__(self, view_name: str, length: int) -> None:
        self.view_name = view_name
        self.length = length
        super().__init__(
            f"custom_id for view {view_name!r} is {length} characters, "
            f"which exceeds the Discord limit of {constants.MAX_CUSTOM_ID_LENGTH}",
        )


class SerializationError(RisaError): ...


class SchemaMismatchError(SerializationError):
    def __init__(self, view_name: str, found_version: int, expected_version: int) -> None:
        self.view_name = view_name
        self.found_version = found_version
        self.expected_version = expected_version
        super().__init__(
            f"state for view {view_name!r} was written by schema version {found_version}, "
            f"but the registered view declares version {expected_version}",
        )


class StateError(RisaError): ...


class StateNotFoundError(StateError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"no state found for key {key!r}; it may have expired or been evicted")


class StateConflictError(StateError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"state for key {key!r} was modified concurrently; the write was rejected")


class LockTimeoutError(StateError):
    def __init__(self, key: str, timeout: float) -> None:
        self.key = key
        self.timeout = timeout
        super().__init__(f"timed out after {timeout}s waiting for the lock on state key {key!r}")
