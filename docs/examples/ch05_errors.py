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
"""Error handlers: one for a failure the bot expects, and one that answers every other."""

from __future__ import annotations

import asyncio
import os

import hikari

import risa
from risa import ui

# --8<-- [start:view]
FORECASTS = {"Berlin": "Sunny, 18 °C"}


class ForecastUnavailableError(Exception):
    """The weather service did not answer."""


async def fetch_forecast(city: str) -> str:
    await asyncio.sleep(0)
    raise ForecastUnavailableError(city)


@risa.register(name="forecast")
class Forecast(risa.View):
    city: str = "Berlin"
    text: risa.Prop[str] = ""

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"**{self.city}**: {self.text or FORECASTS[self.city]}"),
            ui.Row(
                ui.Button(self.refresh, label="Refresh"),
                ui.Button(self.next_city, label="Next city"),
            ),
        )

    @risa.handler
    async def refresh(self, ctx: risa.ComponentContext) -> None:
        self.text = await fetch_forecast(self.city)
        await ctx.rerender()

    @risa.handler
    async def next_city(self, ctx: risa.ComponentContext) -> None:
        self.city = "Paris"
        await ctx.rerender()
        # --8<-- [end:view]


# --8<-- [start:expected]
async def forecast_unavailable(failure: risa.ClickFailure) -> bool:
    if not isinstance(failure.error, ForecastUnavailableError):
        return False
    await failure.ctx.respond(
        "The weather service is not answering. Try again in a minute.",
        ephemeral=True,
    )
    return True
    # --8<-- [end:expected]


# --8<-- [start:everything]
async def something_went_wrong(failure: risa.ClickFailure) -> bool:
    await failure.ctx.respond("Something went wrong.", ephemeral=True)
    return False
    # --8<-- [end:everything]


# --8<-- [start:register]
def add_error_handlers(client: risa.Client) -> None:
    client.error_handler(forecast_unavailable, priority=1)
    client.error_handler(something_went_wrong, priority=-1)
    # --8<-- [end:register]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(Forecast)
    add_error_handlers(client)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!forecast":
            rendered = await client.build(Forecast())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
