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
"""Sections: text with a thumbnail, a button or a link beside it."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui

_IMAGES = "https://rayakame.github.io/hikari-risa/main/assets/images"
_ART = f"{_IMAGES}/mascot-sticker.webp"
_NOTES = "https://github.com/rayakame/hikari-risa"


# --8<-- [start:view]
@risa.register(name="release")
class Release(risa.View):
    likes: int = 0

    def render(self) -> ui.Layout:
        art = ui.Thumbnail(_ART, description="The mascot")
        like = ui.Button(self.like, label="Like")
        notes = ui.LinkButton(_NOTES, label="Read the notes")
        return ui.Container(
            ui.Section(ui.TextDisplay("## Summer update"), accessory=art),
            ui.Section(ui.TextDisplay(f"Likes: {self.likes}"), accessory=like),
            ui.Section(ui.TextDisplay("What changed"), accessory=notes),
        )
        # --8<-- [end:view]

    # --8<-- [start:handler]
    @risa.handler
    async def like(self, ctx: risa.ComponentContext) -> None:
        self.likes += 1
        await ctx.rerender()
        # --8<-- [end:handler]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(Release)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!release":
            rendered = await client.build(Release())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
