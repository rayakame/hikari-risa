# risa — design

Orientation document for building the library. Describes the intended shape, not what
exists. Where something is undecided it says so explicitly.

Status legend: **[decided]** settled, build to it · **[open]** needs a call before that part
gets built.

---

## 1. What this is

A component handler for hikari, built Components V2 first, where **restarting the bot does
not break components on already-sent messages**.

The three existing libraries each solve part of this and none solve all of it:

| | miru | flare | lightbulb.components |
|---|---|---|---|
| Survives restart | no | **yes** | no |
| Components V2 | no | no | no |
| Auto-defer | yes | no | no |
| Arbitrary state size | yes | no (100 chars) | yes |

risa targets all four cells.

### Non-goals

- Being a command framework. Commands are lightbulb/arc/tanjun's job.
- Supporting hikari < 2.3.0. Components V2 builders landed there.
- Holding non-serialisable state (open sockets, running tasks) on a view. That is the one
  thing an in-memory design does better, and it is given up deliberately.

---

## 2. Core principles

Everything below follows from these three. When in doubt, re-derive from here.

### 2.1 Build V2; V1 falls out **[decided]**

V1 is exactly "a V2 tree whose top level contains only action rows". There is **one** tree
model and **one** layout pass. hikari sets `MessageFlag.IS_COMPONENTS_V2` automatically when
it sees a V2 builder, so risa never sets it manually.

Corollary: **do not hard-wire the 5×5 grid into the type surface.** miru's `_ItemArranger`
(a `[0]*5` weight vector) and lightbulb's `RowT` TypeVar constrained to the two row-builder
types are exactly why neither can retrofit V2. Row packing is a property of `ActionRow`
alone, not of the handler.

### 2.2 Nesting is layout; dispatch is flat **[decided]**

This is what makes "you can nest V2 like crazy" a non-problem.

Only three things in the whole spec are interactive:

1. children of an `ActionRow` (buttons, the five select types)
2. a `Section`'s `accessory` when it is a button
3. `TextInput` inside a modal

`Container`, `Section`, `TextDisplay`, `MediaGallery`, `Separator`, `File`, `Thumbnail` are
inert. So the tree is flattened to a sparse set of interactive leaves at build time and
routed against a flat `dict[str, Handler]`. **Tree depth costs nothing at runtime.**

Do not walk the tree on dispatch. Walk it once, at build.

### 2.3 State rides the message; a store is the opt-in for what cannot **[decided — revised]**

`custom_id` carries *identity* (which view, which handler) plus an *anchor* for the view's
durable state. The anchor comes in two dialects, chosen per view at registration:

- **`InMessage` (the default)**: the state *itself*, serialized and chunked across the
  spare `custom_id` characters of the components the view renders. Zero infrastructure,
  restart-proof by construction, capacity-bounded (~85 chars per interactive component);
  state lives exactly as long as the message. This is flare's idea scaled ~40× by V2.
- **`InStore`**: a random 96-bit key, with the state living as one record in a pluggable,
  developer-chosen `Store` (memory, Redis, their own). Unbounded size, expirable, and with
  a distributed store multi-process-correct — miru's ergonomics made durable.

Both are "state reachable from the message" — one embeds the value, the other a pointer —
riding one chunk pipeline, so handler code is byte-identical under either. Random keys (not
message IDs, not anything shard-derived) keep resharding a non-event for the store dialect;
a message ID could not be used anyway, since components enter the send payload before the
ID exists.

---

## 3. Constraints that shape the design

Verified against hikari master; re-check if these change.

- **`ComponentInteraction` does not expose the V2 `id` field.** hikari never deserialises it
  (`impl/entity_factory.py:3181`). `custom_id` is the only routing channel. Structural /
  id-based addressing is off the table.
- **`custom_id` is capped at 100 Unicode code points**, minimum 1. *Measured*
  against the live API (2026-07-26, hikari 2.5.0, 124 requests, binary search per
  character class, REST `create_message` + `fetch_message`); Discord documents only
  "100 characters". UTF-8 bytes, UTF-16 units and graphemes were each ruled out by
  counter-example — 100 CJK characters (300 bytes) and 100 astral emoji (200 UTF-16
  units) are both accepted, while 50 NFD `e´` pairs (100 code points) are rejected at
  51. So Python's `len` is the correct check and `len(s.encode())` is not.
  Discord treats the field as an opaque string: no normalisation (NFD survives as
  NFD), no trimming, no stripping of zero-width characters, byte-exact round trip,
  and no character was rejected — including NUL, `\x1f`, newlines and quotes.
  Note the client counts UTF-16 units instead, which is why emoji count double in
  the Discord UI; `custom_id` has no client-side counter, so only the backend rule
  applies. **Not yet measured:** the interaction payload path (round-trip was
  verified through REST only), select `option.value`, and modal ids — re-run the
  probe before relying on any of this in a new API version.
- **A message with `IS_COMPONENTS_V2` cannot carry `content` or `embeds`.** Everything is
  components. So a view renders *the whole message body*, not just its component list.
- **`ComponentBuilder.build()` returns `(payload, attachments)`**, a tuple — unlike
  `SelectOptionBuilder.build()`, which returns a bare mapping.
- **hikari validates almost nothing** about component layout. `add_select_menu` documents a
  `ValueError` it never raises; `MessageActionRowBuilder(components=[...])` bypasses the row
  homogeneity check entirely. risa does not fill that gap either -- see 5.2.
- **hikari models neither the 3-second nor the 15-minute interaction window.** They surface
  only as `NotFoundError`. Timing is risa's job.
- **`interaction.message.components` is fully deserialised**, so the rendered tree *is*
  readable at interaction time. Not needed for dispatch, but useful for debugging and for a
  possible `View.from_message` later.

---

## 4. Architecture

Passes out, the mirror image in — one pipeline, parameterized by the view's `StateSchema`
and placement.

```text
OUTBOUND
  render()   → node tree          pure, user code, no I/O
  load()     → props filled       DI-injected refetch hook, awaited first
  flatten    → slots              interactive leaves + spare capacity per leaf
  anchor     → fragments          InMessage: encode+chunk state; InStore: store write, key
  emit       → hikari builders    fragments + args distributed into custom_ids

INBOUND
  decode     → (cookie, handler, fragment, args)
  session    → lock + load        per-key lock; gather chunks / store read  ← I/O HERE
  refresh    → props via load()   before user code
  dispatch   → user callback      rerender() = full commit (CAS-then-edit / edit-is-write)
  commit     → final dirty check  auto-commit if mutated without rerender; release
```

The position of the I/O in the inbound path is load-bearing: it is why auto-defer must start
at *decode*, not at handler entry (§8). Locks bracket only this pipeline — never a modal
wait (§7.3, §9).

### Module layout

```text
risa/
  __init__.py       public re-exports                          [exists]
  _about.py         metadata                                   [exists]
  client.py         Client ABC, transports, dispatch           [done]
  context.py        Context, responses, auto-defer             [done]
  di.py             Contexts, INJECTED re-export               [exists]
  errors.py         exception hierarchy                        [exists]
  view.py           View base, @register(state=), @handler, Prop, load()   [done]
  modal.py          Modal base, @modal, TextInput, prompt machinery   [pivot step 5]
  internal/
    wire.py         the printable alphabet every id is written in
    codec.py        custom_id layout: header, fragment slot, args, fingerprints
    anchor.py       anchor dialects, splitting and rejoining across components
    constants.py    Discord limits, stamped attribute names    [exists]
    registry.py     ViewMeta, cookie -> view registries        [exists]
    reader.py       message-component walk for chunk reassembly [done]
  ui/
    nodes.py        full V2 node set                           [done]
    build.py        two-phase flatten + emit                   [done]
  state/
    store.py        Store protocol, MemoryStore                [done]
    schema.py       StateSchema: durable fields, packing, prefix fingerprints
    placement.py    InMessage / InStore policy objects         [done]
    backend.py      StateBackend/StateSession protocols        [done]
    message.py      MessageSession + version cache             [done]
    stored.py       StoredSession                              [done]
    stateless.py    the degenerate placement: nothing to keep  [done]
    serde.py        store-entry envelope {v,n,f,d}             [done]
    redis.py        RedisStore                                 [extra: redis; roadmap]
    testing.py      verify_store conformance kit               [roadmap]
```

---

## 5. The node layer

### 5.1 Node types **[decided; built as `risa/ui/nodes.py`]**

Mirror the V2 spec one-to-one. Every node is an immutable-ish value object; `render()`
returns a fresh tree each time.

```text
Layout (top level, max 10)
├── Container(*children, accent_color=, spoiler=)   # cannot nest a Container
├── Section(*text_displays, accessory=)             # 1-3 TextDisplay + 1 accessory
├── Row(*components)                                # ActionRow
├── TextDisplay(content)
├── MediaGallery(*items)                            # 1-10
├── Separator(divider=, spacing=)
└── File(file, spoiler=)

Interactive leaves
├── Button(handler, label=, emoji=, style=, disabled=)
├── LinkButton(url, ...)                            # no handler, never dispatches
├── PremiumButton(sku_id)                           # ditto
└── TextSelect / UserSelect / RoleSelect / MentionableSelect / ChannelSelect
```

`Thumbnail` is only ever a `Section` accessory. `Button` is both a `Row` child and a valid
`Section` accessory — do not forget the second case when flattening, it is the easiest
interactive leaf to miss.

### 5.2 Discord's limits are not policed **[decided]**

risa validates what it owns and leaves Discord's numeric limits — five per row, one to three
text displays per section, ten top level, forty total, four thousand characters — to Discord.

The reason is an asymmetry in how a stale rule table fails. When Discord *loosens* a limit, a
table that has not caught up rejects a tree that is now perfectly valid, and the user is
blocked with no recourse short of patching risa. When Discord *tightens* one, the request
simply fails as it would have anyway. Discord has historically loosened far more often than
it has tightened, so keeping the table means the likely failure is the one that blocks people,
in order to protect them from an error they would have received regardless.

The half of the rules that never goes stale is enforced already, and for free: a container may
not hold a container, a section holds only text displays, a row holds only interactive
components, and a section must have an accessory. Those are architectural rather than
numeric, and the node constructors' parameter types reject all of them at author time.

What remains risa's own to check, because Discord cannot:

- a `custom_id` that would exceed 100 characters -- `CustomIdOverflowError`
- a component whose handler belongs to a *different* view. The id encodes this view's cookie
  beside a foreign handler token, so Discord accepts it and the click routes to nothing.

The cost accepted here is that a layout mistake surfaces as Discord's `50035 Invalid Form
Body`, whose path indexes the built payload rather than the tree that produced it.

Adding a rule table later is additive: the build pass walks the tree regardless.

### 5.3 Syntax **[decided: literals only]**

Nested literals are the API; loops and conditionals use comprehensions and splats.

```python
def render(self) -> ui.Layout:
    return ui.Container(
        ui.TextDisplay(f"## {self.question}"),
        ui.Separator(),
        *[
            ui.Section(
                ui.TextDisplay(f"**{o.name}** — {o.count}"),
                accessory=ui.Button(self.vote.bind(o.id), label="Vote"),
            )
            for o in self.options
        ],
    )
```

An earlier draft also planned a dominate-style ``with``-block form (indentation as the
tree, via a contextvar parent stack). **Rejected**: it adds no capability, it needs claim
semantics to avoid double-attaching nodes that are both constructed inside an open block
and passed to a constructor, and — decisively — children arriving dynamically through a
parent stack cannot be type-checked, so the structural nesting rules §5.2 gets for free
from constructor parameter types would have had to be re-enforced at runtime. A syntax
that trades away the library's static guarantees to save a splat is a bad trade.

---

## 6. The custom_id codec

### 6.1 Wire format **[decided]**

```text
┌─────┬──────────┬───────────┬─────┬──────────┬──────────┬──────────────┐
│ ver │  cookie  │  handler  │ idx │ frag_len │ fragment │  sig ‖ args  │
│  1  │    6     │     2     │  1  │    1     │  0..n    │   the rest   │
└─────┴──────────┴───────────┴─────┴──────────┴──────────┴──────────────┘

ver       codec version (this format IS v1; nothing older ever shipped)
cookie    b64(blake2s(view_name + ":" + schema_version, digest_size=4))[:6]
handler   b64(blake2s(handler_id + ":" + handler_version, digest_size=2))[:2]
fragment  this component's slice of the view's ANCHOR (below); frag_len frames
          it so the args section is findable; empty for all-Prop views
sig+args  2-char signature fingerprint + length-prefixed wire args, exactly
          when the handler has wire parameters (§6.3/§6.4)

anchor    InMessage: [tag:1][total:2][schema-fp:3][seq:3][msgpack durable fields],
          chunked across the interactive leaves in tree order; reassembled
          from interaction.message.components on every click
          InStore:   [tag:1][96-bit key], replicated whole into every component
```

**Every character risa writes is printable ASCII** (`risa/internal/wire.py`): base85
for packed bytes, a 92-symbol digit alphabet for lengths, indices and counters,
neither containing `"` or `\`, both stable under every normalisation form.

The limit is **100 Unicode code points** (§3), so this is a spend, not a necessity:
latin-1 packing would buy 25% more and a CJK digit alphabet over twice as much. It
is spent for three reasons — `custom_id`s must stay readable in logs, tracebacks and
Discord's own error bodies; one character stays one byte in the request body, so a
forty-component message does not balloon under JSON escaping; and the read-back path
risa depends on most (`interaction.message.components`) has not been measured, while
ASCII is correct regardless of the answer. A denser encoding stays open as an opt-in:
an unrecognised anchor tag already fails closed, so one can be added later without
disturbing live components.

The total length lives inside the message dialect rather than in chunk 0's frame,
so `carve`/`gather` treat the anchor as opaque and never inspect a tag. Reassembly
checks contiguity; the declared total is what catches a message whose trailing
components were removed.

Properties that matter:

- **The cookie hashes the schema version, not just the name.** A state-shape change then
  invalidates old components automatically instead of deserialising garbage into the new
  shape. flare's positional `zip` over annotations is the cautionary tale — reorder two
  fields and old buttons silently mis-assign.
- **The handler token hashes the handler's id and version, not its position.** It therefore
  depends on nothing about its siblings, so reordering, adding or removing other handlers
  never disturbs it. The id defaults to the method name and can be pinned to survive a
  rename; the version defaults to 1 and is bumped to retire or supersede old components
  (§6.4).
- **Components carrying args also carry a signature fingerprint.** Args are positional,
  typeless bytes that only mean something read back through the converter chain that wrote
  them; the fingerprint is what notices when that chain changed in place (§6.4).
- **Routing never depends on the fragment.** A click on a *stale* component still routes
  by its header and reads *current* state from the message or store — which is what keeps
  in-flight clicks valid across rerenders.
- **The placement tag is the one mode byte that earned its place** (an earlier draft
  rejected one as redundant with the registry): it is what makes a Message↔Store placement
  switch fail closed with a precise diagnosis instead of misparsing a value as a key, and
  what a future lazy migration keys on.

### 6.2 Decode must fail soft **[decided]**

```python
def decode(custom_id: str) -> DecodedId | None:
    if not custom_id or custom_id[0] != VER:
        return None        # not ours — let miru/flare/whatever handle it
    ...
```

Returning `None` rather than raising is what lets risa coexist with other handlers in the
same bot. flare does this well.

### 6.3 View state vs component args **[decided]**

Two different kinds of data. Conflating them is expensive to undo.

| | Lives in | Example |
|---|---|---|
| **View state** | the store, one entry per view instance | `{question, votes}` |
| **Component args** | the custom_id, always inline | which of N buttons was clicked |

```python
ui.Button(self.toggle.bind(role_id), label="Add")   # arg baked into THIS button's id

async def toggle(self, ctx: risa.Context, role_id: int) -> None: ...
```

`bind()` typed with `ParamSpec` gives a static error on arity/type mismatch.

Arg converters cover `int`, `str`, `bool`, `Enum`, `Snowflake` — that is essentially
everything. Use flare's byte-oriented encodings (little-endian in latin-1), not `str()`: an
int costs 1–2 chars instead of 19.

**Arg converters must be synchronous. [decided]** flare made them async so they could fetch
users during decode, which puts an unbounded HTTP call inside the 3-second window *before*
the handler can defer. Store IDs; resolve them in the handler.

**Wire args are the contiguous prefix of converter-typed parameters after ``ctx``.
[decided]** Classification is by annotation: a parameter whose annotation has a converter
is a wire arg; the first parameter that does not -- or that defaults to ``linkd.INJECTED``
-- ends the wire section, and everything after it belongs to dependency injection. A
converter-typed parameter *after* that point is rejected at registration, because it would
otherwise silently become a DI lookup for a primitive. Consequence: a handler cannot inject
a bare ``int``/``str`` dependency; wrap such a dependency in its own type. Unions --
including ``X | None`` -- have no converter and are therefore DI, not wire; use a sentinel
default rather than ``None``.

**Framing. [decided]** Each arg is length-prefixed (one char, ≤255 chars of data) and
encoded by its converter; ints are minimal-width little-endian bytes rendered as latin-1.
``bind()`` normalises keywords to positions, requires bound args to cover a contiguous
prefix of the wire parameters, lets trailing defaulted parameters be omitted (dispatch then
lets the *current* Python defaults apply), and encodes eagerly, so a bad value fails at the
``render()`` call site rather than at click time. Handlers with no required wire parameters
may be placed on a component without calling ``bind()`` at all.

### 6.4 Handler identity, versioning and the signature fingerprint **[decided]**

A ``custom_id`` outlives the code that wrote it, and its args only mean something read back
through the same converter chain that wrote them. Two mechanisms keep yesterday's
components and today's signatures honest:

- **The handler token hashes ``(handler_id, version)``.** ``@risa.handler`` defaults to
  version 1; bumping the version retires the old token. Two methods may share a
  ``handler_id`` at different versions, so old components keep routing to old code while
  new renders bind the new version. Arg migration is therefore plain code -- the v1 method
  converts its args and delegates to v2 -- and needs no framework.
- **Components that carry args also carry a 2-char signature fingerprint**: a hash of the
  canonical converter chain (``i`` int/snowflake, ``s`` str, ``b`` bool, ``ei``/``es``
  enum by value kind -- types, never parameter names, so renaming a parameter is free and
  ``int`` -> ``Snowflake`` is free). At decode it is compared against the resolved
  handler's current chain. A mismatch means the signature changed in place without a
  version bump: fail closed, log at ERROR -- it is a developer mistake with a named fix,
  and deliberately the loudest failure in the library.

The three retirement modes:

| The developer does | Old components | Log |
|---|---|---|
| bumps version, keeps the old one | route to the old method | none |
| bumps version, deletes the old code | ``on_outdated`` | WARNING; DEBUG once ``on_outdated`` is overridden |
| edits the signature in place | ``on_outdated`` | ERROR, always |

``on_outdated`` is a classmethod on the view. It is callable at all because a token miss
still resolved its cookie -- the one place fail-closed can still answer the user politely,
which a cookie miss never can. Overriding it counts as acknowledging retirement, which is
what downgrades the token-miss log; a view that has said what to do is not nagged for
doing it. A retired handler can also be kept as a *rejection handler* -- same id, version
and wire signature, body answers "this is outdated" -- which routes as a perfectly normal
handler and logs nothing.

Rejected alternatives: hashing the arg types into the token itself (automatic, but offers
no way to keep old components alive and no per-handler remedy for same-type semantic
drift, whose fix stays the whole-view version bump); per-arg type tags on the wire (more
precise diagnostics and would enable arg migration, but costs a char per arg and versioned
handlers already provide migration as plain code -- deferred; the codec version byte
leaves room to add it later).

---

## 7. State: placements, fields, and the Store

### 7.1 Placement is per view: `state=InMessage() | InStore(...)` **[decided]**

```python
@risa.register(name="poll", version=1)                       # == state=risa.InMessage()
class Poll(risa.View): ...

@risa.register(name="todo", version=1, state=risa.InStore(store="redis", ttl=30 * 86400))
class TodoList(risa.View): ...

client = risa.client_from_app(bot, stores={"redis": MyRedisStore(...)})
```

Placement is configuration, not taxonomy — one `View` base, one handler semantics, one
`render()`/`rerender()` contract under both. The policy objects carry exactly their own
knobs: `ttl` (required) exists only on `InStore`, so invalid combinations are
unrepresentable rather than validated. Store *instances* are wired once, by name, on the
client; a view naming an unconfigured store fails at `add_view()` — a boot error, never a
dead click. `InStore` on the default `MemoryStore` warns loudly at startup: it silently
forfeits the restart promise, and under multi-process REST it is *worse* than `InMessage`.

The decision rule the docs lead with: **bounded and world-readable → default; unbounded,
expirable, or multi-process REST → `InStore`.** (Rails' cookie-store-vs-Redis-sessions is
the same dichotomy with two decades of hindsight; their default is the payload-resident
one too.)

A 1-char **placement tag** heads every anchor blob. Switching a live view's placement
without a version bump therefore fails closed to `on_outdated` with a precise log instead
of misparsing a value as a key — and the tag is what a future opt-in `InStore(adopt=True)`
lazy migration would key on (§15 roadmap).

### 7.2 Fields are durable by default; `Prop[T]` marks the fresh ones **[decided]**

```python
@risa.register(name="board", version=1)
class Leaderboard(risa.View):
    guild_id: int                                             # durable (~9 chars)
    page: int = 0                                             # durable — survives restarts
    rows: risa.Prop[list[Row]] = msgspec.field(default_factory=list[Row])   # fresh, free

    async def load(self, db: Database = linkd.INJECTED) -> None:
        """Awaited before every render — initial send and every dispatch."""
        self.rows = await db.top(self.guild_id, page=self.page)
```

- Plain annotated fields are the durable state — today's mental model, unchanged, and the
  zero-annotation poll stays restart-safe (the founding promise as the default).
- `Prop[T]` (`type Prop[T] = Annotated[T, marker]`; detection is an identity check on the
  alias, which survives `from __future__ import annotations` — verified empirically) marks
  a per-dispatch field: never serialized, zero wire cost, must have a default, rebuilt
  fresh on every dispatch and refilled in `load()` — the one blessed, DI-injected refetch
  point, run after rehydration and before the handler.
- The polarity is chosen for failure loudness: a forgotten `Prop` marker overflows the
  wire budget *at render, naming the field, suggesting the fix*; the inverse polarity
  would silently reset state on every click — the founding promise breaking quietly.
- A view with zero durable fields is the degenerate case, not a mode: the old
  `persist=False` flag is subsumed and deleted. Placement is moot for such views.
- Registration errors: a `Prop` marker in a non-top-level position (`Prop[int] | None`) is
  an import-time error with the fix shown (`Prop[int | None]`); the missing-default error
  names *both* remedies so it stops teaching people to create silently-resetting fields.
- Internally one `StateSchema` per view owns the partition and every view↔bytes
  conversion as *total functions* — empty partition ⇒ empty blob ⇒ zero-width wire
  segment ⇒ never dirty — so build/dispatch/context never branch on a mode.

### 7.3 The concurrency contract **[decided — revised after the lock/modal investigation]**

**The locking law: a state lock is acquired, used, and released within work whose duration
risa controls — never across human think-time.** Everything else follows from it.

The model is a hybrid (Kleppmann's fencing stance): **CAS is the correctness mechanism;
the lock is a throughput optimization.**

- Within a process, a per-key lock (message id for `InMessage`, store key for `InStore`)
  serializes handlers on the same view — atomic check-then-act, the advertised win over
  miru/lightbulb, preserved on every gateway bot by shard-stickiness.
- Across processes, `put_if_version` settles the race. A lost CAS is **converged, never
  raised and never retried**: the loser suppresses nothing it already sent, re-renders the
  winning state so the message ends true, answers its interaction, and logs — a handler
  that already responded must not double-send, and a user must never see a hanging click.
  Convergence works by *adopting* the winner's durable fields onto the instance the handler
  is holding, rather than rendering a second one: the view then re-encodes to exactly what
  was read, so nothing later in the dispatch can commit the discarded values on top of the
  winner. When there is nothing to converge onto — the record vanished, or what replaced it
  cannot be read — the transaction ends and `on_state_missing`/`on_outdated` answer instead,
  with the session detached so the hook cannot commit against a dead baseline.
- `InMessage` cross-process (multi-process REST only) is last-write-wins, stated plainly.
  The version cache + seq counter are load-bearing within a process, advisory beyond it.

The contract sentence the docs print: *"Each click runs your handler as a transaction on
the latest committed state of its view — simultaneous clicks on the same view queue within
a process and resolve first-write-wins across processes — and a modal ends the transaction
before the form opens, so nothing is ever locked while a user is typing."*

Per-handler isolation declarations (`isolation=risa.Serialized()` for distributed
serialization via a store lease; `risa.Concurrent()` to skip the queue for hot read-only
handlers) are designed but deferred post-1.0 (§15).

### 7.4 Write cadence **[decided — revised]**

- **Every `rerender()` is a full commit.** `InMessage`: the message edit *is* the write.
  `InStore`: CAS-write then edit, both under the lock — the message must never show state
  the store has not accepted; a crash between leaves the store authoritative and the
  visuals self-heal on the next click. Each commit advances the dirty baseline, so a
  second rerender in the same handler compares against what the first one committed.
- **Dirty is computed, never declared**: re-encode the durable fields, byte-compare. No
  `state_dirty` flag exists.
- **A handler that mutates durable state and never rerenders still commits** at dispatch
  end (`InStore`: the CAS; `InMessage`: an automatic, visually-identical components edit).
  Mutating state and silently losing it is not an outcome this library ships.
- Clean dispatches on `InStore` `touch()` the key — sliding TTL, so a view in use never
  expires and an abandoned one eventually does.
- `ctx.edit(layout)` must re-embed the current anchor into the ad-hoc tree, or raise
  (`InMessage` + a component-poor layout cannot carry the blob) *before* touching the
  message — an edit must never destroy the only copy of state. **A tree with no interactive
  components at all is the exception, not the failure**: nothing on it can be clicked, so no
  reachable copy of the state is being destroyed. That is what makes the blessed terminal
  screen (`await ctx.edit(ui.TextDisplay("Poll closed."))`) work identically under both
  placements; the overflow is reserved for a tree that still routes but cannot carry.

### 7.5 The Store protocol **[decided — hardened]**

```python
class Store(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def get_versioned(self, key: str) -> tuple[bytes, int] | None: ...
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None: ...
    async def put_if_version(self, key: str, value: bytes, *,
                             expected: int, ttl: float | None) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def touch(self, key: str, *, ttl: float) -> None: ...
    def lock(self, key: str, *, timeout: float = 10.0) -> AsyncContextManager[None]: ...
```

Changes from the original protocol, all verdicts of the lock investigation:

- **`lock()` gains an acquisition timeout**, raising `LockTimeoutError`. Lease duration,
  renewal, and max-hold are *backend configuration* (constructor knobs), not protocol
  parameters — a user's store stays implementable as a thin wrapper. A leasing backend may
  lose exclusivity under a stalled holder, which is why the lock is never the correctness
  story: every commit goes through `put_if_version`.
- **`StoreUnavailableError`** joins the contract: an outage must not masquerade as expiry
  in `on_state_missing`. Lost-race and entry-expired need opposite dispositions and must
  be distinguishable at the `put_if_version` boundary.
- Store entries carry the envelope `{v, n, f, d}` — schema version, view name, durable-
  field fingerprint, data — so rename/remove/retype without a version bump fails closed
  identically on both placements, entries survive store migrations, and the future
  `@risa.migration` hook has something dated to decode. Keys are namespaced
  `risa:<fmt>:<token>`.
- `MemoryStore` (OrderedDict + LRU + per-key asyncio locks) stays the zero-config default;
  `RedisStore` plus a `risa.state.testing.verify_store` conformance kit are 1.0 roadmap —
  the protocol is sharp to implement (leases, atomic put+ttl, CAS races), and handing it
  to users without a test harness would be negligent.

### 7.6 Anchors, keys, and schema evolution **[decided]**

- `InStore` keys: 96 random bits, minted at build, **replicated into every component**
  (not chunked — every custom_id stays self-sufficient, and stored views do not need the
  capacity). Custom_ids are therefore *stable across rerenders* — an in-flight click is
  never invalidated. A failed send leaks one entry; TTL reaps it; no two-phase commit.
- `InMessage` blobs: `[tag][schema-fp:2][seq:3][msgpack positional array of durable
  fields]`, chunked across the interactive leaves. No `{v,n}` envelope — the cookie
  already pins name and schema version, and every char is capacity.
- Evolution policy, both placements: **appending a defaulted durable field is free**
  (prefix fingerprints); remove/reorder/retype/rename fails closed to `on_outdated`.
  Bumping the view `version` changes the cookie and retires old components wholesale, as
  before. `on_state_missing` exists only for `InStore` (expiry/eviction/flush are store
  phenomena; message-resident state cannot go missing — the one torn-message case routes
  to `on_outdated`).
- Durable state is **world-readable and client-forgeable** — never secrets, never the sole
  basis of an authorization decision. Documented at the point of use, not in an appendix.

---

## 8. Context, responses, auto-defer

### 8.1 Auto-defer is a watchdog above the handler **[decided]**

Discord starts a 3-second stopwatch at the click; some response must go out before it runs
out. risa runs a watchdog beside every dispatch: sleep ~2s (leaving ~1s of network slack),
then, under the context's response lock, re-check whether anything was sent and defer if
not. The handler never knows it was slow.

- **The timer starts at decode**, before store I/O — the stopwatch started at the click,
  so store latency counts against risa's budget, not the user's. (miru starts its timer at
  callback dispatch, after its own overhead; deliberately not copied.)
- Default response is **`DEFERRED_MESSAGE_UPDATE`**, the silent ack — a component is
  usually about to edit its own message. The knob is ``risa.AutoDefer``:
  ``UPDATE`` (default) / ``THINKING`` / ``THINKING_EPHEMERAL`` / ``OFF``, settable per view
  on ``@risa.register(defer=...)`` and overridden per handler on
  ``@risa.handler(defer=...)``. ``OFF`` exists for handlers that respond with a modal,
  which must be the *initial* response and so cannot race a watchdog.
- **Every** response path funnels through one initial-response gate — a single
  ``asyncio.Lock`` plus an issued flag — that the watchdog also takes. Whoever wins the
  lock is the initial response; the loser sees the flag. The watchdog issues its REST call
  inline rather than calling ``ctx.defer()``, which would deadlock on the same lock.
- End-of-dispatch behaviour differs by outcome: a handler that **finishes cleanly without
  responding** gets the deferred ack issued immediately (silent, no user-visible error); a
  handler that **raises** gets the watchdog cancelled, so the failure stays visible instead
  of being acked into silence. That ack is **always the silent one**, whatever the watchdog
  was configured to send — a spinner promises something still to come, and nothing is coming
  from a dispatch that has ended — and it is issued only for an interaction risa actually
  adopted, so a `custom_id` another library wrote is still never answered. It fires even
  under `AutoDefer.OFF`, which turns off the *watchdog*, not the promise that a click gets
  answered: that is what a conditionally-modal handler needs on the branch that opens none.
  The watchdog is stood down under the response lock it holds across its own REST call, so a
  deferral already in flight is recorded rather than aborted half-issued and then re-sent.

### 8.2 Context surface **[decided]**

Four verbs, split by *which message* they touch — no ``edit=`` flags changing what a method
means:

```python
await ctx.respond(content, ephemeral=..., ...)  # a NEW message; mirrors hikari's kwargs
await ctx.rerender()                            # redraw the component's message from state
await ctx.edit(layout)                          # replace the component's message with a tree
await ctx.defer(thinking=False, ephemeral=False)
```

**Each surface mirrors the thing that defines the message it touches.** Followups are
ordinary Discord messages risa does not own, so ``respond()`` mirrors hikari. The origin
message is defined by ``render()``, so ``edit()`` takes exactly a ``ui.Layout`` — built
against the same view and anchor (re-embedding the current state, or raising if the tree
cannot carry it, per §7.4), so components in an ad-hoc tree still route. Offering
hikari's kwargs on the origin would be a trap: a V2 message cannot carry ``content`` or
``embeds``, so the familiar-looking parameters could only ever raise.

``rerender()`` is literally sugar — ``rerender() ≡ edit(self.render())`` — and is the
documented default posture: *the message is always ``render(state)``*. ``edit(layout)`` is
the escape hatch for terminal screens (``await ctx.edit(ui.TextDisplay("Poll closed."))``);
its docstring warns that an ad-hoc tree with interactive components will be overwritten by
the next ``rerender``, since state no longer describes what is on screen.

``respond()`` returns a small **`Response` handle** (``edit`` / ``delete`` / ``fetch``) —
an object rather than lightbulb's ``-1`` sentinel snowflake or miru's awaitable proxy.

The origin-message routing handles the case both miru and lightbulb push onto the user:
after a *thinking* defer or a ``respond()``, ``edit_initial_response`` targets the wrong
message. risa's ``rerender``/``edit`` always land on the component's message:

| Call | Nothing sent | After defer (update) | After defer (thinking) or respond() |
|---|---|---|---|
| ``respond()`` | initial `MESSAGE_CREATE` | webhook `execute()` | webhook `execute()` |
| ``rerender()`` / ``edit()`` | initial `MESSAGE_UPDATE` | `edit_initial_response()` | REST edit of the origin message |
| ``defer()`` | initial `DEFERRED_*` | `AlreadyRespondedError` | `AlreadyRespondedError` |

``ctx.prompt(SomeModal, ...)`` remains the modal chapter's problem; the initial-response
gate is built to accommodate it.

### 8.3 Rendered is the whole message **[decided]**

Because V2 forbids `content`/`embeds`, a view owns the entire message body. Make the build
result a `Mapping` of send-kwargs (miru's trick):

```python
rendered = await client.build(view)
await channel.send(**rendered)
await rendered.send_to(channel)
await rendered.respond_to(interaction)
```

Reject `content=` at the API boundary with a real message ("this view uses V2 components; put
your text in a `ui.TextDisplay`") rather than letting Discord 400.

---

## 9. Modals **[decided: callback-only for 1.0]**

A modal is a schema class; opening one is `ctx.prompt`; the submission is an **ordinary
fresh dispatch** — its own lock, load, commit — so modals survive restarts and load
balancers by construction, which none of the three existing libraries manage.

```python
@risa.modal(title="Rename option")
class RenameModal(risa.Modal):
    name: typing.Annotated[str, risa.TextInput(max_length=50)]
    reason: typing.Annotated[str | None, risa.TextInput(paragraph=True)] = None


@risa.handler(defer=risa.AutoDefer.OFF)
async def edit_option(self, ctx: risa.ComponentContext, option_id: int) -> None:
    await ctx.prompt(
        RenameModal(name=self.options[option_id]),   # prefill = constructor
        self.renamed,                                 # submit handler
        carry=(option_id,),                           # typed, wire-encoded flow data
    )

@risa.handler
async def renamed(self, ctx: risa.ModalContext, values: RenameModal, option_id: int) -> None:
    self.options[option_id] = values.name
    await ctx.rerender()
```

- Fields are `Annotated[..., risa.TextInput(...)]` — typed values, optionality from the
  annotation (`str | None` → optional input, empty arrives as `None`), labels defaulting
  from field names. Same pattern language as `Prop`.
- Modals are not registered and have no cookie of their own: identity rides the submit
  handler's token; the modal class resolves from the handler's signature at registration.
  The modal's field schema folds into that handler's fingerprint, so schema drift against
  an open form fails closed like any signature drift.
- **`carry=`** wire-encodes flow data into the modal's `custom_id` (the same trade
  `bind()` made): what would have been a stale closure becomes an explicit, typed,
  restart-surviving parameter.
- **A version token rides the modal's `custom_id`** (Fowler's optimistic offline lock):
  the submission compares it against current state; a mismatch skips the handler and
  routes to an overridable `on_prompt_conflict` (default: re-render the truth, ephemeral
  "this changed while your form was open").

**The inline await form (`prompt_wait`) was investigated and rejected for stateful views**
(§15 roadmap keeps a constrained future variant). Three findings closed it: Discord
forbids a modal in response to a MODAL_SUBMIT, so a frame gets at most one wait and the
multi-step wizard — the flagship reason to want it — cannot exist on the platform in any
design; the rendezvous is process-local, so it cannot work on multi-process REST, the one
deployment where its lock hazard mattered; and every resume mechanism (refresh-in-place,
rebind) converts idiomatic captures into silent wrong-row writes. Wizards are chains of
`prompt` callbacks with `carry=`/durable state threading the steps — which is the only
shape Discord permits anyway.

Check-then-act across a form (claim-then-confirm) is **not atomic in any design** — the
gap is human think-time, which the locking law forbids holding anything across. 1.0
teaches the honest pattern (re-check in the submit handler; the version token catches the
rest); the structural fix, `ctx.reserve(name, ttl=)` — an expiring, atomically-acquired
flow hold outside the blob — is designed and deferred (§15).

---

## 10. Sharding and deployment

Gateway delivery: `shard_id = (guild_id >> 22) % shard_count`; **DMs always go to shard 0**.

So for a gateway bot, all clicks on a given message land on the same process — concurrency on
one view is intra-process. REST bots are the opposite: two clicks can hit two instances.
This is the fact both placements' guarantees stand on; the docs present it as one 2×2:

| | Gateway (sticky shards) | Multi-process REST |
|---|---|---|
| `InMessage` | fully correct (in-process lock + version cache); state travels with the message, so cross-shard sends, DMs, resharding and rolling deploys are all non-events | last-write-wins, documented |
| `InStore` + distributed store | fully correct | fully correct (lock + CAS) |
| `InStore` + `MemoryStore` | works single-process only; **loud startup warning** | broken (N−1)/N of the time — worse than `InMessage`; warned loudly |

The old "`MemoryStore` breaks in four specific ways" analysis (cross-shard sends, DMs,
resharding, rolling deploys) now applies *only* to `InStore`+`MemoryStore` — message
residency dissolved all four for the default placement, which is precisely why it is the
default.

### Redis notes **[decided]**

- **`maxmemory-policy`** — `allkeys-lru` evicts unexpired state and kills components silently.
  Want `volatile-lru` or `noeviction`. Check at startup and warn.
- **Persistence** — RDB-only loses recent state on crash. AOF with `appendfsync everysec` is
  what makes the durability claim real. Document next to the claim, not in an appendix.
- **Hash tags from day one** — `{key}:s` / `{key}:l`, so cluster mode can co-locate lock and
  state. Retrofitting invalidates every live key.
- **Read from the primary.** Replica reads break read-your-writes on re-render.
- **Lock is unsafe under failover.** Accepted; the version check (§7.3) is the backstop.
- **Namespace with the application ID** — `risa:{app_id}:`.

### Lock tiering **[resolved by §7.3]**

The old open question ("skip the distributed lock on gateway bots?") dissolved into the
hybrid contract: the in-process lock is always taken (free, unleakable), the CAS is always
the cross-process arbiter, and a *distributed* lock is never on the default path at all —
it returns only with the deferred `isolation=risa.Serialized()` declaration for handlers
whose side effects must never run twice.

---

## 11. Transports

| | GatewayBot | RESTBot |
|---|---|---|
| trait | `GatewayBotAware` | `RESTBotAware` |
| entry | `subscribe(InteractionCreateEvent, cb)` | `set_listener(ComponentInteraction, cb)` |
| polymorphic | yes | **no** — one listener per interaction class |
| initial response | `create_initial_response(...)` (HTTP) | *return* a builder (no HTTP) |

**The event/204 pattern, not the builder future. [decided — revised]** An earlier draft
followed miru: an `asyncio.Future[ResponseBuilder]` on the context, resolved by the first
response call and returned as the webhook HTTP body. Rejected after comparison with
lightbulb v3, on two facts: the interaction callback endpoint accepts responses via plain
REST even for HTTP-received interactions, and hikari's `InteractionServer` supports
async-*generator* listeners — yield `None` and it answers Discord's webhook POST with
`204 No Content` ("handled out-of-band"), then keeps driving the generator in a background
task it awaits on shutdown.

So risa responds to **everything on both transports through the same REST calls**, and the
entire transport difference is one listener: spawn dispatch as a task, wait for the
context's acknowledged event (set by any first response, or at dispatch end), yield `None`,
then await the task. The future pattern would have leaked the transport into every response
method and duplicated every payload as both kwargs and builders; the cost of the event
pattern is one extra HTTP round trip on the *initial* response, REST bots only.

Two deliberate deviations from lightbulb: the acknowledged event is also set when dispatch
finishes (so a foreign or unroutable component answers 204 promptly instead of burning the
whole wait window), and **a handler that misses the window is never cancelled** — the
interaction is dead either way, but the handler may be mid-write in the user's database,
and killing user work to save a doomed ack is the wrong trade. Log at ERROR and let it
finish.

hikari has no combined Gateway+REST ABC, so a `runtime_checkable` Protocol intersecting
`RESTAware` + `EventManagerAware` is needed (miru calls it `GatewayBotLike`).

---

## 12. Target DX

The thing to keep working toward. If a change makes this worse, it is the wrong change.

```python
import hikari
import msgspec
import risa
from risa import ui

bot = hikari.GatewayBot("...")
client = risa.client_from_app(bot)              # zero infra needed for InMessage views
# client = risa.client_from_app(bot, stores={"redis": MyRedisStore(...)})  # for InStore


@risa.register(name="poll", version=1)          # state=risa.InMessage() — the default
class Poll(risa.View):
    question: str
    votes: dict[str, int] = msgspec.field(default_factory=dict)

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"## {self.question}"),
            ui.Separator(),
            *[
                ui.Section(
                    ui.TextDisplay(f"**{name}** — {count}"),
                    accessory=ui.Button(self.vote.bind(name), label="Vote"),
                )
                for name, count in self.votes.items()
            ],
            ui.Separator(divider=True),
            ui.Row(ui.Button(self.close, label="Close", style=hikari.ButtonStyle.SECONDARY)),
        )

    @risa.handler
    async def vote(self, ctx: risa.ComponentContext, option: str) -> None:
        self.votes[option] += 1
        await ctx.rerender()                    # the message edit IS the state write

    @risa.handler
    async def close(self, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay(f"## {self.question}\n*Poll closed.*"))


await channel.send(components=await client.build(Poll(question="Ship it?")))
```

Everything durable with zero infrastructure, no timeout bookkeeping, no manual custom_id
management, and the same code works on a RESTBot. An `InStore` view differs by exactly one
line of registration; a leaderboard differs by `Prop` markers and a `load()` hook — the
handlers, render, and responses are identical in all three.

---

## 13. Errors

`risa/errors.py` already defines the hierarchy. Map failures onto it rather than inventing
new types ad hoc:

| Raised when | Type |
|---|---|
| tree breaks an invariant risa owns | `LayoutError` (path-qualified) |
| encoded custom_id > 100 chars | `CustomIdOverflowError` |
| durable state exceeds the tree's chunk capacity | `StateOverflowError` (at build/rerender; per-field size breakdown, suggests `Prop`/`InStore`) |
| stored state predates a schema bump | `SchemaMismatchError` |
| store entry gone (expired/evicted) | `StateNotFoundError` → routed to `on_state_missing` |
| store unreachable (never conflated with "gone") | `StoreUnavailableError` |
| CAS lost | converged in dispatch (§7.3) — never raised to the user |
| lock not acquired within `timeout` | `LockTimeoutError` |
| wire args predate a signature edit | `SignatureMismatchError` (caught in dispatch: logged, routed to `on_outdated`) |
| anchor's placement tag ≠ registered placement | routed to `on_outdated`, precise ERROR log |
| handler signature unusable for wire args | `HandlerSignatureError` (at import) |
| view declaration contradicts itself (marker misuse, missing defaults, unknown store name) | `ViewDeclarationError` (at import / `add_view`) |
| `bind()` args don't fit the wire parameters | `ArgBindError` (at render) |
| defer/modal after a response already went out | `AlreadyRespondedError` |
| modal submitted against changed state | routed to `on_prompt_conflict` — never raised |

**[decided]** There is no `NoResponseIssuedError`. Under the event/204 pattern (§11) a
handler that never responds has no caller to throw to — the REST listener logs at ERROR and
answers 204, and Discord shows the user the timeout it would have shown anyway.

---

## 14. Open decisions

Ordered by cost of changing later.

1. **One view per message, or composable sub-views?** Sub-views suit V2 nesting
   (`ui.Container(*ProfileCard(user).render())`) but multiply the anchor story. Punt, but
   do not design them out.
2. **Does `on_state_missing` disable the components in place?** Polite, but costs an extra
   edit.
3. **Is `render()` allowed to be async?** Tempting (fetch data to display), but `load()`
   (§7.2) now covers the legitimate need without I/O inside every redraw. Leaning **no**.

### Settled since

- **State placement is per view: `state=risa.InMessage()` (default) `| risa.InStore(store=,
  ttl=)`** — flare's model scaled by V2 as the zero-infra default, miru's ergonomics made
  durable behind a pluggable store. See §2.3/§7.1.
- **Fields are durable by default; `risa.Prop[T]` marks per-dispatch props, refilled in
  `load()`.** Polarity chosen for failure loudness. The old `persist=False` flag and
  `register(ttl=)` are subsumed and deleted. See §7.2. *(`load()` accepted tentatively —
  revisit its final shape before 1.0.)*
- **The locking law and the hybrid concurrency contract** — locks never span human
  think-time; CAS is correctness, the lock is throughput; CAS losses converge, never
  raise. See §7.3.
- **Every `rerender()` is a full commit; mutate-without-rerender auto-commits; dirty is
  computed.** See §7.4.
- **Modals are callback-only for 1.0** (`ctx.prompt` + `carry=` + version token +
  `on_prompt_conflict`); `prompt_wait` rejected for stateful views after investigation
  (Discord forbids modal-after-modal; the rendezvous is process-local). See §9.
- **No codec version ceremony pre-release**: the chunked wire format simply *is* v1.
- **Handler identity is `(handler_id, version)`; arg-carrying components carry a signature
  fingerprint.** See §6.4 for the full scheme, the retirement modes and the rejected
  alternatives.
- **Wire args are the contiguous prefix of converter-typed parameters after `ctx`;
  everything after belongs to DI.** See §6.3.
- **The Context surface is `respond` / `rerender` / `edit(layout)` / `defer`,** each
  mirroring the thing that defines the message it touches; `respond` returns a `Response`
  handle. See §8.2.
- **Auto-defer is a watchdog with its timer at decode,** configured by `risa.AutoDefer`
  per view and per handler. See §8.1.
- **Both transports respond through the same REST calls** (the event/204 pattern); the
  builder-future approach was considered and rejected. See §11.
- **`msgspec.Struct` for view state.** `View` subclasses `msgspec.Struct`, so a view's
  annotated attributes are its persisted state. Note this rules out lightbulb-style class
  kwargs (`class Poll(View, name="poll")`) — `StructMeta` accepts none — which is why the
  name and version go on the decorator.
- **`name=` is required** on `@risa.register`. Deriving it from `module.qualname` breaks
  silently on refactor, which is flare's worst property.
- **PEP 695 throughout.** `type X = ...` aliases and `def f[T: Bound](...)` generics, no
  `typing.TypeVar`. Note a type-parameter bound is evaluated lazily *at runtime*, so anything
  named in one must be a real import, never `TYPE_CHECKING`-only.

---

## 15. Build order and roadmap

### The state pivot (current work)

Each step should land green. **1-4 have landed;** 5 is next.

1. **Codec** *(landed)* — chunk framing (`idx`/`frag_len`, carve/gather with gap detection), the two
   anchor dialects, the placement tag, Prop-aware positional state encoding. Still v1.
2. **`StateSchema` + `Prop` + `load()`** *(landed)* — field partition, registration validations,
   `register(state=)`, `StateOverflowError` ergonomics.
3. **Backends and sessions** *(landed)* — `MessageSession` (per-message lock, seq + version cache),
   `StoredSession` (lease-capable `lock(timeout=)`, CAS-then-edit commits, converge-on-
   conflict, `on_state_missing`), Store protocol hardening (`StoreUnavailableError`,
   `{v,n,f,d}` envelope, key namespace, `InStore`+`MemoryStore` warning).
4. **Build + dispatch + context rewire** *(landed)* — two-phase build, message-component
   reader, one session-driven dispatch path, commit-per-rerender, the fatal fixes from the
   invariants audit (CAS loss answers the interaction; `edit(layout)` re-embeds or raises;
   watchdog vs conditionally-modal handlers), deletion of the store-era dispatch. Also
   landed here: a third `StatelessBackend` so nothing above branches on whether a view has
   state; named stores on the client (`stores={...}`) validated at `add_view`; and
   `anchor.distribute`, which parks the components an anchor does not reach on one shared
   index rather than counting past it.
5. **`modal.py`** — §9 as decided.

### 1.0 roadmap, after the pivot

- **`RedisStore`** (`[redis]` extra) + **`risa.state.testing.verify_store`** conformance
  kit — the protocol is sharp to implement; shipping it without a harness is negligent.
- Docs: the placement 2×2 (§10), the capacity/introspection story, the world-readable
  warning, a first stateful tutorial example whose state *grows*.

### Deferred, deliberately (revisit post-1.0 — the maintainer wants these re-discussed)

- **`prompt_wait`** — only ever for all-Prop views, where its semantics are vacuous;
  registration-enforced.
- **`ctx.reserve(name, ttl=)` / `ctx.release(name)`** — expiring flow holds; the
  structural fix for claim-then-confirm.
- **`isolation=risa.Serialized() | risa.Concurrent()`** per handler.
- **`InStore(adopt=True)`** — lazy Message→Store placement migration keyed on the tag.
- **`@risa.migration`** — schema migration hooks riding the store envelope + cookie
  aliasing.
- **`load()` final shape** — accepted tentatively; re-examine ergonomics (failure routing,
  per-handler opt-out) before 1.0.
- Sub-views (§14.1); `with`-block syntax stays rejected (§5.3).
