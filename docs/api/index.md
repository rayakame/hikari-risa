---
hide:
  - toc
---

# API reference

::: risa
    options:
      members: false

<div class="risa-overview risa-overview--rows" markdown>

## Subpackages

- [`risa.state`](state/index.md)

    Where a view's state lives, and which of its fields are state.

- [`risa.state.placement`](state/placement.md){ .risa-overview__sub }

    Where a view keeps its durable state: `InMessage` or `InStore`.

- [`risa.state.schema`](state/schema.md){ .risa-overview__sub }

    A view's durable fields, and how their values are packed into state.

- [`risa.store`](store/index.md)

    Stores for `InStore` state: the `Store` protocol and the stores risa ships.

- [`risa.store.memory`](store/memory.md){ .risa-overview__sub }

    `MemoryStore`, the store that keeps state in this process.

- [`risa.store.postgres`](store/postgres.md){ .risa-overview__sub }

    `PostgresStore`, a store on PostgreSQL. Install it with the `[postgres]` extra.

- [`risa.store.protocol`](store/protocol.md){ .risa-overview__sub }

    The contract a state store implements.

- [`risa.store.redis`](store/redis.md){ .risa-overview__sub }

    `RedisStore`, a store on Redis. Install it with the `[redis]` extra.

- [`risa.store.testing`](store/testing.md){ .risa-overview__sub }

    `verify_store`, the conformance check for `Store` implementations.

- [`risa.ui`](ui/index.md)

    The components a view's layout is built from, and `Rendered`, a built view.

- [`risa.ui.build`](ui/build.md){ .risa-overview__sub }

    Building a view's layout into the components of a message.

- [`risa.ui.nodes`](ui/nodes.md){ .risa-overview__sub }

    The components a view's layout is built from, Discord's Components V2.

## Modules

- [`risa.binding`](binding.md)

    Putting handlers on components, with or without wire arguments.

- [`risa.client`](client.md)

    The client that routes clicks to views, and the functions that make one.

- [`risa.codecs`](codecs.md)

    How `InMessage` state is written into `custom_id` characters.

- [`risa.context`](context.md)

    The contexts handlers, hooks and fallbacks answer interactions through.

- [`risa.di`](di.md)

    Dependency injection for handlers, through linkd.

- [`risa.errors`](errors.md)

    The exceptions risa raises.

- [`risa.hooks`](hooks.md)

    Hooks, the steps they run in, and the pipeline every click runs through.

- [`risa.prefab`](prefab.md)

    Ready-made hooks: checks, cooldowns and a limit on clicks that run at once.

- [`risa.view`](view.md)

    Views, their handlers, and how they are registered.

</div>

The names of `risa.binding`, `risa.client`, `risa.context`, `risa.di`, `risa.errors`, `risa.hooks`, `risa.state`, `risa.store` and `risa.view` are also importable directly from `risa`.
