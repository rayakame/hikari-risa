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
"""Hooks: a check only the poll's author passes, its answer, and an audit after every click."""

from __future__ import annotations

import logging
import os

import hikari

import risa
from risa import ui

AUDIT = logging.getLogger("bot.audit")


# --8<-- [start:check]
class NotTheAuthorError(Exception):
    """Someone other than the author tried to close the poll."""


@risa.hook(risa.ExecutionSteps.CHECKS)
async def author_only(_pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext, poll: Poll) -> None:
    if ctx.user.id != poll.author_id:
        raise NotTheAuthorError
    # --8<-- [end:check]


# --8<-- [start:view]
@risa.register(name="closable-poll")
class Poll(risa.View):
    question: str
    author_id: int
    yes: int = 0
    no: int = 0
    closed: bool = False

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"## {self.question}" + (" (closed)" if self.closed else "")),
            ui.Row(
                ui.Button(self.vote_yes, label=f"Yes: {self.yes}", disabled=self.closed),
                ui.Button(self.vote_no, label=f"No: {self.no}", disabled=self.closed),
                ui.Button(self.close, label="Close", style=hikari.ButtonStyle.DANGER, disabled=self.closed),
            ),
        )

    @risa.handler
    async def vote_yes(self, ctx: risa.ComponentContext) -> None:
        if not self.closed:
            self.yes += 1
        await ctx.rerender()

    @risa.handler
    async def vote_no(self, ctx: risa.ComponentContext) -> None:
        if not self.closed:
            self.no += 1
        await ctx.rerender()

    # --8<-- [start:attach]
    @risa.handler(hooks=[author_only])
    async def close(self, ctx: risa.ComponentContext) -> None:
        self.closed = True
        await ctx.rerender()
        # --8<-- [end:attach]
        # --8<-- [end:view]


# --8<-- [start:answer]
async def not_the_author(pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext) -> bool:
    if pipeline.find(NotTheAuthorError) is None:
        return False
    await ctx.respond("Only the author can close this poll.", ephemeral=True)
    return True
    # --8<-- [end:answer]


# --8<-- [start:audit]
@risa.hook(risa.ExecutionSteps.POST_INVOKE)
async def audit(pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext) -> None:
    outcome = "failed" if pipeline.failed else "ran"
    AUDIT.info("%s clicked on message %s, which %s", ctx.user.id, ctx.message.id, outcome)
    # --8<-- [end:audit]


# --8<-- [start:client]
def make_client(bot: hikari.GatewayBot) -> risa.GatewayEnabledClient:
    client = risa.client_from_app(bot, hooks=[audit])
    client.add_view(Poll)
    # --8<-- [start:register]
    client.error_handler(not_the_author)
    # --8<-- [end:register]
    return client
    # --8<-- [end:client]


# --8<-- [start:order]
LAST = risa.ExecutionStep("LAST")


@risa.hook(LAST)
async def audit_last(pipeline: risa.ExecutionPipeline, ctx: risa.ComponentContext) -> None:
    outcome = "failed" if pipeline.failed else "ran"
    AUDIT.info("%s clicked on message %s, which %s", ctx.user.id, ctx.message.id, outcome)


def make_client_that_audits_last(bot: hikari.GatewayBot) -> risa.GatewayEnabledClient:
    client = risa.client_from_app(
        bot,
        hooks=[audit_last],
        execution_step_order=[*risa.DEFAULT_EXECUTION_STEP_ORDER, LAST],
    )
    client.add_view(Poll)
    client.error_handler(not_the_author)
    return client
    # --8<-- [end:order]


# --8<-- [start:test]
async def test_author_only_lets_the_author_through(ctx: risa.ComponentContext) -> None:
    poll = Poll(question="Pizza on Friday?", author_id=ctx.user.id)
    await author_only(risa.ExecutionPipeline(), ctx, poll)
    # --8<-- [end:test]


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    client = make_client(bot)

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!poll":
            poll = Poll(question="Pizza on Friday?", author_id=event.author.id)
            rendered = await client.build(poll)
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
