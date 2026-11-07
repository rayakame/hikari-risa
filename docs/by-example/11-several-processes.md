# Several processes

Run more than one process, and two clicks on one message can land in two places. This chapter shows when that happens,
what it does to your views, and how to keep every click.
{: .risa-lead }

A bot in one process needs nothing from here. With several processes, put every `InStore` view on Redis or PostgreSQL. On a
REST bot with several processes, move every view that cannot lose a click to such a store as well.

## Where a click lands

Two people click the same message. Which process runs each click depends on the kind of bot:

<div class="risa-routes">
<div class="risa-routes__bot">
<strong>Gateway bot</strong>
<svg viewBox="0 0 300 208" role="img" aria-label="Two clicks on one message of a gateway bot. The shard of the guild decides, and both clicks reach process 1.">
<defs>
<marker id="risa-gateway-1" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#f680a1"/></marker>
<marker id="risa-gateway-2" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#ffd479"/></marker>
</defs>
<rect class="risa-routes__click-1" x="20" y="4" width="116" height="32" rx="4"/>
<text class="risa-routes__label-1" x="78" y="20">Click 1</text>
<rect class="risa-routes__click-2" x="164" y="4" width="116" height="32" rx="4"/>
<text class="risa-routes__label-2" x="222" y="20">Click 2</text>
<path class="risa-routes__line-1" d="M78 36L104 68" marker-end="url(#risa-gateway-1)"/>
<path class="risa-routes__line-2" d="M222 36L196 68" marker-end="url(#risa-gateway-2)"/>
<rect class="risa-routes__box" x="46" y="70" width="208" height="32" rx="4"/>
<text x="150" y="86">The guild's shard decides</text>
<path class="risa-routes__line-1" d="M100 102L56 136" marker-end="url(#risa-gateway-1)"/>
<path class="risa-routes__line-2" d="M130 102L92 136" marker-end="url(#risa-gateway-2)"/>
<rect class="risa-routes__box" x="4" y="138" width="138" height="66" rx="4"/>
<text class="risa-routes__name" x="73" y="160">Process 1</text>
<text x="73" y="182">Both clicks</text>
<rect class="risa-routes__box risa-routes__idle" x="158" y="138" width="138" height="66" rx="4"/>
<text class="risa-routes__name risa-routes__idle" x="227" y="160">Process 2</text>
<text class="risa-routes__idle" x="227" y="182">No click</text>
</svg>
<span>All clicks on one message reach one process, so they can wait for each other there.</span>
</div>
<div class="risa-routes__bot">
<strong>REST bot, several processes</strong>
<svg viewBox="0 0 300 208" role="img" aria-label="Two clicks on one message of a REST bot. The load balancer decides, and click 1 reaches process 1 while click 2 reaches process 2.">
<defs>
<marker id="risa-rest-1" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#f680a1"/></marker>
<marker id="risa-rest-2" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#ffd479"/></marker>
</defs>
<rect class="risa-routes__click-1" x="20" y="4" width="116" height="32" rx="4"/>
<text class="risa-routes__label-1" x="78" y="20">Click 1</text>
<rect class="risa-routes__click-2" x="164" y="4" width="116" height="32" rx="4"/>
<text class="risa-routes__label-2" x="222" y="20">Click 2</text>
<path class="risa-routes__line-1" d="M78 36L104 68" marker-end="url(#risa-rest-1)"/>
<path class="risa-routes__line-2" d="M222 36L196 68" marker-end="url(#risa-rest-2)"/>
<rect class="risa-routes__box" x="46" y="70" width="208" height="32" rx="4"/>
<text x="150" y="86">The load balancer decides</text>
<path class="risa-routes__line-1" d="M104 102L73 136" marker-end="url(#risa-rest-1)"/>
<path class="risa-routes__line-2" d="M196 102L227 136" marker-end="url(#risa-rest-2)"/>
<rect class="risa-routes__box" x="4" y="138" width="138" height="66" rx="4"/>
<text class="risa-routes__name" x="73" y="160">Process 1</text>
<text x="73" y="182">Click 1</text>
<rect class="risa-routes__box" x="158" y="138" width="138" height="66" rx="4"/>
<text class="risa-routes__name" x="227" y="160">Process 2</text>
<text x="227" y="182">Click 2</text>
</svg>
<span>Each click can reach another process, so two can run at the same moment.</span>
</div>
</div>

- **A gateway bot** has several processes when you split its shards, with `shard_ids` and `shard_count` on `bot.run`. A
  message's guild decides its shard, by [Discord's formula](https://docs.discord.com/developers/events/gateway#sharding),
  and a DM goes to shard 0. That process need not be the one that sent the message.
- **A REST bot** has several when you run several copies behind a load balancer, which picks a copy for each click.

## What two clicks do to a view

Two people click **Yes** at the same moment on a poll at 5, so the right result is 7:

<table class="risa-grid">
<thead>
<tr><th></th><th scope="col">Gateway bot</th><th scope="col">REST bot, several processes</th></tr>
</thead>
<tbody>
<tr><th scope="row"><code>InMessage</code></th><td data-where="Gateway bot"><strong class="risa-grid__good">7</strong>Both clicks count.</td><td data-where="REST bot, several processes"><strong class="risa-grid__bad">6</strong>Both processes read 5 and write 6, so one click is lost, and nothing logs it.</td></tr>
<tr><th scope="row"><code>InStore</code> on Redis or PostgreSQL</th><td data-where="Gateway bot"><strong class="risa-grid__good">7</strong>Both clicks count.</td><td data-where="REST bot, several processes"><strong class="risa-grid__good">7</strong>Both clicks count. The second waits for the first, across the processes.</td></tr>
<tr><th scope="row"><code>InStore</code> on <code>MemoryStore</code></th><td data-where="Gateway bot"><strong class="risa-grid__warn">7, until a restart</strong>Both clicks count, and a restart loses the poll.</td><td data-where="REST bot, several processes"><strong class="risa-grid__bad">Most clicks fail</strong>Only the process that sent the poll has its record. Elsewhere <code>on_state_missing</code> runs.</td></tr>
</tbody>
</table>

A REST bot with one process behaves like the gateway column. A gateway bot with split shards can miss a `MemoryStore`
record too, when one process sends a message into a guild another process serves, and `add_view` warns about a
`MemoryStore` at startup.

In a message, one more thing holds on every bot: a vote changes the ids, so a click that reaches Discord after the first
edit fails there, and the person has to click again. [In a store](08-where-state-lives.md#message-or-store) the ids stay.

## What to set up

A view in the message needs no store. A view in a store needs [`RedisStore` or `PostgresStore`](09-stores-and-ttl.md#redis-and-postgresql),
on one server for all processes, or [your own](12-writing-your-own-store.md). Every process runs the same setup.

```python
--8<-- "ch11_poll_rest.py:client"
```

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
[three seconds](05-answering-in-time.md#the-clock) keep running meanwhile, which is why the client above sets
[auto-defer](05-answering-in-time.md#where-to-set-it): it answers in time.

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
