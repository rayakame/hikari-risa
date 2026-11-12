# Changelog

What changed in each release of hikari-risa.

<!-- towncrier release notes start -->

## 1.2.0 (2026-11-12)

### Breaking changes

- Error handlers take the click's `ExecutionPipeline` and its `ComponentContext`, as `async def on_error(pipeline, ctx) -> bool`, and `ClickFailure` is gone. `pipeline.failures` lists every failure of the click in order, `pipeline.find(SomeError)` returns the first of a type, and `FailureStage` moved to `risa.hooks` and gained `HOOK`. A click no error handler takes is logged as one entry with an `ExceptionGroup`. ([#83](https://github.com/rayakame/hikari-risa/pull/83))

### Features

- Hooks: functions made with `@risa.hook(step)` that run in steps around a click's handler, such as a check before it or an audit after it. Attach them with `hooks=` to the client, `@risa.register` or `@risa.handler`, and set the steps with `execution_step_order=`. ([#83](https://github.com/rayakame/hikari-risa/pull/83))
- Ready-made hooks in `risa.prefab`: the checks `owner_only`, `invoker_only`, `has_permissions`, `bot_has_permissions`, `has_roles` and `expires_after`, the cooldowns `fixed_window` and `sliding_window`, and `max_concurrency`. Each turns a click away with an exception derived from `ClickRejectedError`. Cooldowns and `max_concurrency` count in memory, or in one of the client's stores with `store=` and `key=`. ([#85](https://github.com/rayakame/hikari-risa/pull/85))


## 1.1.0 (2026-11-09)

### Features

- `InStore(adopt=True)` takes over the messages a view sent while it kept its state in the message: switch `state=` without raising the `version`, and the first click on an old message moves its state into the store. ([#75](https://github.com/rayakame/hikari-risa/pull/75))
- `MemoryStore(warn=False)` turns off the warning `add_view` logs about a `MemoryStore`, for a bot that uses one on purpose. ([#76](https://github.com/rayakame/hikari-risa/pull/76))


## 1.0.0 (2026-11-09)

The first release of hikari-risa, a component handler for [hikari](https://github.com/hikari-py/hikari) built for Components V2. It runs on Python 3.12 to 3.15 with hikari 2.5 or a later 2.x release.

- A view is a class with fields, a `render()` and handlers. Its layout takes the components of a Components V2 message, such as buttons, selects, sections, galleries and containers, and a layout of action rows alone is a V1 message. Modals are not part of 1.0.
- A view keeps its state in its own message by default, so a click still lands after a restart. `InStore` keeps it in memory, Redis, PostgreSQL or a store you write, with a time to live.
- Handlers take arguments bound into the component and answer with `respond`, `rerender`, `edit` or `replace`. Auto-defer, once you turn it on, answers for a slow one in time.
- Clicks on old messages fail closed: when the view changed in a way they cannot follow, they reach `on_outdated`, and when the stored state is gone, `on_state_missing`.
- Dependency injection through linkd, shared with lightbulb, error handlers on the client, and both gateway and REST bots.
- The `hikari-risa` command, also `python -m risa`, prints the versions of risa, Python and what risa installs for a bug report.
