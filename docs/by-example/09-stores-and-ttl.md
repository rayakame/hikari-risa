# Stores and TTL

A store gives the poll room for every voter and a lifetime. This chapter moves the poll into one, first in memory and
then in Redis.
{: .risa-lead }

## Moving the poll

[`risa.InStore`](../api/state/placement.md#risa.state.placement.InStore) on `@risa.register` moves the view, and it takes
`ttl`, in seconds. The handlers stay as they were. Polls already sent cannot follow, so `version` goes up, as
[Changing a view safely](10-changing-a-view-safely.md#a-change-that-is-not-free) explains.

```python
--8<-- "ch09_poll_memory.py:fields"
```

The thirteen voters that raised an error now fit:

--8<-- "previews/poll-stored.html"

The buttons hold a random key to one record in the store, so the voters' ids stay out of the message, and a vote leaves
the buttons' ids as they are:

--8<-- "previews/poll-stored-ids.html"

## `MemoryStore`, for trying things out

A client gets its store from `store=`. [`risa.MemoryStore`](../api/store/memory.md#risa.store.memory.MemoryStore) needs
nothing installed.

```python
--8<-- "ch09_poll_memory.py:client"
```

`add_view` warns you about it:

--8<-- "previews/poll-memory-warning.html"

A bot with several stores passes `stores={"votes": store}`, and the view names one with
`risa.InStore(ttl=WEEK, store="votes")`.

`add_view` raises [`ViewDeclarationError`](../api/errors.md#risa.errors.ViewDeclarationError) at startup if the client
has no store for the view. A client made with
`use_global=True` skips `add_view` and finds out late: `client.build` raises, and a click on a poll already sent fails,
with an `ERROR` in your log.

## `ttl`

`ttl` is how many seconds a record lives untouched. Every click restarts the clock, even one that changes nothing, so
only a poll nobody clicks for a week expires. `ttl=None` keeps a record forever, including one that a deleted message or
a failed send leaves behind.

The message stays as it is when the time runs out, and the next click runs
[`on_state_missing`](../api/view.md#risa.view.View.on_state_missing).

```python
--8<-- "ch09_poll_memory.py:missing"
```

--8<-- "previews/poll-expired.html"

Without this method the click fails in Discord, and risa logs a warning. `ctx` has no view here, so `ctx.respond` and a
`ctx.edit` with nothing to click work, and `ctx.rerender()` raises [`NoViewError`](../api/errors.md#risa.errors.NoViewError), as a
`ctx.edit` with a button does.

??? example "Full file"

    ```python
    --8<-- "ch09_poll_memory.py:20:"
    ```

## Redis and PostgreSQL

These two keep the state through a restart, and [all processes of a bot](11-several-processes.md) can share them. Each
lives in a module of its own and needs its [extra](../get-started/installation.md#extras).

| Store | Extra | It takes |
|---|---|---|
| [`risa.store.redis.RedisStore`](../api/store/redis.md#risa.store.redis.RedisStore) | `redis` | A `redis.asyncio.Redis` |
| [`risa.store.postgres.PostgresStore`](../api/store/postgres.md#risa.store.postgres.PostgresStore) | `postgres` | An `asyncpg` pool |

The connection stays yours, so the bot closes it when it stops. An asyncpg pool opens inside the running event loop, so
make the pool and the client in a `hikari.StartingEvent` listener.

```python
--8<-- "ch09_poll_redis.py:client"
```

!!! tip "Try it."
    Vote, stop the bot and start it again. A second vote from you gets "You already voted.", because the record is still
    there.

Run Redis with `maxmemory-policy noeviction`. Any other policy may evict a record before it expires, which ends that
poll, and `RedisStore` warns about those on first use, if the server lets it read the setting. Turn on AOF as well (`appendonly yes`, `appendfsync everysec`),
because RDB snapshots alone lose recent votes in a crash.

PostgreSQL needs no cleanup job: `PostgresStore` creates its tables on first use and deletes expired rows itself.

??? example "Full file"

    ```python
    --8<-- "ch09_poll_redis.py:20:"
    ```
