# Handoff — continuing the `foundation` rewrite

State of the ground-up rewrite as of 2026-08-12, and the plan for what comes next.
`DESIGN.md` is the authority on the target; this file is only "where we are and what to
do next". The old near-complete implementation lives on the `start-implementation`
branch as a DX reference — read it for ideas, error wording and test cases; its
internals are not a blueprint.

## Setting up on a new machine

1. Fresh clone, or in an existing clone: `git fetch origin && git checkout foundation
   && git reset --hard origin/foundation`. **Do not `git pull`** an old clone — the
   branch history was rewritten (secret redaction), so old local commits share no
   hashes with the remote and a pull would try to merge the two histories.
2. Recreate `testbot/.env` with `TOKEN=...` — it is gitignored and does not travel.
   The probe scripts under `test_bots/` read `DISCORD_TOKEN` / `TEST_CHANNEL_ID` from
   the environment.

## Working rules for this rewrite

- **No docstrings, no comments** until a dedicated final pass. The comment lint
  families (`CPY`, `D`, `DOC`, `ERA`, `FIX`, `TD`) are disabled in `ruff.toml` for
  exactly this reason; existing MIT headers stay.
- **Never `tuple` in annotations.** Public signatures promise
  `collections.abc.Sequence`; storing a tuple internally is fine.
- Everything runs through uv/nox: `uv run --group nox nox` (all sessions) or
  `-s ruff pyright pytest` while iterating; `uv run --group pytest pytest -q` for the
  fast loop. pyright strict is the gate.
- Commits are authored solely by the repository owner. No co-author trailers, no
  generated-with footers. Never commit or push without deciding to.
- Tests mirror the package: `risa/internal/wire.py` ↔ `tests/risa/internal/test_wire.py`.

## What is built on `foundation`

The complete component interaction loop, restart-proof, for zero-argument handlers on
stateless views — render → build → send → click → decode → route → dispatch → respond:

- `risa/internal/wire.py` — the 92-char printable alphabet, `pack_uint`/`unpack_uint`,
  base85 `pack_bytes`/`unpack_bytes`, `pack_digest`. Law: packs raise, unpacks return
  `None`.
- `risa/internal/codec.py` — `make_cookie` (6 chars) / `make_handler_token` (2 chars),
  the `CustomID` struct whose `encode()` is the single overflow choke point, fail-soft
  `parse_custom_id`. Layout `[ver:1][cookie:6][handler:2][idx:1][frag_len:1][fragment]
  [tail]`; the `tail` is reserved for the future `sig‖args`.
- `risa/view.py` — the handler machinery: `BoundHandler` (frozen identity + encoded
  payload), `HandlerMethod` (the two-personality descriptor), `BoundHandlerMethod`
  (`bind()` + delegation), the `ZeroArgHandler` protocol, the dual-form `@handler`
  decorator (`handler_id=`, `version=`, `defer=`), `AutoDefer`, and `@register`, whose
  census collects handlers into `token -> HandlerRecord` on `ViewMeta` (duplicate
  `(id, version)` → `DuplicateHandlerError`; per-handler `defer` beats the view's).
- `risa/ui/nodes.py` — every inert V2 node, plus the interactive layer: `Button` and
  the five selects behind one `Select` base, `SelectOption` (value defaults to label),
  handler normalization in `_resolve_handler`, and `Interactive._routing_id` carrying
  both risa-owned checks (foreign handler → path-qualified `LayoutError`; overflow via
  `CustomID.encode`). `BuildContext` threads cookie, token set and the fragment-index
  counter through `build(ctx, path)`; the path grammar is
  `Container[0] > Row[2] > Button[1]`. Fragments are `""` until the state layer.
- `risa/context.py` — `Context[T]` generic over component/modal interactions,
  `ComponentContext` (narrowed `message`, `values`, `resolved`), the response gate
  (`DispatchState`: lock + `_InitialResponse` + `acknowledged` event), `respond()`
  returning a `Response` handle (initial vs followup resolved internally),
  `defer()` (raises `AlreadyRespondedError` when late) and `acknowledge()` (its
  idempotent sibling — the watchdog and end-of-dispatch entry point).
- `risa/client.py` — dispatch: parse → cookie → token → construct (`meta.cls()`, the
  placeholder the state layer replaces) → handler, with the routing-failure ladder
  (foreign: silent; unknown cookie: DEBUG; token miss: WARNING — the `on_outdated`
  placeholder; handler raise: ERROR, contained). The auto-defer watchdog races every
  dispatch (timer starts at decode; stood down under the response lock; clean finish
  without a response is always answered with the silent ack, even under `OFF`).
  Client-level `auto_defer` / `auto_defer_delay` defaults thread through both
  transports and factories.
- `testbot/bot.py` — live demo: ping button, slow button (watchdog), text select,
  user select, surviving restarts.
- 197 tests across `tests/risa/`, all green under ruff `ALL` (preview) + pyright
  strict; `uv audit` clean.

## Next step: codec bite 2 — wire args (DESIGN §6.3, §6.4)

Converters (sync only: `int`/`str`/`bool`/`Enum`/`Snowflake`, byte-oriented,
little-endian latin-1), length-prefixed frames in the `tail`, the 2-char signature
fingerprint over type-ids (never parameter names), `HandlerSignature` resolution from
annotations (wire args are the contiguous converter-typed prefix after `ctx`;
everything after belongs to DI), real `bind(*args)` with `ParamSpec` (eager encoding —
failures at the `render()` call site), and decode-through-the-chain in dispatch with
fingerprint comparison. New errors: `ArgBindError`, `SignatureMismatchError`,
`HandlerSignatureError`.

## After that, in order

1. **Event/204 REST restructure** (§11) — spawn dispatch as a task, wait on the
   context's `acknowledged` event (already built and set), yield `None`, await the
   task; missed-window ERROR logging. Plus the §8.3 rendered surface: `client.build`
   returning send-kwargs (`send_to`/`respond_to`, reject `content=`), which is also
   where `File` attachments get handled.
2. **`on_outdated`** (§6.4) — replace the token-miss WARNING with the real classmethod
   hook and its log-level dance; DI wiring of dispatch (`Contexts.DEFAULT` nested with
   `Contexts.COMPONENT`) once handlers can declare dependencies.
3. **The state pivot** — DESIGN §15 order: chunk framing + anchor dialects, then
   `StateSchema`/`Prop`/`load()`, then backends and sessions, then the build + dispatch
   rewire (`rerender()`/`edit(layout)` land here, per §7.4/§8.2).
4. **Modals** (§9), then the 1.0 roadmap: `RedisStore` + `verify_store`, docs pass.
