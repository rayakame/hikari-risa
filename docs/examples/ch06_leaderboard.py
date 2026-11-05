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
"""A field for what to show, and a prop for what the database says right now."""

from __future__ import annotations

import asyncio
import os

import hikari
import msgspec

import risa
from risa import ui

_TOP = {
    "chess": ["Mika", "Jo", "Sam"],
    "go": ["Noor", "Mika", "Ren"],
}


class Scores:
    async def top(self, game: str) -> list[str]:
        await asyncio.sleep(0.1)
        return _TOP[game]


# --8<-- [start:fields]
@risa.register(name="leaderboard")
class Leaderboard(risa.View):
    game: str = "chess"
    rows: risa.Prop[list[str]] = msgspec.field(default_factory=list[str])

    async def load(self, scores: Scores = risa.INJECTED) -> None:
        self.rows = await scores.top(self.game)
        # --8<-- [end:fields]

    # --8<-- [start:render]
    def render(self) -> ui.Layout:
        lines = [f"{n}. {name}" for n, name in enumerate(self.rows, start=1)]
        buttons = [
            ui.Button(
                risa.bind(self.switch, game),
                label=game.title(),
                disabled=game == self.game,
            )
            for game in _TOP
        ]
        return ui.Container(
            ui.TextDisplay(f"## Top players in {self.game}"),
            ui.TextDisplay("\n".join(lines)),
            ui.Row(*buttons, ui.Button(self.refresh, label="Refresh")),
        )
        # --8<-- [end:render]

    # --8<-- [start:refresh]
    @risa.handler
    async def refresh(self, ctx: risa.ComponentContext) -> None:
        await ctx.rerender()
        # --8<-- [end:refresh]

    # --8<-- [start:switch]
    @risa.handler
    async def switch(
        self,
        ctx: risa.ComponentContext,
        game: str,
        scores: Scores,
    ) -> None:
        self.game = game
        self.rows = await scores.top(game)
        await ctx.rerender()
        # --8<-- [end:switch]


# --8<-- [start:dependencies]
def add_dependencies(client: risa.Client) -> None:
    registry = client.di.registry_for(risa.Contexts.DEFAULT)
    registry.register_value(Scores, Scores())
    # --8<-- [end:dependencies]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    add_dependencies(client)
    client.add_view(Leaderboard)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!leaderboard":
            rendered = await client.build(Leaderboard())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
