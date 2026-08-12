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
"""Manual test bot: hikari + lightbulb + risa, against a real Discord app."""

from __future__ import annotations

import asyncio
import logging
import os
import dotenv
import hikari
import lightbulb

import risa
from risa import ui

dotenv.load_dotenv()

TOKEN = os.getenv("TOKEN")
assert TOKEN is not None

bot = hikari.GatewayBot(TOKEN, logs="INFO", intents=hikari.Intents.ALL)
lightbulb_client = lightbulb.client_from_app(bot)
risa_client = risa.client_from_lightbulb(lightbulb_client)

logging.getLogger("risa").setLevel(logging.DEBUG)
bot.subscribe(hikari.StartingEvent, lightbulb_client.start)


@risa.register(name="demo", version=1)
class Demo(risa.View):
  def render(self) -> ui.Layout:
      return ui.Container(
          ui.TextDisplay("## risa"),
          ui.Row(
              ui.Button(self.ping, label="Ping"),
              ui.Button(self.slow, label="Slow (4s)", style=hikari.ButtonStyle.SECONDARY),
          ),
          ui.Row(
              ui.TextSelect(
                  self.pick,
                  ui.SelectOption("Red"),
                  ui.SelectOption("Green"),
                  ui.SelectOption("Blue"),
                  placeholder="Pick a color",
              ),
          ),
          ui.Row(ui.UserSelect(self.who, placeholder="Pick a user", max_values=10)),
      )

  @risa.handler
  async def ping(self, ctx: risa.ComponentContext) -> None:
      await ctx.respond("pong", ephemeral=True)

  @risa.handler()
  async def slow(self, ctx: risa.ComponentContext) -> None:
      await asyncio.sleep(20)
      await ctx.respond("that took a while - the watchdog answered for me", ephemeral=True)

  @risa.handler
  async def pick(self, ctx: risa.ComponentContext) -> None:
      await ctx.respond(f"you picked {ctx.values[0]}", ephemeral=True)

  @risa.handler
  async def who(self, ctx: risa.ComponentContext) -> None:
      resolved = ctx.resolved
      names = "nobody"
      if resolved is not None and resolved.users:
          names = ", ".join(str(user) for user in resolved.users.values())
      await ctx.respond(f"you picked {names}", ephemeral=True)


risa_client.add_view(Demo)


@lightbulb_client.register()
class Test(lightbulb.SlashCommand, name="test", description="Post the components under test."):
    """Post a V2 message and report what risa currently knows."""

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        """Answer with the message ``Demo.render()`` produces."""
        await ctx.respond(components=await risa_client.build(Demo()))


if __name__ == "__main__":
    bot.run()
