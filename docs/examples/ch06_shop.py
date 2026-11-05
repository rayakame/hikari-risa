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
"""A shop whose handler takes a wire argument, your database and the client."""

from __future__ import annotations

import asyncio
import os

import hikari

import risa
from risa import ui

_PRICES = {"apple": 3, "pear": 4}


class Prices:
    async def of(self, item: str) -> int:
        await asyncio.sleep(0.1)
        return _PRICES[item]


# --8<-- [start:offer]
@risa.register(name="offer")
class Offer(risa.View):
    item: str
    price: int

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"{self.item.title()}: {self.price} coins"),
            ui.Row(ui.Button(self.buy, label="Buy")),
        )

    @risa.handler
    async def buy(self, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay(f"You bought the {self.item}."))
        # --8<-- [end:offer]


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

    # --8<-- [start:show]
    @risa.handler
    async def show(
        self,
        ctx: risa.ComponentContext,
        item: str,
        prices: Prices,
        client: risa.Client,
    ) -> None:
        offer = Offer(item=item, price=await prices.of(item))
        await ctx.respond(**await client.build(offer), ephemeral=True)
        # --8<-- [end:show]


# --8<-- [start:dependencies]
def add_dependencies(client: risa.Client) -> None:
    registry = client.di.registry_for(risa.Contexts.DEFAULT)
    registry.register_value(Prices, Prices())
    # --8<-- [end:dependencies]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    add_dependencies(client)
    client.add_view(Shop)
    client.add_view(Offer)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!shop":
            rendered = await client.build(Shop())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
