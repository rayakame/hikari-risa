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
"""The counter, sent from a lightbulb slash command."""

from __future__ import annotations

import os

import hikari
import lightbulb

import risa
from risa import ui


# --8<-- [start:view]
@risa.register(name="counter")
class Counter(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"Clicks: **{self.count}**"),
            ui.Row(ui.Button(self.click, label="+1")),
        )

    @risa.handler
    async def click(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()
        # --8<-- [end:view]


def main() -> None:
    bot = hikari.GatewayBot(os.environ["DISCORD_TOKEN"])
    lightbulb_client = lightbulb.client_from_app(bot)
    bot.subscribe(hikari.StartingEvent, lightbulb_client.start)

    # --8<-- [start:send]
    risa_client = risa.client_from_lightbulb(lightbulb_client)
    risa_client.add_view(Counter)

    @lightbulb_client.register()
    class CounterCommand(
        lightbulb.SlashCommand,
        name="counter",
        description="Post the counter.",
    ):
        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            await ctx.respond(**await risa_client.build(Counter()))
            # --8<-- [end:send]

    bot.run()


if __name__ == "__main__":
    main()
