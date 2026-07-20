# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

`hikari-risa` — a component handler for [hikari](https://github.com/hikari-py/hikari), built
Components V2 first. Published to PyPI, ships `py.typed`. This is a **library**, not an
application: the public API surface and its type annotations are the product.

## Git

- **Never commit unless explicitly asked.** Make the changes, report what changed, and wait.
  This applies to `git add` staging as much as to committing.
- **Never push unless explicitly asked.**
- **Commits are authored solely by the repository owner.** Do not add `Co-Authored-By`
  trailers, `Generated with` footers, or any other attribution to Claude or Anthropic in
  commit messages or PR descriptions.
- Never use `--no-verify` or skip hooks.

## Commands

Everything runs through `uv` and `nox`. Do not `pip install` anything.

```sh
uv run --group nox nox            # reformat, ruff, pyright, pytest
uv run --group nox nox -s ruff    # a single session
uv run --group pytest pytest -q   # fast inner loop while iterating
uv lock                           # after ANY dependency change
```

`uv.lock` is committed. The nox sessions run `uv sync --locked`, so a stale lockfile fails CI.

## Toolchain

| | |
|---|---|
| Python floor | 3.12 (PEP 695 `type X = ...` and `class Foo[T]` are available) |
| Formatter / linter | ruff, `select = ["ALL"]`, `preview = true`, line length 120 |
| Type checker | pyright, `typeCheckingMode = "strict"` |
| Tests | pytest, `asyncio_mode = "auto"`, warnings are errors |

pyright strict is the CI gate and is non-negotiable — for a `py.typed` library it is the
guarantee shipped to downstream users. `ty` was evaluated in July 2026 and rejected: no strict
mode, no annotation-completeness rules, and Protocol support is its weakest area (4 of 11
conformance tests unsupported, including variance). Revisit if
[astral-sh/ty#527](https://github.com/astral-sh/ty/issues/527) closes.

## Code style

These are enforced by ruff `ALL`; the point of listing them is to write compliant code the
first time rather than discovering it from lint output.

- **Every `.py` file starts with the 19-line MIT header.** Copy it verbatim from an existing
  file. This includes test files and `__init__.py`.
- **`from __future__ import annotations` is a required import** in every module.
- **One import per line.** `force-single-line = true` — no `from x import a, b`.
- **Numpy docstring convention.** Module, class and public function docstrings are mandatory.
- **`__all__` is a tuple, sorted alphabetically**, declared right after the module docstring
  and imports.
- **Exceptions build their own messages in `__init__`** and expose structured attributes.
  `TRY003` and `EM101` forbid long message literals at the raise site, and callers should be
  able to branch on fields rather than parse strings. See `risa/errors.py` for the pattern.
- `**/__init__.py` is excluded from pyright and exempt from `F401`/`F403`, so it is the one
  place star re-exports are acceptable.
- Prefer `# type: ignore[rule]` over `# pyright: ignore[rule]` where both work.
  `reportUnnecessaryTypeIgnoreComment` is an error, so unused suppressions fail the build.

## Layout

```text
risa/
  __init__.py    star re-exports of the public API
  _about.py      package metadata (__version__, __author__, ...)
  errors.py      RisaError-rooted exception hierarchy
  py.typed
tests/
noxfile.py       reformat / ruff / pyright / pytest sessions
ruff.toml        lint + format config (NOT in pyproject.toml)
```

Flat layout, not `src/`. Package config lives in `pyproject.toml` except ruff, which has its
own `ruff.toml`.

## Design direction

Decided but not yet built. Useful context; treat as intent, not as an implemented contract.

- **Components V2 is the model; V1 falls out of it.** V1 is just a tree whose top level
  contains only action rows. There is one tree type and one layout pass. Do not hard-wire the
  5x5 grid into the type surface — that is precisely what stops miru, flare and lightbulb from
  supporting V2.
- **Nesting is a layout concern; dispatch is flat.** Only action-row children, a `Section`
  accessory button, and modal text inputs are interactive. The tree is flattened to a sparse
  set of interactive leaves at build time and routed against a flat dict. Tree depth costs
  nothing at runtime.
- **State lives in a pluggable store, keyed by a random token in the `custom_id`.** Random
  keys (not message IDs or anything shard-derived) are what make resharding a non-event.
  `MemoryStore` is the zero-infra default and is only safe single-process; a shared store such
  as Redis is required for multi-process shards or a REST bot behind a load balancer.
- **`custom_id` wire format:** `[ver:1][cookie:6][handler:2][mode:1][payload]` within Discord's
  100-character limit. The cookie hashes the view name *and* its schema version, so a schema
  change fails closed rather than deserialising into the wrong shape. Unrecognised
  `custom_id`s must be ignored silently so risa can coexist with other handlers.
- **Auto-defer sits above the handler, not inside it.** Store I/O happens during dispatch,
  before user code runs, so the defer timer starts at decode. Default to
  `DEFERRED_MESSAGE_UPDATE` (silent ack), not `DEFERRED_MESSAGE_CREATE`.
- **hikari validates almost nothing about component layout.** Rejecting invalid trees at build
  time with a path-qualified error is a large share of this library's value, so encode the
  nesting rules as data rather than as branching logic.

## Gotchas

- `ComponentInteraction` does **not** expose the V2 `id` field — hikari never deserialises it.
  `custom_id` is the only routing channel available.
- A message with `IS_COMPONENTS_V2` set cannot carry `content` or `embeds`; everything is
  components. hikari sets that flag automatically when it sees a V2 builder, so a view renders
  the whole message body, not just its components.
- `ComponentBuilder.build()` returns a `(payload, attachments)` **tuple**, unlike
  `SelectOptionBuilder.build()` which returns a bare mapping.
- ruff warns that `COM812` conflicts with the formatter. It arrives via `select = ["ALL"]` and
  is currently tolerated.
