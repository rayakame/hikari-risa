# Handoff — continuing the rewrite on `wire-args`

State as of 2026-08-13. `DESIGN.md` is the authority on the target; this file is only
"where we are and what to do next". The old near-complete implementation lives on the
`start-implementation` branch as a DX reference — read it for ideas, error wording and
test cases; its internals are not a blueprint.

## Branch situation

- **`main`** carries the first milestone (PR #13): the complete component interaction
  loop, restart-proof, for zero-argument handlers on stateless views. All work branches
  off `main` and lands back on it as PRs.
- **`wire-args`** is the current working branch: codec bite 2 (DESIGN §6.3/§6.4).
- **`foundation` is retired.** PR #13 was squash-merged, so its commits are not
  ancestors of `main` — never branch from it or PR it again. Its granular history
  stays readable inside PR #13.

## Setting up on a new machine

1. **Fresh clone strongly preferred**; `git checkout wire-args` (or branch anew off
   `main`). The histories of `main` and `start-implementation` were rewritten on
   2026-08-13 (attribution scrub) — an older clone must `git fetch origin` and
   `git reset --hard origin/<branch>` on every branch it touches; never `git pull`.
2. Recreate `testbot/.env` with `TOKEN=...` — it is gitignored and does not travel.
   The probe scripts under `test_bots/` read `DISCORD_TOKEN` / `TEST_CHANNEL_ID` from
   the environment.
3. Commits are SSH-signed; the key sometimes drops out of the agent mid-session. If a
   commit fails on `ssh_askpass`, run `ssh-add ~/.ssh/github` interactively.

## Working rules for this rewrite

- **The maintainer implements by hand.** Claude explains the next step in depth — one
  step at a time, what/where/why/DX — then reviews, fixes minor things, and writes
  tests; it writes feature code only when explicitly asked. Deep-dive one step, stop,
  wait.
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
- House laws worth knowing before touching the codec: **packs/encodes raise, unpacks/
  decodes return `None`** (render-time failures are the developer's bug and deserve a
  traceback; click-time input is client-forgeable and must fail soft). Errors build
  their messages in `__init__` and expose structured fields. Loose storage, precise
  boundary: internals may store `Callable[..., Awaitable[None]]`-grade types as long
  as the public decorator/`bind()` boundary is precisely typed.

## What is built (on `main`, PR #13)

The component interaction loop end to end — render → build → send → click → decode →
route → dispatch → respond — restart-proof, for zero-argument handlers on stateless
views:

- `internal/wire.py` — 92-char printable alphabet, `pack_uint`/`unpack_uint`, base85
  `pack_bytes`/`unpack_bytes`, `pack_digest`.
- `internal/codec.py` — cookies (6 chars, name+version), handler tokens (2 chars,
  id+version), the `CustomID` struct (`encode()` is the overflow choke point),
  fail-soft `parse_custom_id`. Layout `[ver:1][cookie:6][handler:2][idx:1]
  [frag_len:1][fragment][tail]`.
- `view.py` — `BoundHandler`, the `HandlerMethod` descriptor (class access → itself,
  instance access → `BoundHandlerMethod`), zero-arg `bind()`, `ZeroArgHandler`,
  dual-form `@handler` (id/version/defer), `AutoDefer`, `@register` with the handler
  census (`token -> HandlerRecord` on `ViewMeta`; duplicate identity and 2-char hash
  collisions raise `DuplicateHandlerError`).
- `ui/nodes.py` — all inert V2 nodes, `Button`, the five selects behind one `Select`
  base, `SelectOption`; `_resolve_handler` normalization; `Interactive._routing_id`
  carries both risa-owned checks (foreign handler → path-qualified `LayoutError`,
  overflow via `CustomID.encode`); `BuildContext` threads cookie/tokens/fragment
  counter; path grammar `Container[0] > Row[2] > Button[1]`.
- `context.py` — `Context[T]` base (component/modal bound), `ComponentContext`
  (`message` narrowed, `values`, `resolved`), the response gate (`DispatchState`),
  `respond()` → `Response` handle, `defer()` (raises `AlreadyRespondedError`),
  `acknowledge()` (idempotent sibling; watchdog + end-of-dispatch entry point).
- `client.py` — dispatch with the routing-failure ladder (foreign: silent; unknown
  cookie: DEBUG; token miss: WARNING, the `on_outdated` placeholder; handler failure:
  ERROR, contained), `meta.cls()` as the state layer's construction placeholder, the
  auto-defer watchdog (timer at decode; stood down under the response lock; risa's own
  ack REST calls are contained too), client-level `auto_defer`/`auto_defer_delay`.
- `testbot/bot.py` — live demo: ping, slow button (watchdog), text select, user select.

## Current work: codec bite 2 — wire args (DESIGN §6.3, §6.4)

Goal: `bind("Red", 5)` bakes per-component args into the custom_id tail; dispatch
decodes them back and calls the handler with them; a 2-char signature fingerprint
makes an in-place signature edit fail closed instead of misreading old components.

### Sub-step 1 — converters + frames *(landed, 235 tests green)*

In `internal/codec.py`: `ArgConverter` ABC (`type_id` property; `encode(object) -> str`
raises; `decode(str) -> object | None`), with `IntConverter(target)` (minimal-width
little-endian signed bytes via `(bit_length + 8) // 8`, base85-rendered; `target` is
`int` or `hikari.Snowflake`, so both share type_id `"i"`; rejects `bool` explicitly
since `isinstance(True, int)`), `StrConverter` (UTF-8 → base85), `BoolConverter`
(single alphabet char, strict length), `EnumConverter(enum_cls, inner, type_id)`
(wraps Int/Str converter, type_id `"ei"`/`"es"`; decode validates membership so a
deleted member fails closed). `pack_frames`/`unpack_frames`: each arg is
`[len:1 wire char][data]`, ≤91 chars per arg, strict walk, `""` ↔ `[]`. A property
test pins that everything emitted stays inside `wire.ALPHABET`.

### Sub-step 2 — `HandlerSignature` *(next)*

The annotation→converter bridge, in `internal/codec.py`:

- A resolver mapping one annotation to a converter instance: `int` → `IntConverter(int)`,
  `hikari.Snowflake` → `IntConverter(hikari.Snowflake)`, `str` → `StrConverter()`,
  `bool` → `BoolConverter()`, enum subclasses → `EnumConverter` (inner picked by
  inspecting member value types; mixed-value enums rejected). Anything else — unions
  (including `X | None`), containers, unknown classes — resolves to "not a wire type",
  which is not an error by itself: it ends the wire section (DI territory).
- `HandlerSignature`: the ordered converter chain for the contiguous convertible
  prefix after `(self, ctx)`, how many are required (no default), and the fingerprint:
  `wire.pack_digest("".join(type_ids), chars=2)`. A convertible parameter *after* the
  first non-convertible one (or after a `linkd.INJECTED` default) raises the new
  `HandlerSignatureError` — otherwise a plain `int` would silently become a DI lookup.
- Resolution must run **lazily at registration**, via `typing.get_type_hints` on the
  raw callback — annotations are strings under `from __future__ import annotations`
  and only resolvable once the defining module is fully imported.

### Then, in order

3. **Thread signatures through registration** — `HandlerMethod` holds a lazy signature;
   the `@register` census resolves + validates; `HandlerRecord` grows what dispatch
   needs.
4. **Real `bind()`** in `view.py` — normalize keywords to positions, contiguous-prefix
   + trailing-defaults rules, eager encode through the converters (`TypeError`/
   `ValueError` → `ArgBindError` at the render site), payload = fingerprint + frames.
   `ParamSpec` typing so `bind` is statically checked; `Button(bare_handler)` on a
   handler with required wire args now fails at render via the same path.
5. **Dispatch decode** in `client.py` — split fingerprint from frames, compare against
   the resolved chain (`SignatureMismatchError`: logged at ERROR, fail closed — the
   loudest failure in the library, §6.4), decode each frame (`None` → fail closed),
   call `callback(view, ctx, *decoded)`, current Python defaults fill omitted
   trailing params.
6. **Testbot poll** — per-option `bind(name)` vote buttons, votes counted, surviving a
   restart.

New errors along the way (`errors.py`, house pattern): `HandlerSignatureError`
(registration), `ArgBindError` (render), `SignatureMismatchError` (dispatch; logged,
never raised to users). The node layer needs **zero changes** — `BoundHandler.payload`
already flows into the tail.

## After bite 2, in order

1. **Event/204 REST restructure** (§11) — spawn dispatch as a task, wait on the
   context's `acknowledged` event (already built and set), yield `None`, await the
   task; missed-window ERROR logging. Plus the §8.3 rendered surface: `client.build`
   returning send-kwargs (`send_to`/`respond_to`, reject `content=`), which is also
   where `File` attachments get handled.
2. **`on_outdated`** (§6.4) — replace the token-miss WARNING with the real classmethod
   hook; DI wiring of dispatch (`Contexts.DEFAULT` nested with `Contexts.COMPONENT`).
3. **The state pivot** — DESIGN §15 order. `rerender()`/`edit(layout)` land here.
4. **Modals** (§9), then the 1.0 roadmap: `RedisStore` + `verify_store`, docs pass
   (docstrings, MIT headers for the files that lack them, DESIGN §13's error-table
   additions such as `NotAHandlerError`).
