<p align="center">
  <img src="https://raw.githubusercontent.com/rayakame/hikari-risa/a14722c65c6df514d463402cde7f7f48d133bd66/assets/web/banner.webp" alt="hikari-risa" width="100%">
</p>

<p align="center">
  <a href="https://pypi.org/project/hikari-risa/"><img src="https://img.shields.io/pypi/v/hikari-risa" alt="PyPI"></a>
  <a href="https://pypi.org/project/hikari-risa/"><img src="https://img.shields.io/pypi/pyversions/hikari-risa" alt="Python versions"></a>
  <a href="https://github.com/rayakame/hikari-risa/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/rayakame/hikari-risa/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://hikari-risa.rayakame.dev/"><img src="https://img.shields.io/badge/docs-hikari--risa.rayakame.dev-f680a1" alt="Docs"></a>
  <a href="https://github.com/microsoft/pyright"><img src="https://img.shields.io/badge/pyright-strict-blue" alt="pyright strict"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
  <a href="https://github.com/rayakame/hikari-risa/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/hikari-risa" alt="License: MIT"></a>
  <a href="https://discord.gg/hikari"><img src="https://img.shields.io/badge/discord-%23risa-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
</p>

# Components V2 views for hikari, with state that survives restarts.

risa is a component handler for [hikari](https://github.com/hikari-py/hikari), built for Components V2 first.
A view keeps its state in the message it renders, or in a store you choose, so a click still lands where it should
after a restart.

```python
import risa
from risa import ui


@risa.register(name="counter")
class Counter(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"Clicks: **{self.count}**"),
            ui.Row(ui.Button(self.click, label="+1")),
        )

    @risa.handler
    async def click(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()
```

## Install

risa runs on Python 3.12 to 3.15.

```sh
uv add hikari-risa
pip install hikari-risa
```

Extras: `hikari-risa[redis]`, `hikari-risa[postgres]` and `hikari-risa[radixly]`.

## Documentation

<https://hikari-risa.rayakame.dev/>

## License

MIT, see [LICENSE](https://github.com/rayakame/hikari-risa/blob/main/LICENSE).
