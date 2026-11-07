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
"""A handler's arguments change, and its old version stays for buttons already sent."""

from __future__ import annotations

import os

import hikari
import msgspec
import redis.asyncio

import risa
from risa import ui
from risa.store.redis import RedisStore

WEEK = 7 * 24 * 60 * 60


@risa.register(name="poll", version=3, state=risa.InStore(ttl=WEEK))
class Poll(risa.View):
    question: str
    votes: dict[str, int]
    voters: list[int] = msgspec.field(default_factory=list[int])
    note: str = "One vote each."

    # --8<-- [start:render]
    def render(self) -> ui.Layout:
        buttons = [
            ui.Button(risa.bind(self.vote, position), label=f"{option}: {count}")
            for position, (option, count) in enumerate(self.votes.items())
        ]
        return ui.Container(
            ui.TextDisplay(f"## {self.question}"),
            ui.TextDisplay(self.note),
            ui.Row(*buttons),
        )
        # --8<-- [end:render]

    # --8<-- [start:renamed]
    @risa.handler(handler_id="vote")
    async def vote_by_name(self, ctx: risa.ComponentContext, option: str) -> None:
        await self._count(ctx, option)
        # --8<-- [end:renamed]

    # --8<-- [start:version-two]
    @risa.handler(version=2)
    async def vote(self, ctx: risa.ComponentContext, position: int) -> None:
        await self._count(ctx, list(self.votes)[position])
        # --8<-- [end:version-two]

    async def _count(self, ctx: risa.ComponentContext, option: str) -> None:
        if ctx.user.id in self.voters:
            await ctx.respond("You already voted.", ephemeral=True)
            return
        self.voters.append(ctx.user.id)
        self.votes[option] += 1
        await ctx.rerender()

    @classmethod
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("This poll has expired."))


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    connection = redis.asyncio.Redis(host="localhost")
    client = risa.client_from_app(bot, store=RedisStore(connection))
    # --8<-- [start:client]
    client.add_view(Poll)
    # --8<-- [end:client]

    @bot.listen()
    async def on_stopped(_: hikari.StoppedEvent) -> None:
        await connection.aclose()

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!poll":
            poll = Poll(
                question="Pizza on Friday?",
                votes={"Yes": 0, "No": 0, "Maybe": 0},
            )
            rendered = await client.build(poll)
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
