# Installation

Add hikari-risa to your bot's project. It runs on Python 3.12 to 3.15, and you import it as `risa`.
{: .risa-lead }

## Install

=== "uv"

    ```sh
    uv add hikari-risa
    ```

=== "pip"

    ```sh
    pip install hikari-risa
    ```

This also installs what risa depends on: hikari, linkd and msgspec. `uv add` needs a project to add to, so run
`uv init` first if you have no `pyproject.toml` yet.

Print the version to check that it worked:

=== "uv"

    ```sh
    uv run python -c "import risa; print(risa.__version__)"
    ```

=== "pip"

    ```sh
    python -c "import risa; print(risa.__version__)"
    ```

## Extras

A view keeps its state in its message by default, and that needs nothing extra. Three extras add more:

| Extra | Installs | You want it when |
|---|---|---|
| `redis` | `redis` | Your views keep their state in Redis, with [`RedisStore`](../by-example/08-stores-and-ttl.md#redis-and-postgresql). |
| `postgres` | `asyncpg` | Your views keep their state in PostgreSQL, with [`PostgresStore`](../by-example/08-stores-and-ttl.md#redis-and-postgresql). |
| `radixly` | `radixly` | A view runs out of room in its message. The [codecs](../by-example/07-where-state-lives.md#what-you-can-do) `BASE32768` and `URO14` fit more than twice as much. |

Name one when you install:

=== "uv"

    ```sh
    uv add "hikari-risa[redis]"
    ```

=== "pip"

    ```sh
    pip install "hikari-risa[redis]"
    ```

## Packages your bot may need

Two more packages depend on how your bot runs, and hikari-risa installs neither:

- A bot with slash commands through lightbulb needs `hikari-lightbulb`.
- A REST bot needs `hikari[server]`, because hikari's interaction server checks Discord's signatures with it.

[Your first view](first-view.md) is the next step.
