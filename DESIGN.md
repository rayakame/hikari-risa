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

### 2.3 State lives in a store, keyed by a random token in the custom_id **[decided]**

`custom_id` carries *identity* (which view, which handler) plus a *key*. The state itself
lives in a pluggable store.

Random keys — not message IDs, not anything shard-derived — are what make resharding a
non-event. A message ID cannot be used anyway: components go into the send payload, so the
ID does not exist yet when the `custom_id` is built.

---

## 3. Constraints that shape the design

Verified against hikari master; re-check if these change.

- **`ComponentInteraction` does not expose the V2 `id` field.** hikari never deserialises it
  (`impl/entity_factory.py:3181`). `custom_id` is the only routing channel. Structural /
  id-based addressing is off the table.
- **`custom_id` is capped at 100 characters** by Discord.
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

Four passes out, the mirror image in.

```text
OUTBOUND
  render()   → node tree          pure, user code, no I/O
  flatten    → routing table      dict[handler_key, handler] + hikari builders
  commit     → store write        allocate key, persist state

INBOUND
  decode     → (cookie, handler, payload)
  load       → state              ← I/O HAPPENS HERE, before user code
  lock       → held for the handler's duration
  dispatch   → user callback
  save       → store write, if the handler mutated state
```

The position of the I/O in the inbound path is load-bearing: it is why auto-defer must start
at *decode*, not at handler entry (§8).

### Module layout

```text
risa/
  __init__.py       public re-exports                          [exists]
  _about.py         metadata                                   [exists]
  client.py         Client ABC, transports, dispatch           [exists]
  context.py        Context, responses, auto-defer             [partial: respond only]
  di.py             Contexts, INJECTED re-export               [exists]
  errors.py         exception hierarchy                        [exists]
  view.py           View base, @register, @handler             [exists]
  internal/
    codec.py        custom_id encode/decode, cookies, tokens   [exists]
    constants.py    Discord limits, stamped attribute names    [exists]
    registry.py     ViewMeta, cookie -> view registries        [exists]
  ui/               node types + flatten                       [partial]
    nodes.py        Container, Section, TextDisplay, Row, Button, selects, ...  [partial]
    build.py        flatten + emit hikari builders
  state/
    store.py        Store protocol, MemoryStore
    redis.py        RedisStore                                 [extra: redis]
    codec.py        msgspec encode/decode + migrations
  modal.py
```

---

## 5. The node layer

### 5.1 Node types **[decided in shape, names open]**

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

### 5.3 Syntax **[decided: both]**

Nested literals are the core API; `with`-blocks are an optional layer that builds the *same*
node objects, so neither is privileged.

```python
# literals — explicit, fully type-checkable
def render(self) -> ui.Layout:
    return ui.Container(
        ui.TextDisplay(f"## {self.question}"),
        ui.Separator(),
        ui.Section(ui.TextDisplay("**Yes**"), accessory=ui.Button(self.vote_yes, label="Yes")),
    )

# with-blocks — indentation is the tree; better for loops and conditionals
def render(self) -> ui.Layout:
    with ui.Container(accent_color=0x5865F2) as root:
        ui.TextDisplay(f"## {self.question}")
        for option in self.options:
            with ui.Section(accessory=ui.Button(self.vote.bind(option.id), label="Vote")):
                ui.TextDisplay(f"**{option.name}** — {option.count}")
    return root
```

The `with` form needs a contextvar-based implicit parent stack. Build literals first; the
block form is sugar on top and can come later.

---

## 6. The custom_id codec

### 6.1 Wire format **[decided]**

```text
┌─────┬──────────┬───────────┬─────────────────────────────────────┐
│ ver │  cookie  │  handler  │ payload                             │
│  1  │    6     │     2     │ ≤ 91                                │
└─────┴──────────┴───────────┴─────────────────────────────────────┘

ver      codec version. Bumping it fails every old component closed.
cookie   b64(blake2s(view_name + ":" + schema_version, digest_size=4))[:6]
handler  b64(blake2s(handler_id + ":" + handler_version, digest_size=2))[:2]
payload  stateful view: 16-char state key ‖ sig ‖ args
         stateless view: sig ‖ args
sig      2-char signature fingerprint, present exactly when args are (§6.4)
```

Four properties that matter:

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
- **There is no mode byte.** An earlier draft reserved one to distinguish a store key from
  inline state, but whether a view is stateful is a static property of the view — derivable
  from the cookie once it resolves through the registry — so the byte would have been
  redundant with a lookup that has to happen anyway.

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

## 7. State and the Store

### 7.1 Protocol **[decided]**

Get this right first — it is the hardest thing to change later.

```python
class Store(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def get_versioned(self, key: str) -> tuple[bytes, int] | None: ...
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None: ...
    async def put_if_version(self, key: str, value: bytes, *,
                             expected: int, ttl: float | None) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def touch(self, key: str, *, ttl: float) -> None: ...
    def lock(self, key: str) -> AsyncContextManager[None]: ...
```

Deliberately **not** in there:

- No combined `get_and_lock` — keep them separate so a dumb KV backend can supply a no-op
  lock and still work single-process.
- No typed state. The store moves `bytes`; serialisation is a separate concern, otherwise
  every backend has to know about the codec.
- `touch` separate from `get`, so sliding-TTL is a client-level policy rather than baked into
  each backend.

Include `get_versioned` / `put_if_version` from the **first** commit even if unused at first.
Adding CAS later means changing every backend.

### 7.2 Backends

- **`MemoryStore`** — the default. `OrderedDict` + LRU cap, `asyncio.Lock` per key. Zero
  infra; equivalent to miru's behaviour. **Only safe single-process** (§10).
- **`RedisStore`** — `[redis]` extra. Notes in §10.

### 7.3 Concurrency: lock, and also version **[decided]**

The failure nobody else handles. Two users click a poll at once:

```
A: load {yes: 5}
B: load {yes: 5}
A: save {yes: 6}
B: save {yes: 6}      ← A's vote is gone
```

Policy: **pessimistic lock for liveness, version check for correctness.**

```python
async with store.lock(key):
    raw, version = await store.get_versioned(key)
    view = codec.loads(view_cls, raw)
    await handler(view, ctx, *args)
    if ctx.state_dirty:
        if not await store.put_if_version(key, codec.dumps(view), expected=version, ttl=ttl):
            raise StateConflictError(key)
```

The lock makes conflicts rare; the version check makes the rare case loud instead of a silent
lost update. **Do not retry on conflict** — a retried handler that already sent a followup
would double-send.

Opt out per handler for read-only callbacks:

```python
@risa.handler(lock=False)
async def show_details(self, ctx: risa.Context) -> None: ...
```

### 7.4 Key allocation **[decided]**

```python
key = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")   # 16 chars
```

Allocated at build time, **reused across re-renders** (update in place). That gives a genuinely
nice property inline-mode cannot have: *custom_ids are stable across re-renders*, so a
user's in-flight click is never invalidated by a concurrent edit.

A failed send leaks one store entry. TTL cleans it up; do not build two-phase commit for this.

### 7.5 Schema evolution **[decided]**

- Store a **dict**, not a pickle — pickle couples the store to import paths and is a
  deserialisation hazard.
- Records carry their own version: `{"v": 1, "n": "poll", "d": {...}}`.
- Additive changes are free (new field with a default).
- Breaking changes bump `version`, which changes the cookie, so old components route to
  nothing and hit `on_outdated`. That is *correct* — fail closed.
- Offer a migration hook so "restart doesn't break components" survives schema changes:

```python
@risa.register(name="poll", version=2)
class Poll(risa.View):
    @risa.migration(from_version=1)
    @staticmethod
    def _from_v1(old: dict) -> dict: ...
```

This requires registering the view under both cookies, with the old one routing through the
migration.

### 7.6 Props-only views: `persist=False` **[decided]**

Not every view has state of its own. A leaderboard rendered from the bot's database is a
pure function of that database plus which button was clicked; storing a snapshot in the
risa store duplicates data whose source of truth is elsewhere. Registering with
``persist=False`` turns the store off for one view:

```python
@risa.register(name="leaderboard", version=1, persist=False)
class Leaderboard(risa.View):
    ranks: list[Rank] = msgspec.field(default_factory=list)   # props, not state
```

- Fields become render inputs -- *props* -- supplied by whoever constructs the view.
  ``build()`` renders from them and writes nothing; components carry no state key, so the
  whole payload budget belongs to args.
- Dispatch constructs the view empty, so **every field must have a default** (checked at
  registration) and **handlers must treat fields as garbage-in**: refill anything render
  needs -- from the database, from wire args -- before redrawing.
- ``ttl=`` is rejected beside ``persist=False``; there is nothing it could apply to. A
  silently ignored knob would be worse than a loud one.
- No lock, no CAS, no ``on_state_missing`` -- there is no state to contend over or lose.
  Concurrent clicks both refetch and both edit; last write wins, which is correct for a
  display of external data. Clicks that mutate *domain* data write to the caller's own
  database inside the handler, whose transactionality is the concurrency story.
- Flipping ``persist`` on a live view changes what the payload means, so it is a
  view-version bump like any other shape change.

A view with no fields at all is implicitly persist-less, exactly as before; the flag
extends that mode to views that need render inputs. Choose by where the data lives: a
message that is a pure function of external data plus args wants ``persist=False``; data
that lives nowhere else (poll votes, wizard progress) wants the store.

---

## 8. Context, responses, auto-defer

### 8.1 Auto-defer sits above the handler **[decided]**

Store I/O happens during dispatch, before user code runs, so the timer starts at **decode**.

```python
defer_task = asyncio.create_task(self._autodefer(interaction, state))
try:
    await self._dispatch(decoded, interaction, state)
finally:
    defer_task.cancel()
```

- Sleep ~2s, leaving ~1s of slack against Discord's 3s deadline.
- Default to **`DEFERRED_MESSAGE_UPDATE` (6)**, the silent ack — not
  `DEFERRED_MESSAGE_CREATE` (5), which shows a "thinking…" spinner. A component is usually
  about to edit its own message.
- Funnel **every** respond path through one `_create_response` that cancels the pending
  defer. miru does this and it is the reason their version cannot get out of sync.

### 8.2 Context surface **[open — exact names]**

```python
await ctx.respond(...)                 # MESSAGE_CREATE
await ctx.edit(...)                    # MESSAGE_UPDATE
await ctx.rerender()                   # re-run render(), save state, edit message
await ctx.defer(ephemeral=False)
await ctx.prompt(SomeModal, on_submit=self.handler)
ctx.state_dirty                        # set by rerender/mutation, drives the save
```

`rerender()` is the headline ergonomic: mutate `self`, call it, done. No decode-mutate-encode
dance like flare's tictactoe.

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

## 9. Modals

Modal `custom_id` carries the same `(cookie, handler, key)` triple, so **modals survive
restarts too** — which none of the three existing libraries manage.

```python
async def edit_subject(self, ctx: risa.Context) -> None:
    await ctx.prompt(SubjectModal, on_submit=self.subject_set)

async def subject_set(self, ctx: risa.Context, values: SubjectModal) -> None:
    self.subject = values.subject
    await ctx.rerender()
```

**[open]** lightbulb's `result = await modal.attach(client, cid)` — returning a typed value
from an await — is the nicest modal ergonomic in any of these libraries, but it is an
in-memory await and dies on restart. Either support both (documenting the await form as
non-durable) or commit to the callback form.

---

## 10. Sharding and deployment

Gateway delivery: `shard_id = (guild_id >> 22) % shard_count`; **DMs always go to shard 0**.

So for a gateway bot, all clicks on a given message land on the same process — concurrency on
one view is intra-process. REST bots are the opposite: two clicks can hit two instances.

| Deployment | State visible where needed | Concurrent clicks |
|---|---|---|
| 1 process, N shards | yes | same process |
| N processes, shard subsets | only if sender owns that shard | same process |
| RESTBot behind a LB | **no** | **different processes** |

### `MemoryStore` breaks in four specific ways **[decided: warn loudly]**

1. **Cross-shard sends** — a cron job or dashboard in process A sends into a guild owned by
   process B. The click arrives at B, which has no key. This is the common one.
2. **DMs** — any process can DM; the interaction returns on shard 0.
3. **Resharding** — the guild→shard mapping changes wholesale.
4. **Rolling deploys** — half the state dies, which is worse to debug than all of it.

Emit a startup warning on `shard_count > 1` or `RESTBotAware`.

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

### Lock tiering **[open]**

Since gateway bots have the sticky-shard property, the distributed lock can be skipped:
`lock_mode="auto"` → local `asyncio.Lock` for `GatewayBotAware`, distributed for
`RESTBotAware`. Correct by default for both, saves two RTTs against a 3s budget. Keep the
version check on in all modes — it is what catches you when the "local" assumption is wrong
(e.g. a web dashboard mutating the same views).

---

## 11. Transports

| | GatewayBot | RESTBot |
|---|---|---|
| trait | `GatewayBotAware` | `RESTBotAware` |
| entry | `subscribe(InteractionCreateEvent, cb)` | `set_listener(ComponentInteraction, cb)` |
| polymorphic | yes | **no** — one listener per interaction class |
| initial response | `create_initial_response(...)` (HTTP) | *return* a builder (no HTTP) |

miru's approach is the right one: an `asyncio.Future` on the context. Under REST the handler
resolves the future with a builder and the client `await asyncio.wait_for(fut, timeout=3.0)`
hands it back to hikari's interaction server. Everything else is shared.

hikari has no combined Gateway+REST ABC, so a `runtime_checkable` Protocol intersecting
`RESTAware` + `EventManagerAware` is needed (miru calls it `GatewayBotLike`).

---

## 12. Target DX

The thing to keep working toward. If a change makes this worse, it is the wrong change.

```python
import hikari
import risa
from risa import ui

bot = hikari.GatewayBot("...")
client = risa.Client(bot)                       # MemoryStore by default
# client = risa.Client(bot, store=risa.RedisStore(redis))   # durable, multi-process


@risa.register(name="poll", version=1)
class Poll(risa.View):
    question: str
    votes: dict[str, int] = field(default_factory=dict)

    def render(self) -> ui.Layout:
        total = sum(self.votes.values())
        return ui.Container(
            ui.TextDisplay(f"## {self.question}"),
            ui.Separator(),
            *[
                ui.Section(
                    ui.TextDisplay(f"**{name}** — {count} ({self._pct(count, total)})"),
                    accessory=ui.Button(self.vote.bind(name), label="Vote"),
                )
                for name, count in self.votes.items()
            ],
            ui.Separator(divider=True),
            ui.Row(ui.Button(self.close, label="Close", style=hikari.ButtonStyle.SECONDARY)),
        )

    async def vote(self, ctx: risa.Context, option: str) -> None:
        self.votes[option] += 1
        await ctx.rerender()

    async def close(self, ctx: risa.Context) -> None:
        await ctx.respond("Poll closed.", edit=True, components=None)

    @classmethod
    async def on_state_missing(cls, ctx: risa.Context) -> None:
        await ctx.respond("This poll has expired.", ephemeral=True)


rendered = await client.build(Poll(question="Ship it?", votes={"yes": 0, "no": 0}))
await channel.send(**rendered)
```

Everything durable, no timeout bookkeeping, no manual custom_id management, and the same code
works on a RESTBot.

---

## 13. Errors

`risa/errors.py` already defines the hierarchy. Map failures onto it rather than inventing
new types ad hoc:

| Raised when | Type |
|---|---|
| tree breaks an invariant risa owns | `LayoutError` (path-qualified) |
| encoded custom_id > 100 chars | `CustomIdOverflowError` |
| stored state predates a schema bump | `SchemaMismatchError` |
| store key gone (expired/evicted) | `StateNotFoundError` |
| CAS lost | `StateConflictError` |
| lock not acquired in time | `LockTimeoutError` |
| wire args predate a signature edit | `SignatureMismatchError` (caught in dispatch: logged, routed to `on_outdated`) |
| handler signature unusable for wire args | `HandlerSignatureError` (at import) |
| `bind()` args don't fit the wire parameters | `ArgBindError` (at render) |

**[open]** No `ResponseError` / `NoResponseIssuedError` yet. Needed if RESTBot support lands
in v0 — that is the "handler never resolved the future within 3s" case.

---

## 14. Open decisions

Ordered by cost of changing later.

1. **One view per message, or composable sub-views?** Sub-views suit V2 nesting
   (`ui.Container(*ProfileCard(user).render())`) but multiply the state-key story. Punt, but
   do not design them out.
2. **Default TTL, sliding vs absolute.** Sliding (touch on access) means an active message
   never expires, which is what users expect. 7 days is a reasonable floor.
3. **Does `on_state_missing` disable the components in place?** Polite, but costs an extra
   edit.
4. **Is `render()` allowed to be async?** Tempting (fetch data to display), disastrous for the
   same reason async converters are — I/O inside the response window on every re-render.
   Leaning **no**; fetch in the handler.

### Settled since

- **Handler identity is `(handler_id, version)`; arg-carrying components carry a signature
  fingerprint.** See §6.4 for the full scheme, the retirement modes and the rejected
  alternatives.
- **Wire args are the contiguous prefix of converter-typed parameters after `ctx`;
  everything after belongs to DI.** See §6.3.
- **`persist=False` on `@risa.register` declares a props-only view.** Fields become render
  inputs, nothing is stored, dispatch constructs the view from its defaults. See §7.6.
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

## 15. Build order

Each step should land green and be independently testable.

1. **`_codec.py`** — encode/decode, arg converters, overflow, fail-soft on foreign ids.
   No dependencies on anything else; exhaustively testable in isolation. **Start here.**
2. **`state/store.py`** — protocol + `MemoryStore` + lock + CAS. Also pure, also easily
   tested.
3. **`ui/nodes.py` + `build.py`** — tree, flatten to hikari
   builders. Test by asserting on emitted payloads; no network needed.
4. **`view.py`** — `@risa.register`, handler registry, `bind()`.
5. **`client.py` + `context.py`** — dispatch, auto-defer, gateway transport. First point
   requiring a live bot to exercise properly.
6. **`modal.py`**
7. **RESTBot transport**
8. **`state/redis.py`**
9. **`ui` with-block sugar**

Steps 1–3 are the bulk of the library's value and need no Discord connection at all.
