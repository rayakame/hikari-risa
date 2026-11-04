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
"""A question that expires, for readers who know miru's timeout."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui


# --8<-- [start:view]
@risa.register(name="question", state=risa.InStore(ttl=600))
class Question(risa.View):
    text: str

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(self.text),
            ui.Row(ui.Button(self.yes, label="Yes")),
        )

    @risa.handler
    async def yes(self, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay(f"{self.text} **Yes.**"))

    @classmethod
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        await ctx.respond("This question has expired.", ephemeral=True)
        # --8<-- [end:view]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )

    # --8<-- [start:client]
    client = risa.client_from_app(bot, store=risa.MemoryStore())
    client.add_view(Question)
    # --8<-- [end:client]

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!question":
            rendered = await client.build(Question(text="Ship it?"))
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
