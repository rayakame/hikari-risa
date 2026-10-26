# Handoff — continuing the state pivot on `anchor-codec`

State as of 2026-08-17 (reconstructed 2026-10-25 — the Claude session transcripts for
2026-08-16/17 were lost to transcript cleanup, so this file is the *only* record of that
evening's decisions). `DESIGN.md` is the authority on the target; this file is only
"where we are and what to do next". The old near-complete implementation lives on the
`start-implementation` branch as a DX reference — read it for ideas, error wording and
test cases; its internals are not a blueprint.

## Resume here (written 2026-10-25, end of session)

Everything below this block is still accurate; this is the short path back in.

**2026-10-26, uncommitted on top:** the six session decisions (below), the reader
(`internal/reader.py`), `MessageBackend`/`MessageSession` (`state/message.py`),
`StatelessBackend`/`StatelessSession` (`state/stateless.py`), `KeyedLocks`
(`internal/locks.py`, now also behind `MemoryStore`), `codec.decode_text` (moved out of
dispatch), `StateSchema.empty_fingerprint`, and `backend.py` speaking bytes. 738 tests,
nox green. Built to these calls, all taken that day: a codec this host cannot load makes
`restore()` raise `CodecUnavailableError` (no hook, like a store outage); the version cache
is keyed by (message id, cookie), an LRU of 10 000, and wins only when its `seq` is strictly
newer, compared modulo 2²⁴; the in-process lock waits `DEFAULT_LOCK_TIMEOUT`; OUTDATED logs
ERROR for a schema edit or placement switch without a version bump and WARNING for torn or
forged. Also built the same day: `StoredBackend`/`StoredSession`/`KeyNamespace`
(`state/stored.py`, 26 tests against `MemoryStore`), 764 tests in all. Calls taken for it:
`Store.put_if_version` now returns the new version or `None` (DESIGN §7.5, MemoryStore and
its tests follow); one `StoredBackend` per store slot; the CAS happens in `stage()`, so
`published()` is a no-op for stores; every store call goes through `_guarded`, which lets
`LockTimeoutError` through and turns anything else into `StoreUnavailableError(key,
reason)`; `StoreAnchor.token` is hex; `StateConflictError` carries `expected`/`found`.
Open, small: `MessageAnchor.parse` and `StoreAnchor.parse` do not check their own tag
byte (only `anchor.parse` does) — `commit_first` tripped over it. Then the hooks and the
client, 778 tests: `View.on_state_missing` with `ViewMeta.state_missing` /
`handles_state_missing` (`OutdatedCallback` renamed `HookCallback`, one convention for both
hooks; allowed on InMessage views, where it never fires); `store=`, `stores={}` and
`key_prefix=` on the client and on `client_from_lightbulb`, "default" both ways and a blank
prefix are `ValueError`; `add_view` raises `ViewDeclarationError` naming the fix for a
missing store and warns once per `MemoryStore` slot; the client builds one
`MessageBackend`, one `StatelessBackend` and one `StoredBackend` per slot, and
`Client._backend_for(meta)` picks one (`None` for an unconfigured store, which only a
`use_global` view can reach). A review pass over all of step 3 then landed: detached
sessions raise a fresh exception each time and log a later discarded change at DEBUG; an
unchanged InStore `stage()` writes nothing and only touches (DESIGN §7.4); `_locate`
treats a fragment index other than 0, an empty fragment or a message-tagged one as a
placement switch, which makes **InStore components carry fragment index 0** a constraint
on the step-4 build (DESIGN §7.6); `StateSchema.pack_view`; the dead `wire.pack_bytes` /
`unpack_bytes` are gone. Declined from that review, with reasons recorded in the
conversation: a store check for `use_global` views at construction (§7.1), one shared
`_injected_hook` (costs strict typing), a module-level empty fingerprint (ordering rule),
and docstrings (the no-docstrings rule). **Step 3 is complete. Next: step 4**, below.

**Where the code stands.** Branch `anchor-codec`, four commits ahead of `main`, working tree
clean, all seven nox sessions green, 688 tests. The last three commits landed today:
`57e52f2` (schema, placements, `load()`, store package, serde, backend skeleton — the August
work), `3a9ad47` (anchor moved to `state/` and rebuilt on bytes, the codec character in the
header, `risa.codecs`, the `hikari-risa[radixly]` extra), `fc29e35` (lockfile refresh,
`Literal` values in the schema fingerprint). Still deliberately not a PR.

**How we work.** Claude writes the code; the maintainer decides design and DX and wants to
understand every piece. Before building a step: present the design and the snippet a bot
author would write, let the maintainer choose, then implement, run `uv run --group nox nox`,
and explain the result at concept level. No docstrings or comments until the final pass.
`collections.abc.Sequence`, never `tuple`, in annotations. Never commit, stage or push
without being asked; commits carry no attribution of any kind.

**The three explainer pages of 2026-10-25** (Lagebild, custom_id anatomy, step 3) were
private claude.ai artifacts bound to an account the maintainer is leaving; do not rely on
their links. Everything load-bearing from them lives here and in DESIGN. The one piece
that did not, the step-3 verb table, is below.

**What each session verb does per backend** (the build checklist for step 3; anchors are
bytes, so `stage`, `first_anchor` and `commit_first` move from `str` to `bytes`):

| Verb | `MessageSession` | `StoredSession` | `StatelessSession` |
|---|---|---|---|
| `open()` | per-message-id `asyncio.Lock`, dropped when the last waiter leaves (like `MemoryStore`) | enter `store.lock(key, timeout=)`; a throwing store → `StoreUnavailableError` | nothing |
| `restore()` | reader → `gather` → `parse`; wrong tag, unknown fingerprint, failed unpack → `OUTDATED`; empty gather → arity-0 prefix | `get_versioned`; `None` → `MISSING`; envelope `n`/`v`/`f` mismatch → `OUTDATED`; remember the version | `meta.cls()` |
| `stage(view)` | `schema.pack`, `seq + 1`, `MessageAnchor.pack()`; no I/O | envelope, `put_if_version`; on `None` re-read: gone → `StateNotFoundError`, unreadable → `StateConflictError`, else adopt + take version + `StateConflictError` | no anchor |
| `published(view)` | baseline := packed bytes, `seq` into the version cache | nothing — the CAS in `stage` already committed | nothing |
| `settle(view)` | packed ≠ baseline → `True`, dispatch renders once more | packed ≠ baseline → CAS as in `stage`, a loss is WARNING + discard; clean → `touch(key, ttl)` | never dirty |
| `close()` | release the lock | leave the lock context | nothing |
| `first_anchor` | `MessageAnchor(fp, seq=0, pack(values))` | `StoreAnchor.mint()` | none |
| `commit_first` | nothing, the send is the write | `put(key, envelope, ttl)` before the send; a failed send leaks one entry to the TTL | nothing |

**The next step is step 4, wiring the sessions in** — nothing built in step 3 is live yet:
dispatch still builds views with `meta.cls()` and the build writes no anchor. The pieces:
- **Two-phase build**: `first_anchor` / `commit_first` on send; InMessage carves the
  anchor across the leaves, InStore replicates the key into every one; `anchor.distribute`
  parks the components the anchor does not reach on one shared index.
- **Dispatch through the session** (E1): context without a view, watchdog armed,
  `open` → `restore` → `MISSING` to `on_state_missing` (WARNING, DEBUG once overridden),
  `OUTDATED` to `on_outdated`, `StoreUnavailableError` / `CodecUnavailableError` to ERROR
  with no hook → attach the view → `load()` → handler → `settle` (InMessage `True` means one
  more commit) → `close`. A `None` from `Client._backend_for` is an ERROR at click time.
- **Context**: `rerender()` = `stage` → build with the anchor → edit → `published`;
  `rerender()` without a view raises; `edit(layout)` re-embeds the anchor or raises.
- Then delete `read_counts` from the testbot and repeat the live test.

**The six session decisions, taken 2026-10-26** (all written into DESIGN):

1. Context before hydration: `ComponentContext` is built without a view, the watchdog is
   armed, the session opens, and the hydrated view is attached after `restore()`. Hooks run
   with no view: `edit(layout)` works, `rerender()` raises (§6.4).
2. A lost CAS inside `rerender()` **raises** `StateConflictError(key, expected, found)`
   after adopting the winner's fields and version onto `self` — reversing §7.3's old
   "converge, never raise". A record gone mid-handler raises `StateNotFoundError(key)`; an
   unreadable winner raises the conflict without adopting; both detach the session. A race
   lost by the end-of-dispatch commit logs WARNING and discards. Both error classes stay.
3. Store key: `risa:{app_id}:1:<24 hex digits>`. The app id comes from
   `interaction.application_id` on a click, else one cached `rest.fetch_application()`;
   never from the bot token. `key_prefix=` on the client replaces `risa:{app_id}`
   wholesale. The key text is independent of the wire codec (§7.5).
4. A `StatelessBackend` after all, because views without durable fields take **no lock**
   (a public panel must not queue unrelated clicks). They write no anchor at all; an empty
   contiguous gather reads as the arity-0 prefix, so a first durable field stays free.
5. `on_state_missing` default: empty, WARNING, DEBUG once overridden. Closes §14.2.
6. The reader gathers only components carrying the clicked component's cookie; repeated
   indices under one cookie read as torn.

**Also pending, small:** record the modal field decision in DESIGN §9 (bare fields are the
default, `Annotated[..., risa.TextInput(...)]` only for constraints, a `risa.Paragraph`
alias for the multi-line case) — agreed on 2026-10-25, not yet written down there.

**New machine checklist, in addition to the section below:** `ssh-add ~/.ssh/github` before
the first commit; `testbot/.env` with `TOKEN=...`; and set `"cleanupPeriodDays"` high in
`~/.claude/settings.json` — the default of 30 days is what deleted the August transcripts.

## Branch situation

- **`main`** carries six merged milestones: the foundation (#13), typed wire args and
  the dispatch surface (#15), `on_outdated` (#17), the structural cleanup (#18),
  slotscheck (#19), and the CI checkout hardening (#20, #21).
- **`anchor-codec`** is the current working branch: four commits (see "Resume here"), the
  first of them pushed. **Deliberately not a PR yet** — the maintainer wants more of the state pivot
  in before opening one. Keep landing pivot steps on this branch.
- Branches are squash-merged, so a merged branch is never an ancestor of `main`: delete
  local copies with `git branch -D`, never `-d`, and never branch from a merged one.

## Setting up on a new machine

1. **Fresh clone strongly preferred**; `git checkout anchor-codec`. The histories of
   `main` and `start-implementation` were rewritten on 2026-08-13 (attribution scrub) —
   an older clone must `git fetch origin` and `git reset --hard origin/<branch>` on
   every branch it touches (stash or back up local work first; the reset discards it);
   never `git pull`.
2. Recreate `testbot/.env` with `TOKEN=...` — it is gitignored and does not travel.
   The probe scripts under `test_bots/` read `DISCORD_TOKEN` / `TEST_CHANNEL_ID` from
   the environment.
3. Commits are SSH-signed; the key sometimes drops out of the agent mid-session. If a
   commit fails on `ssh_askpass`, ask the maintainer to run `ssh-add ~/.ssh/github`
   themselves — it needs a passphrase only they have.

## Working rules for this rewrite

- **Since 2026-10-25 Claude writes the code; the maintainer decides.** Present the design
  and the downstream DX of a step first, let the maintainer choose, then implement, run
  nox and explain the result at concept level — what a thing *is* and why, with the
  snippet a bot author would write. (Until 2026-08-17 the split was the other way
  round: the maintainer typed, Claude guided.)
- **No docstrings, no comments** until a dedicated final pass. The comment lint families
  (`CPY`, `D`, `DOC`, `ERA`, `FIX`, `TD`) are disabled in `ruff.toml`; existing MIT
  headers stay, and new modules under `risa/` carry one (`internal/` and `ui/nodes.py`
  predate that convention and are left alone). Automated reviewers ask for docstrings
  every single round — decline with this rule, every time.
- **Never `tuple` in annotations.** Public signatures promise
  `collections.abc.Sequence`; storing a tuple internally is fine.
- Everything runs through uv/nox: `uv run --group nox nox` (all six sessions) or
  `-s ruff pyright pytest` while iterating; `uv run --group pytest pytest -q` for the
  fast loop. pyright strict is the gate. **688 tests pass** in the working tree, all nox
  sessions are green (405 tests at the branch head commit; everything from step 2 onward
  is not yet committed — `git status` shows the full extent).
- Commits are authored solely by the repository owner: no co-author trailers, no
  generated-with footers, and no mention of Claude or Anthropic anywhere in a commit
  message, PR body or code comment. Never commit, stage or push without being asked.
- Tests mirror the package. Helpers worth knowing: `engine(handler)` in
  `test_view.py`/`test_nodes.py` (isinstance-narrows past the typing fiction to reach
  `HandlerBinder`), `component_ctx(...)` in `test_context.py`, `custom_id_of(cls, button)`
  and `rest_of(client)` in `test_client.py`. Log assertions capture on the **`risa`**
  namespace, never one submodule, so extracting a module does not move them.
- House laws: **packs/encodes raise, unpacks/decodes return `None`** — render-time
  failures are the developer's bug and must be loud; click-time input is
  client-forgeable and must fail soft. Errors build their messages in `__init__` and
  expose structured fields so callers branch on data, not strings.
- **Loose storage, precise boundary** — but the boundary includes the registry.
  `HandlerRecord.callback` and `ViewMeta.outdated` are typed `DispatchCallback` /
  `OutdatedCallback` because they are called with two *different* conventions, and one
  anonymous shared type could not say so.
- **Tools, not guardrails.** risa hands the developer everything needed to answer an
  interaction (`ctx.rest` included) and does not police *how* they answer. Errors and
  logs cover what risa itself did or cannot do — routing failures, forged input,
  operations Discord rejects — never a judgement on a legitimate style. A check that
  cannot observe every valid answer will eventually false-accuse someone; that is why
  the "thinking defer never answered" detection was removed after a live probe showed a
  raw `edit_interaction_response` tripping it.
- **Bare REST only.** hikari's model helper methods (`interaction.create_initial_response`,
  `message.edit`, `channel.send`, ...) and model `.app` properties are slated for removal
  upstream. The client stores and exposes `rest`; contexts require it; `Response` carries
  it; `Rendered.send_to`/`respond_to` take it as their first argument. Never reintroduce
  a helper-method call.
- **Verify third-party behaviour empirically before designing on it.** Probe the
  installed source or live Discord; do not answer from memory. Everything under
  "verified facts" below was settled that way, and several entries overturned a
  confident wrong answer.
- **Placement and ordering are written into CLAUDE.md**: a helper lives with the concept
  it serves, each file reads in bands ending with its entry point, private module
  functions follow the class band. New modules should land already ordered.

## The deliberate typing fictions (do not "fix" these)

1. **`risa.bind` IS `functools.partial`**, a bare alias, living with the rest of the
   mechanism in `risa/binding.py` (`Binding`, `HandlerBinder`, `resolve`, `BindTarget`,
   `Handlerish`). Wrapping it in a risa function kills the checker special-casing that
   statically validates every bind site. DESIGN §6.3 `[decided — revised]`.
2. **`HandlerMethod.__get__` casts instance access to `binding_.BindTarget[P]`** — a lie
   over the runtime `HandlerBinder`, and the type pyright's `partial` logic consumes.
   Handlers are therefore not directly callable from user code; delegation goes through
   the class-level descriptor's `.func`.
3. **`HandlerBinder.__call__` raises `HandlerNotCallableError` and must keep existing.**
   `functools.partial` checks callability of its first argument *at construction*, so
   deleting the method breaks every `risa.bind(...)` site with
   `TypeError: the first argument must be callable`. Verified by deleting it and watching
   the render path die. A test pins `callable(binder) is True`.
4. **Merging `HandlerBinder` into `HandlerMethod` is forbidden.** Verified twice with the
   project's pyright: giving the descriptor a `__call__` makes it structurally assignable
   to `Callable[..., Awaitable[None]]`, so `risa.bind(View.handler, 5)`,
   `ui.Button(View.handler)` and `View.handler()` go from four errors to **zero**,
   silently, and the runtime `NotAHandlerError` stops firing. `typing_guard.py` cannot
   catch the regression because every line in it uses *instance* access.
5. **The census casts `linkd.inject(...)`** through one named `_inject` helper — linkd's
   overload wants `Coroutine`-returning callables; runtime flattens via `maybe_await`.
6. **`ui.Rendered` is `Mapping[str, typing.Any]`** — probed: `object` values make
   `rest.create_message(ch, **rendered)` a pyright error against every typed kwarg. `Any`
   is the only spelling under which §8.3's decided spread DX typechecks (miru does the same).
7. `tests/risa/typing_guard.py` pins these in both directions: correct binds must stay
   accepted; wrong ones carry suppressions that fail CI via
   `reportUnnecessaryTypeIgnoreComment` if pyright ever stops flagging them. It is **not**
   collected by pytest (no `test_` prefix) but **is** type-checked.

## Layout as it stands

```text
risa/
  __init__.py     explicit `as` re-exports — a plain import is not a re-export for a
                  py.typed package, and pyright excludes **/__init__.py so the repo
                  cannot see the difference; a downstream consumer collected 12 strict
                  errors before this was fixed. There is still no in-repo guard: the only
                  real one is a nox session pyrighting an out-of-tree consumer against a
                  built wheel. Worth doing.
  binding.py      the partial fiction: bind, Binding (frozen wire value: fingerprint +
                  byte Frames, rendered to text only at build), HandlerBinder
                  (per-instance accessor whose only real method is bind()), resolve,
                  Handlerish, BindTarget
  codecs.py       NEW 2026-10-25 — the text-codec extension point: Codec struct
                  (tag, name, encode, decode, max_bytes), BASE85 / BASE32768 / URO14,
                  register / by_tag / ensure_available; the radixly two import lazily
  client.py       Client ABC, the two transports, registry wiring, DI registration —
                  a facade, 278 lines (was 600 before the cleanup)
  context.py      Context / ComponentContext, ResponseGate, Response,
                  respond/defer/acknowledge/edit/rerender
  dispatch.py     Dispatcher: _route, _dispatch, _run_handler, _run_outdated,
                  _make_context, the watchdog, the decode functions. Logs as
                  `risa.dispatch` — not `risa.client`
  di.py           Contexts (DEFAULT/COMPONENT/MODAL), INJECTED re-export
  errors.py       RisaError-rooted hierarchy
  view.py         View, @register, @handler, HandlerMethod, AutoDefer, on_outdated
  internal/       private by virtue of living here; no leading underscores
    wire.py       the 92-char alphabet, pack_uint/pack_digest, largest_value
    codec.py      the custom_id layout (12-char header incl. the codec letter),
                  byte converters, HandlerSignature, MAX_FRAGMENTS
    constants.py  Discord limits, the attribute names risa stamps
    registry.py   ViewMeta (+ schema, state, loader), HandlerRecord, Registry,
                  stamp/meta_of/require_meta
  state/          NEW, uncommitted — the state pivot's public half
    placement.py  InMessage, InStore(ttl, store="default"), StatePlacement, DEFAULT_*
    schema.py     StateSchema, Prop[T], DurableField, structural_type_id — the field
                  partition, prefix fingerprints, pack/unpack, adopt
    serde.py      the store envelope: Envelope{v,n,f,d}, pack_entry / unpack_entry
    anchor.py     the state anchor as BYTES: MessageAnchor, StoreAnchor (+ .token),
                  carve(bytes, capacities, wire_codec=) -> encoded fragments,
                  gather(Mapping[int, bytes]) -> bytes
    backend.py    StateBackend / StateSession ABCs, Restored, Unreadably, hydrate —
                  skeleton only, no implementation yet (below)
  store/          NEW, uncommitted — pluggable storage behind InStore
    protocol.py   Store (a typing.Protocol), VersionedEntry, DEFAULT_LOCK_TIMEOUT
    memory.py     MemoryStore — OrderedDict LRU, per-key asyncio locks, injectable clock
  ui/
    build.py      BuildContext (owns routing_id), build(), Rendered
    nodes.py      the V2 node set only — imports neither errors, codec, view nor registry
```

Names that changed in the structural cleanup and will not match older notes:
`BoundHandler` → `Binding`, `BoundHandlerMethod` → `HandlerBinder`, `_resolve_handler` →
`binding.resolve`, `HandlerT` → `Handlerish`, `DispatchState` → `ResponseGate` (keyword
`state=` → `gate=`), `ViewMeta.key` → `.cookie`, `_callback_name` → `_func_name`.
`registry.stamp` / `meta_of` / `require_meta` replaced the `getattr(cls, VIEW_META)`
ritual — and `meta_of` reads `cls.__dict__`, so an **undecorated subclass of a registered
view no longer inherits its parent's identity**. It used to, silently routing every click
on the subclass to the parent.

## What is built

The whole pre-state loop — render → build → send → click → decode → route → dispatch →
respond — restart-proof, with typed wire arguments and dependency injection.

- **Identity codec** (`internal/codec.py`): 92-char printable alphabet, cookies (6 chars,
  hashing name + schema version), handler tokens (2, hashing id + version), the `CustomID`
  struct whose `encode()` is the single overflow choke point, and fail-soft
  `parse_custom_id`. Layout
  `[ver:1][cookie:6][handler:2][idx:1][frag_len:1][fragment][tail]` — an 11-char header
  inside Discord's 100, leaving 89. `idx` and `fragment` are threaded end to end but are
  always `0` / `""` until the pivot fills them.
- **Wire args** (§6.3/§6.4): converters (`i`/`s`/`b`/`ei`/`es`; Snowflake shares `"i"`),
  `resolve_signature` → `HandlerSignature` (insertion-ordered `Mapping[name, converter]`,
  `required`, a 2-char `fingerprint`), lazy resolution forced by the `@register` census so
  a bad signature fails at import. `risa.bind` encodes eagerly at the render site
  (`ArgBindError` names the offending parameter); dispatch verifies the fingerprint and
  decodes each length-prefixed frame fail-closed.
- **DI**: the census wraps every callback with `linkd.inject`, so `HandlerRecord.callback`
  IS the dispatch-ready callable (the raw function when `LINKD_DI_DISABLED=true`).
  Dispatch opens `Contexts.DEFAULT` nested with `Contexts.COMPONENT`, registers the
  `ComponentContext` and the raw interaction into the component container, and calls
  `callback(view, ctx, *decoded)`. Bare `db: Database` parameters work — no marker needed.
  The client **registers itself** under `Client`, deliberately outside the
  `register_app_dependencies` gate (lightbulb never registers risa's client), so a handler
  in any module can take `client: risa.Client`.
- **Transports**: the gateway subscribes to `InteractionCreateEvent`; the REST listener
  uses the event/204 pattern — spawn dispatch as a task, wait on `gate.acknowledged` with
  `constants.INTERACTION_WINDOW` (3s), yield the 204, then await the task. A missed window
  logs ERROR and **never cancels** the handler. An adopted interaction that answered
  nothing is noted at DEBUG; foreign custom_ids stay silent.
- **Response surface**: `respond` / `defer` / `acknowledge`, the `Response` handle, and
  `edit(layout)` / `rerender()` with origin-message routing — nothing sent yet → initial
  `MESSAGE_UPDATE`; silent defer or prior edit → `edit_interaction_response`; thinking
  defer or a prior `respond()` → a REST edit of `ctx.message`, which raises
  `EphemeralOriginError` when that origin is ephemeral instead of 404ing.
- **`on_outdated`** (§6.4): a classmethod on `View`, empty by default, invoked only when a
  view overrides it (`ViewMeta.handles_outdated`, derived from `ViewMeta.outdated` — the
  `linkd.inject`-wrapped callable). The routing ladder's log levels are deliberate: a
  foreign id is silent, a cookie miss is DEBUG, a token miss is WARNING (DEBUG when the
  view handles it), and a fingerprint mismatch is ERROR *always* — that is a live signature
  edit, not retirement. Decode failures never reach the hook: forgeable input must not
  invoke user code.
- **`ui.Rendered`** (§8.3): `client.build(view)` returns a one-key `Mapping`
  (`"components"`) with `.components`, `send_to(rest, channel)`,
  `respond_to(rest, interaction, ephemeral=)` and `**forbidden: typing.Never` guards that
  reject `content=` / `embeds=` statically *and* at runtime with the "put text in a
  ui.TextDisplay" message. Attachments need no key — hikari's `_build_message_payload`
  lifts them out of each builder's `(payload, attachments)` tuple on every send path.
- **Client config**: `_LightbulbOptions` / `_ClientOptions` TypedDicts plus
  `typing.Unpack` forwarding, so shared option defaults exist once, in `Client.__init__`.
- **Testbot**: `/test` and `/poll` — per-option `risa.bind(self.vote, index)` buttons,
  counts parsed back out of the message text (`read_counts`, a deliberately crude stand-in
  for the anchor), the handler mutating `self` and calling `rerender()`, plus a bare
  `fortnite: hikari.GatewayBot` DI parameter as the live injection check. Votes survive a
  restart; repeat that live test after any big change.

### The anchor codec — `state/anchor.py` (step 1 of the pivot; moved out of `internal/` and
rebuilt on bytes with a pluggable text codec on 2026-10-25)

**2026-10-25 rework, decided with the maintainer and built the same day.** The wire header
grew a `codec` character behind `ver` (12 chars, 88 free); `ver` stays layout-only so a
future layout bump never multiplies codec letters. Every byte field of a component —
fragment slices, string/int/bool argument frames, the store key — is written in that
component's codec and read back by its tag; `InMessage(codec=risa.codecs.BASE32768)` is the
opt-in, `InStore` always writes base85. `StateSchema.pack/unpack` now speak bytes;
`MessageAnchor.payload` and `StoreAnchor.key` are bytes; `carve` sizes slices with
`codec.max_bytes` and encodes each slice separately, `gather` concatenates already-decoded
bytes (the message reader of step 4 decodes each fragment with its own component's codec).
`Binding` no longer carries encoded text: it holds the fingerprint plus byte `Frame`s, and
`BuildContext._tail` renders them, which is where the 91-char frame limit is now checked
(`ArgBindError` naming parameter and codec). `codecs.register` runs at view declaration so a
custom codec's tag becomes readable; a taken tag is a `ViewDeclarationError`, a missing
radixly a `CodecUnavailableError` at import. The notes below predate the rework where they
mention characters; the layout and the reasoning stand.

The anchor is the view's state written as one string, cut into pieces across the rendered
components' custom_ids, and reassembled when one of them is clicked. Nothing imports the
module yet, on purpose: it is a leaf with 47 tests, so if step 2 proves the layout wrong
you delete one file and there are no call sites to unpick.

- `MessageAnchor(schema_fp, seq, payload)` and `StoreAnchor(key)` carry their **own**
  layout as `pack()` / `parse()` (plus `StoreAnchor.mint()`). The layout lives on the
  struct it describes; `parse()` is a classmethod returning `Self | None`.
- Module-level because they belong to no single dialect: `carve`, `gather`, `parse` (the
  tag dispatcher), `pack_payload` / `unpack_payload`.
- **Layout is Option A**, matching §6.1 — `[tag:1][total:2][schema-fp:3][seq:3][msgpack
  positional array]`, base85-packed into the wire alphabet. `total` is the anchor's own
  length and is the *only* thing that catches a message whose trailing components were
  deleted: that gathers contiguously and cleanly, and msgpack would happily decode the
  prefix. There is a test for exactly that case.
- `carve(anchor, capacities, *, view_name)` fills components **greedily**, clamping each
  slot to `codec.MAX_FRAGMENT_LENGTH` and ignoring slots past `codec.MAX_FRAGMENTS` (92 —
  the addressable limit of a one-character `idx`). `capacities` is one number per
  interactive component in tree order: 89 minus whatever that component's own handler args
  already spent (a bare button 89; `bind(self.vote, 2)` 84; `bind(kick, snowflake)` 76).
  Balanced splitting was rejected — it wastes the largest slots and re-cuts every fragment
  whenever a field is appended.
- `gather(Mapping[int, str])` is dialect-blind by design and enforces only contiguity from
  zero. A `Mapping` rather than a sequence because Discord returns components in any order
  and a forged message can repeat an index.
- `StateOverflowError(view_name, needed, available, slots)` raises at render and names the
  way out (`state=risa.InStore(...)`).
- §7.6 was amended to match §6.1 and is marked `[decided — revised]`; the earlier text had
  no `total` and a 2-char fingerprint.

## Verified facts (probed, not remembered)

- **`functools.partial` checks callability at construction** — `TypeError: the first
  argument must be callable`. Hence fiction 3.
- **pyright's partial special-casing engages only on bare `Callable` types**; an object
  with a generic `__call__` gets no argument checking at all. Hence fiction 2.
- **linkd**: a parameter is injectable iff annotated, POS_OR_KW/KW_ONLY, and its default
  is absent **or** `INJECTED`. The generated resolver skips anything supplied positionally
  (an `arglen` check) or by name, so wire args and DI compose; non-`INJECTED` defaults are
  `CANNOT_INJECT`, so omitted trailing wire defaults stay Python defaults. `AutoInjecting`
  codegens lazily on first call, takes its container from the `DI_CONTAINER` contextvar,
  and raises `DependencyNotSatisfiableException` (contained by the handler-failure rung).
- **hikari's enums are not `enum.Enum`** — they are `hikari.internal.enums.Enum`/`.Flag`,
  so `hikari.ChannelType` resolves to *no* converter and is treated as a DI parameter.
  Declined deliberately; DESIGN §6.3 records why (unknown values pass through instead of
  raising, and flags are combinable, so membership is not a validity test).
- **A webhook followup does resolve a thinking defer** (live Discord, 2026-08-15). A review
  pass argued only a PATCH of `@original` clears the "Bot is thinking..." placeholder,
  which would have stranded a spinner after `respond()`. It does not; §8.2's table stands.
- **`uv lock --upgrade` on 2026-10-25** took msgspec 0.21.1 → 0.22.0 and linkd 0.6.2 → 0.6.5
  (the audit session had started failing on stale transitive pins). msgspec 0.22 now
  describes mixed-type `Literal`s instead of raising, which exposed that `Literal` values
  were never part of the schema fingerprint; `_render` now folds them in (`literal('a','b')`),
  so retyping a `Literal` fails closed like any other retype.
- **msgpack ints are bounded to 64 bits**, unlike the wire-args codec's arbitrary
  precision: a durable field holding a larger int raises `OverflowError` at render. A test
  records it; `StateSchema` should turn it into a real diagnostic.
- **`msgspec.msgpack.decode(data, type=list[object])` validates the shape itself** — a
  forged dict or bare int raises `ValidationError`, garbage raises `DecodeError` — so no
  `isinstance` + `cast` dance is needed on the way out.
- **`AutoDefer.OFF` is the client default** (§8.1 `[decided — revised]`) and means risa
  answers nothing on its own; the end-of-dispatch acknowledge is gated on the mode.
- **Non-ASCII `custom_id`s survive every read-back path** (live Discord, 2026-10-25,
  `test_bots/dense_roundtrip.py`): 11 ASCII header chars + 89 chars of radixly
  `base32768` / `uro14` / `braille` came back byte-identical from the `create_message`
  response, `fetch_message`, `interaction.message.components` and
  `interaction.custom_id`, and decoded to the original bytes. Capacity per 89-char slot:
  base85 71 bytes, uro14 154, base32768 166. Body cost: hikari without `[speedups]`
  serialises with `json.dumps(ensure_ascii=True)`, so a BMP char costs 6 bytes on the
  wire (3 with orjson); a 40-component message grows from ~4 KB to ~21 KB. PyPI
  radixly 1.0.0 ships base32768, uro14, braille and hexagram only.

## Next, in order

The state pivot, DESIGN §15. Step 1 (the anchor codec, this branch) is done.

1. **`StateSchema` + `Prop[T]` + `load()` — DONE (2026-08-17, uncommitted).** Landed as
   `risa/state/{placement,schema}.py`, `ViewMeta.{schema,state,loader}`,
   `register(state=)`, `View.load()` + `_injected_load` census, and the `risa/store/`
   package split (`protocol.py`: `Store`, `VersionedEntry`, `DEFAULT_LOCK_TIMEOUT`;
   `StoreUnavailableError` + `StateEncodeError` in errors). The msgpack 64-bit bound
   became `StateEncodeError` **at render, naming the field** — not an import error as the
   earlier note here hoped: `int` is a fine annotation and the overflow is a value
   problem. `hikari.Snowflake` is blessed via enc/dec hooks and fingerprints as `int`.
   Gotchas recorded: msgspec resolves struct annotations lazily (a `TYPE_CHECKING`-only
   field type fails in `StateSchema.of`, not at class creation); a Struct defined inside
   a function cannot resolve annotations naming other function-locals; `_is_prop` is an
   identity check on the PEP 695 alias (`__metadata__` does not exist on it); ruff RUF012
   flags `= []` defaults on `risa.View` subclasses but not on plain Structs, so views use
   `msgspec.field(default_factory=...)`. After adding a public name, check it at runtime
   (`python -c "import risa; risa.Thing"`) — nothing lints a missing `__all__` entry, and
   pyright excludes `__init__.py`, so this has bitten three times.

   Store-surface decisions (2026-08-17), all recorded in DESIGN §7.1/§7.5: no
   client-level default placement (cookie⇒placement invariant; the project-wide default
   is a user-land wrapper decorator, which pyright checks fully); client wiring is
   `store=` (fills the unnamed `"default"` slot) plus `stores={...}` for named extras,
   both-ways `"default"` a construction error; a missing store is an `add_view` boot
   error naming the fix — `MemoryStore` is never auto-created; `put_if_version` stays
   `bool` and `StoredSession` re-reads after `False` to split lost-race from vanished;
   store keys are namespaced `risa:{app_id}:1:<token>` by the backend — the anchor
   carries the bare token.
2. **Backends and sessions — IN PROGRESS (2026-08-17 evening, uncommitted).**

   Done, with tests:
   - **`MemoryStore`** (`store/memory.py`, 29 tests): `OrderedDict` LRU bounded by
     `max_entries` (default 10 000, one WARNING when first exceeded), per-key
     `asyncio.Lock`s that are dropped when the last waiter leaves, lazy expiry on read
     (`_live` deletes a dead entry rather than hiding it), `put` advances the version,
     `put_if_version` refuses a missing or expired key, `touch` on a missing key is a
     DEBUG no-op. The clock is injectable (`clock=`) so TTL tests do not sleep.
     `lock()` wraps `asyncio.timeout` and translates `TimeoutError` into
     `LockTimeoutError(key, timeout)`; the `timeout` parameter carries
     `# ruff:ignore[async-function-with-timeout]` (ASYNC109) because the protocol
     mandates it — that is the new `ruff:ignore[rule-name]` syntax, same as the
     `too-many-return-statements` one in `schema.py`.
   - **The envelope** (`state/serde.py`, 8 tests): `Envelope(v, n, f, d)` is a frozen
     msgspec Struct; `pack_entry` raises, `unpack_entry` returns `None` on
     `DecodeError` (house law). A record from a *newer* risa with extra fields still
     decodes — msgspec ignores unknown keys — so the envelope is forward-compatible.
   - **`backend.py` skeleton**: `StateBackend` ABC (`session(meta=, component=,
     interaction=)`, `first_anchor(meta=, view=)`, `commit_first(meta=, view=, anchor=)`)
     and `StateSession` ABC (async context manager over `open`/`close`, plus `restore()
     -> Restored | Unreadably`, `stage(view) -> str`, `published(view)`, `settle(view)
     -> bool`), `Unreadably.{MISSING, OUTDATED}`, the frozen `Restored(view)` wrapper
     and `hydrate(meta, values)` which zips `meta.schema.fields` onto `meta.cls(**kw)`.
     **Every method is abstract — decided 2026-08-17**: a default implementation on the
     base would let a backend silently inherit behaviour it never thought about; each
     backend spells out all of it. Nothing imports `backend.py` yet, and it has no tests.

   Decisions taken that evening, besides the §7.1/§7.5 ones recorded under step 1:
   - **`Store` stays a `typing.Protocol`, not an ABC.** A user's Redis wrapper must not
     have to inherit from risa; structural typing is the contract and pyright checks it.
     `MemoryStore` therefore subclasses nothing; `test_memory.py` pins protocol
     conformance. Validating a user store at client construction was discussed and
     dropped — pyright already does it, and a runtime check cannot see signatures.
   - Stores got their own package `risa.store` (one module per backend) rather than a
     single module; `risa.state` holds everything placement/schema/session-side.
   - **Decided 2026-10-25:** `anchor.py` lives in `risa/state/`, not `internal/` — it is
     state machinery like schema and serde. Not star-exported from `risa.state`.
   - **Decided and built 2026-10-25:** pluggable text codecs (see the anchor section
     above and DESIGN §6.1). Rejected on the way: identifying the codec by configuration
     alone (alphabets overlap, measured), hashing it into the cookie (kills live panels on
     a switch), reusing `ver` as a combined layout+codec letter (multiplies letters on a
     layout bump), and a byte-layout v2 with one encode over the whole id (a rewrite of
     the whole wire for ~5–7 % under dense codecs and a *longer* header under base85;
     stays possible later via `ver`).

   Still to build in this step (DESIGN §7.3–§7.6):
   - **`MessageBackend` / `MessageSession`**: per-message `asyncio.Lock` keyed on the
     message id, the `seq` counter and version cache from §7.3, `restore()` gathering the
     fragments from `interaction.message` via `anchor.gather` + `schema.unpack`
     (`OUTDATED` on a fingerprint miss or torn anchor), `stage()` packing a fresh
     `MessageAnchor`, dirty computed by byte-comparing the re-encoded fields (§7.4).
   - **`StoredBackend` / `StoredSession`**: key namespace `risa:{app_id}:1:<token>`
     prepended *here*, not in the anchor; `lock(timeout=)` around the dispatch;
     `restore()` = `get_versioned` → `unpack_entry` → check `n`/`v`/`f` against meta
     (`OUTDATED` on mismatch, `MISSING` on `None`); `settle()` = CAS via `put_if_version`,
     on `False` re-read: `None` → vanished, else → lost the race → `schema.adopt` the
     winner onto the live instance and return `False` so dispatch re-renders the winning
     state (converge, never raise — §7.3); clean dispatches `touch()` the key (sliding
     TTL); `StoreUnavailableError` is raised by the *backend* when the store itself
     throws, so an outage never masquerades as expiry.
   - **`StatelessBackend`** for all-Prop views so dispatch never branches on "has state"
     (DESIGN §15 puts it in step 3, but it is cheap to add while the ABCs are fresh).
   - **`on_state_missing`** on `View` (InStore only, §7.6) mirroring `on_outdated`'s
     census wiring (`_injected_*`, `ViewMeta.handles_*`).
3. **Build + dispatch + context rewire** — the two-phase build, the message-component
   reader (`internal/reader.py`), one session-driven dispatch path, commit-per-rerender,
   `anchor.distribute` parking unreached components on a shared index, a `StatelessBackend`
   so nothing branches on whether a view has state, and named stores on the client.
   **Constraint recorded earlier:** the auto-defer watchdog is armed *after* `meta.cls()`,
   so §7.2's `async def load()` must be awaited **after** the watchdog is armed — otherwise
   store I/O burns the 3s window unprotected, contradicting §8.1's "the timer starts at
   decode". `rerender()` gains its real meaning here (hydrated `self`), and the poll
   testbot's `read_counts` is deleted the same day.
4. **Modals** (§9), then the 1.0 roadmap: `RedisStore` + `verify_store`, the docs pass.

Smaller open items, any order: a `ui.File` upload through the testbot to watch a real
multipart render; a nox session that pyrights an out-of-tree consumer against a built wheel
(the only real guard for the `__init__.py` export fix).

## Notes that outlive the current step

**slotscheck** is adopted (nox session + `[tool.slotscheck]`, `require-subclass = true`).
`detect-unused-slots` is deliberately **off**: it cannot see that msgspec `Struct`s use the
slots msgspec generates for them, so every Struct in the package reports a false positive.

**The docs pass**, when it comes, starts by re-enabling `D` / `DOC` in `ruff.toml` so the
linter drives it. Things decided along the way that must be written down there:

- The **rejection handler** as the precise alternative to `on_outdated` (§6.4): keep the
  retired handler at the same id, version and wire signature with a body that says "this is
  outdated". It routes as an ordinary handler and logs nothing, where `on_outdated` is the
  catch-all for anything on the view that no longer exists.
- Wire enums are stdlib `enum.Enum` only. hikari's own enums are not wire types by
  decision, so `kind: hikari.ChannelType` is a DI parameter and binding one fails at render
  with an arity `ArgBindError`; pass `int`/`str` and rebuild it in the handler.
- Nested `risa.bind` keyword rebinding is CPython's behaviour, not risa's: nested partials
  flatten with the outer keyword winning before risa can see it, so the double-supply
  `ArgBindError` is unreachable for keyword-over-keyword. Positional conflicts still raise.
- `Rendered.respond_to` targets *non-dispatched* interactions (commands and the like);
  inside a risa handler it bypasses the response gate, so `ctx.respond(**rendered)` is the
  correct spelling there.
- Handlers return `None` by contract, enforced statically. Registration resolves all
  annotations through `typing.get_type_hints`, so an exotic runtime-unimportable *return*
  annotation fails registration by design rather than being special-cased.
- Pair the defer modes with the response verbs: THINKING ↔ `respond()`, UPDATE/OFF ↔
  `rerender()`.
- The durable-state warning from §7.6: an InMessage anchor is world-readable and
  client-forgeable. Never put secrets in durable fields, and never make one the sole basis
  of an authorization decision.

**Explicitly rejected by the structure audit**, with reasons: splitting `codec.py` (DESIGN
§4 scopes it as one unit), reordering `errors.py` by phase (§13's table already provides
that view), a `_Watchdog` context manager (it would invert the lock ordering §8.1
specifies — "passes the suite and fails under load"), narrowing `HandlerFunction` to
`Coroutine` (fiction 5, and it narrows a public contract), and a namespace class of
`@staticmethod`s (a module already is the namespace). What *was* worth taking from that
last one — moving a layout onto the struct it describes — is done, in `anchor.py`.
