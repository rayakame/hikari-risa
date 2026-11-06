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
"""A gallery, a separator and an attached file."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui

_IMAGES = "https://rayakame.github.io/hikari-risa/main/assets/images"
_BANNER = f"{_IMAGES}/banner.webp"
_ART = f"{_IMAGES}/mascot-sticker.webp"


# --8<-- [start:view]
@risa.register(name="meetup")
class Meetup(risa.View):
    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay("## Photos from the meetup"),
            ui.MediaGallery(
                ui.MediaGalleryItem(_BANNER, description="The banner"),
                ui.MediaGalleryItem(_ART, description="The mascot"),
            ),
            ui.Separator(),
            ui.TextDisplay("The notes are attached."),
            ui.File(hikari.Bytes(b"Bring a friend next time.", "notes.txt")),
        )
        # --8<-- [end:view]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(Meetup)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!meetup":
            rendered = await client.build(Meetup())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
