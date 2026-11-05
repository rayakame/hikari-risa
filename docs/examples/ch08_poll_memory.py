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
"""The poll in a store, a `MemoryStore` for trying things out."""

from __future__ import annotations

import os

import hikari
import msgspec

import risa
from risa import ui

WEEK = 7 * 24 * 60 * 60


# --8<-- [start:fields]
@risa.register(name="poll", version=2, state=risa.InStore(ttl=WEEK))
class Poll(risa.View):
    question: str
    yes: int = 0
    no: int = 0
    voters: list[int] = msgspec.field(default_factory=list[int])
    # --8<-- [end:fields]

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"## {self.question}"),
            ui.Row(
                ui.Button(self.vote_yes, label=f"Yes: {self.yes}"),
                ui.Button(self.vote_no, label=f"No: {self.no}"),
            ),
        )

    @risa.handler
    async def vote_yes(self, ctx: risa.ComponentContext) -> None:
        if await self._first_vote(ctx):
            self.yes += 1
            await ctx.rerender()

    @risa.handler
    async def vote_no(self, ctx: risa.ComponentContext) -> None:
        if await self._first_vote(ctx):
            self.no += 1
            await ctx.rerender()

    async def _first_vote(self, ctx: risa.ComponentContext) -> bool:
        if ctx.user.id in self.voters:
            await ctx.respond("You already voted.", ephemeral=True)
            return False
        self.voters.append(ctx.user.id)
        return True

    # --8<-- [start:missing]
    @classmethod
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("This poll has expired."))
        # --8<-- [end:missing]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )

    # --8<-- [start:client]
    client = risa.client_from_app(bot, store=risa.MemoryStore())
    client.add_view(Poll)
    # --8<-- [end:client]

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!poll":
            rendered = await client.build(Poll(question="Pizza on Friday?"))
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
