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
"""Manual test bot: hikari + lightbulb + risa, against a real Discord app.

Run it with::

    BOT_TOKEN=... TEST_GUILD=... uv run --group bot python testbot/bot.py

``TEST_GUILD`` is optional but scopes the command to one guild, where it
appears immediately instead of taking up to an hour.

``/test`` posts a Components V2 message. risa cannot render or route yet, so
the components are built with hikari's builders and clicking one only shows up
as risa's DEBUG line saying it saw the interaction and could not route it --
which is the end-to-end proof that the gateway subscription is live. Replace
the hand-built tree with ``render()`` as the library grows.
"""

from __future__ import annotations

import logging
import os

import hikari
import lightbulb
import msgspec

import risa

TOKEN = os.environ["BOT_TOKEN"]
GUILDS = [int(guild) for guild in os.environ.get("TEST_GUILD", "").split(",") if guild.strip()]

bot = hikari.GatewayBot(TOKEN, logs="INFO", intents=hikari.Intents.ALL_UNPRIVILEGED)
lightbulb_client = lightbulb.client_from_app(bot, default_enabled_guilds=GUILDS)
risa_client = risa.client_from_lightbulb(lightbulb_client, use_global=True)

logging.getLogger("risa").setLevel(logging.DEBUG)
bot.subscribe(hikari.StartingEvent, lightbulb_client.start)


@risa.register(name="counter", version=1)
class Counter(risa.View):
    """Bounded state, the case ``InMessage`` placement is the default for."""

    label: str
    count: int = 0


@risa.register(name="poll", version=1)
class Poll(risa.View):
    """State that grows with use, which is why placement is a per-view choice."""

    question: str
    votes: dict[str, int] = msgspec.field(default_factory=dict)


@lightbulb_client.register()
class Test(lightbulb.SlashCommand, name="test", description="Post the components under test."):
    """Post a V2 message and report what risa currently knows."""

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        """Answer with the tree ``Counter.render()`` will eventually produce."""
        counter = Counter(label="Clicks")
        await ctx.respond(
            components=[
                hikari.impl.ContainerComponentBuilder(
                    accent_color=hikari.Color(0x5865F2),
                    components=[
                        hikari.impl.TextDisplayComponentBuilder(
                            content=f"## {counter.label}: {counter.count}\n"
                            f"`{Counter.__risa_view_meta__.key}` and "
                            f"`{Poll.__risa_view_meta__.key}` are registered.",
                        ),
                        hikari.impl.SeparatorComponentBuilder(divider=True),
                        hikari.impl.SectionComponentBuilder(
                            components=[hikari.impl.TextDisplayComponentBuilder(content="A section accessory button.")],
                            accessory=hikari.impl.InteractiveButtonBuilder(
                                style=hikari.ButtonStyle.SECONDARY,
                                custom_id="testbot:accessory",
                                label="Accessory",
                            ),
                        ),
                        hikari.impl.MessageActionRowBuilder(
                            components=[
                                hikari.impl.InteractiveButtonBuilder(
                                    style=hikari.ButtonStyle.PRIMARY,
                                    custom_id="testbot:increment",
                                    label="+1",
                                ),
                                hikari.impl.InteractiveButtonBuilder(
                                    style=hikari.ButtonStyle.DANGER,
                                    custom_id="testbot:close",
                                    label="Close",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )


if __name__ == "__main__":
    bot.run()
