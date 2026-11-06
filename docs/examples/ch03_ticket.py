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
"""One handler behind several buttons, each carrying its own argument."""

from __future__ import annotations

import enum
import os

import hikari

import risa
from risa import ui


# --8<-- [start:priority]
class Priority(enum.Enum):
    LOW = 1
    NORMAL = 2
    URGENT = 3
    # --8<-- [end:priority]


# --8<-- [start:render]
@risa.register(name="ticket")
class Ticket(risa.View):
    priority: Priority = Priority.NORMAL

    def render(self) -> ui.Layout:
        buttons = [
            ui.Button(
                risa.bind(self.pick, priority),
                label=priority.name.title(),
                disabled=priority is self.priority,
            )
            for priority in Priority
        ]
        return ui.Container(
            ui.TextDisplay(f"Priority: **{self.priority.name.lower()}**"),
            ui.Row(*buttons),
        )
        # --8<-- [end:render]

    # --8<-- [start:handler]
    @risa.handler
    async def pick(self, ctx: risa.ComponentContext, priority: Priority) -> None:
        self.priority = priority
        await ctx.rerender()
        # --8<-- [end:handler]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(Ticket)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!ticket":
            rendered = await client.build(Ticket())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
