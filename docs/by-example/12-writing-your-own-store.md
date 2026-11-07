# Writing your own store

A store is six methods. Write them against what you already run, and `verify_store` tells you whether risa can rely on
them.
{: .risa-lead }

## The six methods

[`risa.Store`](../api/store/protocol.md#risa.store.protocol.Store) is a protocol, and the reference has each method in
full.

| Method | What it has to do |
|---|---|
| `get_versioned(key)` | Return the bytes last written, unchanged, with the entry's version, as a `risa.VersionedEntry`, or `None` if the entry is missing or expired |
| `put(key, value, *, ttl)` | Write whether or not an entry exists, and change the version |
| `put_if_version(key, value, *, expected, ttl)` | Write only if the entry is at version `expected`, in one atomic step, and return the new version. Otherwise write nothing and return `None` |
| `delete(key)` | Remove the entry, if there is one |
| `touch(key, *, ttl)` | Restart the entry's lifetime, leave its data and version alone, and leave a missing key missing |
| `lock(key, *, timeout=...)` | Return an async context manager that holds the key exclusively and leaves other keys free. It raises `risa.LockTimeoutError` after waiting `timeout` seconds |

`ttl` is in seconds, fractions included. `None` means no expiry and clears any earlier one.

A missing entry is never an error. Let every other failure raise: risa reports it as
[`StoreUnavailableError`](../api/errors.md#risa.errors.StoreUnavailableError), so an outage cannot pass for an expired
view.

## A minimal store

`DictStore` keeps its entries in a dict. `put_if_version` decides whose write lands when two clicks race:

```python
--8<-- "ch12_dict_store.py:compare-and-set"
```

No `await` sits between the compare and the write here, so nothing can run between them. A real backend needs one step of
its own for this, as `RedisStore` has a Lua script and `PostgresStore` one `UPDATE`.

The lock is one `asyncio.Lock` per key:

```python
--8<-- "ch12_dict_store.py:lock"
```

risa calls `lock` without `await`, as `asynccontextmanager` allows. A client takes the store like any other:

```python
--8<-- "ch12_dict_store.py:client"
```

??? example "Full file"

    ```python
    --8<-- "ch12_dict_store.py:20:"
    ```

## Test it

Add [`verify_store`](../api/store/testing.md#risa.store.testing.verify_store) to your tests, with a store connected to a
real instance of your backend.

```python
--8<-- "ch12_dict_store.py:test"
```

This one needs pytest-asyncio with `asyncio_mode = "auto"`. In a script, `asyncio.run(verify_store(DictStore()))` does the
same. A failed check
makes it raise [`StoreConformanceError`](../api/errors.md#risa.errors.StoreConformanceError), which lists every failure.

!!! tip "Try it."
    Take `or entry.version != expected` out of `put_if_version`. `verify_store` now names `a_stale_cas_is_refused` and
    `racing_cas_writes_have_one_winner`.

## Stores that processes share

`verify_store` runs in one process, so a lock that excludes only its own process passes. For
[several processes](11-several-processes.md), `lock` has to exclude all of them. Without that, two clicks run at once, and
one of them gets [`StateConflictError`](11-several-processes.md#when-a-handler-loses-the-race).

`DictStore` is a teaching example, for one process only. It gets no startup warning, and it keeps every lock and every
expired entry nobody reads, so a bot that runs on it grows until it restarts.
