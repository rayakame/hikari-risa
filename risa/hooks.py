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
"""Hooks, the steps they run in, and the pipeline every click runs through."""

from __future__ import annotations

import enum
import inspect
import typing

import linkd
import msgspec

from risa import errors
from risa import view as view_

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import context

__all__ = (
    "DEFAULT_EXECUTION_STEP_ORDER",
    "AnyHook",
    "ErrorHandler",
    "ExecutionHook",
    "ExecutionPipeline",
    "ExecutionStep",
    "ExecutionSteps",
    "Failure",
    "FailureStage",
    "HookFunction",
    "ViewHookFunction",
    "hook",
)

type AnyHook = ExecutionHook[typing.Never]
"""Any hook, whichever view it takes."""

type HookFunction[**P] = collections.abc.Callable[
    typing.Concatenate[ExecutionPipeline, context.ComponentContext, P],
    collections.abc.Awaitable[None],
]
"""A hook function for any view: it takes the pipeline, a `ComponentContext`, then anything to inject."""

type ViewHookFunction[V: view_.View, **P] = collections.abc.Callable[
    typing.Concatenate[ExecutionPipeline, context.ComponentContext, V, P],
    collections.abc.Awaitable[None],
]
"""A hook function for one view class: the pipeline, a `ComponentContext`, the view, then anything to inject."""

type ErrorHandler = collections.abc.Callable[
    typing.Concatenate[ExecutionPipeline, context.ComponentContext, ...],
    collections.abc.Awaitable[bool],
]
"""A function `Client.error_handler` registers: it takes the pipeline, a `ComponentContext`, then anything to inject."""


class _HookDecorator(typing.Protocol):
    @typing.overload
    def __call__[V: view_.View, **P](self, func: ViewHookFunction[V, P], /) -> ExecutionHook[V]: ...

    @typing.overload
    def __call__[**P](self, func: HookFunction[P], /) -> ExecutionHook[view_.View]: ...


class FailureStage(enum.StrEnum):
    """Where a click failed, as `Failure.stage` names it.

    Attributes
    ----------
    HANDLER
        The handler raised.
    LOAD
        The view's `load` raised, so the handler never ran.
    HOOK
        A hook raised; `Failure.hook` says which.
    ON_OUTDATED
        `View.on_outdated` raised.
    ON_STATE_MISSING
        `View.on_state_missing` raised.
    STATE
        The view's state could not be reached before any of the bot's code ran: the store
        failed or is missing, its lock timed out, or the codec's extra is not installed.
    """

    HANDLER = enum.auto()
    LOAD = enum.auto()
    HOOK = enum.auto()
    ON_OUTDATED = enum.auto()
    ON_STATE_MISSING = enum.auto()
    STATE = enum.auto()


class _Unresolved(enum.Enum):
    UNRESOLVED = enum.auto()


class ExecutionStep(msgspec.Struct, frozen=True):
    """A step of a click that hooks run in.

    `ExecutionSteps` holds the built-in ones. Make your own with a name of your choice and
    put it into the client's `execution_step_order`.

    Attributes
    ----------
    name
        The step's name; two steps with one name are the same step.
    """

    name: str


class Failure(msgspec.Struct, frozen=True):
    """One exception a click raised, and where it was raised.

    Attributes
    ----------
    error
        The exception.
    stage
        Where it was raised.
    hook
        The hook that raised it, or `None` if no hook did.
    """

    error: Exception
    stage: FailureStage
    hook: AnyHook | None = None


@typing.final
class ExecutionSteps:
    """The built-in execution steps, in the order a client runs them by default.

    Attributes
    ----------
    MAX_CONCURRENCY
        For hooks that limit how many clicks run at once.
    CHECKS
        For hooks that decide whether the click may run at all.
    COOLDOWNS
        For hooks that limit how often a click may run.
    PRE_INVOKE
        For hooks that run right before the handler.
    INVOKE
        The view's `load` and the handler, or `View.on_outdated` or `View.on_state_missing` for a
        click that cannot reach the view. No hook runs in it.
    POST_INVOKE
        For hooks that run after the handler, even after a failure.
    """

    __slots__ = ()

    MAX_CONCURRENCY: typing.Final[ExecutionStep] = ExecutionStep("MAX_CONCURRENCY")
    CHECKS: typing.Final[ExecutionStep] = ExecutionStep("CHECKS")
    COOLDOWNS: typing.Final[ExecutionStep] = ExecutionStep("COOLDOWNS")
    PRE_INVOKE: typing.Final[ExecutionStep] = ExecutionStep("PRE_INVOKE")
    INVOKE: typing.Final[ExecutionStep] = ExecutionStep("INVOKE")
    POST_INVOKE: typing.Final[ExecutionStep] = ExecutionStep("POST_INVOKE")


DEFAULT_EXECUTION_STEP_ORDER: typing.Final[collections.abc.Sequence[ExecutionStep]] = (
    ExecutionSteps.MAX_CONCURRENCY,
    ExecutionSteps.CHECKS,
    ExecutionSteps.COOLDOWNS,
    ExecutionSteps.PRE_INVOKE,
    ExecutionSteps.INVOKE,
    ExecutionSteps.POST_INVOKE,
)
"""The order a client runs the steps in unless it is given another."""


class ExecutionHook[V: view_.View]:
    """A function risa runs in one step of every click it reaches, made by `@risa.hook`.

    Attach it with `hooks=[...]` to the client, to `@risa.register` or to `@risa.handler`.
    Within a step, the client's hooks run first, then the view's, then the handler's.

    A hook can change the view's state. Before the handler, the handler's save takes the
    change along, and the change is lost if a later hook or the handler fails. After the
    handler, the hook has to call `ctx.rerender()` to save it, which works only while nothing
    in the click has failed. A change it leaves unsaved is lost, and if nothing in the click
    failed, risa logs a warning.
    """

    __slots__ = ("_func", "_injected", "_name", "_skip_when_failed", "_step", "_view_type")

    def __init__(
        self,
        func: collections.abc.Callable[..., collections.abc.Awaitable[None]],
        *,
        step: ExecutionStep,
        skip_when_failed: bool,
        name: str,
    ) -> None:
        self._func = func
        self._injected = typing.cast(
            "collections.abc.Callable[..., collections.abc.Awaitable[None]]",
            linkd.inject(func),
        )
        self._step = step
        self._skip_when_failed = skip_when_failed
        self._name = name
        self._view_type: type[view_.View] | _Unresolved | None = _Unresolved.UNRESOLVED

    @property
    def step(self) -> ExecutionStep:
        """The step the hook runs in."""
        return self._step

    @property
    def skip_when_failed(self) -> bool:
        """Whether the hook is skipped once something in the click has failed."""
        return self._skip_when_failed

    @property
    def name(self) -> str:
        """The hook's name, the function's by default."""
        return self._name

    @property
    def func(self) -> collections.abc.Callable[..., collections.abc.Awaitable[None]]:
        """The decorated function."""
        return self._func

    @property
    def view_type(self) -> type[view_.View] | None:
        """The view class the hook takes, or `None` if it takes no view.

        Raises
        ------
        HookDeclarationError
            If the hook's annotations cannot be resolved, or its view parameter is annotated
            with a union or wrapper around a view class.
        """
        return self.view_type_for(None)

    def view_type_for(self, cls: type[view_.View] | None) -> type[view_.View] | None:
        """Return `view_type`, with `cls` standing in for a name its module has not bound yet.

        `client.add_view` calls this, since as a decorator it gets the class before the module
        that defines it has bound the name.

        Parameters
        ----------
        cls
            The view class being added, or `None`.

        Returns
        -------
        :
            The view class the hook takes, or `None` if it takes no view.

        Raises
        ------
        HookDeclarationError
            As for `view_type`.
        """
        if self._view_type is _Unresolved.UNRESOLVED:
            self._view_type = _resolve_view_type(self._func, self._name, cls)
        return self._view_type

    async def __call__(self, pipeline: ExecutionPipeline, ctx: context.ComponentContext, view: V | None, /) -> None:
        """Run the hook, with the view if it takes one."""
        if self.view_type is None:
            await self._injected(pipeline, ctx)
        else:
            await self._injected(pipeline, ctx, view)


class ExecutionPipeline:
    """The run of one click, and what has failed in it so far.

    Every click risa answers has one. Hooks and error handlers receive it first; a handler,
    `View.on_outdated` or `View.on_state_missing` can have it injected. Make one yourself to
    call a hook or an error handler in a test.

    ```python
    @client.error_handler
    async def on_error(
        pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext
    ) -> bool:
        if pipeline.find(NotTheAuthorError) is None:
            return False
        await ctx.respond("You cannot interact with this.", ephemeral=True)
        return True
    ```

    Parameters
    ----------
    failures
        What has failed in the click so far, none by default.
    """

    __slots__ = ("_failures",)

    def __init__(self, failures: collections.abc.Sequence[Failure] = ()) -> None:
        self._failures = failures

    @property
    def failures(self) -> collections.abc.Sequence[Failure]:
        """Every failure of the click so far, in the order they happened."""
        return self._failures

    @property
    def failed(self) -> bool:
        """Whether anything has failed so far."""
        return bool(self._failures)

    @property
    def any_hook_failed(self) -> bool:
        """Whether a hook has failed so far."""
        return any(failure.stage is FailureStage.HOOK for failure in self._failures)

    @property
    def invocation_failed(self) -> bool:
        """Whether `load`, the handler, `View.on_outdated` or `View.on_state_missing` failed."""
        return any(failure.stage not in {FailureStage.HOOK, FailureStage.STATE} for failure in self._failures)

    def find[E: Exception](self, kind: type[E]) -> E | None:
        """Return the first exception of the click so far that is a `kind`, or `None`.

        Parameters
        ----------
        kind
            The exception class to look for; its subclasses match too.

        Returns
        -------
        :
            The exception, or `None` if the click raised none of that class.
        """
        for failure in self._failures:
            if isinstance(failure.error, kind):
                return failure.error
        return None


def _hints(
    func: collections.abc.Callable[..., collections.abc.Awaitable[None]],
    cls: type[view_.View] | None,
) -> dict[str, object]:
    try:
        return typing.get_type_hints(func)
    except NameError as exc:
        if cls is None or exc.name != cls.__name__:
            raise
        return typing.get_type_hints(func, localns={cls.__name__: cls})


def _resolve_view_type(
    func: collections.abc.Callable[..., collections.abc.Awaitable[None]],
    name: str,
    cls: type[view_.View] | None,
) -> type[view_.View] | None:
    try:
        hints = _hints(func, cls)
    except (NameError, AttributeError, SyntaxError, TypeError) as exc:
        reason = f"its annotations cannot be resolved: {exc}"
        raise errors.HookDeclarationError(name, reason) from exc
    positional = [
        parameter
        for parameter in inspect.signature(func).parameters.values()
        if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    third = positional[2:3]
    annotation = hints.get(third[0].name) if third else None
    if isinstance(annotation, type) and issubclass(annotation, view_.View):
        return annotation
    if any(isinstance(arg, type) and issubclass(arg, view_.View) for arg in typing.get_args(annotation)):
        reason = "annotate its view parameter with the view class alone, without a union or a wrapper"
        raise errors.HookDeclarationError(name, reason)
    return None


def hook(step: ExecutionStep, /, *, skip_when_failed: bool = False, name: str | None = None) -> _HookDecorator:
    """Make a function a hook that runs in `step`.

    ```python
    @risa.hook(risa.ExecutionSteps.CHECKS)
    async def author_only(
        pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext, view: Poll
    ) -> None:
        if ctx.user.id != view.author_id:
            raise NotTheAuthorError(ctx.user.id)
    ```

    A hook takes the pipeline and a `ComponentContext`. A third parameter annotated with a
    view class gets the view, with its durable state, and the hook then runs only on clicks
    that reach one; without it the hook runs on every click that reaches the view or one of
    its fallbacks, `View.on_outdated` and `View.on_state_missing`. Every further parameter is
    injected, as a handler's are. A hook fails the click by raising: a handler that has not
    run yet no longer runs, the exception reaches the error handlers, and the hooks after it
    still run unless they were made with `skip_when_failed=True`.

    Annotate the view parameter with the class alone: for `Poll | None`, `client.add_view`
    raises `HookDeclarationError`. A hook for one view class belongs on that view or its
    handlers, or its view parameter names a base class of every view it runs for. Your type
    checker refuses a `Poll` hook on the client, and `client.add_view` raises
    `HookDeclarationError` for a hook that takes a view of another class. With
    `use_global=True`, a view never passes through `add_view`, so there the hook fails each
    click with that error.

    Parameters
    ----------
    step
        The step the hook runs in.
    skip_when_failed
        Skip the hook once something in the click has failed.
    name
        The hook's name in logs, the function's by default.

    Returns
    -------
    :
        The decorator that makes the hook.

    Raises
    ------
    HookDeclarationError
        If `step` is `ExecutionSteps.INVOKE`, which runs the handler and no hooks.
    """
    if step == ExecutionSteps.INVOKE:
        reason = "INVOKE runs the handler and takes no hooks; use PRE_INVOKE or POST_INVOKE"
        raise errors.HookDeclarationError(name or "<hook>", reason)

    def decorate(func: collections.abc.Callable[..., collections.abc.Awaitable[None]]) -> ExecutionHook[view_.View]:
        return ExecutionHook(
            func,
            step=step,
            skip_when_failed=skip_when_failed,
            name=name if name is not None else func.__name__,
        )

    return typing.cast("_HookDecorator", decorate)
