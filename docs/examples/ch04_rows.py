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
"""Rows and nothing else: the one shape that can carry `content` beside it."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui


# --8<-- [start:view]
@risa.register(name="yes-no")
class YesNo(risa.View):
    def render(self) -> ui.Layout:
        return ui.Row(
            ui.Button(self.yes, label="Yes"),
            ui.Button(self.no, label="No", style=hikari.ButtonStyle.SECONDARY),
        )

    @risa.handler
    async def yes(self, ctx: risa.ComponentContext) -> None:
        await ctx.respond("Shipping it.", ephemeral=True)

    @risa.handler
    async def no(self, ctx: risa.ComponentContext) -> None:
        await ctx.respond("Holding it back.", ephemeral=True)
        # --8<-- [end:view]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(YesNo)

    # --8<-- [start:send]
    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!shipit":
            rendered = await client.build(YesNo())
            await bot.rest.create_message(
                event.channel_id,
                "Ship it?",
                **rendered,
            )
            # --8<-- [end:send]

    bot.run()


if __name__ == "__main__":
    main()
