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
"""The counter, sent from a gateway event."""

from __future__ import annotations

import os

import hikari

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
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )

    # --8<-- [start:send]
    client = risa.client_from_app(bot)
    client.add_view(Counter)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!counter":
            rendered = await client.build(Counter())
            await rendered.send_to(bot.rest, event.channel_id)
            # --8<-- [end:send]

    bot.run()


if __name__ == "__main__":
    main()
