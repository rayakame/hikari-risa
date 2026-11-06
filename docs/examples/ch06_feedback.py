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
"""A feedback message that shows what else injection can do."""

from __future__ import annotations

import dataclasses
import os

import hikari
import linkd

import risa
from risa import ui

_LOG_CHANNEL = hikari.Snowflake(123456789)
_VOTES: list[hikari.Snowflake] = []


class Database:
    def __init__(self, name: str) -> None:
        self.name = name

    async def add_vote(self, user: hikari.Snowflake) -> None:
        _VOTES.append(user)

    async def count_votes(self) -> int:
        return len(_VOTES)


class Metrics:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def count(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1


@dataclasses.dataclass
class Reason:
    text: str


# --8<-- [start:types]
class Primary(Database):
    pass


class Replica(Database):
    pass


def add_databases(client: risa.Client) -> None:
    registry = client.di.registry_for(risa.Contexts.DEFAULT)
    registry.register_value(Primary, Primary("primary"))
    registry.register_value(Replica, Replica("replica"))
    # --8<-- [end:types]


@linkd.inject
async def record(
    reason: Reason = risa.INJECTED,
    rest: hikari.api.RESTClient = risa.INJECTED,
) -> None:
    await rest.create_message(_LOG_CHANNEL, reason.text)


@risa.register(name="feedback")
class Feedback(risa.View):
    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay("Did this help?"),
            ui.Row(
                ui.Button(self.vote, label="Yes"),
                ui.Button(self.like, label="Love it"),
                ui.Button(self.report, label="Report a problem"),
                ui.Button(self.flag, label="Flag"),
            ),
        )

    # --8<-- [start:vote]
    @risa.handler
    async def vote(
        self,
        ctx: risa.ComponentContext,
        primary: Primary,
        replica: Replica,
    ) -> None:
        await primary.add_vote(ctx.user.id)
        total = await replica.count_votes()
        await ctx.respond(f"{total} votes so far.", ephemeral=True)
        # --8<-- [end:vote]

    # --8<-- [start:like]
    @risa.handler
    async def like(self, ctx: risa.ComponentContext, metrics: Metrics | None) -> None:
        if metrics is not None:
            metrics.count("likes")
        await ctx.respond("Thanks!", ephemeral=True)
        # --8<-- [end:like]

    # --8<-- [start:report]
    @risa.handler
    async def report(
        self,
        ctx: risa.ComponentContext,
        rest: hikari.api.RESTClient,
    ) -> None:
        await rest.create_message(_LOG_CHANNEL, f"{ctx.user.username} reported a problem.")
        await ctx.respond("Thanks, we'll take a look.", ephemeral=True)
        # --8<-- [end:report]

    # --8<-- [start:flag]
    @risa.handler
    async def flag(
        self,
        ctx: risa.ComponentContext,
        container: risa.ComponentContainer,
    ) -> None:
        container.add_value(Reason, Reason(f"Flagged by {ctx.user.username}."))
        await record()
        await ctx.respond("Flagged.", ephemeral=True)
        # --8<-- [end:flag]


# --8<-- [start:outside]
@linkd.inject
async def post_votes(
    rest: hikari.api.RESTClient = risa.INJECTED,
    replica: Replica = risa.INJECTED,
) -> None:
    total = await replica.count_votes()
    await rest.create_message(_LOG_CHANNEL, f"{total} votes so far.")


def post_on_start(bot: hikari.GatewayBot, client: risa.Client) -> None:
    @bot.listen()
    async def on_started(_: hikari.StartedEvent) -> None:
        async with client.di.enter_context(risa.Contexts.DEFAULT):
            await post_votes()
            # --8<-- [end:outside]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = risa.client_from_app(bot)
    add_databases(client)
    client.add_view(Feedback)
    post_on_start(bot, client)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!feedback":
            rendered = await client.build(Feedback())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
