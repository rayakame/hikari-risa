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
"""Ready-made hooks: a daily reward with a cooldown and an owner's restock, a slow export, and one answer for all."""

from __future__ import annotations

import asyncio
import os
import time

import hikari

import risa
from risa import ui

STOCK = 100

# --8<-- [start:store]
DAILY = risa.prefab.fixed_window(86400, 1, "user", store="default", key="daily-reward")
# --8<-- [end:store]


# --8<-- [start:view]
@risa.register(name="reward", hooks=[risa.prefab.expires_after(3600)])
class Reward(risa.View):
    claimed: int = 0

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"## Daily reward\n{STOCK - self.claimed} of {STOCK} left"),
            ui.Row(
                ui.Button(self.claim, label="Claim"),
                ui.Button(self.restock, label="Restock", style=hikari.ButtonStyle.DANGER),
            ),
        )

    @risa.handler(hooks=[risa.prefab.fixed_window(86400, 1, "user")])
    async def claim(self, ctx: risa.ComponentContext, cooldown: risa.prefab.Cooldown = risa.INJECTED) -> None:
        if self.claimed >= STOCK:
            await cooldown.undo()
            await ctx.respond("Nothing is left today.", ephemeral=True)
            return
        self.claimed += 1
        await ctx.rerender()

    @risa.handler(hooks=[risa.prefab.owner_only])
    async def restock(self, ctx: risa.ComponentContext) -> None:
        self.claimed = 0
        await ctx.rerender()
        # --8<-- [end:view]


# --8<-- [start:export]
@risa.register(name="reward-export")
class Export(risa.View):
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.export, label="Export the claims"))

    @risa.handler(hooks=[*risa.prefab.max_concurrency(1, "global")])
    async def export(self, ctx: risa.ComponentContext) -> None:
        await ctx.defer(thinking=True, ephemeral=True)
        await asyncio.sleep(5)
        await ctx.respond("The export is done.")
        # --8<-- [end:export]


# --8<-- [start:answer]
async def turned_away(pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext) -> bool:
    error = pipeline.find(risa.ClickRejectedError)
    if error is None:
        return False
    if isinstance(error, risa.OnCooldownError):
        text = f"Come back <t:{int(time.time() + error.remaining)}:R>."
    elif isinstance(error, risa.ComponentExpiredError):
        text = "This message has expired."
    else:
        text = "You cannot use this."
    await ctx.respond(text, ephemeral=True)
    return True
    # --8<-- [end:answer]


# --8<-- [start:client]
def make_client(bot: hikari.GatewayBot) -> risa.GatewayEnabledClient:
    client = risa.client_from_app(bot)
    client.add_view(Reward)
    client.add_view(Export)
    client.error_handler(turned_away)
    return client
    # --8<-- [end:client]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = make_client(bot)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!reward":
            for view in (Reward(), Export()):
                rendered = await client.build(view)
                await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
