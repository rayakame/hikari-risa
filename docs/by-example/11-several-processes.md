# Several processes

With more than one process, every `InStore` view needs a store they all reach, and a REST bot's `InMessage` views can lose
a click.
{: .risa-lead }

## Which store you need

A view in the message needs no store. A view in a store needs [`RedisStore` or `PostgresStore`](08-stores-and-ttl.md#redis-and-postgresql),
on one server for all processes, or [your own](12-writing-your-own-store.md).

<table class="risa-grid">
<thead>
<tr><th></th><th scope="col">Gateway bot</th><th scope="col">REST bot, several processes</th></tr>
</thead>
<tbody>
<tr><th scope="row"><code>InMessage</code></th><td data-where="Gateway bot">Clicks on one message queue up.</td><td data-where="REST bot, several processes">The last edit of the message wins.</td></tr>
<tr><th scope="row"><code>InStore</code> on Redis or PostgreSQL</th><td data-where="Gateway bot">Clicks on one view queue up.</td><td data-where="REST bot, several processes">Clicks on one view queue up across the processes.</td></tr>
<tr><th scope="row"><code>InStore</code> on <code>MemoryStore</code></th><td data-where="Gateway bot">A restart loses the state, and <code>add_view</code> warns at startup.</td><td data-where="REST bot, several processes">Most clicks do not find the state, and <code>add_view</code> warns at startup.</td></tr>
</tbody>
</table>

A click that does not find the state runs [`on_state_missing`](08-stores-and-ttl.md#ttl).

Every process runs the same setup.

```python
--8<-- "ch11_poll_rest.py:client"
```

## Where a click lands

| Bot | It has several processes when | A click lands on |
|---|---|---|
| Gateway | You split its shards, with `shard_ids` and `shard_count` on `bot.run` | The process with the shard of the message's guild, by [Discord's formula](https://docs.discord.com/developers/events/gateway#sharding), and shard 0 for a DM |
| REST | You run several copies behind a load balancer | Whichever copy the balancer picks |

So all clicks on one message of a gateway bot reach one process, although that need not be the process that sent the
message. On a REST bot, two clicks on one message can run at the same moment in two processes.

## How clicks take turns

On Redis or PostgreSQL, clicks on one view run one after another, across processes too. Each goes through these steps:

<ol class="risa-flow risa-flow--4">
<li class="risa-flow__step"><span class="risa-flow__n">1</span><strong>In-process lock</strong><span>Other clicks on this record in this process wait.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2</span><strong>Lease</strong><span>An expiring claim in the store makes clicks in other processes wait.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3</span><strong>The handler</strong><span>It runs on the record it read, and the lease keeps renewing.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">4</span><strong>Compare-and-set</strong><span><code>rerender()</code> writes only if the record is still at the version it read.</span></li>
</ol>

A process that crashes during step 3 blocks that view until its lease runs out, 5 seconds by default, or the `lease=` you
give [`RedisStore`](../api/store/redis.md#risa.store.redis.RedisStore) or
[`PostgresStore`](../api/store/postgres.md#risa.store.postgres.PostgresStore).

A click waits up to 10 seconds, then fails with an `ERROR` in your log. Discord's
[three seconds](05-answering-in-time.md#the-clock) keep running meanwhile, and
[auto-defer](05-answering-in-time.md#where-to-set-it) can answer in time.

## `InMessage` on a REST bot

An `InMessage` view has only step 1, so nothing spans the processes. Two clicks at the same moment read the same state:
both raise a count from 5 to 6, and both edits say 6. The last edit wins, one click is lost, and your log shows nothing. If a
view cannot lose a click, move it to a store.

## When a handler loses the race

A lease can run out under a handler that runs past `max_hold`, 60 seconds by default, or in a process that stalls, and a
Redis failover can lose one. The next click then runs beside the first, and step 4 lets only one write through. The other's
`ctx.rerender()` raises [`StateConflictError`](../api/errors.md#risa.errors.StateConflictError). By then `self` holds the
state that won, so the handler can apply its change once more.

```python
--8<-- "ch11_poll_rest.py:conflict"
```

`_first_vote` runs again on the record that won, so someone whose vote is already in gets "You already voted."

The retry is one attempt. If it conflicts too, `rerender()` raises again, risa logs the error and that click fails.

Without the `except`, the handler fails: risa logs the error, the click fails in Discord, and the message shows what the
winner wrote. A handler that changes a field and never calls `rerender()` cannot catch anything, so risa logs a `WARNING`
and drops the change.

??? example "Full file"

    ```python
    --8<-- "ch11_poll_rest.py:20:"
    ```
