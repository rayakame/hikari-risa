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
"""A hikari enum as an argument: a view that fails to build, and the fix."""

from __future__ import annotations

import os

import hikari

import risa
from risa import ui


# --8<-- [start:wrong]
@risa.register(name="channel-kind-wrong")
class ChannelKindWrong(risa.View):
    def render(self) -> ui.Layout:
        voice = hikari.ChannelType.GUILD_VOICE
        return ui.Container(
            ui.TextDisplay("Which kind of channel?"),
            ui.Row(ui.Button(risa.bind(self.pick, voice), label="Voice")),
        )

    @risa.handler
    async def pick(
        self,
        ctx: risa.ComponentContext,
        kind: hikari.ChannelType,
    ) -> None:
        await ctx.respond(f"You picked {kind.name}.", ephemeral=True)
        # --8<-- [end:wrong]


# --8<-- [start:fixed-render]
@risa.register(name="channel-kind")
class ChannelKind(risa.View):
    def render(self) -> ui.Layout:
        text = hikari.ChannelType.GUILD_TEXT
        voice = hikari.ChannelType.GUILD_VOICE
        return ui.Container(
            ui.TextDisplay("Which kind of channel?"),
            ui.Row(
                ui.Button(risa.bind(self.pick, int(text)), label="Text"),
                ui.Button(risa.bind(self.pick, int(voice)), label="Voice"),
            ),
        )
        # --8<-- [end:fixed-render]

    # --8<-- [start:fixed-handler]
    @risa.handler
    async def pick(self, ctx: risa.ComponentContext, kind: int) -> None:
        picked = hikari.ChannelType(kind)
        await ctx.respond(f"You picked {picked.name}.", ephemeral=True)
        # --8<-- [end:fixed-handler]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    client.add_view(ChannelKind)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!channelkind":
            rendered = await client.build(ChannelKind())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
