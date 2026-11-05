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
"""Select menus: `ctx.values` holds the picks, and `ctx.resolved` the objects."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui


# --8<-- [start:lunch]
@risa.register(name="lunch")
class Lunch(risa.View):
    choice: str = "not decided"

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"Lunch today: **{self.choice}**"),
            ui.Row(
                ui.TextSelect(
                    self.pick,
                    ui.SelectOption("Ramen", "ramen"),
                    ui.SelectOption("Curry", "curry"),
                    placeholder="Pick one",
                ),
            ),
        )
        # --8<-- [end:lunch]

    # --8<-- [start:lunch-handler]
    @risa.handler
    async def pick(self, ctx: risa.ComponentContext) -> None:
        self.choice = ctx.values[0]
        await ctx.rerender()
        # --8<-- [end:lunch-handler]


# --8<-- [start:reviewer]
@risa.register(name="reviewer")
class Reviewer(risa.View):
    reviewer: str = "nobody"

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"Reviewer: **{self.reviewer}**"),
            ui.Row(ui.UserSelect(self.pick, placeholder="Pick a reviewer")),
        )
        # --8<-- [end:reviewer]

    # --8<-- [start:reviewer-handler]
    @risa.handler
    async def pick(self, ctx: risa.ComponentContext) -> None:
        resolved = ctx.resolved
        if resolved is not None:
            user_id = hikari.Snowflake(ctx.values[0])
            self.reviewer = resolved.users[user_id].username
        await ctx.rerender()
        # --8<-- [end:reviewer-handler]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(Lunch)
    client.add_view(Reviewer)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!lunch":
            rendered = await client.build(Lunch())
            await rendered.send_to(bot.rest, event.channel_id)
        elif event.content == "!reviewer":
            rendered = await client.build(Reviewer())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
