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
"""A slow handler, and which answer goes with which auto-defer."""

from __future__ import annotations

import asyncio
import logging
import os

import hikari

import risa
from risa import ui


async def build_report() -> int:
    await asyncio.sleep(5)
    return 128


async def send_to_team(rows: int) -> None:
    await asyncio.sleep(0.1)
    logging.getLogger("report").info("Sent %d rows to the team.", rows)


# --8<-- [start:view]
@risa.register(name="report")
class Report(risa.View):
    rows: int = 0

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"Rows in the report: **{self.rows}**"),
            ui.Row(
                ui.Button(self.refresh, label="Refresh"),
                ui.Button(self.export, label="Export"),
                ui.Button(self.close, label="Close"),
            ),
        )
        # --8<-- [end:view]

    # --8<-- [start:refresh]
    @risa.handler(defer=risa.AutoDefer.UPDATE)
    async def refresh(self, ctx: risa.ComponentContext) -> None:
        self.rows = await build_report()
        await ctx.rerender()
        # --8<-- [end:refresh]

    # --8<-- [start:export]
    @risa.handler(defer=risa.AutoDefer.THINKING_EPHEMERAL)
    async def export(self, ctx: risa.ComponentContext) -> None:
        rows = await build_report()
        await ctx.respond(f"Exported {rows} rows.", ephemeral=True)
        # --8<-- [end:export]

    # --8<-- [start:close]
    @risa.handler(defer=risa.AutoDefer.UPDATE)
    async def close(self, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay(f"Closed with {self.rows} rows."))
        # --8<-- [end:close]


@risa.register(name="report-by-hand")
class ReportByHand(risa.View):
    rows: int = 0

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"Rows in the report: **{self.rows}**"),
            ui.Row(
                ui.Button(self.refresh, label="Refresh"),
                ui.Button(self.publish, label="Publish"),
            ),
        )

    # --8<-- [start:defer]
    @risa.handler
    async def refresh(self, ctx: risa.ComponentContext) -> None:
        await ctx.defer()
        self.rows = await build_report()
        await ctx.rerender()
        # --8<-- [end:defer]

    # --8<-- [start:acknowledge]
    @risa.handler
    async def publish(self, ctx: risa.ComponentContext) -> None:
        if self.rows == 0:
            await ctx.respond("There is nothing to publish.", ephemeral=True)
        else:
            await send_to_team(self.rows)
        await ctx.acknowledge()
        # --8<-- [end:acknowledge]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(Report)
    client.add_view(ReportByHand)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!report":
            rendered = await client.build(Report())
            await rendered.send_to(bot.rest, event.channel_id)
        elif event.content == "!byhand":
            rendered = await client.build(ReportByHand())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
