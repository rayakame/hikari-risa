from __future__ import annotations

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
