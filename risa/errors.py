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
"""Exceptions raised by risa.

The hierarchy is rooted at `RisaError`, so downstream code can catch everything
this library raises with a single `except` clause.
"""

from __future__ import annotations

from risa.internal.constants import MAX_CUSTOM_ID_LENGTH

__all__ = (
    "AlreadyRespondedError",
    "ArgBindError",
    "CustomIdOverflowError",
    "DuplicateHandlerError",
    "DuplicateViewError",
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
    "StateDecodeError",
    "StateError",
    "StateNotFoundError",
    "StateOverflowError",
    "StoreUnavailableError",
    "ViewDeclarationError",
)


class RisaError(Exception):
    """Base class every exception raised by risa inherits from."""


class DuplicateHandlerError(RisaError):
    """Raised when two handlers in one view collide on the same token.

    Either both were pinned to the same ``handler_id`` and version, or two
    distinct identities hashed into the same token. Raised while the view is
    being declared, so the clash surfaces at import rather than as a silently
    dropped callback.

    Attributes
    ----------
    view_name
        Name of the view the handlers belong to.
    handler_id
        Id of the handler being collected.
    version
        Version of the handler being collected.
    existing_id
        Id of the handler already holding the token.
    existing_version
        Version of the handler already holding the token.
    token
        The token both handlers produced.
    """

    def __init__(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]
        self,
        view_name: str,
        handler_id: str,
        version: int,
        existing_id: str,
        existing_version: int,
        token: str,
    ) -> None:
        self.view_name = view_name
        self.handler_id = handler_id
        self.version = version
        self.existing_id = existing_id
        self.existing_version = existing_version
        self.token = token
        super().__init__(
            f"handlers {handler_id!r} (version {version}) and {existing_id!r} (version {existing_version})"
            f" on view {view_name!r} both map to token {token!r}; give them distinct ids or versions",
        )


class DuplicateViewError(RisaError):
    """Raised when two different views register under the same cookie.

    Because a cookie is a hash of the view name and schema version, this means
    two views share a name and version. Raised at registration time so the clash
    surfaces on startup rather than as mis-routed interactions later.

    Attributes
    ----------
    name
        Name of the view being registered.
    existing_name
        Name of the view already registered under the cookie.
    cookie
        The cookie both views produced.
    """

    def __init__(self, name: str, existing_name: str, cookie: str) -> None:
        self.name = name
        self.existing_name = existing_name
        self.cookie = cookie
        super().__init__(
            f"view {name!r} collides with already-registered view {existing_name!r} "
            f"on cookie {cookie!r}; give them distinct names or versions",
        )


class NotAHandlerError(RisaError):
    """Raised when a component is given a callback that is not a handler.

    A component routes by the durable id ``@risa.handler`` stamps onto a method,
    so an undecorated callback has nothing to route under. Raised while the tree
    is being built, which points at the component rather than at the click that
    would otherwise go nowhere.

    Attributes
    ----------
    callback_name
        Name of the callback that was passed.
    """

    def __init__(self, callback_name: str) -> None:
        self.callback_name = callback_name
        super().__init__(f"{callback_name!r} is not a handler; decorate it with @risa.handler")


class NotAViewError(RisaError):
    """Raised when a class is added to a client without having been registered.

    Subclassing ``risa.View`` declares the metadata attribute but leaves it
    unset; only ``@risa.register`` fills it in. A class that skipped the
    decorator would otherwise be added silently and route nothing.

    Attributes
    ----------
    class_name
        Name of the class that was passed.
    """

    def __init__(self, class_name: str) -> None:
        self.class_name = class_name
        super().__init__(f"{class_name!r} is missing view metadata; decorate it with @risa.register")


class AlreadyRespondedError(RisaError):
    """Raised when an action needed to be the initial response but came too late.

    A defer or a modal can only ever be the first response to an interaction.
    Anything sent earlier -- by the handler or by the auto-defer watchdog --
    forecloses them, and this error names what got there first. Handlers that
    open modals should disable auto-defer for exactly this reason.

    Attributes
    ----------
    attempted
        The action that arrived too late, for example ``"defer"``.
    """

    def __init__(self, attempted: str) -> None:
        self.attempted = attempted
        super().__init__(f"cannot {attempted}: an initial response was already issued for this interaction")


class ViewDeclarationError(RisaError):
    """Raised when a view's registration options contradict its shape.

    A prop without a default, a ``risa.Prop`` marker buried where it would be
    ignored, a frozen view, a view with nothing durable asking for a store, a
    hook that could never fire where the view's state lives, or a store named
    that the client was never given. Raised while the view is being declared,
    so the contradiction surfaces at import rather than at build or click
    time.

    Attributes
    ----------
    view_name
        Name of the view being registered.
    reason
        Description of the contradiction.
    """

    def __init__(self, view_name: str, reason: str) -> None:
        self.view_name = view_name
        self.reason = reason
        super().__init__(f"view {view_name!r} cannot be registered: {reason}")


class HandlerSignatureError(RisaError):
    """Raised when a handler's signature cannot be used for wire arguments.

    Wire parameters must form a contiguous block directly after ``ctx``: the
    first parameter with no converter for its annotation ends the block, and
    everything after it belongs to dependency injection. A converter-typed
    parameter appearing later would silently become a dependency lookup for a
    primitive, so it is rejected while the view is being declared instead.

    Attributes
    ----------
    handler_id
        Id of the handler whose signature was rejected.
    reason
        Description of what makes the signature unusable.
    """

    def __init__(self, handler_id: str, reason: str) -> None:
        self.handler_id = handler_id
        self.reason = reason
        super().__init__(f"handler {handler_id!r} has an unusable signature: {reason}")


class ArgBindError(RisaError):
    """Raised when ``bind()`` is given arguments that do not fit the handler.

    Raised eagerly, while the tree is being rendered, so a bad binding fails at
    the ``render()`` call site rather than when the component is clicked.

    Attributes
    ----------
    handler_id
        Id of the handler the arguments were bound for.
    reason
        Description of what does not fit.
    """

    def __init__(self, handler_id: str, reason: str) -> None:
        self.handler_id = handler_id
        self.reason = reason
        super().__init__(f"cannot bind arguments for handler {handler_id!r}: {reason}")


class SignatureMismatchError(RisaError):
    """Raised when a component's wire arguments no longer fit its handler.

    Means the handler's signature was edited in place: the component was
    encoded against a converter chain the handler no longer has, and its
    arguments cannot be trusted to decode into the current parameters. The
    remedy is to declare the old signature under the old version and move the
    new one to a bumped version.

    Caught during dispatch rather than propagated: the interaction is routed to
    ``on_outdated`` and the mismatch is logged as an error.

    Attributes
    ----------
    view_name
        Name of the view the handler belongs to.
    handler_id
        Id of the handler the component routes to.
    version
        Version of the handler the component routes to.
    reason
        Description of the mismatch.
    """

    def __init__(self, view_name: str, handler_id: str, version: int, reason: str) -> None:
        self.view_name = view_name
        self.handler_id = handler_id
        self.version = version
        self.reason = reason
        super().__init__(
            f"component args for handler {handler_id!r} (version {version}) on view {view_name!r}"
            f" were encoded against a different signature: {reason}",
        )


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


class StateOverflowError(RisaError):
    """Raised when a view's state does not fit the components it renders.

    Message-resident state is carried in the spare ``custom_id`` characters of
    the interactive components the view rendered, so capacity is a property of
    the tree rather than a fixed number: a render with fewer buttons carries
    less. Raised at build and at every re-render, before anything is sent, so
    that an oversized view fails as a Python traceback rather than as a message
    whose state silently could not be saved.

    The remedies are to mark bulky fields ``risa.Prop`` so they are refilled
    per dispatch instead of persisted, or to move the view to
    ``state=risa.InStore(...)`` where size is unbounded.

    Attributes
    ----------
    view_name
        Name of the view whose state could not be carried.
    required
        Characters the encoded state needs.
    capacity
        Characters the rendered tree has room for.
    """

    def __init__(self, view_name: str, required: int, capacity: int) -> None:
        self.view_name = view_name
        self.required = required
        self.capacity = capacity
        super().__init__(
            f"state for view {view_name!r} needs {required} characters but the rendered components"
            f" have room for {capacity}; mark bulky fields risa.Prop, or register the view with"
            f" state=risa.InStore(...)",
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


class StateDecodeError(SerializationError):
    """Raised when stored state cannot be read back into its view.

    Usually means a view's fields were changed without bumping its schema
    version, so state written by the old shape no longer fits the new one.

    Attributes
    ----------
    view_name
        Name of the view the state belongs to.
    reason
        What the decoder objected to.
    """

    def __init__(self, view_name: str, reason: str) -> None:
        self.view_name = view_name
        self.reason = reason
        super().__init__(f"could not read stored state for view {view_name!r}: {reason}")


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


class StoreUnavailableError(StateError):
    """Raised when a store cannot be reached at all.

    Deliberately distinct from :class:`StateNotFoundError`, which means the
    record is genuinely gone. An outage is not an expiry, and a view told to
    say "this has expired" would be lying about a store that is merely down --
    so dispatch keeps the two apart and never routes an outage to
    ``on_state_missing``.

    Implementations should raise this rather than letting a backend-specific
    connection error escape, so that a view's behaviour does not depend on
    which store it was given.

    Attributes
    ----------
    key
        The key that could not be reached, when the failure concerned one.
    reason
        What the store reported.
    """

    def __init__(self, reason: str, key: str | None = None) -> None:
        self.key = key
        self.reason = reason
        where = f" while reading key {key!r}" if key is not None else ""
        super().__init__(f"the store could not be reached{where}: {reason}")


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
