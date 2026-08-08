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
    "CustomIdOverflowError",
    "DuplicateHandlerError",
    "DuplicateViewError",
    "LayoutError",
    "LockTimeoutError",
    "NotAHandlerError",
    "NotAViewError",
    "RisaError",
    "SchemaMismatchError",
    "SerializationError",
    "StateConflictError",
    "StateError",
    "StateNotFoundError",
    "ViewDeclarationError",
)


class RisaError(Exception): ...


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


class NotAHandlerError(RisaError):
    def __init__(self, type_name: str) -> None:
        self.type_name = type_name
        super().__init__(
            f"{type_name} has no handler identity to route under; pass a handler method"
            f" accessed on the view instance, or the result of its bind()",
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
