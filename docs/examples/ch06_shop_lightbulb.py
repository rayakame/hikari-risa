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
"""The shop on lightbulb's manager, so one registration reaches both."""

from __future__ import annotations

import asyncio
import os

import hikari
import lightbulb

import risa
from risa import ui

_PRICES = {"apple": 3, "pear": 4}


class Prices:
    async def of(self, item: str) -> int:
        await asyncio.sleep(0.1)
        return _PRICES[item]


@risa.register(name="shop")
class Shop(risa.View):
    def render(self) -> ui.Layout:
        buttons = [
            ui.Button(
                risa.bind(self.show, item),
                label=item.title(),
            )
            for item in _PRICES
        ]
        return ui.Container(ui.TextDisplay("## Shop"), ui.Row(*buttons))

    @risa.handler
    async def show(
        self,
        ctx: risa.ComponentContext,
        item: str,
        prices: Prices,
    ) -> None:
        price = await prices.of(item)
        await ctx.respond(f"{item.title()}: {price} coins", ephemeral=True)


# --8<-- [start:command]
class ShopCommand(
    lightbulb.SlashCommand,
    name="shop",
    description="Open the shop.",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context, client: risa.Client) -> None:
        await ctx.respond(**await client.build(Shop()))
        # --8<-- [end:command]


def main() -> None:
    bot = hikari.GatewayBot(os.environ["DISCORD_TOKEN"])
    lightbulb_client = lightbulb.client_from_app(bot)
    bot.subscribe(hikari.StartingEvent, lightbulb_client.start)

    # --8<-- [start:share]
    registry = lightbulb_client.di.registry_for(lightbulb.di.Contexts.DEFAULT)
    registry.register_value(Prices, Prices())
    risa_client = risa.client_from_lightbulb(lightbulb_client)
    risa_client.add_view(Shop)
    lightbulb_client.register(ShopCommand)
    # --8<-- [end:share]

    bot.run()


if __name__ == "__main__":
    main()
