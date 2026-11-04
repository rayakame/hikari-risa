---
hide:
  - navigation
  - toc
---

<div class="risa-hero" markdown>
<div class="risa-hero__copy" markdown>

![](assets/images/logo-light.svg){ .risa-brand__night width="1024" height="1024" }
![](assets/images/logo.svg){ .risa-brand__banner width="1024" height="1024" }
{: .risa-hero__logo }

# hikari-risa

Components V2 views for hikari, with state that survives restarts.
{: .risa-hero__tagline }

[Get started](get-started/installation.md){ .risa-hero__start } [Learn by example](get-started/first-view.md){ .risa-hero__example }
{: .risa-hero__actions }

`uv add hikari-risa`
{: .risa-hero__install }

</div>
<div class="risa-hero__art" markdown>

![Risa, the mascot of hikari-risa](assets/images/mascot-sticker.webp){ width="1216" height="1600" }

</div>
</div>

<div class="risa-why" markdown>

**Survives restarts.** The state travels inside the message. Add Redis or Postgres when it outgrows the message or many people click at once.

**Components V2 first.** Build with containers, sections and galleries. A row of buttons is the simple case.

**Typed all the way.** risa passes pyright strict, so your editor knows every field.

</div>

Coming from miru? [See what changes.](get-started/coming-from-miru.md){ .risa-miru__link }
{: .risa-miru }

--8<-- "previews/counter-live.html"
