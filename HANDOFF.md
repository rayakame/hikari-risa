# Handoff — continuing the `foundation` rewrite

State of the ground-up rewrite as of 2026-08-03, and the plan for what comes next.
`DESIGN.md` is the authority on the target; this file is only "where we are and what to
do next". The old near-complete implementation lives on the `start-implementation`
branch for reference — read it for ideas, don't copy it wholesale.

## Setting up on a new machine

1. Fresh clone, or in an existing clone: `git fetch origin && git checkout foundation
   && git reset --hard origin/foundation`. **Do not `git pull`** an old clone — the
   branch history was rewritten (secret redaction), so old local commits share no
   hashes with the remote and a pull would try to merge the two histories.
2. Recreate `testbot/.env` with `TOKEN=...` — it is gitignored and does not travel.
   The probe scripts under `test_bots/` read `DISCORD_TOKEN` / `TEST_CHANNEL_ID` from
   the environment.
3. **Rotate the bot token in the Discord developer portal first.** The old one was
   hardcoded in git history (since redacted, never pushed) and must be treated as
   burned.

## Working rules for this rewrite

- **No docstrings, no comments** until a dedicated final pass. The comment lint
  families (`CPY`, `D`, `DOC`, `ERA`, `FIX`, `TD`) are disabled in `ruff.toml` for
  exactly this reason; existing MIT headers stay, new files may omit them for now.
- **Never `tuple` in annotations.** Public signatures promise
  `collections.abc.Sequence`; storing a tuple internally is fine.
- Everything runs through uv/nox: `uv run --group nox nox` (all sessions) or
  `-s ruff pyright pytest` while iterating; `uv run --group pytest pytest -q` for the
  fast loop. pyright strict is the gate.
- Commits are authored solely by the repository owner. No co-author trailers, no
  generated-with footers. Never commit or push without deciding to.
- Tests mirror the package: `risa/internal/wire.py` ↔ `tests/risa/internal/test_wire.py`.

## What is built on `foundation`

- `risa/ui/nodes.py` — every inert V2 node (`Container`, `Section`, `Row`,
  `TextDisplay`, `Separator`, `MediaGallery`+`Item`, `File`, `Thumbnail`,
  `LinkButton`, `PremiumButton`), each owning its hikari emission via a `build()`
  method with a narrowed return type. The alias lattice (`RowChild`,
  `SectionAccessory`, `ContainerChild`, `TopLevelComponent`, `Layout`) carries the
  structural rules; Discord's numeric limits are deliberately not policed (§5.2).
  `ui.build(layout)` flattens a `Layout`; `Client.build(view)` is the async send path.
- `risa/internal/wire.py` — the 92-char printable alphabet (no `"`, no `\`),
  `pack_uint`/`unpack_uint` (fixed-width, MSB-first), `pack_bytes`/`unpack_bytes`
  (base85, length-validated), `pack_digest` (blake2s, `digest_size` ceil-divided from
  the requested chars). Law: packs raise, unpacks return `None`.
- `risa/internal/codec.py` — bite 1: `make_cookie` (6 chars) / `make_handler_token`
  (2 chars), the `CustomID` struct whose `encode()` is the single overflow choke
  point, and fail-soft `parse_custom_id`. Layout:
  `[ver:1][cookie:6][handler:2][idx:1][frag_len:1][fragment][tail]`; header 11,
  `MAX_FRAGMENT_LENGTH` 89. The `tail` is reserved for the future `sig‖args`.
- Routing skeleton — `ViewMeta.key` *is* the cookie; `Client._process_interaction`
  parses every incoming `custom_id`, stays silent for foreign components, debug-logs
  unknown cookies and recognised views. Dispatch itself does not exist yet.
- 133 tests across `tests/risa/`, all green under ruff `ALL` + pyright strict.

## Next step (in progress): the handler machinery in `view.py`

Zero-argument handlers only — converters and wire args are the step after. Build in
this order; each piece is testable before the next exists:

1. *(recommended first)* Minimal `risa/context.py`: a `Context` holding the
   `hikari.ComponentInteraction` behind a property. Exists so handler callbacks have
   a typed `ctx`; the response surface grows onto it much later.
2. `BoundHandler` — frozen `msgspec.Struct`: `handler_id`, `version`, `token`,
   `payload: str = ""`. The only object that crosses from the view layer into the
   node layer; a `Button` stores this and nothing else.
3. `HandlerMethod` — the descriptor that replaces the function on the class. Holds
   the wrapped function, `handler_id` (defaults to the method name — identity is
   declared so a rename never retires live components), `version`, and the token
   precomputed once via `codec.make_handler_token`. `__get__` returns *itself* on
   class access (how registration will find handlers) and a `BoundHandlerMethod` on
   instance access (what `render()` uses). Two `typing.overload`s on `__get__` for
   the two access forms. Store the callback as
   `Callable[..., Awaitable[None]]` — the precise `Callable[[View, Context], ...]`
   fails on real methods because parameters are contravariant (`self: Poll` does not
   satisfy "accepts any View"); precision returns at the decorator boundary.
4. `BoundHandlerMethod` — `bind() -> BoundHandler` (today just packages identity with
   an empty payload; stays a method because argument encoding will happen here,
   eagerly, per §6.3) and `__call__(ctx)` delegating to the wrapped function so
   handlers can still call each other. Also `ZeroArgHandler`, the runtime-checkable
   protocol (`bind() -> BoundHandler`) that later lets `Button(self.close)` skip the
   explicit bind.
5. The `@handler` decorator, dual form: bare and `@handler(handler_id=..., version=...)`
   — kwarg is `handler_id`, not `id` (ruff A002). One implementation, two overloads.
6. Registration: `register()` collects `HandlerMethod`s via `inspect.getmembers`
   (class access → the descriptor returns itself; MRO walk collects inherited
   handlers free), builds `token -> HandlerRecord(callback, handler_id, version)`
   (record lives in `registry.py`; `ViewMeta` gains
   `handlers: Mapping[str, HandlerRecord]` with a `default_factory=dict[...]`).
   Token clash → new `DuplicateHandlerError` in `errors.py` (fields: view name,
   token, both colliding ids+versions). Same `handler_id` at *different* versions is
   legal — that is the §6.4 migration path.
7. Tests: token determinism and `== make_handler_token(...)`; default id is the
   method name; pinned `handler_id` keeps tokens stable across a rename; version
   bump changes the token; class vs instance access types; `bind()` payload is `""`;
   calling the bound method runs the body; duplicate `(id, version)` raises;
   zero-handler views register with an empty mapping.

## After that, in order

1. **`Button` + `BuildContext`** — `Button` subclasses `Interactive`, joins
   `RowChild`/`SectionAccessory`; node `build()` grows a context parameter carrying
   cookie, known-token set (foreign handler → `LayoutError` with tree path), and
   fragments (empty until the state layer). The two risa-owned checks land here.
2. **First dispatch** — `_process_interaction` looks up `custom_id.handler` in
   `meta.handlers`, constructs the view, calls the handler with a `Context`. Testbot
   round trip: a clicked button runs a handler across a restart.
3. **Codec bite 2** — arg converters (sync only: `int`/`str`/`bool`/`Enum`/
   `Snowflake`, byte-oriented), length-prefixed frames in the `tail`, the 2-char
   signature fingerprint over type-ids (never parameter names),
   `SignatureMismatchError`. Then `bind(*args)` becomes real (§6.3/§6.4).
4. The five selects, then the state layer (DESIGN §7, §15 has the build order).
