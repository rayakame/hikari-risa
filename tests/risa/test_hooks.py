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

import typing
import unittest.mock

import pytest

import risa
from risa import hooks


@risa.register(name="hooks-poll")
class Poll(risa.View):
    author_id: int = 0


class Database: ...


@risa.hook(risa.ExecutionSteps.CHECKS)
async def anywhere(pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext) -> None:
    await ctx.respond(str(pipeline.failed))


@risa.hook(risa.ExecutionSteps.CHECKS, skip_when_failed=True, name="needs-a-database")
async def with_dependency(  # ruff:ignore[unused-async]
    _pipeline: risa.ExecutionPipeline,
    _ctx: risa.ComponentContext,
    db: Database,
) -> None:
    assert isinstance(db, Database)


@risa.hook(risa.ExecutionSteps.PRE_INVOKE)
async def on_a_poll(_pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext, view: Poll) -> None:
    await ctx.respond(str(view.author_id))


@risa.hook(risa.ExecutionSteps.CHECKS)
async def unresolvable(
    _pipeline: risa.ExecutionPipeline,
    _ctx: risa.ComponentContext,
    _view: Missing,  # type: ignore[reportUndefinedVariable]  # ruff:ignore[undefined-name]
) -> None: ...


@risa.hook(risa.ExecutionSteps.CHECKS)
async def maybe_a_poll(_pipeline: risa.ExecutionPipeline, _ctx: risa.ComponentContext, _view: Poll | None) -> None: ...


@risa.hook(risa.ExecutionSteps.CHECKS)
async def needs_a_missing_dependency(
    _pipeline: risa.ExecutionPipeline,
    _ctx: risa.ComponentContext,
    *,
    _db: Missing,  # type: ignore[reportUndefinedVariable]  # ruff:ignore[undefined-name]
) -> None: ...


@risa.hook(risa.ExecutionSteps.CHECKS)
async def broken_annotation(
    _pipeline: risa.ExecutionPipeline,
    _ctx: risa.ComponentContext,
    _view: "list[",  # type: ignore[reportInvalidTypeForm]  # ruff:ignore[forward-annotation-syntax-error]
) -> None: ...


def test_a_hook_without_a_view_takes_none() -> None:
    assert anywhere.view_type is None
    assert with_dependency.view_type is None


def test_a_hook_whose_third_parameter_is_a_view_takes_that_class() -> None:
    assert on_a_poll.view_type is Poll


def test_a_hook_carries_its_function_step_name_and_flag() -> None:
    assert (anywhere.step, anywhere.name, anywhere.skip_when_failed) == (risa.ExecutionSteps.CHECKS, "anywhere", False)
    assert anywhere.func.__name__ == "anywhere"
    assert (with_dependency.name, with_dependency.skip_when_failed) == ("needs-a-database", True)


def test_a_hook_on_invoke_is_refused() -> None:
    with pytest.raises(risa.HookDeclarationError, match="INVOKE runs the handler"):
        risa.hook(risa.ExecutionSteps.INVOKE)


@pytest.mark.parametrize(
    "hook", typing.cast("list[risa.AnyHook]", [unresolvable, needs_a_missing_dependency, broken_annotation])
)
def test_a_hook_whose_annotations_cannot_be_resolved_says_so(hook: risa.AnyHook) -> None:
    with pytest.raises(risa.HookDeclarationError, match="annotations cannot be resolved"):
        _ = hook.view_type


def test_a_hook_whose_view_parameter_is_a_union_is_refused() -> None:
    with pytest.raises(risa.HookDeclarationError, match="the view class alone"):
        _ = maybe_a_poll.view_type


async def test_a_hook_gets_the_view_only_if_it_takes_one() -> None:
    ctx = unittest.mock.AsyncMock(spec=risa.ComponentContext)
    pipeline = risa.ExecutionPipeline([])

    await anywhere(pipeline, ctx, Poll(author_id=7))
    await on_a_poll(pipeline, ctx, Poll(author_id=7))

    assert [call.args for call in ctx.respond.await_args_list] == [("False",), ("7",)]


def test_steps_are_equal_by_name() -> None:
    assert risa.ExecutionStep("CHECKS") == risa.ExecutionSteps.CHECKS
    assert risa.ExecutionStep("AUDIT") != risa.ExecutionSteps.CHECKS


def test_the_default_order_runs_invoke_between_the_steps_before_and_after() -> None:
    assert [step.name for step in risa.DEFAULT_EXECUTION_STEP_ORDER] == [
        "MAX_CONCURRENCY",
        "CHECKS",
        "COOLDOWNS",
        "PRE_INVOKE",
        "INVOKE",
        "POST_INVOKE",
    ]


class NotTheAuthorError(Exception): ...


def test_a_pipeline_without_failures_has_not_failed() -> None:
    pipeline = risa.ExecutionPipeline([])

    assert not pipeline.failed
    assert not pipeline.any_hook_failed
    assert not pipeline.invocation_failed
    assert pipeline.find(Exception) is None


def test_a_pipeline_tells_hook_failures_from_invocation_failures() -> None:
    hooked = risa.ExecutionPipeline([risa.Failure(NotTheAuthorError(), risa.FailureStage.HOOK, anywhere)])
    handled = risa.ExecutionPipeline([risa.Failure(KeyError("x"), risa.FailureStage.HANDLER)])
    unreached = risa.ExecutionPipeline([risa.Failure(risa.LockTimeoutError("k", 1.0), risa.FailureStage.STATE)])

    assert (hooked.any_hook_failed, hooked.invocation_failed) == (True, False)
    assert (handled.any_hook_failed, handled.invocation_failed) == (False, True)
    assert (unreached.failed, unreached.any_hook_failed, unreached.invocation_failed) == (True, False, False)


def test_find_returns_the_first_exception_of_the_kind_in_order() -> None:
    first, second = NotTheAuthorError("first"), NotTheAuthorError("second")
    failures = [
        hooks.Failure(KeyError("x"), risa.FailureStage.HANDLER),
        hooks.Failure(first, risa.FailureStage.HOOK, anywhere),
        hooks.Failure(second, risa.FailureStage.HOOK, anywhere),
    ]
    pipeline = risa.ExecutionPipeline(failures)

    assert pipeline.find(NotTheAuthorError) is first
    assert pipeline.find(LookupError) is failures[0].error
    assert pipeline.find(ValueError) is None


def test_a_pipeline_shows_failures_added_after_it_was_made() -> None:
    failures: list[risa.Failure] = []
    pipeline = risa.ExecutionPipeline(failures)

    failures.append(risa.Failure(KeyError("x"), risa.FailureStage.LOAD))

    assert pipeline.failed
    assert list(pipeline.failures) == failures
