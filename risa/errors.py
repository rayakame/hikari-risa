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
"""The exceptions risa raises.

Every exception derives from `RisaError`, builds its own message, and keeps the
details it was raised with as attributes, so callers can branch on fields instead
of parsing the message.
"""

from __future__ import annotations

import typing

from risa.internal import constants

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "AlreadyRespondedError",
    "ArgBindError",
    "CodecUnavailableError",
    "CustomIdOverflowError",
    "DuplicateHandlerError",
    "DuplicateViewError",
    "EphemeralOriginError",
    "HandlerNotCallableError",
    "HandlerSignatureError",
    "LayoutError",
    "LockTimeoutError",
    "NoViewError",
    "NotAHandlerError",
    "NotAViewError",
    "RisaError",
    "SerializationError",
    "SignatureMismatchError",
    "StateConflictError",
    "StateEncodeError",
    "StateError",
    "StateNotFoundError",
    "StateOverflowError",
    "StoreConformanceError",
    "StoreUnavailableError",
    "ViewDeclarationError",
)


class RisaError(Exception):
    """Base class of every exception risa raises."""


class HandlerSignatureError(RisaError):
    """Raised at registration when a handler's parameters cannot be routed.

    Attributes
    ----------
    callback_name
        The qualified name of the handler.
    parameter
        The parameter at fault.
    reason
        What is wrong with it.
    """

    def __init__(self, callback_name: str, parameter: str, reason: str) -> None:
        self.callback_name: str = callback_name
        self.parameter: str = parameter
        self.reason: str = reason
        super().__init__(f"handler {callback_name!r}: parameter {parameter!r} {reason}")


class ArgBindError(RisaError):
    """Raised when `risa.bind` gets arguments the handler cannot carry on the wire.

    Attributes
    ----------
    callback_name
        The qualified name of the handler.
    parameter
        The parameter at fault, or `None` when the arguments as a whole do not fit.
    reason
        What is wrong with them.
    """

    def __init__(self, callback_name: str, parameter: str | None, reason: str) -> None:
        self.callback_name: str = callback_name
        self.parameter: str | None = parameter
        self.reason: str = reason
        if parameter is None:
            super().__init__(f"handler {callback_name!r}: {reason}")
        else:
            super().__init__(f"handler {callback_name!r}: parameter {parameter!r} {reason}")


class SignatureMismatchError(RisaError):
    """Logged, not raised, when a click carries wire arguments for another handler signature.

    The handler's wire parameters changed without a version bump. risa logs this error
    and routes the click to `View.on_outdated`. Bump the version in
    `@risa.handler(version=...)` so the old components are retired instead.

    Attributes
    ----------
    view_name
        The view the handler belongs to.
    handler_id
        The handler's id.
    version
        The handler's version.
    found
        The fingerprint the component carries.
    expected
        The fingerprint of the handler's current signature.
    """

    def __init__(self, view_name: str, handler_id: str, version: int, *, found: str, expected: str) -> None:
        self.view_name: str = view_name
        self.handler_id: str = handler_id
        self.version: int = version
        self.found: str = found
        self.expected: str = expected
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
    """Raised when a handler is called directly instead of being dispatched by risa.

    Attributes
    ----------
    callback_name
        The qualified name of the handler.
    """

    def __init__(self, callback_name: str) -> None:
        self.callback_name: str = callback_name
        super().__init__(
            f"handler {callback_name!r} is dispatched by risa, not called directly;"
            " place it on a component with risa.bind(...), or delegate through the view"
            " class's descriptor (Type.handler.func)",
        )


class EphemeralOriginError(RisaError):
    """Raised when an ephemeral origin message can no longer be edited.

    On an ephemeral view, `rerender()` and `edit()` must be the initial response, or
    follow a silent `defer()`.

    Attributes
    ----------
    view_name
        The view whose message could not be edited.
    """

    def __init__(self, view_name: str) -> None:
        self.view_name: str = view_name
        super().__init__(
            f"the origin message of view {view_name!r} is ephemeral and can no longer be edited:"
            " on ephemeral views, rerender() or edit() must be the initial response,"
            " or follow a silent defer",
        )


class StateOverflowError(RisaError):
    """Raised when an `InMessage` view's durable state does not fit its components.

    Attributes
    ----------
    view_name
        The view whose state overflowed.
    needed
        The bytes of state to write.
    available
        The bytes the rendered components can carry.
    slots
        The number of interactive components.
    codec_name
        The codec the state was written with.
    fields
        The bytes each durable field takes, by name, in declaration order. What they leave
        of `needed` is risa's own framing.
    """

    def __init__(
        self,
        view_name: str,
        *,
        needed: int,
        available: int,
        slots: int,
        codec_name: str,
        fields: collections.abc.Mapping[str, int] | None = None,
    ) -> None:
        self.view_name: str = view_name
        self.needed: int = needed
        self.available: int = available
        self.slots: int = slots
        self.codec_name: str = codec_name
        self.fields: collections.abc.Mapping[str, int] = dict(fields or {})
        components = "component carries" if slots == 1 else "components carry"
        head = (
            f"view {view_name!r} needs {needed} bytes of durable state, but its {slots} interactive"
            f" {components} only {available} with codec {codec_name!r}"
        )
        remedy = (
            "Make room by marking per-dispatch fields risa.Prop[...], adding components, shortening"
            " fields, using a denser codec (state=risa.InMessage(codec=risa.codecs.BASE32768) with"
            " hikari-risa[radixly]), or moving the view to state=risa.InStore(...)."
        )
        if not self.fields:
            super().__init__(f"{head}.\n{remedy}")
            return
        rows = sorted(self.fields.items(), key=lambda row: -row[1])
        rows.append(("framing", needed - sum(self.fields.values())))
        name_width = max(len(name) for name, _ in rows)
        size_width = max(len(str(size)) for _, size in rows)
        breakdown = "\n".join(
            f"    {name:<{name_width}}  {size:>{size_width}} {'byte' if size == 1 else 'bytes'}" for name, size in rows
        )
        super().__init__(f"{head}:\n{breakdown}\n{remedy}")


class CodecUnavailableError(RisaError):
    """Raised when a codec needs an optional dependency that is not installed.

    Attributes
    ----------
    codec_name
        The codec that is unavailable.
    extra
        The `hikari-risa` extra that installs it.
    """

    def __init__(self, codec_name: str, extra: str) -> None:
        self.codec_name: str = codec_name
        self.extra: str = extra
        super().__init__(
            f"codec {codec_name!r} needs the optional dependency {extra!r}, which is not installed;"
            f" install it with: pip install hikari-risa[{extra}]",
        )


class ViewDeclarationError(RisaError):
    """Raised when a view is declared or added in a way risa cannot serve.

    Attributes
    ----------
    view_name
        The view at fault.
    reason
        What is wrong with it.
    """

    def __init__(self, view_name: str, reason: str) -> None:
        self.view_name: str = view_name
        self.reason: str = reason
        super().__init__(f"view {view_name!r}: {reason}")


class DuplicateViewError(RisaError):
    """Raised when two views would route under the same cookie.

    Attributes
    ----------
    view_name
        The view being registered.
    existing_name
        The view already registered under that cookie.
    cookie
        The cookie both views hash to.
    """

    def __init__(self, view_name: str, existing_name: str, cookie: str) -> None:
        self.view_name: str = view_name
        self.existing_name: str = existing_name
        self.cookie: str = cookie
        super().__init__(f"view {view_name!r} collides with {existing_name!r}: both are registered under {cookie!r}")


class DuplicateHandlerError(RisaError):
    """Raised when two handlers of one view would route under the same token.

    Attributes
    ----------
    view_name
        The view both handlers belong to.
    token
        The token both handlers hash to.
    first_id
        The id of the first handler.
    first_version
        The version of the first handler.
    second_id
        The id of the second handler.
    second_version
        The version of the second handler.
    """

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
        self.view_name: str = view_name
        self.token: str = token
        self.first_id: str = first_id
        self.first_version: int = first_version
        self.second_id: str = second_id
        self.second_version: int = second_version
        super().__init__(
            f"view {view_name!r}: handlers {first_id!r} (version {first_version}) and "
            f"{second_id!r} (version {second_version}) both route under token {token!r}",
        )


class AlreadyRespondedError(RisaError):
    """Raised when an interaction that already has its initial response is deferred.

    Attributes
    ----------
    attempted
        What was attempted.
    already_sent
        The initial response the interaction already received.
    """

    def __init__(self, attempted: str, already_sent: str) -> None:
        self.attempted: str = attempted
        self.already_sent: str = already_sent
        super().__init__(
            f"cannot {attempted}: this interaction already received its initial response ({already_sent})",
        )


class NotAHandlerError(RisaError):
    """Raised when a component is given something that is not a risa handler.

    Attributes
    ----------
    type_name
        The type of the object that was given.
    """

    def __init__(self, type_name: str) -> None:
        self.type_name: str = type_name
        super().__init__(
            f"{type_name} has no handler identity to route under; pass a handler method"
            " accessed on the view instance, bare or through risa.bind(...)",
        )


class NotAViewError(RisaError):
    """Raised when a class or instance that was never `@risa.register`-ed is used as a view.

    Attributes
    ----------
    type_name
        The type that was given.
    """

    def __init__(self, type_name: str) -> None:
        self.type_name: str = type_name
        super().__init__(f"{type_name} is not a registered view; decorate it with @risa.register")


class NoViewError(RisaError):
    """Raised when an operation needs the view's state, but the context has none.

    That is the case in `on_outdated` and `on_state_missing`, and in a context kept past
    the end of its dispatch.

    Attributes
    ----------
    view_name
        The view the context belongs to.
    attempted
        The operation that needed the state.
    """

    def __init__(self, view_name: str, attempted: str) -> None:
        self.view_name: str = view_name
        self.attempted: str = attempted
        super().__init__(
            f"{attempted} on view {view_name!r} needs the view's state, but this context has none - it"
            " belongs to on_outdated or on_state_missing, or to a dispatch that has already ended;"
            " answer with ctx.edit(layout) using a tree without interactive components, or with"
            " ctx.respond(...)",
        )


class LayoutError(RisaError):
    """Raised when a layout cannot be built.

    Attributes
    ----------
    path
        Where in the tree the problem is, such as `Container > Row[1] > Button[0]`.
    reason
        What is wrong there.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path: str = path
        self.reason: str = reason
        super().__init__(f"{path}: {reason}")


class CustomIdOverflowError(RisaError):
    """Raised when a component's `custom_id` would exceed Discord's limit.

    Attributes
    ----------
    view_name
        The view the component belongs to.
    length
        The length the `custom_id` would have.
    """

    def __init__(self, view_name: str, length: int) -> None:
        self.view_name: str = view_name
        self.length: int = length
        super().__init__(
            f"custom_id for view {view_name!r} is {length} characters, "
            f"which exceeds the Discord limit of {constants.MAX_CUSTOM_ID_LENGTH}",
        )


class SerializationError(RisaError):
    """Base class of the errors raised while writing or reading view state."""


class StateEncodeError(SerializationError):
    """Raised when a durable field holds a value that cannot be serialized.

    Attributes
    ----------
    view_name
        The view the field belongs to.
    field
        The field at fault.
    reason
        Why its value cannot be written.
    """

    def __init__(self, view_name: str, field: str, reason: str) -> None:
        self.view_name: str = view_name
        self.field: str = field
        self.reason: str = reason
        super().__init__(
            f"view {view_name!r}: durable field {field!r} cannot be written to state: {reason};"
            " store a value msgpack can carry, or mark the field risa.Prop[...] if it should not persist",
        )


class StateError(RisaError):
    """Base class of the errors raised while working with `InStore` state."""


class StateNotFoundError(StateError):
    """Raised when a view's stored state is gone in the middle of a dispatch.

    Attributes
    ----------
    key
        The store key of the state.
    """

    def __init__(self, key: str) -> None:
        self.key: str = key
        super().__init__(f"no state found for key {key!r}; it may have expired or been evicted")


class StateConflictError(StateError):
    """Raised when another process wrote a view's state first.

    By the time it is raised, the view already holds the state that won, so the handler
    can apply its change again on top of it. The exception is a winning record that cannot
    be read: risa logs that as an `ERROR` and the view keeps its own state.

    Attributes
    ----------
    key
        The store key of the state.
    expected
        The version this dispatch read.
    found
        The version the store holds.
    """

    def __init__(self, key: str, *, expected: int, found: int) -> None:
        self.key: str = key
        self.expected: int = expected
        self.found: int = found
        super().__init__(
            f"state for key {key!r} was written concurrently: this handler read version {expected},"
            f" the store holds version {found}; the write was rejected",
        )


class LockTimeoutError(StateError):
    """Raised when the lock on a view's state is not granted in time.

    Attributes
    ----------
    key
        The store key of the state.
    timeout
        How long risa waited, in seconds.
    """

    def __init__(self, key: str, timeout: float) -> None:
        self.key: str = key
        self.timeout: float = timeout
        super().__init__(f"timed out after {timeout}s waiting for the lock on state key {key!r}")


class StoreUnavailableError(StateError):
    """Raised when the store holding a view's state cannot be reached.

    The state is not lost; it only cannot be read or written right now. A store reports
    missing state by returning nothing, never with this error.

    Attributes
    ----------
    key
        The store key of the state.
    reason
        What went wrong.
    """

    def __init__(self, key: str, reason: str) -> None:
        self.key: str = key
        self.reason: str = reason
        super().__init__(
            f"the store backing key {key!r} could not be reached: {reason};"
            " the state is not lost - the interaction cannot be answered from it right now",
        )


class StoreConformanceError(RisaError):
    """Raised by `risa.store.testing.verify_store` when a store fails any check.

    Attributes
    ----------
    failures
        One line per failed check, naming the check and what went wrong.
    """

    def __init__(self, failures: collections.abc.Sequence[str]) -> None:
        self.failures: collections.abc.Sequence[str] = failures
        listed = "".join(f"\n- {failure}" for failure in failures)
        checks = "check" if len(failures) == 1 else "checks"
        super().__init__(f"the store failed {len(failures)} conformance {checks}:{listed}")
