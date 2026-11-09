# Changelog

What changed in each release of hikari-risa.

<!-- towncrier release notes start -->

## 1.0.0 (2026-11-09)

The first release of hikari-risa, a component handler for [hikari](https://github.com/hikari-py/hikari) built for Components V2. It runs on Python 3.12 to 3.15 with hikari 2.5 or a later 2.x release.

- A view is a class with fields, a `render()` and handlers. Its layout takes the components of a Components V2 message, such as buttons, selects, sections, galleries and containers, and a layout of action rows alone is a V1 message. Modals are not part of 1.0.
- A view keeps its state in its own message by default, so a click still lands after a restart. `InStore` keeps it in memory, Redis, PostgreSQL or a store you write, with a time to live.
- Handlers take arguments bound into the component and answer with `respond`, `rerender`, `edit` or `replace`. Auto-defer, once you turn it on, answers for a slow one in time.
- Clicks on old messages fail closed: when the view changed in a way they cannot follow, they reach `on_outdated`, and when the stored state is gone, `on_state_missing`.
- Dependency injection through linkd, shared with lightbulb, error handlers on the client, and both gateway and REST bots.
- The `hikari-risa` command, also `python -m risa`, prints the versions of risa, Python and what risa installs for a bug report.
