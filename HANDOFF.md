# Handoff — continuing the rewrite on `wire-args`

State as of 2026-08-14. `DESIGN.md` is the authority on the target; this file is only
"where we are and what to do next". The old near-complete implementation lives on the
`start-implementation` branch as a DX reference — read it for ideas, error wording and
test cases; its internals are not a blueprint.

## Branch situation

- **`main`** carries the first milestone (PR #13): the complete component interaction
  loop, restart-proof, for zero-argument handlers on stateless views. All work branches
  off `main` and lands back on it as PRs.
- **`wire-args`** is the current working branch and has grown far past its name: codec
  bite 2 (wire args) is **complete**, and so are DI wiring of dispatch, the event/204
  REST restructure, `ctx.edit`/`ctx.rerender` (the routing half), and the `Rendered`
  build surface. Much of this lives uncommitted in the working tree — the maintainer
  commits by hand; never commit for them.
- **`foundation` is retired.** PR #13 was squash-merged, so its commits are not
  ancestors of `main` — never branch from it or PR it again.

## Setting up on a new machine

1. **Fresh clone strongly preferred**; `git checkout wire-args` (or branch anew off
   `main`). The histories of `main` and `start-implementation` were rewritten on
   2026-08-13 (attribution scrub) — an older clone must `git fetch origin` and
   `git reset --hard origin/<branch>` on every branch it touches (stash or back up
   any local work first; the reset discards it); never `git pull`.
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
  families (`CPY`, `D`, `DOC`, `ERA`, `FIX`, `TD`) are disabled in `ruff.toml`;
  existing MIT headers stay, new files may omit them for now.
- **Never `tuple` in annotations.** Public signatures promise
  `collections.abc.Sequence`; storing a tuple internally is fine.
- Everything runs through uv/nox: `uv run --group nox nox` (all sessions) or
  `-s ruff pyright pytest` while iterating; `uv run --group pytest pytest -q` for the
  fast loop. pyright strict is the gate.
- Commits are authored solely by the repository owner. No co-author trailers, no
  generated-with footers. Never commit or push without deciding to.
- Tests mirror the package. Test helpers worth knowing: `engine(handler)` in
  `test_view.py`/`test_nodes.py` (isinstance-narrows past the typing fiction to reach
  `BoundHandlerMethod` at runtime), `component_ctx(...)` in `test_context.py` (builds a
  context with the now-required `view`/`meta`).
- House laws: **packs/encodes raise, unpacks/decodes return `None`** (render-time
  failures are the developer's bug; click-time input is client-forgeable and fails
  soft). Errors build their messages in `__init__` and expose structured fields.
  Loose storage (`Callable[..., Awaitable[None]]`-grade internals), precise boundary.
- **Verify third-party behaviour empirically before designing on it.** The partial
  hijack, linkd's injection rules, pyright's mapping-spread checking, hikari's
  attachment lifting and the thinking-defer followup question were all settled by
  probing installed sources or live Discord, not memory.
- **Tools, not guardrails.** risa hands the developer everything needed to answer an
  interaction (`ctx.rest` included) and does not police *how* they answer. Errors and
  logs are for things risa itself did or cannot do — routing failures, forged input,
  operations Discord rejects — never for judging a legitimate style. A check that
  cannot observe every valid answer will false-accuse someone: that is why the
  "thinking defer never answered" detection was removed after the `/spinner` probe
  showed a raw `edit_interaction_response` tripping it.
- **Bare REST only.** hikari's model helper methods (`interaction.create_initial_
  response`, `message.edit`, `channel.send`, ...) and model `.app` properties are
  slated for removal upstream; risa calls `RESTClient` methods directly everywhere.
  The client stores and exposes `rest`; contexts require it; `Response` carries it;
  `Rendered.send_to`/`respond_to` take it as their first argument. Never reintroduce
  a helper-method call.

## The deliberate typing fictions (do not "fix" these)

1. **`risa.bind` IS `functools.partial`**, a bare alias. Wrapping it in a risa function
   would kill the checker special-casing that statically validates bind sites. See
   DESIGN §6.3 `[decided — revised after the typing investigation]`.
2. **`HandlerMethod.__get__` instance access is cast to
   `Callable[P, Awaitable[None]]`** — a lie over the runtime `BoundHandlerMethod`, and
   the type pyright's `partial` logic consumes. Handlers are therefore not directly
   callable in user code; delegation goes through the class-level descriptor's `.func`.
3. **The census casts `linkd.inject(...)` back to `Callable[..., Awaitable[None]]`** —
   linkd's `AsyncFnT` overload wants `Coroutine`-returning callables; runtime flattens
   via `maybe_await`.
4. **`ui.Rendered` is `Mapping[str, typing.Any]`** — probed: `object` values make
   `channel.send(**rendered)` a pyright error against every typed kwarg; `Any` is the
   only spelling under which §8.3's decided spread DX typechecks (miru does the same).
5. `tests/risa/typing_guard.py` pins the pyright behaviours in both directions:
   correct binds must stay accepted; wrong ones carry `# type: ignore[...]` lines that
   fail CI via `reportUnnecessaryTypeIgnoreComment` if pyright stops flagging them.

## What is built

Everything from PR #13 (nodes, registry, flat dispatch, respond/defer/acknowledge, the
auto-defer watchdog), plus — all on `wire-args`, all gated by 332 green tests:

- **Codec bite 2, complete (§6.3/§6.4).** Converters (`i`/`s`/`b`/`ei`/`es`;
  Snowflake shares `"i"`), `resolve_converter` (identity-matched scalars; enums
  validated, mixed/bool/empty rejected at registration), `resolve_signature` →
  `HandlerSignature` with `converters: Mapping[name -> converter]` (insertion-ordered;
  names and chain in one field), `required`, 2-char `fingerprint`
  (`FINGERPRINT_LENGTH`), lazy resolution forced by the `@register` census,
  `HandlerSignatureError` (incl. the TYPE_CHECKING-import `NameError` wrap). The
  runtime bind engine lives on `BoundHandlerMethod.bind(*args, **kwargs)` → payload
  `fingerprint + frames` (per-frame cap `MAX_FRAME_LENGTH` = 91); `ArgBindError`
  (render-time, names the parameter). Dispatch decodes via `Client._decode_args` +
  `_fingerprint_mismatch`: fingerprint drift → ERROR (`SignatureMismatchError`,
  logged never raised, "bump the handler version"); unreadable frames / bad counts /
  undecodable values → WARNING, all fail closed. The node layer unwraps
  `functools.partial` (nested partials flatten) and bare handlers via `_resolve_handler`.
- **DI wiring.** The census wraps every callback with `linkd.inject` — the record's
  single `callback` field IS the dispatch-ready callable (raw function when
  `LINKD_DI_DISABLED=true`). Dispatch opens `Contexts.DEFAULT` nested with
  `Contexts.COMPONENT`, registers the `ComponentContext` and raw interaction into the
  component container, and calls `callback(view, ctx, *decoded)` — linkd skips
  positionally-supplied params, so wire args and DI compose; bare DI params
  (`db: Database`, no marker) work. The client **registers itself** under `Client`,
  deliberately outside the `register_app_dependencies` gate (lightbulb never registers
  risa's client) — handlers in any file can take `client: risa.Client`.
- **Event/204 (§11).** `_process_interaction(interaction, state=None)` wraps
  `_dispatch` in `try/finally: state.acknowledged.set()` (every exit acks, incl. all
  fail-closed returns). The REST listener spawns dispatch as a task, waits on the
  event with `constants.INTERACTION_WINDOW` (3s), yields the 204, awaits the task;
  a missed window logs ERROR and **never cancels** the handler. Routing itself lives
  in `Client._route`.
- **`ctx.edit(layout)` / `ctx.rerender()` (§8.2/§11, the routing half).**
  `ComponentContext` requires `view=`/`meta=` (dispatch constructs the view first,
  with its own containment). `edit` takes exactly a `ui.Layout`, builds outside the
  gate lock, then the three-row table: nothing sent → initial `MESSAGE_UPDATE`
  (new `_InitialResponse.MESSAGE_UPDATE`); silent defer or prior edit →
  `edit_initial_response`; thinking defer or `respond()` → REST edit of
  `ctx.message`. `rerender() ≡ edit(self._view.render())` — until the state pivot,
  putting state onto `self` is the handler's job (see the poll testbot).
- **`Rendered` (§8.3).** `client.build(view)` returns `ui.Rendered`, a one-key
  `Mapping` (`"components"`) with `.components`, `send_to(rest, channel)`,
  `respond_to(rest, interaction, ephemeral=)`, and `**forbidden: typing.Never` guards that
  reject `content=`/`embeds=` statically and with the "put text in a ui.TextDisplay"
  message at runtime. `ui.build` (the internal pass) still returns the bare builder
  sequence. Attachments need no extra key: hikari's `_build_message_payload`
  (`rest.py` ~1500) unpacks every builder's `(payload, attachments)` tuple and mounts
  the files on the multipart form for all send paths.
- **Client config.** Shared options live once: `_LightbulbOptions` /
  `_ClientOptions` TypedDicts + `typing.Unpack` forwarding; defaults exist only in
  `Client.__init__`. **`auto_defer` defaults to `OFF`** — decided; §8.1 carries the
  `[decided — revised]` amendment (watchdog is opt-in; when enabled, silent update).
- **`on_outdated` (§6.4).** `View.on_outdated(cls, ctx)` — classmethod, default body
  empty, called only when a view overrides it (`ViewMeta.handles_outdated`, resolved
  once at registration by comparing `cls.on_outdated.__func__` against `View`'s, so
  inherited overrides count). `Client._route` now returns `_Route(meta, handler=None)`
  for the two retirement paths instead of collapsing them into `None`: a **token
  miss** (log downgrades WARNING → DEBUG when the view handles it) and a **fingerprint
  mismatch** (ERROR *always*, override or not — that is a live signature edit, not
  retirement, and §6.4 wants it unmissable; the check moved up out of `_decode_args`
  so the two are distinguishable). `_run_outdated` builds the context through the
  shared `_make_context` and runs the hook with DI open, no watchdog, no
  end-of-dispatch ack. Decode failures deliberately never reach it — forgeable input
  must not invoke user code — and a view with required fields cannot be
  default-constructed, so its hook is skipped with a contained ERROR until the state
  pivot hydrates instead.
- **Testbot.** `/test` (zero-arg demo) and `/poll` — per-option `risa.bind(self.vote,
  index)` buttons, counts parsed from the message text (`read_counts` — a crude
  stand-in for the InMessage anchor, deliberately), handler mutates `self` and
  `rerender()`s, plus a bare `fortnite: hikari.GatewayBot` DI param as the live DI
  check. Votes survive restarts; that live restart test is worth repeating after big
  changes.

## linkd facts (verified in installed source, v. as of lockfile)

- A param is injectable iff annotated, POS_OR_KW/KW_ONLY, and its default is absent
  **or** `INJECTED` — bare DI params need no marker.
- The generated resolver skips anything supplied positionally (`arglen` check) or by
  name; non-`INJECTED` defaults are `CANNOT_INJECT`, so omitted trailing wire
  defaults stay Python defaults.
- `AutoInjecting` codegens lazily on first call; container comes from the
  `DI_CONTAINER` contextvar; failures raise `DependencyNotSatisfiableException`
  (contained by the handler-failure rung).

## Next, in order

1. **The state pivot** — DESIGN §15 order: `risa/state/`, the InMessage anchor
   (carve/gather across fragments), `load()`, `Prop[T]`, `InStore`. `rerender()`
   gains its real meaning (hydrated `self`); the poll's `read_counts` is deleted the
   same day.
2. **Modals (§9)**, then the 1.0 roadmap: `RedisStore` + `verify_store`, the docs
   pass (docstrings, MIT headers for files that lack them, DESIGN §13's error-table
   additions).

Smaller open items, any order: slotscheck adoption (maintainer wants it, deferred —
dev dep + nox session + config, watch the msgspec-Struct interplay); a `ui.File`
upload through the testbot to watch a real multipart render.

Open findings from the reviews (verified, decisions pending):

- ~~Does a followup resolve a thinking defer?~~ **refuted empirically** (2026-08-15,
  live Discord, testbot `/spinner`): a review pass argued that only a PATCH of
  `@original` clears the "Bot is thinking..." placeholder, which would have made
  `respond()` after `defer(thinking=True)` strand a spinner. It does not — the
  followup resolves it. §8.2's table stands and records the verification; the
  throwaway `/spinner` probe that settled it has been removed from the testbot.
- **State-pivot constraint**: the auto-defer watchdog is armed *after* `meta.cls()`
  (construction must precede the context it is stored on). `__init__` is a sync
  msgspec constructor today, so nothing blocks there — but §7.2's `async def load()`
  hydration must be awaited *after* the watchdog is armed, or store I/O will burn
  the 3s window unprotected, contradicting §8.1's "the timer starts at decode".

- ~~`edit()`/`rerender()` on ephemeral origins~~ **resolved**: row three raises
  `EphemeralOriginError` (named guidance: initial response or silent defer) instead
  of 404ing; the thinking-defer half resolved earlier the same day via
  `DispatchState.thinking_unanswered` + the end-of-dispatch ERROR. Docs pass still
  pairs the modes: THINKING ↔ `respond()`, UPDATE/OFF ↔ `rerender()`.
- ~~The foreign-view build check is token-only~~ **resolved**: `HandlerMethod` learns
  its owner via `__set_name__`, `BoundHandler` carries `owner: type[View] | None`,
  `BuildContext` carries the view class, and `_routing_id` rejects
  `not issubclass(ctx.cls, owner)` with a "belongs to view X" `LayoutError` before
  the token-membership backstop (which still governs owner-less hand-built
  `BoundHandler`s). Inherited handlers pass via the subclass check.
- ~~Fingerprint ignores requiredness~~ **resolved** with the diagnosis-only option:
  requiredness stays unhashed (§6.4 `[decided — revised]`); dispatch diagnoses
  fingerprint-match + under-required frames as the in-place edit at ERROR with the
  bump-the-version fix. Folding `required` into the digest was consciously declined
  (would tax the harmless add-a-default direction; the wire stays stable).
- **Docs pass**: document the **rejection handler** as the precise alternative to
  `on_outdated` (§6.4) — keep the retired handler at the same id, version and wire
  signature with a body that says "this is outdated"; it routes as an ordinary handler
  and logs nothing, where `on_outdated` is the catch-all for anything on the view that
  no longer exists. Also document that wire enums are stdlib `enum.Enum` only — hikari's own
  enums (`ChannelType`, `Permissions`, ...) are *not* wire types by decision
  (DESIGN §6.3), so `kind: hikari.ChannelType` is a DI parameter and binding one
  fails at render with an arity `ArgBindError`; pass the value as `int`/`str` and
  rebuild it in the handler. Also document nested `risa.bind` keyword rebinding — CPython flattens
  nested partials with the outer keyword winning before risa can see it, so the
  double-supply `ArgBindError` is unreachable for keyword-over-keyword; positional
  conflicts still raise. Also document that `Rendered.respond_to` targets
  *non-dispatched* interactions (commands and the like) — inside a risa handler it
  bypasses the response gate, so `ctx.respond(**rendered)` is the correct spelling
  there. And document that handlers return `None` by contract (statically enforced
  by `HandlerFunction`): registration resolves all annotations via
  `typing.get_type_hints`, so an exotic runtime-unimportable *return* annotation
  fails registration by design rather than being special-cased.
