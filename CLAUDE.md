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
uv run --group nox nox            # reformat, ruff, pyright, pytest, audit
uv run --group nox nox -s ruff    # a single session
uv run --group pytest pytest -q   # fast inner loop while iterating
uv lock                           # after ANY dependency change
uv lock --upgrade                 # periodically; plain `uv lock` never bumps existing pins
```

`uv.lock` is committed. The nox sessions run `uv sync --locked`, so a stale lockfile fails CI.

The `audit` session runs `uv audit`, which needs **uv >= 0.9** (the subcommand is still behind
`--preview-features audit-command`). Because `nox[uv]` puts uv in the lockfile, the project's
uv is pinned per-project and the copy in `.venv` shadows any global install. If `audit` fails
with `unrecognized subcommand`, the fix is `uv lock --upgrade`, not `uv self update`.

Note that plain `uv lock` is conservative — it resolves only what changed and leaves existing
pins alone. Without a periodic `uv lock --upgrade`, the lockfile silently rots and `audit`
starts failing on transitive dependencies.

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
- **Exceptions are named with an `Error` suffix** and live in `risa/errors.py`.
- **Exceptions build their own messages in `__init__`** and expose structured attributes.
  `TRY003` and `EM101` forbid long message literals at the raise site, and callers should be
  able to branch on fields rather than parse strings. See `risa/errors.py` for the pattern.
- **Nothing fails silently.** No `contextlib.suppress`, no bare `except: pass` for anything
  that changes behaviour — log it or raise it. Each module logs through
  `_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.<module>")`; the library
  never configures logging itself.
- **Type as strictly as Python allows.** `typing.Any` is not an acceptable shortcut, and
  neither is loosening a signature to satisfy a lint rule — restructure the code instead.
- **Few inline comments.** A reason worth recording goes in the docstring, not beside the code.
- `**/__init__.py` is excluded from pyright and exempt from `F401`/`F403`, so it is the one
  place star re-exports are acceptable.
- Prefer `# type: ignore[rule]` over `# pyright: ignore[rule]` where both work.
  `reportUnnecessaryTypeIgnoreComment` is an error, so unused suppressions fail the build.

## Placement and ordering

A reader should be able to predict where a symbol lives before looking for it.

**Placement.** A private helper belongs to the concept it serves, not to the file that happens
to call it. Pick one of four: fold it into its only consumer; put it immediately above its only
consumer; join the shared-helper block under the type aliases; or move it to the module that owns
the concept.

**Order.** A file reads in dependency order and ends with its entry point — nothing appears above
something it names, except PEP 695 `type` aliases, which are lazily evaluated and therefore free
to sit at the top. The bands, in order:

```text
MIT header → from __future__ → imports → __all__ → _LOGGER → Final constants →
type aliases → Protocols / TypedDicts → enums and frozen value objects →
classes (base before subclass) → module functions in call order, entry point last
```

Inside a class: `__slots__`, `__init__`, dunders, properties, public methods, then private
methods as pipeline stages in flow order, each followed by the leaf helpers it owns.

**Log channels are observable surface.** `_LOGGER` names the component a user filters on, not the
file it happens to live in; a module extracted out of another keeps the original channel rather
than silently moving what downstream logging config has to match.

## Layout

```text
risa/
  __init__.py       explicit re-exports of the public API (py.typed requires `as` form)
  _about.py         package metadata (__version__, __author__, ...)
  binding.py        the functools.partial fiction: bind, Binding, HandlerBinder, resolve
  client.py         Client ABC + Gateway/Rest implementations, registry, transports
  context.py        Context base + ComponentContext, respond/defer, the response gate
  di.py             Contexts (DEFAULT/COMPONENT/MODAL), INJECTED re-export
  dispatch.py       Dispatcher: route, decode, run, watchdog (logs as `risa.client`)
  errors.py         RisaError-rooted hierarchy
  view.py           View base, @register, @handler machinery, AutoDefer
  internal/         not public API; modules here carry no leading underscore
    wire.py         the printable alphabet every id is written in
    codec.py        custom_id encode/decode, cookie and handler tokens
    constants.py    Discord limits and the attribute names risa stamps
    registry.py     ViewMeta, HandlerRecord, the cookie -> view registries
  ui/
    build.py        BuildContext, the build pass, and Rendered (the send-kwargs surface)
    nodes.py        the V2 node set
  py.typed
tests/              mirrors the package layout
testbot/            manual test bot against a real Discord app (TOKEN via .env)
test_bots/          one-off probe scripts against the live API
noxfile.py          reformat / format-check / ruff / pyright / pytest / audit
ruff.toml           lint + format config (NOT in pyproject.toml)
```

Flat layout, not `src/`. Package config lives in `pyproject.toml` except ruff, which has its
own `ruff.toml`. Anything under `internal/` is private by virtue of living there — those
modules do not take a leading underscore.

## Design direction

Built so far (the `foundation` rewrite): the identity half of the `custom_id` codec
(cookies, handler tokens, the chunk-framed layout with a reserved args tail), the view
registry, the handler machinery (`@handler` with id/version/defer, the `HandlerMethod`
descriptor, zero-argument `bind()`), `@register` with the handler census, the complete
node layer (all inert V2 nodes, `Button`, the five selects behind one `Select` base) with
the build pass carrying the two risa-owned checks, flat dispatch with the routing-failure
ladder, and the response surface: `respond`/`defer`/`acknowledge` on a generic `Context`
base, the `Response` handle, and the auto-defer watchdog (`risa.AutoDefer`, per handler /
view / client). `HANDOFF.md` tracks the precise state and the next step.

**The dual-placement state architecture** decided in DESIGN.md (§2.3, §7) is the target,
not yet the code: nothing under `risa/state/` exists on this rewrite, wire args
(converters, the signature fingerprint, real `bind(*args)`), `rerender()`/`edit(layout)`,
the event/204 REST pattern, `on_outdated` and modals are all still to come. DESIGN.md
remains the authority and §15 holds the build order.

Treat the rest as intent, not as an implemented contract.

- **Components V2 is the model; V1 falls out of it.** V1 is just a tree whose top level
  contains only action rows. There is one tree type and one layout pass. Do not hard-wire the
  5x5 grid into the type surface — that is precisely what stops miru, flare and lightbulb from
  supporting V2.
- **Nesting is a layout concern; dispatch is flat.** Only action-row children, a `Section`
  accessory button, and modal text inputs are interactive. The tree is flattened to a sparse
  set of interactive leaves at build time and routed against a flat dict. Tree depth costs
  nothing at runtime.
- **State placement is per view** (DESIGN.md §7.1): `InMessage` (default) serializes the
  durable fields into the spare custom_id characters of the rendered components and reads
  them back from `interaction.message` — zero infrastructure, restart-proof, capacity-
  bounded; `InStore` keeps one record in a pluggable, developer-chosen `Store` behind a
  replicated random key — unbounded, expirable (`ttl` required), multi-process-correct with
  a distributed store. Fields are durable by default; `risa.Prop[T]` marks per-dispatch
  props refilled in `load()`. Handler code is identical under both placements.
- **`custom_id` wire format** (DESIGN.md §6.1): `[ver:1][cookie:6][handler:2][idx:1]`
  `[frag_len:1][fragment][fingerprint ‖ args]` within Discord's 100-character limit; the
  fragment is the component's slice of the state anchor (inline blob or store key, led by a
  1-char placement tag). The cookie hashes the view name *and* its schema version, the
  handler token hashes the handler id *and* its version, and the 2-char fingerprint catches
  a signature edited without a version bump — schema, signature, and placement changes all
  fail closed (§6.4, §7.1). Unrecognised `custom_id`s must be ignored silently so risa can
  coexist with other handlers; a recognised one that resolves to nothing is logged, since
  that means a genuine routing failure.
- **Dependency injection is linkd**, so a manager can be shared with lightbulb. Handlers are
  dispatched inside `Contexts.DEFAULT` nested with `Contexts.COMPONENT`; `ctx` is passed
  positionally rather than injected, so handlers keep working when DI is disabled.
- **Auto-defer sits above the handler, not inside it.** Store I/O happens during dispatch,
  before user code runs, so the defer timer starts at decode. Default to
  `DEFERRED_MESSAGE_UPDATE` (silent ack), not `DEFERRED_MESSAGE_CREATE`.
- **Discord's layout limits are deliberately not policed.** Do not add a rule table for "5 per
  row", "1-3 text displays", "40 total" and the like. A stale table that is *too strict* blocks
  users outright when Discord loosens a limit, while one that is too lax merely lets the
  request fail as it would have anyway — and Discord loosens far more often than it tightens.
  The structural half (a container may not nest a container, a section takes only text
  displays, a row takes only interactive components) is already enforced for free by the node
  constructors' parameter types. risa checks only what Discord cannot: `custom_id` overflow,
  and a component whose handler belongs to a different view.

## Gotchas

- `ComponentInteraction` does **not** expose the V2 `id` field — hikari never deserialises it.
  `custom_id` is the only routing channel available.
- A message with `IS_COMPONENTS_V2` set cannot carry `content` or `embeds`; everything is
  components. hikari sets that flag automatically when it sees a V2 builder, so a view renders
  the whole message body, not just its components.
- `ComponentBuilder.build()` returns a `(payload, attachments)` **tuple**, unlike
  `SelectOptionBuilder.build()` which returns a bare mapping.
- `COM812` (which `select = ["ALL"]` would pull in) is in the `ignore` list: it conflicts with
  the formatter, which owns trailing commas.
