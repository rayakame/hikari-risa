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
from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import time
import typing
import unittest.mock

import fakeredis
import hikari
import linkd
import pytest

import risa
from risa import ui
from risa.internal import registry
from risa.store.redis import RedisStore
from risa.ui import build as build_

if typing.TYPE_CHECKING:
    import collections.abc

SENT = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
INTERACTION_IDS = itertools.count(1)
APPLICATION_IDS = itertools.count(1)
ACTIONS: list[str] = []


class GatewayApp:
    def __init__(self) -> None:
        self.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
        self.event_manager = unittest.mock.Mock(spec=hikari.api.EventManager)


@risa.register(name="prefab-panel")
class Panel(risa.View):
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.press), ui.Button(self.hold), ui.Button(self.fail), ui.Button(self.claim))

    @risa.handler
    async def press(self, _ctx: risa.ComponentContext) -> None: ...

    @risa.handler
    async def hold(self, _ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await Gate.event.wait()

    @risa.handler
    async def fail(self, _ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        raise RuntimeError

    @risa.handler
    async def claim(self, _ctx: risa.ComponentContext, cooldown: risa.prefab.Cooldown = risa.INJECTED) -> None:  # ruff:ignore[no-self-use]
        for action in ACTIONS:
            await getattr(cooldown, action)()


class Gate:
    event: typing.ClassVar[asyncio.Event]


class Clock:
    def __init__(self) -> None:
        self.now = SENT.timestamp()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(time, "time", lambda: clock.now)  # ruff:ignore[shared-clock-mock]
    return clock


def context(
    *,
    user: int = 1,
    guild: int | None = 100,
    permissions: hikari.Permissions = hikari.Permissions.NONE,
    bot_permissions: hikari.Permissions = hikari.Permissions.NONE,
    roles: collections.abc.Sequence[int] = (),
    invoker: int | None = None,
    edited: dt.datetime | None = None,
) -> unittest.mock.Mock:
    ctx = unittest.mock.Mock(spec=risa.ComponentContext)
    ctx.user.id = hikari.Snowflake(user)
    ctx.guild_id = None if guild is None else hikari.Snowflake(guild)
    ctx.member = None
    if guild is not None:
        ctx.member = unittest.mock.Mock(
            spec=hikari.InteractionMember,
            permissions=permissions,
            role_ids=[hikari.Snowflake(role) for role in roles],
        )
    ctx.interaction.app_permissions = bot_permissions
    ctx.interaction.application_id = hikari.Snowflake(next(APPLICATION_IDS))
    ctx.message.timestamp = SENT
    ctx.message.edited_timestamp = edited
    ctx.message.interaction_metadata = None
    if invoker is not None:
        ctx.message.interaction_metadata = unittest.mock.Mock(spec=hikari.PartialInteractionMetadata)
        ctx.message.interaction_metadata.user.id = hikari.Snowflake(invoker)
    return ctx


async def run(hook: risa.ExecutionHook[risa.View], ctx: unittest.mock.Mock) -> None:
    await hook(risa.ExecutionPipeline(), ctx, None)


def owned_by(ctx: unittest.mock.Mock, owner: int, *team: int) -> None:
    application = ctx.rest.fetch_application.return_value
    application.owner.id = hikari.Snowflake(owner)
    application.team = None
    if team:
        application.team = unittest.mock.Mock(
            members={
                hikari.Snowflake(member): unittest.mock.Mock(
                    id=hikari.Snowflake(member), membership_state=hikari.TeamMembershipState.ACCEPTED
                )
                for member in team
            }
        )
    ctx.rest.fetch_application = unittest.mock.AsyncMock(return_value=application)


async def test_owner_only_lets_the_owner_through() -> None:
    ctx = context(user=7)
    owned_by(ctx, 7)

    await run(risa.prefab.owner_only, ctx)


async def test_owner_only_lets_a_member_of_the_owning_team_through() -> None:
    ctx = context(user=8)
    owned_by(ctx, 7, 8, 9)

    await run(risa.prefab.owner_only, ctx)


async def test_owner_only_turns_away_a_team_member_who_has_not_accepted_yet() -> None:
    ctx = context(user=8)
    owned_by(ctx, 7, 8)
    ctx.rest.fetch_application.return_value.team.members[8].membership_state = hikari.TeamMembershipState.INVITED

    with pytest.raises(risa.NotOwnerError):
        await run(risa.prefab.owner_only, ctx)


async def test_owner_only_turns_everyone_else_away() -> None:
    ctx = context(user=5)
    owned_by(ctx, 7)

    with pytest.raises(risa.NotOwnerError) as raised:
        await run(risa.prefab.owner_only, ctx)

    assert raised.value.user_id == 5


async def test_owner_only_asks_discord_for_the_owners_once() -> None:
    ctx = context(user=7)
    owned_by(ctx, 7)

    await run(risa.prefab.owner_only, ctx)
    await run(risa.prefab.owner_only, ctx)

    assert ctx.rest.fetch_application.await_count == 1


async def test_invoker_only_lets_the_user_through_whose_interaction_sent_the_message() -> None:
    await run(risa.prefab.invoker_only, context(user=3, invoker=3))


async def test_invoker_only_turns_everyone_else_away() -> None:
    with pytest.raises(risa.NotInvokerError) as raised:
        await run(risa.prefab.invoker_only, context(user=4, invoker=3))

    assert (raised.value.user_id, raised.value.invoker_id) == (4, 3)


async def test_invoker_only_turns_everyone_away_from_a_message_no_interaction_sent() -> None:
    with pytest.raises(risa.NotInvokerError, match="no interaction sent the message") as raised:
        await run(risa.prefab.invoker_only, context(user=3))

    assert raised.value.invoker_id is None


BAN = hikari.Permissions.BAN_MEMBERS
KICK = hikari.Permissions.KICK_MEMBERS
ADMIN = hikari.Permissions.ADMINISTRATOR


async def test_has_permissions_lets_a_user_with_all_of_them_through() -> None:
    await run(risa.prefab.has_permissions(BAN | KICK), context(permissions=BAN | KICK))


async def test_has_permissions_names_what_the_user_lacks() -> None:
    with pytest.raises(risa.MissingPermissionsError) as raised:
        await run(risa.prefab.has_permissions(BAN | KICK), context(permissions=KICK))

    assert (raised.value.missing, raised.value.actual) == (BAN, KICK)


async def test_has_permissions_lets_an_administrator_through_unless_told_otherwise() -> None:
    await run(risa.prefab.has_permissions(BAN), context(permissions=ADMIN))
    with pytest.raises(risa.MissingPermissionsError):
        await run(risa.prefab.has_permissions(BAN, permit_admin=False), context(permissions=ADMIN))


async def test_has_permissions_turns_a_direct_message_away_unless_told_otherwise() -> None:
    await run(risa.prefab.has_permissions(BAN, fail_in_dm=False), context(guild=None))
    with pytest.raises(risa.MissingPermissionsError) as raised:
        await run(risa.prefab.has_permissions(BAN), context(guild=None))

    assert (raised.value.missing, raised.value.actual) == (BAN, hikari.Permissions.NONE)


async def test_bot_has_permissions_lets_a_click_through_where_the_bot_has_all_of_them() -> None:
    await run(risa.prefab.bot_has_permissions(BAN), context(bot_permissions=BAN | KICK))
    await run(risa.prefab.bot_has_permissions(BAN), context(bot_permissions=ADMIN))


async def test_bot_has_permissions_names_what_the_bot_lacks() -> None:
    with pytest.raises(risa.BotMissingPermissionsError) as raised:
        await run(risa.prefab.bot_has_permissions(BAN | KICK), context(bot_permissions=KICK))

    assert (raised.value.missing, raised.value.actual) == (BAN, KICK)


async def test_bot_has_permissions_turns_a_direct_message_away_unless_told_otherwise() -> None:
    await run(risa.prefab.bot_has_permissions(BAN, fail_in_dm=False), context(guild=None, bot_permissions=BAN))
    with pytest.raises(risa.BotMissingPermissionsError):
        await run(risa.prefab.bot_has_permissions(BAN), context(guild=None, bot_permissions=BAN))
    with pytest.raises(risa.BotMissingPermissionsError):
        await run(risa.prefab.bot_has_permissions(BAN, permit_admin=False), context(bot_permissions=ADMIN))


async def test_has_roles_needs_every_role_by_default() -> None:
    await run(risa.prefab.has_roles(1, 2), context(roles=[1, 2, 3]))
    with pytest.raises(risa.MissingRolesError) as raised:
        await run(risa.prefab.has_roles(1, 2), context(roles=[1]))

    assert raised.value.role_ids == [2]


async def test_has_roles_in_any_mode_needs_one_of_them() -> None:
    await run(risa.prefab.has_roles(1, 2, mode="any"), context(roles=[2]))
    with pytest.raises(risa.MissingRolesError) as raised:
        await run(risa.prefab.has_roles(1, 2, mode="any"), context(roles=[3]))

    assert raised.value.role_ids == [1, 2]


@pytest.mark.parametrize("mode", ["all", "any"])
def test_has_roles_without_a_role_is_a_declaration_error(mode: typing.Literal["all", "any"]) -> None:
    with pytest.raises(risa.HookDeclarationError, match="has_roles") as caught:
        risa.prefab.has_roles(mode=mode)
    assert caught.value.reason == "it needs at least one role"


async def test_has_roles_treats_an_unknown_mode_as_all() -> None:
    mode = typing.cast('typing.Literal["all"]', "al")
    with pytest.raises(risa.MissingRolesError):
        await run(risa.prefab.has_roles(1, 2, mode=mode), context(roles=[1]))


async def test_has_roles_turns_a_direct_message_away_unless_told_otherwise() -> None:
    await run(risa.prefab.has_roles(1, fail_in_dm=False), context(guild=None))
    with pytest.raises(risa.MissingRolesError):
        await run(risa.prefab.has_roles(1), context(guild=None))


async def test_expires_after_counts_from_when_the_message_was_sent(clock: Clock) -> None:
    hook = risa.prefab.expires_after(60)

    clock.now += 60
    await run(hook, context())
    clock.now += 1
    with pytest.raises(risa.ComponentExpiredError) as raised:
        await run(hook, context())

    assert (raised.value.age, raised.value.lifetime) == (61, 60)


async def test_expires_after_counts_again_from_an_edit(clock: Clock) -> None:
    clock.now += 500

    await run(risa.prefab.expires_after(60), context(edited=SENT + dt.timedelta(seconds=450)))


def client_with(
    *hooks: risa.ExecutionHook[risa.View],
    store: risa.Store | None = None,
) -> tuple[risa.GatewayEnabledClient, list[BaseException]]:
    built = risa.client_from_app(
        typing.cast("risa.GatewayClientAppT", GatewayApp()), hooks=hooks, store=store, key_prefix="test"
    )
    built.add_view(Panel)
    ACTIONS.clear()
    Gate.event = asyncio.Event()
    errors: list[BaseException] = []

    @built.error_handler
    async def on_error(pipeline: risa.ExecutionPipeline, _ctx: risa.ComponentContext) -> bool:  # ruff:ignore[unused-async]
        errors.extend(failure.error for failure in pipeline.failures)
        return True

    return built, errors


async def click(
    built: risa.Client,
    button: str = "press",
    *,
    user: int = 1,
    channel: int = 10,
    guild: int | None = 100,
    message: int = 1000,
    roles: collections.abc.Sequence[int] = (),
) -> None:
    meta = registry.require_meta(Panel)
    ids = [
        typing.cast("str", leaf["custom_id"])
        for builder in build_.plan(Panel().render(), meta).emit(b"")
        for leaf in typing.cast("list[dict[str, object]]", builder.build()[0]["components"])
    ]
    interaction = unittest.mock.Mock(spec=hikari.ComponentInteraction)
    interaction.custom_id = ids[["press", "hold", "fail", "claim"].index(button)]
    interaction.id = hikari.Snowflake(next(INTERACTION_IDS))
    interaction.application_id = hikari.Snowflake(42)
    interaction.user.id = hikari.Snowflake(user)
    interaction.member.role_ids = [hikari.Snowflake(role) for role in roles]
    interaction.channel_id = hikari.Snowflake(channel)
    interaction.guild_id = None if guild is None else hikari.Snowflake(guild)
    interaction.message = unittest.mock.Mock(
        spec=hikari.Message, id=hikari.Snowflake(message), flags=hikari.MessageFlag.NONE, components=[]
    )
    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


async def test_a_fixed_window_allows_its_clicks_and_then_says_how_long_to_wait(clock: Clock) -> None:
    built, errors = client_with(risa.prefab.fixed_window(60, 2, "user"))

    await click(built)
    clock.now += 20
    await click(built)
    clock.now += 10
    await click(built)

    assert [type(error) for error in errors] == [risa.OnCooldownError]
    assert typing.cast("risa.OnCooldownError", errors[0]).remaining == 30


async def test_a_fixed_window_starts_over_once_it_has_closed(clock: Clock) -> None:
    built, errors = client_with(risa.prefab.fixed_window(60, 1, "user"))

    await click(built)
    clock.now += 60
    await click(built)
    await click(built)

    assert len(errors) == 1


async def test_a_sliding_window_frees_a_use_when_its_oldest_click_leaves(clock: Clock) -> None:
    built, errors = client_with(risa.prefab.sliding_window(60, 2, "user"))

    await click(built)
    clock.now += 40
    await click(built)
    clock.now += 10
    await click(built)
    clock.now += 10
    await click(built)
    await click(built)

    assert [type(error) for error in errors] == [risa.OnCooldownError, risa.OnCooldownError]
    assert [typing.cast("risa.OnCooldownError", error).remaining for error in errors] == [10, 40]


@pytest.mark.parametrize(
    ("bucket", "second", "shared"),
    [
        ("global", {"user": 2, "channel": 11, "guild": 101, "message": 1001}, True),
        ("user", {"channel": 11, "guild": 101, "message": 1001}, True),
        ("user", {"user": 2}, False),
        ("channel", {"user": 2, "message": 1001}, True),
        ("channel", {"channel": 11}, False),
        ("guild", {"user": 2, "channel": 11}, True),
        ("guild", {"guild": 101}, False),
        ("guild", {"guild": None}, False),
        ("message", {"user": 2}, True),
        ("message", {"message": 1001}, False),
    ],
)
async def test_a_bucket_counts_clicks_together_that_share_it(
    bucket: typing.Literal["global", "user", "channel", "guild", "message"],
    second: dict[str, typing.Any],
    shared: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    clock: Clock,
) -> None:
    del clock
    built, errors = client_with(risa.prefab.fixed_window(60, 1, bucket))

    await click(built)
    await click(built, **second)

    assert bool(errors) is shared


async def test_the_guild_bucket_counts_a_direct_message_by_its_channel(clock: Clock) -> None:
    del clock
    built, errors = client_with(risa.prefab.fixed_window(60, 1, "guild"))

    await click(built, guild=None, channel=10)
    await click(built, guild=None, channel=11)
    await click(built, guild=None, channel=10)

    assert len(errors) == 1


async def test_a_bucket_can_be_a_function_sync_or_async(clock: Clock) -> None:
    del clock

    async def by_parity(ctx: risa.ComponentContext) -> str:  # ruff:ignore[unused-async]
        return "even" if ctx.user.id % 2 == 0 else "odd"

    def by_remainder(ctx: risa.ComponentContext) -> int:
        return ctx.user.id % 2

    for bucket in (by_parity, by_remainder):
        built, errors = client_with(risa.prefab.fixed_window(60, 1, bucket))

        await click(built, user=1)
        await click(built, user=2)
        await click(built, user=3)

        assert len(errors) == 1


async def test_a_cooldown_is_not_used_up_by_a_click_a_check_turned_away(clock: Clock) -> None:
    del clock
    built, errors = client_with(risa.prefab.has_roles(1), risa.prefab.fixed_window(60, 1, "user"))

    await click(built)
    await click(built, roles=[1])
    await click(built, roles=[1])

    assert [type(error) for error in errors] == [risa.MissingRolesError, risa.OnCooldownError]


async def test_with_two_cooldowns_on_a_click_the_handler_gets_the_one_that_ran_last(clock: Clock) -> None:
    del clock
    built, errors = client_with(risa.prefab.fixed_window(60, 1, "user"), risa.prefab.sliding_window(60, 2, "user"))
    other, other_errors = client_with(
        risa.prefab.sliding_window(60, 1, "user"), risa.prefab.fixed_window(60, 2, "user")
    )
    ACTIONS.append("undo")

    for _ in range(2):
        await click(built, "claim")
        await click(other, "claim")

    assert [type(error) for error in errors] == [risa.OnCooldownError]
    assert [type(error) for error in other_errors] == [risa.OnCooldownError]


async def test_each_call_makes_a_cooldown_of_its_own(clock: Clock) -> None:
    del clock
    built, errors = client_with(risa.prefab.fixed_window(60, 1, "user"), risa.prefab.sliding_window(60, 1, "user"))
    other, other_errors = client_with(risa.prefab.fixed_window(60, 1, "user"))

    await click(built)
    await click(other)

    assert not errors
    assert not other_errors


@pytest.mark.parametrize("make", [risa.prefab.fixed_window, risa.prefab.sliding_window])
@pytest.mark.parametrize(
    ("actions", "rejected"),
    [([], 1), (["undo"], 0), (["reset"], 0), (["undo", "undo"], 0), (["apply"], 2)],
)
async def test_a_handler_can_take_the_cooldown_back_or_apply_it_again(
    make: collections.abc.Callable[..., risa.ExecutionHook[risa.View]],
    actions: list[str],
    rejected: int,
    clock: Clock,
) -> None:
    del clock
    built, errors = client_with(make(60, 2, "user"))
    await click(built)
    ACTIONS.extend(actions)

    await click(built, "claim")
    ACTIONS.clear()
    await click(built)

    assert len(errors) == rejected


@pytest.mark.parametrize("make", [risa.prefab.fixed_window, risa.prefab.sliding_window])
async def test_applying_a_cooldown_that_is_used_up_raises(
    make: collections.abc.Callable[..., risa.ExecutionHook[risa.View]], clock: Clock
) -> None:
    del clock
    built, errors = client_with(make(60, 1, "user"))
    ACTIONS.append("apply")

    await click(built, "claim")

    assert [type(error) for error in errors] == [risa.OnCooldownError]


@pytest.mark.parametrize("make", [risa.prefab.fixed_window, risa.prefab.sliding_window])
async def test_undoing_a_cooldown_with_nothing_to_take_back_changes_nothing(
    make: collections.abc.Callable[..., risa.ExecutionHook[risa.View]], clock: Clock
) -> None:
    del clock
    built, errors = client_with(make(60, 1, "user"))
    ACTIONS.extend(["reset", "undo"])

    await click(built, "claim")
    await click(built)

    assert not errors


async def test_a_cooldown_counts_in_the_clients_store_under_its_key(clock: Clock) -> None:
    del clock
    store = risa.MemoryStore(warn=False)
    built, errors = client_with(risa.prefab.fixed_window(60, 1, "user", store="default", key="refresh"), store=store)
    other, other_errors = client_with(
        risa.prefab.fixed_window(60, 1, "user", store="default", key="refresh"), store=store
    )

    await click(built, user=5)
    await click(other, user=5)

    assert await store.get_versioned("test:hook:fixed_window:refresh:5") is not None
    assert not errors
    assert [type(error) for error in other_errors] == [risa.OnCooldownError]


async def test_a_store_key_starts_with_the_application_id_without_a_key_prefix(clock: Clock) -> None:
    del clock
    store = risa.MemoryStore(warn=False)
    built = risa.client_from_app(
        typing.cast("risa.GatewayClientAppT", GatewayApp()),
        hooks=[risa.prefab.sliding_window(60, 1, "global", store="default", key="refresh")],
        store=store,
    )
    built.add_view(Panel)

    await click(built)

    assert await store.get_versioned("risa:42:hook:sliding_window:refresh:0") is not None


@pytest.mark.parametrize("given", [{"store": "default"}, {"key": "refresh"}])
def test_a_store_and_a_key_only_come_together(given: dict[str, str]) -> None:
    with pytest.raises(risa.HookDeclarationError, match="pass store= and key= together"):
        risa.prefab.fixed_window(60, 1, "user", **given)
    with pytest.raises(risa.HookDeclarationError, match="pass store= and key= together"):
        risa.prefab.sliding_window(60, 1, "user", **given)
    with pytest.raises(risa.HookDeclarationError, match="pass store= and key= together"):
        risa.prefab.max_concurrency(1, "user", **given)


def test_an_unknown_bucket_is_a_declaration_error() -> None:
    bucket = typing.cast("typing.Literal['user']", "usr")

    with pytest.raises(risa.HookDeclarationError, match="'usr' is no bucket") as caught:
        risa.prefab.fixed_window(60, 1, bucket)
    with pytest.raises(risa.HookDeclarationError, match="'usr' is no bucket"):
        risa.prefab.sliding_window(60, 1, bucket)
    with pytest.raises(risa.HookDeclarationError, match="'usr' is no bucket"):
        risa.prefab.max_concurrency(1, bucket)

    assert caught.value.hook_name == "fixed_window"
    assert "'global', 'user', 'channel', 'guild', 'message'" in caught.value.reason


def test_a_store_needs_dependency_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linkd, "DI_ENABLED", False)

    with pytest.raises(risa.HookDeclarationError, match="store= needs dependency injection"):
        risa.prefab.fixed_window(60, 1, "user", store="default", key="refresh")


async def test_a_store_the_client_lacks_fails_the_click() -> None:
    built, errors = client_with(risa.prefab.fixed_window(60, 1, "user", store="redis", key="refresh"))

    await click(built)

    assert [str(error) for error in errors] == [
        "hook 'fixed_window': it counts in the store 'redis', which the client was not given"
    ]


async def test_a_store_that_fails_fails_the_click_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(_store: risa.MemoryStore, _key: str) -> None:  # ruff:ignore[unused-async]
        raise ConnectionError

    monkeypatch.setattr(risa.MemoryStore, "get_versioned", down)
    built, errors = client_with(
        risa.prefab.fixed_window(60, 1, "user", store="default", key="refresh"), store=risa.MemoryStore(warn=False)
    )

    await click(built, user=5)

    assert [type(error) for error in errors] == [risa.StoreUnavailableError]
    assert isinstance(errors[0].__cause__, ConnectionError)


async def test_max_concurrency_turns_a_click_away_while_as_many_as_allowed_run() -> None:
    built, errors = client_with(*risa.prefab.max_concurrency(1, "user"))
    running = asyncio.create_task(click(built, "hold"))
    await asyncio.sleep(0)

    await click(built)
    await click(built, user=2)

    assert [type(error) for error in errors] == [risa.MaxConcurrencyReachedError]
    assert typing.cast("risa.MaxConcurrencyReachedError", errors[0]).limit == 1
    Gate.event.set()
    await running


async def test_a_sliding_window_that_allows_nothing_turns_every_click_away(clock: Clock) -> None:
    del clock
    built, errors = client_with(risa.prefab.sliding_window(60, 0, "user"))

    await click(built)

    assert [typing.cast("risa.OnCooldownError", error).remaining for error in errors] == [60]


async def test_two_kinds_of_hook_can_share_a_key(clock: Clock) -> None:
    del clock
    store = risa.MemoryStore(warn=False)
    built, errors = client_with(
        risa.prefab.fixed_window(60, 1, "user", store="default", key="shared"),
        risa.prefab.sliding_window(60, 1, "user", store="default", key="shared"),
        *risa.prefab.max_concurrency(1, "user", store="default", key="shared"),
        store=store,
    )

    await click(built)

    assert not errors


async def test_a_cooldown_counts_across_processes_on_redis(clock: Clock) -> None:
    del clock
    connection = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer())
    clients = [
        client_with(
            risa.prefab.sliding_window(60, 3, "user", store="default", key="burst"), store=RedisStore(connection)
        )
        for _ in range(3)
    ]

    await asyncio.gather(*(click(built) for built, _ in clients for _ in range(4)))

    assert sum(len(errors) for _, errors in clients) == 9
    await connection.aclose()


async def test_max_concurrency_attached_twice_takes_one_slot_for_a_click() -> None:
    pair = risa.prefab.max_concurrency(1, "user")
    built, errors = client_with(*pair, *pair)

    await click(built)
    await click(built)

    assert not errors


async def test_max_concurrency_frees_the_slot_of_a_click_a_check_turned_away() -> None:
    built, errors = client_with(*risa.prefab.max_concurrency(1, "user"), risa.prefab.has_roles(1))

    await click(built)
    await click(built, roles=[1])

    assert [type(error) for error in errors] == [risa.MissingRolesError]


async def test_max_concurrency_frees_the_slot_when_the_click_is_over() -> None:
    built, errors = client_with(*risa.prefab.max_concurrency(1, "user"))

    await click(built)
    await click(built, "fail")
    await click(built)

    assert [type(error) for error in errors] == [RuntimeError]


async def test_a_click_max_concurrency_turned_away_frees_no_slot() -> None:
    built, errors = client_with(*risa.prefab.max_concurrency(1, "user"))
    running = asyncio.create_task(click(built, "hold"))
    await asyncio.sleep(0)

    await click(built)
    await click(built)

    assert len(errors) == 2
    Gate.event.set()
    await running


async def test_a_slot_that_was_never_given_back_frees_itself_after_fifteen_minutes(clock: Clock) -> None:
    built, errors = client_with(risa.prefab.max_concurrency(1, "user")[0])

    await click(built)
    clock.now += 899
    await click(built)
    clock.now += 1
    await click(built)

    assert len(errors) == 1


async def test_max_concurrency_counts_in_the_clients_store_under_its_key() -> None:
    store = risa.MemoryStore(warn=False)
    built, errors = client_with(*risa.prefab.max_concurrency(1, "global", store="default", key="export"), store=store)
    other, other_errors = client_with(
        *risa.prefab.max_concurrency(1, "global", store="default", key="export"), store=store
    )
    running = asyncio.create_task(click(built, "hold"))
    await asyncio.sleep(0)

    await click(other)
    Gate.event.set()
    await running
    await click(other)

    assert not errors
    assert [type(error) for error in other_errors] == [risa.MaxConcurrencyReachedError]
