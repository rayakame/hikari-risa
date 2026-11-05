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
"""The poll from chapter 08 on a REST bot.

Needs `hikari-risa[redis]`, `hikari[server]` and hikari-lightbulb.
"""

from __future__ import annotations

import os

import hikari
import lightbulb
import msgspec
import redis.asyncio

import risa
from risa import ui
from risa.store.redis import RedisStore

WEEK = 7 * 24 * 60 * 60


@risa.register(name="poll", version=2, state=risa.InStore(ttl=WEEK))
class Poll(risa.View):
    question: str
    yes: int = 0
    no: int = 0
    voters: list[int] = msgspec.field(default_factory=list[int])

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"## {self.question}"),
            ui.Row(
                ui.Button(self.vote_yes, label=f"Yes: {self.yes}"),
                ui.Button(self.vote_no, label=f"No: {self.no}"),
            ),
        )

    # --8<-- [start:conflict]
    @risa.handler
    async def vote_yes(self, ctx: risa.ComponentContext) -> None:
        if not await self._first_vote(ctx):
            return
        self.yes += 1
        try:
            await ctx.rerender()
        except risa.StateConflictError:
            if await self._first_vote(ctx):
                self.yes += 1
                await ctx.rerender()
            # --8<-- [end:conflict]

    @risa.handler
    async def vote_no(self, ctx: risa.ComponentContext) -> None:
        if not await self._first_vote(ctx):
            return
        self.no += 1
        try:
            await ctx.rerender()
        except risa.StateConflictError:
            if await self._first_vote(ctx):
                self.no += 1
                await ctx.rerender()

    async def _first_vote(self, ctx: risa.ComponentContext) -> bool:
        if ctx.user.id in self.voters:
            await ctx.respond("You already voted.", ephemeral=True)
            return False
        self.voters.append(ctx.user.id)
        return True

    @classmethod
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("This poll has expired."))


class PollCommand(
    lightbulb.SlashCommand,
    name="poll",
    description="Post a poll.",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context, client: risa.Client) -> None:
        poll = Poll(question="Pizza on Friday?")
        await ctx.respond(**await client.build(poll))


def main() -> None:
    bot = hikari.RESTBot(
        os.environ["DISCORD_TOKEN"],
        hikari.TokenType.BOT,
        public_key=os.environ["DISCORD_PUBLIC_KEY"],
    )
    lightbulb_client = lightbulb.client_from_app(bot)
    bot.add_startup_callback(lightbulb_client.start)

    # --8<-- [start:client]
    connection = redis.asyncio.Redis(host=os.environ["REDIS_HOST"])
    client = risa.client_from_lightbulb(
        lightbulb_client,
        store=RedisStore(connection),
    )
    client.add_view(Poll)
    # --8<-- [end:client]

    lightbulb_client.register(PollCommand)

    async def on_shutdown(_: hikari.RESTBot) -> None:
        await connection.aclose()

    bot.add_shutdown_callback(on_shutdown)
    bot.run(port=int(os.environ["PORT"]))


if __name__ == "__main__":
    main()
