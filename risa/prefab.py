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
"""Ready-made hooks: checks, cooldowns and a limit on clicks that run at once."""

from __future__ import annotations

import abc
import contextlib
import inspect
import time
import typing

import hikari
import linkd
import msgspec

from risa import client as client_
from risa import context
from risa import di
from risa import errors
from risa import hooks
from risa.store import memory

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import view as view_
    from risa.store import protocol

__all__ = (
    "Bucket",
    "BucketFunction",
    "Cooldown",
    "bot_has_permissions",
    "expires_after",
    "fixed_window",
    "has_permissions",
    "has_roles",
    "invoker_only",
    "max_concurrency",
    "owner_only",
    "sliding_window",
)

# Discord takes no answer to a click after 15 minutes, so no click holds its slot for longer.
_SLOT_LIFETIME: typing.Final[float] = 900.0

_MEMORY_BUCKETS: typing.Final[int] = 100_000

_BUCKETS: typing.Final[collections.abc.Mapping[str, BucketFunction]] = {
    "global": lambda _: 0,
    "user": lambda ctx: ctx.user.id,
    "channel": lambda ctx: ctx.channel_id,
    "guild": lambda ctx: ctx.guild_id or ctx.channel_id,
    "message": lambda ctx: ctx.message.id,
}

_OWNERS: typing.Final[dict[hikari.Snowflake, frozenset[hikari.Snowflake]]] = {}

type BucketFunction = collections.abc.Callable[
    [context.ComponentContext],
    int | str | collections.abc.Awaitable[int | str],
]
"""A function that names the bucket a click counts in, sync or async."""

type Bucket = typing.Literal["global", "user", "channel", "guild", "message"] | BucketFunction
"""What a cooldown or `max_concurrency` counts clicks by.

`"global"` counts every click together, `"user"` per user, `"channel"` per channel,
`"guild"` per guild, or per channel in a direct message, and `"message"` per message. A
`BucketFunction` returns the bucket itself.
"""


class _Uses(msgspec.Struct):
    count: int
    until: float


class _Entry:
    __slots__ = ("_key", "_store")

    def __init__(self, store: protocol.Store, key: str) -> None:
        self._store = store
        self._key = key

    async def read[T](self, kind: type[T]) -> T | None:
        found = await self._store.get_versioned(self._key)
        return None if found is None else msgspec.msgpack.decode(found.data, type=kind)

    async def write(self, value: _Uses | list[float], *, ttl: float) -> None:
        await self._store.put(self._key, msgspec.msgpack.encode(value), ttl=ttl)

    async def delete(self) -> None:
        await self._store.delete(self._key)


class _Ledger:
    """Where one hook counts: in a store of its own in this process, or under `key` in one of the client's."""

    __slots__ = ("_bucket", "_hook", "_key", "_memory", "_store")

    def __init__(self, hook: str, bucket: Bucket, store: str | None, key: str | None) -> None:
        if (store is None) != (key is None):
            reason = (
                "pass store= and key= together: the store to count in, and the name every process finds the count by"
            )
            raise errors.HookDeclarationError(hook, reason)
        if store is not None and not linkd.DI_ENABLED:
            reason = "store= needs dependency injection to reach the client, and LINKD_DI_DISABLED turned it off"
            raise errors.HookDeclarationError(hook, reason)
        self._hook = hook
        self._bucket = _BUCKETS[bucket] if isinstance(bucket, str) else bucket
        self._store = store
        self._key = key
        self._memory = memory.MemoryStore(max_entries=_MEMORY_BUCKETS, warn=False)

    @contextlib.asynccontextmanager
    async def open(
        self, ctx: context.ComponentContext, client: client_.Client
    ) -> collections.abc.AsyncGenerator[_Entry]:
        """Hold the click's bucket, so that reading and writing its entry is one step."""
        bucket = self._bucket(ctx)
        if inspect.isawaitable(bucket):
            bucket = await bucket
        store, key = self._store_for(client), str(bucket)
        if self._store is not None:
            prefix = client.key_prefix or f"risa:{ctx.interaction.application_id}"
            key = f"{prefix}:hook:{self._hook}:{self._key}:{bucket}"
        async with store.lock(key):
            yield _Entry(store, key)

    def _store_for(self, client: client_.Client) -> protocol.Store:
        if self._store is None:
            return self._memory
        found = client.stores.get(self._store)
        if found is None:
            reason = f"it counts in the store {self._store!r}, which the client was not given"
            raise errors.HookDeclarationError(self._hook, reason)
        return found


class _Cooldown(abc.ABC):
    __slots__ = ("_allowed", "_ledger", "_length")

    def __init__(self, ledger: _Ledger, window_length: float, allowed_invocations: int) -> None:
        self._ledger = ledger
        self._length = window_length
        self._allowed = allowed_invocations

    async def hook(
        self,
        _pipeline: hooks.ExecutionPipeline,
        ctx: context.ComponentContext,
        client: client_.Client = di.INJECTED,
        container: di.ComponentContainer = di.INJECTED,
    ) -> None:
        if linkd.DI_ENABLED:
            container.add_value(Cooldown, Cooldown(self, ctx, client))
        await self.apply(ctx, client)

    @abc.abstractmethod
    async def apply(self, ctx: context.ComponentContext, client: client_.Client) -> None: ...

    @abc.abstractmethod
    async def undo(self, ctx: context.ComponentContext, client: client_.Client) -> None: ...

    async def reset(self, ctx: context.ComponentContext, client: client_.Client) -> None:
        async with self._ledger.open(ctx, client) as entry:
            await entry.delete()


class _FixedWindow(_Cooldown):
    __slots__ = ()

    @typing.override
    async def apply(self, ctx: context.ComponentContext, client: client_.Client) -> None:
        async with self._ledger.open(ctx, client) as entry:
            now = time.time()
            uses = await entry.read(_Uses)
            if uses is None or uses.until <= now:
                uses = _Uses(0, now + self._length)
            if uses.count >= self._allowed:
                raise errors.OnCooldownError(uses.until - now)
            uses.count += 1
            await entry.write(uses, ttl=uses.until - now)

    @typing.override
    async def undo(self, ctx: context.ComponentContext, client: client_.Client) -> None:
        async with self._ledger.open(ctx, client) as entry:
            now = time.time()
            uses = await entry.read(_Uses)
            if uses is not None and uses.count > 0 and uses.until > now:
                uses.count -= 1
                await entry.write(uses, ttl=uses.until - now)


class _SlidingWindow(_Cooldown):
    __slots__ = ()

    @typing.override
    async def apply(self, ctx: context.ComponentContext, client: client_.Client) -> None:
        async with self._ledger.open(ctx, client) as entry:
            now = time.time()
            uses = [use for use in await entry.read(list[float]) or [] if use > now - self._length]
            if len(uses) >= self._allowed:
                raise errors.OnCooldownError(min(uses, default=now) + self._length - now)
            await entry.write([*uses, now], ttl=self._length)

    @typing.override
    async def undo(self, ctx: context.ComponentContext, client: client_.Client) -> None:
        async with self._ledger.open(ctx, client) as entry:
            uses = await entry.read(list[float])
            if uses:
                await entry.write(uses[:-1], ttl=self._length)


class Cooldown:
    """The cooldown that ran for this click, to take back, clear or apply again.

    A cooldown registers it for injection when it runs, so a handler, or a hook after it,
    can take `cooldown: risa.prefab.Cooldown`. With two cooldowns on one click it is the one
    that ran last. It needs dependency injection.

    ```python
    @risa.handler(hooks=[risa.prefab.fixed_window(60, 1, "user")])
    async def claim(
        self,
        ctx: risa.ComponentContext,
        cooldown: risa.prefab.Cooldown = risa.INJECTED,
    ) -> None:
        if not self.open:
            await cooldown.undo()
    ```
    """

    __slots__ = ("_client", "_cooldown", "_ctx")

    def __init__(self, cooldown: _Cooldown, ctx: context.ComponentContext, client: client_.Client) -> None:
        self._cooldown = cooldown
        self._ctx = ctx
        self._client = client

    async def undo(self) -> None:
        """Give the click's bucket back its last use."""
        await self._cooldown.undo(self._ctx, self._client)

    async def reset(self) -> None:
        """Give the click's bucket back all of its uses."""
        await self._cooldown.reset(self._ctx, self._client)

    async def apply(self) -> None:
        """Take one more use from the click's bucket.

        Raises
        ------
        OnCooldownError
            If the bucket has no use left.
        """
        await self._cooldown.apply(self._ctx, self._client)


async def _owner_only(_pipeline: hooks.ExecutionPipeline, ctx: context.ComponentContext) -> None:
    application_id = ctx.interaction.application_id
    owners = _OWNERS.get(application_id)
    if owners is None:
        application = await ctx.rest.fetch_application()
        members = application.team.members.values() if application.team is not None else ()
        accepted = hikari.TeamMembershipState.ACCEPTED
        team = [member.id for member in members if member.membership_state is accepted]
        owners = _OWNERS[application_id] = frozenset({application.owner.id, *team})
    if ctx.user.id not in owners:
        raise errors.NotOwnerError(ctx.user.id)


owner_only: typing.Final[hooks.ExecutionHook[view_.View]] = hooks.hook(
    hooks.ExecutionSteps.CHECKS, skip_when_failed=True, name="owner_only"
)(_owner_only)
"""A check that lets only the bot's owner click, or a member of the team that owns it.

It runs in the `CHECKS` step and raises `NotOwnerError`. It asks Discord for the owners on
the first click and keeps them until the bot restarts.
"""


async def _invoker_only(_pipeline: hooks.ExecutionPipeline, ctx: context.ComponentContext) -> None:  # ruff:ignore[unused-async]
    metadata = ctx.message.interaction_metadata
    invoker_id = metadata.user.id if metadata is not None else None
    if ctx.user.id != invoker_id:
        raise errors.NotInvokerError(ctx.user.id, invoker_id)


invoker_only: typing.Final[hooks.ExecutionHook[view_.View]] = hooks.hook(
    hooks.ExecutionSteps.CHECKS, skip_when_failed=True, name="invoker_only"
)(_invoker_only)
"""A check that lets only the user click whose command or click sent the message.

It runs in the `CHECKS` step and raises `NotInvokerError`. A message no interaction sent,
such as one the bot posted into a channel by itself, has no such user, and every click on
it is turned away.
"""


def _missing(
    required: hikari.Permissions, actual: hikari.Permissions | None, *, fail_in_dm: bool, permit_admin: bool
) -> hikari.Permissions:
    if actual is None:
        return required if fail_in_dm else hikari.Permissions.NONE
    if permit_admin and actual & hikari.Permissions.ADMINISTRATOR:
        return hikari.Permissions.NONE
    return required & ~actual


def has_permissions(
    permissions: hikari.Permissions, /, *, fail_in_dm: bool = True, permit_admin: bool = True
) -> hooks.ExecutionHook[view_.View]:
    """Make a check that lets only users with all of `permissions` click.

    The hook runs in the `CHECKS` step and raises `MissingPermissionsError`. It reads the
    permissions Discord sends with the click, which count the channel's overwrites in.

    Parameters
    ----------
    permissions
        The permissions the user needs.
    fail_in_dm
        Whether a click in a direct message is turned away.
    permit_admin
        Whether an administrator passes without them.

    Returns
    -------
    :
        The hook.
    """

    @hooks.hook(hooks.ExecutionSteps.CHECKS, skip_when_failed=True, name="has_permissions")
    async def check(_pipeline: hooks.ExecutionPipeline, ctx: context.ComponentContext) -> None:  # ruff:ignore[unused-async]
        actual = ctx.member.permissions if ctx.member is not None else None
        missing = _missing(permissions, actual, fail_in_dm=fail_in_dm, permit_admin=permit_admin)
        if missing:
            raise errors.MissingPermissionsError(missing, actual or hikari.Permissions.NONE)

    return check


def bot_has_permissions(
    permissions: hikari.Permissions, /, *, fail_in_dm: bool = True, permit_admin: bool = True
) -> hooks.ExecutionHook[view_.View]:
    """Make a check that lets a click through only where the bot has all of `permissions`.

    The hook runs in the `CHECKS` step and raises `BotMissingPermissionsError`. It reads the
    bot's permissions in the channel, which Discord sends with the click.

    Parameters
    ----------
    permissions
        The permissions the bot needs.
    fail_in_dm
        Whether a click in a direct message is turned away.
    permit_admin
        Whether a bot that is an administrator passes without them.

    Returns
    -------
    :
        The hook.
    """

    @hooks.hook(hooks.ExecutionSteps.CHECKS, skip_when_failed=True, name="bot_has_permissions")
    async def check(_pipeline: hooks.ExecutionPipeline, ctx: context.ComponentContext) -> None:  # ruff:ignore[unused-async]
        actual = ctx.interaction.app_permissions if ctx.guild_id is not None else None
        missing = _missing(permissions, actual, fail_in_dm=fail_in_dm, permit_admin=permit_admin)
        if missing:
            raise errors.BotMissingPermissionsError(missing, actual or hikari.Permissions.NONE)

    return check


def has_roles(
    *role_ids: hikari.Snowflakeish, mode: typing.Literal["all", "any"] = "all", fail_in_dm: bool = True
) -> hooks.ExecutionHook[view_.View]:
    """Make a check that lets only users with the given roles click.

    The hook runs in the `CHECKS` step and raises `MissingRolesError`.

    Parameters
    ----------
    *role_ids
        The roles to look for.
    mode
        `"all"` needs every role, `"any"` at least one.
    fail_in_dm
        Whether a click in a direct message is turned away.

    Returns
    -------
    :
        The hook.

    Raises
    ------
    HookDeclarationError
        If no role is given.
    """
    name = "has_roles"
    if not role_ids:
        reason = "it needs at least one role"
        raise errors.HookDeclarationError(name, reason)
    required = [hikari.Snowflake(role_id) for role_id in role_ids]

    @hooks.hook(hooks.ExecutionSteps.CHECKS, skip_when_failed=True, name=name)
    async def check(_pipeline: hooks.ExecutionPipeline, ctx: context.ComponentContext) -> None:  # ruff:ignore[unused-async]
        if ctx.member is None:
            if fail_in_dm:
                raise errors.MissingRolesError(required)
            return
        missing = [role_id for role_id in required if role_id not in ctx.member.role_ids]
        if mode == "any" and len(missing) == len(required):
            raise errors.MissingRolesError(required)
        if mode == "all" and missing:
            raise errors.MissingRolesError(missing)

    return check


def expires_after(seconds: float, /) -> hooks.ExecutionHook[view_.View]:
    """Make a check that turns clicks away once the message has gone unedited for `seconds`.

    The hook runs in the `CHECKS` step and raises `ComponentExpiredError`. The time counts
    from when the message was sent, and again from every edit, a handler's `ctx.rerender()`
    included. It needs no store: the message carries both times.

    Parameters
    ----------
    seconds
        How long the message's components stay usable after its last edit.

    Returns
    -------
    :
        The hook.
    """

    @hooks.hook(hooks.ExecutionSteps.CHECKS, skip_when_failed=True, name="expires_after")
    async def check(_pipeline: hooks.ExecutionPipeline, ctx: context.ComponentContext) -> None:  # ruff:ignore[unused-async]
        age = time.time() - (ctx.message.edited_timestamp or ctx.message.timestamp).timestamp()
        if age > seconds:
            raise errors.ComponentExpiredError(age, seconds)

    return check


def fixed_window(
    window_length: float,
    allowed_invocations: int,
    bucket: Bucket,
    *,
    store: str | None = None,
    key: str | None = None,
) -> hooks.ExecutionHook[view_.View]:
    """Make a cooldown that allows a number of clicks per window of time.

    The window opens with a bucket's first click and closes `window_length` seconds later,
    whatever happens in between. The hook runs in the `COOLDOWNS` step, raises
    `OnCooldownError` and registers a `Cooldown` for injection. Each call without a store
    makes a cooldown of its own. Attach one hook in two places to count their clicks
    together.

    Parameters
    ----------
    window_length
        Seconds a window lasts.
    allowed_invocations
        How many clicks a bucket may make in one window.
    bucket
        What to count by.
    store
        The name of one of the client's stores to count in, so that every process and a
        restarted one see the same count. Left out, the hook counts in this process's
        memory, for up to 100,000 buckets at a time.
    key
        The name the count is kept under in `store`, unique to this cooldown.

    Returns
    -------
    :
        The hook.

    Raises
    ------
    HookDeclarationError
        If only one of `store` and `key` is given, or `store` is given while dependency
        injection is off.
    """
    ledger = _Ledger("fixed_window", bucket, store, key)
    cooldown = _FixedWindow(ledger, window_length, allowed_invocations)
    return hooks.hook(hooks.ExecutionSteps.COOLDOWNS, skip_when_failed=True, name="fixed_window")(cooldown.hook)


def sliding_window(
    window_length: float,
    allowed_invocations: int,
    bucket: Bucket,
    *,
    store: str | None = None,
    key: str | None = None,
) -> hooks.ExecutionHook[view_.View]:
    """Make a cooldown that allows a number of clicks in any stretch of `window_length` seconds.

    A click is allowed again once the oldest counted one is `window_length` seconds old. The
    hook runs in the `COOLDOWNS` step, raises `OnCooldownError` and registers a `Cooldown`
    for injection. Each call without a store makes a cooldown of its own. Attach one hook
    in two places to count their clicks together.

    Parameters
    ----------
    window_length
        Seconds a click counts for.
    allowed_invocations
        How many clicks a bucket may make within that time.
    bucket
        What to count by.
    store
        The name of one of the client's stores to count in, so that every process and a
        restarted one see the same count. Left out, the hook counts in this process's
        memory, for up to 100,000 buckets at a time.
    key
        The name the count is kept under in `store`, unique to this cooldown.

    Returns
    -------
    :
        The hook.

    Raises
    ------
    HookDeclarationError
        If only one of `store` and `key` is given, or `store` is given while dependency
        injection is off.
    """
    ledger = _Ledger("sliding_window", bucket, store, key)
    cooldown = _SlidingWindow(ledger, window_length, allowed_invocations)
    return hooks.hook(hooks.ExecutionSteps.COOLDOWNS, skip_when_failed=True, name="sliding_window")(cooldown.hook)


def max_concurrency(
    n_invocations: int,
    bucket: Bucket,
    *,
    store: str | None = None,
    key: str | None = None,
) -> collections.abc.Sequence[hooks.ExecutionHook[view_.View]]:
    """Make the two hooks that let only `n_invocations` clicks of a bucket run at once.

    The first runs in the `MAX_CONCURRENCY` step, takes a slot and raises
    `MaxConcurrencyReachedError` when none is free. The second runs in `POST_INVOKE` and
    gives the slot back.

    Unpack both into one `hooks=[...]` list, after any other `POST_INVOKE` hook that should
    still count as running. A slot that is never given back, for example because the bot
    was stopped in the middle of a click, frees itself after 15 minutes.

    Within one process, risa already runs the clicks on one message with durable state one
    after another, so there this limits clicks across messages. A click that waits for
    another on its message still has to answer within Discord's three seconds.

    Parameters
    ----------
    n_invocations
        How many clicks of one bucket may run at once.
    bucket
        What to count by.
    store
        The name of one of the client's stores to count in, so that the limit holds across
        processes. Left out, the hooks count in this process's memory.
    key
        The name the count is kept under in `store`, unique to this limit.

    Returns
    -------
    :
        The hook that takes a slot and the hook that gives it back.

    Raises
    ------
    HookDeclarationError
        If only one of `store` and `key` is given, or `store` is given while dependency
        injection is off.
    """
    ledger = _Ledger("max_concurrency", bucket, store, key)
    held: dict[hikari.Snowflake, float] = {}

    @hooks.hook(hooks.ExecutionSteps.MAX_CONCURRENCY, skip_when_failed=True, name="max_concurrency")
    async def take(
        _pipeline: hooks.ExecutionPipeline,
        ctx: context.ComponentContext,
        client: client_.Client = di.INJECTED,
    ) -> None:
        if ctx.interaction.id in held:
            return
        async with ledger.open(ctx, client) as entry:
            now = time.time()
            slots = [slot for slot in await entry.read(list[float]) or [] if slot > now]
            if len(slots) >= n_invocations:
                raise errors.MaxConcurrencyReachedError(n_invocations)
            held[ctx.interaction.id] = now + _SLOT_LIFETIME
            await entry.write([*slots, now + _SLOT_LIFETIME], ttl=_SLOT_LIFETIME)

    @hooks.hook(hooks.ExecutionSteps.POST_INVOKE, name="max_concurrency_release")
    async def give_back(
        _pipeline: hooks.ExecutionPipeline,
        ctx: context.ComponentContext,
        client: client_.Client = di.INJECTED,
    ) -> None:
        slot = held.pop(ctx.interaction.id, None)
        if slot is None:
            return
        async with ledger.open(ctx, client) as entry:
            slots = await entry.read(list[float]) or []
            if slot in slots:
                slots.remove(slot)
            if slots:
                await entry.write(slots, ttl=_SLOT_LIFETIME)
            else:
                await entry.delete()

    return take, give_back
