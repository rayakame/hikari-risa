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
"""Exceptions raised by risa.

The hierarchy is rooted at `RisaError`, so downstream code can catch everything
this library raises with a single `except` clause.
"""

from __future__ import annotations

__all__ = (
    "CustomIdOverflowError",
    "LayoutError",
    "LockTimeoutError",
    "RisaError",
    "SchemaMismatchError",
    "SerializationError",
    "StateConflictError",
    "StateError",
    "StateNotFoundError",
)

# The Discord-imposed hard limit on the length of a component's ``custom_id``.
MAX_CUSTOM_ID_LENGTH = 100


class RisaError(Exception):
    """Base class every exception raised by risa inherits from."""


class LayoutError(RisaError):
    """Raised when a component tree violates Discord's nesting rules.

    Raised during the build pass, before anything is sent to Discord, so that an
    invalid layout surfaces as a Python traceback rather than an opaque HTTP 400.

    Attributes
    ----------
    path
        Human-readable position of the offending node within the tree, for
        example ``"Container[0] > Section[2]"``.
    reason
        Description of the rule that was violated.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class CustomIdOverflowError(RisaError):
    """Raised when an encoded ``custom_id`` would exceed Discord's length limit.

    Attributes
    ----------
    view_name
        Name of the view whose component could not be encoded.
    length
        The length the encoded ``custom_id`` would have had.
    """

    def __init__(self, view_name: str, length: int) -> None:
        self.view_name = view_name
        self.length = length
        super().__init__(
            f"custom_id for view {view_name!r} is {length} characters, "
            f"which exceeds the Discord limit of {MAX_CUSTOM_ID_LENGTH}",
        )


class SerializationError(RisaError):
    """Base class for failures encoding or decoding persisted state."""


class SchemaMismatchError(SerializationError):
    """Raised when stored state does not match the current view schema.

    This is the expected outcome when a view's schema version is bumped and an
    interaction arrives from a component that was rendered by an older version.

    Attributes
    ----------
    view_name
        Name of the view the state belongs to.
    found_version
        Schema version recorded on the stored state.
    expected_version
        Schema version the currently-registered view declares.
    """

    def __init__(self, view_name: str, found_version: int, expected_version: int) -> None:
        self.view_name = view_name
        self.found_version = found_version
        self.expected_version = expected_version
        super().__init__(
            f"state for view {view_name!r} was written by schema version {found_version}, "
            f"but the registered view declares version {expected_version}",
        )


class StateError(RisaError):
    """Base class for failures reading or writing view state in a store."""


class StateNotFoundError(StateError):
    """Raised when a state key is absent from the store.

    Usually means the entry expired, was evicted, or the store was flushed.

    Attributes
    ----------
    key
        The state key that could not be found.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"no state found for key {key!r}; it may have expired or been evicted")


class StateConflictError(StateError):
    """Raised when a state write loses an optimistic-concurrency check.

    Indicates that another handler mutated the same view between this handler's
    read and its write. Retrying is unsafe in general, because the handler may
    already have sent a response.

    Attributes
    ----------
    key
        The state key that was being written.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"state for key {key!r} was modified concurrently; the write was rejected")


class LockTimeoutError(StateError):
    """Raised when a state lock could not be acquired in time.

    Attributes
    ----------
    key
        The state key whose lock was contended.
    timeout
        How long, in seconds, acquisition was attempted for.
    """

    def __init__(self, key: str, timeout: float) -> None:
        self.key = key
        self.timeout = timeout
        super().__init__(f"timed out after {timeout}s waiting for the lock on state key {key!r}")
