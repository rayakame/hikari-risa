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
"""Two fields and two buttons: the state travels in the message."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui


# --8<-- [start:fields]
@risa.register(name="scoreboard")
class Scoreboard(risa.View):
    home: int = 0
    away: int = 0

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"## Home {self.home} : {self.away} Away"),
            ui.Row(
                ui.Button(self.score_home, label="Home +1"),
                ui.Button(self.score_away, label="Away +1"),
            ),
        )
        # --8<-- [end:fields]

    # --8<-- [start:handlers]
    @risa.handler
    async def score_home(self, ctx: risa.ComponentContext) -> None:
        self.home += 1
        await ctx.rerender()

    @risa.handler
    async def score_away(self, ctx: risa.ComponentContext) -> None:
        self.away += 1
        await ctx.rerender()
        # --8<-- [end:handlers]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(Scoreboard)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!scoreboard":
            rendered = await client.build(Scoreboard())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
