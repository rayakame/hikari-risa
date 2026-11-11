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

import hikari

import risa
from risa import ui


class GuardService: ...


@risa.register(name="typing-guard")
class Guard(risa.View):
    @risa.handler
    async def vote(self, ctx: risa.ComponentContext, option: str, count: int = 1) -> None: ...

    @risa.handler
    async def buy(self, ctx: risa.ComponentContext, item: hikari.Snowflake, service: GuardService) -> None: ...

    @risa.handler
    async def press(self, ctx: risa.ComponentContext) -> None: ...


class LoadsADependency(risa.View):
    async def load(self, service: GuardService) -> None: ...


class LoadsNothing(risa.View):
    @typing.override
    async def load(self) -> None: ...


class LoadsReturningSomething(risa.View):
    @typing.override
    async def load(self, service: GuardService) -> str: ...  # type: ignore[reportIncompatibleVariableOverride]


class LoadsSynchronously(risa.View):
    @typing.override
    def load(self, service: GuardService) -> None: ...  # type: ignore[reportIncompatibleVariableOverride]


def pyright_must_accept(view: Guard) -> None:
    ui.Button(risa.bind(view.vote, "Red"))
    ui.Button(risa.bind(view.vote, "Red", 5))
    ui.Button(risa.bind(view.vote, count=5, option="Red"))
    ui.Button(risa.bind(view.buy, hikari.Snowflake(123)))
    ui.Button(view.press)
    ui.Button(view.vote)


def pyright_must_reject(view: Guard) -> None:
    risa.bind(view.vote, 5)  # type: ignore[reportArgumentType]
    risa.bind(view.vote, "Red", "five")  # type: ignore[reportArgumentType]
    risa.bind(view.vote, colour="Red")  # type: ignore[reportCallIssue]
    risa.bind(view.buy, "not-a-snowflake")  # type: ignore[reportArgumentType]


def rendered_is_spreadable(rendered: ui.Rendered, rest: hikari.api.RESTClient) -> None:
    _ = rest.create_message(hikari.Snowflake(1), **rendered)


@risa.hook(risa.ExecutionSteps.CHECKS)
async def guard_check(pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext, view: Guard) -> None: ...


@risa.hook(risa.ExecutionSteps.CHECKS)
async def any_view_check(
    pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext, service: GuardService
) -> None: ...


def hooks_pyright_must_accept(app: risa.GatewayClientAppT) -> None:
    risa.client_from_app(app, hooks=[any_view_check])
    risa.register(name="typing-guard-hooked", hooks=[guard_check, any_view_check])


def hooks_pyright_must_reject(app: risa.GatewayClientAppT) -> None:
    risa.client_from_app(app, hooks=[guard_check])  # type: ignore[reportArgumentType]
