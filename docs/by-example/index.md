---
hide:
  - toc
---

# By example

Twelve chapters that start with a single message and end with your own store. Read them in order, or jump to the one you need.
{: .risa-lead }

<div class="risa-overview risa-overview--numbered" markdown>

## Build a view

- [Views](01-views.md)

    Describe a whole message in `render()` and send it.

- [Buttons that remember](02-buttons-that-remember.md)

    Keep a view's fields from click to click and across restarts.

- [Handlers and arguments](03-handlers-and-arguments.md)

    Let one handler serve many buttons, each carrying its own value.

- [Selects, sections, galleries](04-selects-sections-galleries.md)

    Draw more than text and buttons: menus, images and files.

- [Answering in time](05-answering-in-time.md)

    Answer within Discord's three seconds, even when the work takes longer, and answer a click that failed.

- [Dependency injection](06-dependency-injection.md)

    Hand your database to a handler, and choose how long each dependency lives.

## Keep its state

- [Fresh data](07-fresh-data.md)

    Read a database row on every click instead of saving it.

- [Where state lives](08-where-state-lives.md)

    See what you can do when a view runs out of room in its message.

- [Stores and TTL](09-stores-and-ttl.md)

    Move a view into a store, with room to grow and a lifetime.

## Fit it into your bot

- [Changing a view safely](10-changing-a-view-safely.md)

    See which changes break the buttons already sent, and every way to deal with one that does.

- [Several processes](11-several-processes.md)

    Run more than one process, and handle two clicks that race.

- [Writing your own store](12-writing-your-own-store.md)

    Write six methods against what you already run, and test them with `verify_store`.

</div>
