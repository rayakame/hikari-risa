# Ready-made hooks

`risa.prefab` holds the hooks most bots end up writing: who may click, how often, and how many clicks may run at once.
Each raises an exception that says why it turned the click away.
{: .risa-lead }

## A view with three of them

This reward can be claimed once a day per user. Only the bot's owner restocks it, and the message stops working an hour
after its last edit:

```python
--8<-- "ch14_prefab.py:view"
```

--8<-- "previews/reward.html"

Each is an ordinary [hook](13-hooks.md), attached with `hooks=[...]` to the client, a view or a handler. None of them
takes the view, so they also run on clicks that go to a [fallback](13-hooks.md#with-or-without-the-view). There a
cooldown still counts the click, and a check that turns it away keeps the fallback from running.

## Answering a click that was turned away

Every exception these hooks raise to turn a click away derives from
[`ClickRejectedError`](../api/errors.md#risa.errors.ClickRejectedError), so one error handler answers all of them:

```python
--8<-- "ch14_prefab.py:answer"
```

--8<-- "previews/reward-cooldown.html"

Without an error handler the click fails in Discord, and risa logs it as an error.

## Checks

The checks run in the `CHECKS` step. Once anything in the click has failed, they are skipped.

| Hook | Lets the click through when | Raises |
|---|---|---|
| [`owner_only`](../api/prefab.md#risa.prefab.owner_only) | The user owns the bot, or belongs to the team that does | `NotOwnerError` |
| [`invoker_only`](../api/prefab.md#risa.prefab.invoker_only) | The user's own command or click sent the message | `NotInvokerError` |
| [`has_permissions(...)`](../api/prefab.md#risa.prefab.has_permissions) | The user has every permission given | `MissingPermissionsError` |
| [`bot_has_permissions(...)`](../api/prefab.md#risa.prefab.bot_has_permissions) | The bot has every permission given in the channel | `BotMissingPermissionsError` |
| [`has_roles(...)`](../api/prefab.md#risa.prefab.has_roles) | The user has all of the roles, or one of them with `mode="any"` | `MissingRolesError` |
| [`expires_after(seconds)`](../api/prefab.md#risa.prefab.expires_after) | The message was sent or last edited at most `seconds` ago | `ComponentExpiredError` |

- **Permissions and roles.** These checks turn a click in a direct message away, or let it through unchecked with
  `fail_in_dm=False`. The permission checks let an administrator through unless you pass `permit_admin=False`.
- **`invoker_only`** reads the user from the interaction that sent the message. A message the bot posted by itself has
  none, and every click on it is turned away. For those, keep the user's id in a field and write the check yourself, as
  [Hooks](13-hooks.md#a-check) does.
- **`expires_after`** gives a view a lifetime without a store. A `ctx.rerender()` is an edit, so the hour above starts
  over with every claim. A click that answers without it, such as a claim with nothing left, does not restart it. The
  buttons stay on the message, and a click on them raises `ComponentExpiredError`. For a view whose state lives in a
  store, [`ttl`](09-stores-and-ttl.md#ttl) restarts on every click and also removes the record.

## Cooldowns

A cooldown runs in the `COOLDOWNS` step and raises
[`OnCooldownError`](../api/errors.md#risa.errors.OnCooldownError), whose `remaining` is the seconds until the next click
is allowed.

| Hook | Allows |
|---|---|
| [`fixed_window(60, 3, bucket)`](../api/prefab.md#risa.prefab.fixed_window) | 3 clicks in the 60 seconds that start with the first one, then 3 again |
| [`sliding_window(60, 3, bucket)`](../api/prefab.md#risa.prefab.sliding_window) | 3 clicks in any 60 seconds: the next is allowed once the oldest is 60 seconds old |

The bucket says which clicks count together:

| Bucket | Counts |
|---|---|
| `"global"` | Every click together |
| `"user"` | Per user |
| `"channel"` | Per channel |
| `"guild"` | Per guild, and per channel in a direct message |
| `"message"` | Per message |
| A function | Per value it returns for the context, an `int` or a `str`; it may be `async` |

- **One hook object, one count.** Each call without a store makes a cooldown of its own, and the same hook object
  attached to two handlers counts their clicks together.
- **A click that was turned away costs no use.** Like the checks, a cooldown is skipped once anything has failed.
- **Every click it lets through counts**, even one that fails afterwards or that a later cooldown turns away.
- **A handler can give the use back** through [`Cooldown`](../api/prefab.md#risa.prefab.Cooldown), which the cooldown
  registers for [injection](06-dependency-injection.md#what-risa-registers): `claim` calls `cooldown.undo()` when
  nothing is left. `reset()` gives the bucket all of its uses back, and `apply()` takes one more or raises
  `OnCooldownError`.

## Counting in a store

By default a cooldown counts in the memory of its process: a restart forgets it, and each process of a
[REST bot](11-several-processes.md) counts alone. Pass the name of one of the client's [stores](09-stores-and-ttl.md)
and a key to count there:

```python
--8<-- "ch14_prefab.py:store"
```

Put `DAILY` on `claim` in place of the cooldown above, and give the client a store with
`risa.client_from_app(bot, store=...)`: `"default"` is that store's name. `max_concurrency` takes both too.

- **The key** names this cooldown in the store, so every process finds the same count. Give each cooldown its own.
- **A name the client has no store for** fails every click with `HookDeclarationError`, from the first click on.
- **While the store cannot be reached**, a click fails with
  [`StoreUnavailableError`](../api/errors.md#risa.errors.StoreUnavailableError).

## Clicks that run at once

An export takes five seconds, and only one should run at a time:

```python
--8<-- "ch14_prefab.py:export"
```

[`max_concurrency(1, "global")`](../api/prefab.md#risa.prefab.max_concurrency) returns two hooks: one takes a slot
before the handler and raises `MaxConcurrencyReachedError` when none is free, the other gives it back in `POST_INVOKE`.

- **Unpack both into the list**, as `export` does. A `POST_INVOKE` hook of your own that should still count as running
  goes before them in the list.
- **Keep slow work on a view without durable state**, as `Export` is. Within one process, risa runs the clicks on one
  message with durable state one after another, so a second click there waits for the first instead of being turned
  away, and it still has to answer within Discord's three seconds. There the limit only matters across messages.
- **A slot frees itself after 15 minutes** if it is never given back, for example because the bot stopped in the middle
  of a click. A handler that runs longer than that loses its slot too, and another click may start beside it.

??? example "Full file"

    ```python
    --8<-- "ch14_prefab.py:20:"
    ```
