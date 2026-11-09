# Contributing to hikari-risa

`hikari-risa` is a component handler for [hikari](https://github.com/hikari-py/hikari), built
Components V2 first. It is published to PyPI as `hikari-risa` and ships `py.typed`. This is a **library**, not
an application: the public API surface and its type annotations are the product.

## Commands

Everything runs through `uv` and `nox`. Do not `pip install` anything.

```sh
uv run --group nox nox            # reformat, ruff, slotscheck, pyright, pytest, audit, docs
uv run --group nox nox -s ruff    # a single session
uv run --group nox nox -s compat  # the tests at the oldest and newest versions the dependency ranges allow
uv run --group pytest pytest -q   # fast inner loop while iterating
uv lock                           # after ANY dependency change
uv lock --upgrade                 # periodically; plain `uv lock` never bumps existing pins
```

`uv.lock` is committed. The nox sessions run `uv sync --locked`, so a stale lockfile fails CI. The lockfile holds hikari
where lightbulb pins it, which is why CI also runs `compat`: it installs without the lockfile, once at the lowest
versions of the direct dependencies and once at the highest.

The `audit` session runs `uv audit`, which needs **uv >= 0.9** (the subcommand is still behind
`--preview-features audit-command`). Because `nox[uv]` puts uv in the lockfile, the project's
uv is pinned per-project and the copy in `.venv` shadows any global install. If `audit` fails
with `unrecognized subcommand`, the fix is `uv lock --upgrade`, not `uv self update`.

Plain `uv lock` is conservative: it resolves only what changed and leaves existing pins alone.
Without a periodic `uv lock --upgrade`, the lockfile silently rots and `audit` starts failing
on transitive dependencies.

The `docs` session checks the API reference against the code (every public name on one page,
everything public annotated), builds the documentation site (`zensical build --strict`) and
checks its invariants. To preview it locally:

```sh
uv run --group nox nox -s docs
uv run --group docs zensical serve
```

Each section of the site opens on an overview, its `index.md`, whose `risa-overview` block lists the section's pages:
`check_site` fails a guide page that is missing from it, and `check_api` fails a line of the API overview that differs
from the first line of the module's docstring.

A guide page links a concept to the chapter that explains it, and that chapter links it to the API reference; a concept
with no chapter yet links to the reference. A page's risa code comes from a real file in `docs/examples/`, which starts with the license header and which ruff and
pyright check; another library's code, such as miru's on the comparison page, stays inline. Mark a section with `# --8<-- [start:name]` and `# --8<-- [end:name]`, show it on the page with
`--8<-- "file.py:name"` inside a fence, and show the whole file with `--8<-- "file.py:20:"` in a collapsed
`??? example "Full file"` block. For its preview, add an entry to `docs/examples/previews.toml` and put one top-level line,
`--8<-- "previews/<id>.html"`, directly after the code block, never inside a `div markdown`.
An example's module-level `add_dependencies(client)` runs once before the first build, the `ids` kind draws the real `custom_id`
of each component, `content` on a `view` preview draws text above a view made of rows alone, and the `log` kind shows what
`add_view` warns about. The `live` kind draws the landing page's counter, whose script works out each `custom_id` itself;
a test holds it against `client.build`, which needs `node` and skips without it outside CI.

The changelog page shows `CHANGELOG.md`.

The artwork in `assets/web/` is generated from the originals in `assets/`. Run
`uv run --group images python -m tools.optimize_images` only when the artwork changes.

The site is versioned and published to the `gh-pages` branch by `.github/workflows/docs.yml`. A push to
main replaces `main/`; a published release, once it is on PyPI, replaces its minor folder (`1.0/`), and `stable/` when it is the
newest release. Release tags are `vX.Y.Z` with no suffix, and GitHub pre-releases are not published. With no release
published, `main` carries the `stable` alias in `versions.json`, so no page shows the outdated banner.
`tools/docs/publish.py` places a built version and keeps `versions.json` and the root redirect.

A pull request from a branch of this repository also gets a preview. `.github/workflows/docs-preview.yml` places it under
`pr/<number>/` on `gh-pages` and names the link in a comment on the pull request, and `docs-preview-remove.yml` removes it
when the pull request closes. A preview is missing from `versions.json`, so it shows no version selector and no outdated
banner, and it asks search engines not to index it, which `check_site` checks in both directions. A fork gets no preview,
because its code must not run beside a token that can write to `gh-pages`. Neither does a bot's bump, which can change the
actions of that job, unless it bumps what draws the site, such as Zensical, or only the lockfile; the pull request of a
release gets one too. The job that publishes takes its tools from the default branch, so a change to
`tools/docs/publish.py` reaches a preview only after its merge.

The Redis tests run against fakeredis everywhere. Set `RISA_TEST_REDIS_URL` (for example
`redis://localhost:6379/0`) to run them against a real Redis as well; CI does.

The Postgres tests need a real server and are skipped without `RISA_TEST_POSTGRES_URL`.
Each test works in tables of its own and drops them afterwards, so any database you can
create tables in will do:

```sh
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:18-alpine
RISA_TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run --group nox nox
```

## Branches and pull requests

- Work on a branch; `main` only moves through pull requests.
- Pull requests are squash-merged, so the PR title becomes the commit subject on `main`.
- Never skip hooks with `--no-verify`.
- A pull request that changes what users of risa see adds a changelog fragment to `changes/`, named after its number
  and the kind of change, such as `71.feature.md`; the kinds are `breaking`, `deprecation`, `feature` and `bugfix`.
  towncrier adds the link to the pull request. `changelog.yml` runs `towncrier check`, which a pull request users do not
  notice skips with the label `skip-changelog`.

## Releasing

1. Run the workflow Release from the Actions tab with the version, such as `1.2.0`. Through `nox -s release` it raises
   the version in `pyproject.toml`, `uv.lock` and `risa/_about.py` and turns the fragments in `changes/` into the
   version's section of `CHANGELOG.md`, then opens a pull request from `release/1.2.0`.
2. Merge it. The workflow then publishes the GitHub release `v1.2.0` on the merge commit, with the version's section of
   `CHANGELOG.md` as its notes.

The workflow acts as a GitHub App with read and write access to contents and pull requests, whose client ID and private
key are the secrets `RELEASE_APP_CLIENT_ID` and `RELEASE_APP_PRIVATE_KEY`, since a pull request or release made with the
default token starts no workflows.

`.github/workflows/publish.yml` builds the package, checks its contents and the tag against it, and uploads it to PyPI
through trusted publishing from the `pypi` environment, so no PyPI token is stored anywhere. Then it calls `docs.yml` for
the release's docs, so they never name a version PyPI lacks. A GitHub pre-release publishes neither.

## Toolchain

| | |
|---|---|
| Python floor | 3.12 (PEP 695 `type X = ...` and `class Foo[T]` are available) |
| Formatter / linter | ruff, `select = ["ALL"]`, `preview = true`, line length 120 |
| Type checker | pyright, `typeCheckingMode = "strict"` |
| Tests | pytest, `asyncio_mode = "auto"`, warnings are errors |
| Slots | slotscheck, `require-subclass = true` (see `[tool.slotscheck]`) |

pyright strict is the CI gate and is non-negotiable: for a `py.typed` library it is the
guarantee shipped to downstream users. `ty` was evaluated in July 2026 and rejected: no strict
mode, no annotation-completeness rules, and Protocol support is its weakest area (4 of 11
conformance tests unsupported, including variance). Revisit if
[astral-sh/ty#527](https://github.com/astral-sh/ty/issues/527) closes.

## Code style

Most of these are enforced by ruff `ALL`; they are listed so that code is compliant the first
time rather than after the lint output.

- **Every `.py` file starts with the 19-line MIT header.** Copy it verbatim from an existing
  file. This includes test files and `__init__.py`.
- **`from __future__ import annotations` is a required import** in every module.
- **One import per line.** `force-single-line = true`: no `from x import a, b`.
- **Every module has a docstring, and so does all of the public API, kept short.** numpy
  style, saying what a caller needs to know rather than how it works inside; ruff's `D` and
  `DOC` rules enforce it, and every public parameter has an entry in `Parameters` (in `Attributes`
  for a constructor's, as for a Struct), except those of constructors only risa calls
  (`_RISA_ONLY_CONSTRUCTORS` in `tools/docs/check_api.py`). In `risa/internal/`, `dispatch.py`, the session machinery in
  `state/` and `tools/`, classes and functions carry one only where they are hard to follow without it:
  a layout, a lifecycle, an invariant. Tests and the noxfile need none. A one-line
  docstring needs no `Returns` or `Raises` section. A longer one documents what it returns,
  if anything, and what it raises, including what a callee raises, which is why `DOC502`
  is off. Review bots that ask for a docstring on every internal function are declined
  with this rule.
- **Docstrings of the public API name no types.** The reference takes them from the code, so
  `self.needed: int = needed` carries its annotation even where pyright would infer it,
  a public `typing.Final` is subscripted, and a `Returns` or `Yields` section opens with a lone `:`
  line, since numpy style would read its first line as a type.
- **Comments only for a why the code cannot show**: a workaround, a constraint that is not
  obvious, a link to an issue. One line, never what the code does or what changed. The
  license header and linter directives are not comments and stay.
- **`__all__` is a tuple, sorted alphabetically**, declared right after the imports.
- **`collections.abc.Sequence`, never `tuple`, in annotations.** Public signatures promise a
  sequence; storing a tuple internally is fine.
- **Exceptions are named with an `Error` suffix** and live in `risa/errors.py`.
- **Exceptions build their own messages in `__init__`** and expose structured attributes.
  `TRY003` and `EM101` forbid long message literals at the raise site, and callers should be
  able to branch on fields rather than parse strings. See `risa/errors.py` for the pattern.
- **Nothing fails silently.** No `contextlib.suppress`, no bare `except: pass` for anything
  that changes behaviour: log it or raise it. Each module logs through
  `_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.<module>")`; the library
  never configures logging itself.
- **Type as strictly as Python allows.** `typing.Any` is not an acceptable shortcut, and
  neither is loosening a signature to satisfy a lint rule: restructure the code instead.
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

**Order.** A file reads top-down in the bands below, and within a band in dependency order:
nothing appears above something it names. PEP 695 `type` aliases are exempt: they are lazily
evaluated, so they sit at the top regardless. Private module functions are the implementation of
the classes above them, so they follow the class band rather than preceding it.

```text
MIT header → from __future__ → imports → __all__ → _LOGGER → Final constants →
type aliases → Protocols / TypedDicts → enums and frozen value objects →
classes (base before subclass) → module functions in call order, entry point last
```

Inside a class: `__slots__`, `__init__`, dunders, properties, public methods, then private
methods as pipeline stages in flow order, each followed by the leaf helpers it owns.

## Layout

```text
risa/
  __init__.py       explicit re-exports of the public API (py.typed requires `as` form)
  _about.py         package metadata (__version__, __author__, ...)
  binding.py        the functools.partial fiction: bind, Binding, HandlerBinder, resolve
  client.py         Client ABC + Gateway/Rest implementations, registry, transports
  codecs.py         the wire codecs: base85 built in, radixly through the [radixly] extra
  context.py        Context base + ComponentContext, respond/defer, the response gate
  di.py             Contexts (DEFAULT/COMPONENT/MODAL), INJECTED re-export
  dispatch.py       Dispatcher: route, decode, run, the watchdog
  errors.py         RisaError-rooted hierarchy
  view.py           View base, @register, @handler machinery, AutoDefer
  internal/         not public API; modules here carry no leading underscore
    wire.py         the printable alphabet every id is written in
    codec.py        custom_id encode/decode, cookie and handler tokens
    constants.py    Discord limits and the attribute names risa stamps
    registry.py     ViewMeta, HandlerRecord, the cookie -> view registries
    reader.py       the message-component walk that reassembles chunked state
    locks.py        KeyedLocks, shared by MessageBackend, MemoryStore and LeaseLocks
    leases.py       LeaseLocks, the cross-process lease lock behind RedisStore and PostgresStore
  state/            risa's own state machinery; users never implement against it
    schema.py       StateSchema: durable fields, packing, prefix fingerprints
    anchor.py       anchor dialects, splitting and rejoining across components
    placement.py    InMessage / InStore policy objects
    serde.py        the store-entry envelope {v,n,f,d}
    backend.py      StateBackend / StateSession
    stateless.py    the degenerate placement: nothing to keep
    message.py      MessageSession + version cache
    stored.py       StoredSession, KeyNamespace
  store/            the storage extension point: what implementers target and the
                    implementations risa ships; imports nothing from state/
    protocol.py     Store, VersionedEntry, DEFAULT_LOCK_TIMEOUT
    memory.py       MemoryStore
    redis.py        RedisStore (the [redis] extra)
    postgres.py     PostgresStore (the [postgres] extra)
    testing.py      verify_store, the conformance kit for Store implementations
  ui/
    build.py        two-phase build: plan (measure) + emit, and Rendered (the send kwargs)
    nodes.py        the V2 node set
  py.typed
docs/               site pages, stylesheets, fonts
  api/              the reference pages
  get-started/      an overview, installation, the first view, coming from miru
  by-example/       an overview of the walkthrough and one page per chapter
  examples/         the example files the pages show, and previews.toml, the list of their previews
  javascripts/      the script behind the landing page's counter, which fills its frame and works out each custom_id
  stylesheets/      the theme (risa.css), the Discord frame (discord.css) and the diagrams (diagrams.css)
overrides/          theme template overrides for the site
changes/            changelog fragments waiting for the next release
CHANGELOG.md        every release's changes, which the docs show too
assets/             the artwork and the social preview; web/ holds the optimized copies the site and README use
tools/              optimize_images, check_dist (what the wheel and sdist hold), docs/prepare (stage artwork, draw previews), docs/manifest (read previews.toml), docs/render_example (build an example's views), docs/discord_html (draw a preview), docs/check_api (reference coverage and annotations), docs/griffe_structs (msgspec Struct signatures for the reference), docs/check_site (site invariants), docs/publish (place a version or a pull request's preview in gh-pages)
zensical.toml       the docs site configuration
tests/              mirrors the package layout
noxfile.py          reformat / format-check / ruff / slotscheck / pyright / pytest / compat / audit / docs / release
ruff.toml           lint + format config (NOT in pyproject.toml)
```

Flat layout, not `src/`. Package config lives in `pyproject.toml` except ruff, which has its
own `ruff.toml`. Anything under `internal/` is private by virtue of living there; those
modules do not take a leading underscore.

## Design direction

- **Components V2 is the model; V1 falls out of it.** V1 is just a tree whose top level
  contains only action rows. There is one tree type and one layout pass. Do not hard-wire the
  5x5 grid into the type surface; that is precisely what stops miru, flare and lightbulb from
  supporting V2.
- **Nesting is a layout concern; dispatch is flat.** Only action-row children, a `Section`
  accessory button, and modal inputs are interactive. The tree is flattened to a sparse set of
  interactive leaves at build time and routed against a flat dict. Tree depth costs nothing at
  runtime.
- **State placement is per view.** `InMessage` (the default) serializes the
  durable fields into the spare custom_id characters of the rendered components and reads
  them back from `interaction.message`: zero infrastructure, restart-proof,
  capacity-bounded. `InStore` keeps one record in a pluggable, developer-chosen `Store`
  behind a replicated random key: unbounded, expirable, multi-process-correct with a
  distributed store. Fields are durable by default; `risa.Prop[T]` marks per-dispatch props
  refilled in `load()`. Handler code is identical under both placements.
- **`custom_id` wire format:**
  `[ver:1][codec:1][cookie:6][handler:2][idx:1][frag_len:1][fragment][fingerprint ‖ args]` within
  Discord's 100-character limit. The fragment is the component's slice of the state anchor
  (inline blob or store key, led by a 1-char placement tag). The cookie hashes the view name
  *and* its schema version, the handler token hashes the handler id *and* its version, and
  the 2-char fingerprint catches a signature edited without a version bump: schema,
  signature and placement changes all fail closed. Unrecognised `custom_id`s
  are ignored silently so risa can coexist with other handlers; a recognised one that
  resolves to nothing is logged, since that means a genuine routing failure.
- **Dependency injection is linkd**, so a manager can be shared with lightbulb. Handlers are
  dispatched inside `Contexts.DEFAULT` nested with `Contexts.COMPONENT`; `ctx` is passed
  positionally rather than injected, so handlers keep working when DI is disabled.
- **Auto-defer sits above the handler, not inside it.** Store I/O happens during dispatch,
  before user code runs, so the defer timer starts at decode. The default is
  `AutoDefer.OFF`; the silent `DEFERRED_MESSAGE_UPDATE` is what `UPDATE` sends.
- **Discord's layout limits are deliberately not policed.** Do not add a rule table for
  "5 per row", "1-3 text displays", "40 total" and the like. A stale table that is *too
  strict* blocks users outright when Discord loosens a limit, while one that is too lax merely
  lets the request fail as it would have anyway, and Discord loosens far more often than it
  tightens. The structural half (a container may not nest a container, a section takes only
  text displays, a row takes only interactive components) is already enforced for free by the
  node constructors' parameter types. risa checks only what Discord cannot: `custom_id`
  overflow, and a component whose handler belongs to a different view.

## Gotchas

- `ComponentInteraction` does **not** expose the V2 `id` field; hikari never deserialises it.
  `custom_id` is the only routing channel available.
- A message with `IS_COMPONENTS_V2` set cannot carry `content` or `embeds`; everything is
  components. hikari sets that flag automatically when it sees a V2 builder, so a view renders
  the whole message body, not just its components.
- `ComponentBuilder.build()` returns a `(payload, attachments)` **tuple**, unlike
  `SelectOptionBuilder.build()` which returns a bare mapping.
- `COM812` (which `select = ["ALL"]` would pull in) is in the `ignore` list: it conflicts with
  the formatter, which owns trailing commas.
