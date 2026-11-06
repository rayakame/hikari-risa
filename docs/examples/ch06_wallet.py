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
"""A stall whose wallets open for each click, from a database that opens once."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui


class Config:
    database_url = "postgres://localhost/shop"


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.coins: dict[hikari.Snowflake, int] = {}

    @classmethod
    async def connect(cls, url: str) -> Database:
        return cls(url)

    async def close(self) -> None:
        self.coins.clear()

    async def coins_of(self, user: hikari.Snowflake) -> int:
        return self.coins.get(user, 10)

    async def set_coins(self, user: hikari.Snowflake, coins: int) -> None:
        self.coins[user] = coins


class Wallet:
    def __init__(self, database: Database, user: hikari.Snowflake, coins: int) -> None:
        self.database = database
        self.user = user
        self.coins = coins

    async def pay(self, price: int) -> None:
        self.coins -= price
        await self.database.set_coins(self.user, self.coins)


# --8<-- [start:database]
async def open_database(config: Config) -> Database:
    return await Database.connect(config.database_url)


def add_database(client: risa.Client) -> None:
    registry = client.di.registry_for(risa.Contexts.DEFAULT)
    registry.register_value(Config, Config())
    registry.register_factory(Database, open_database, teardown=Database.close)
    # --8<-- [end:database]


# --8<-- [start:wallet]
async def open_wallet(ctx: risa.ComponentContext, database: Database) -> Wallet:
    coins = await database.coins_of(ctx.user.id)
    return Wallet(database, ctx.user.id, coins)


def add_wallet(client: risa.Client) -> None:
    registry = client.di.registry_for(risa.Contexts.COMPONENT)
    registry.register_factory(Wallet, open_wallet)
    # --8<-- [end:wallet]


@risa.register(name="stall")
class Stall(risa.View):
    price: int = 3

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"An apple costs {self.price} coins."),
            ui.Row(ui.Button(self.buy, label="Buy")),
        )

    # --8<-- [start:buy]
    @risa.handler
    async def buy(self, ctx: risa.ComponentContext, wallet: Wallet) -> None:
        if wallet.coins < self.price:
            await ctx.respond("You can't afford that.", ephemeral=True)
            return
        await wallet.pay(self.price)
        await ctx.respond(f"You have {wallet.coins} coins left.", ephemeral=True)
        # --8<-- [end:buy]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    add_database(client)
    add_wallet(client)
    client.add_view(Stall)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!stall":
            rendered = await client.build(Stall())
            await rendered.send_to(bot.rest, event.channel_id)

    # --8<-- [start:stopping]
    @bot.listen()
    async def on_stopping(_: hikari.StoppingEvent) -> None:
        await client.di.close()

    # --8<-- [end:stopping]

    bot.run()


if __name__ == "__main__":
    main()
