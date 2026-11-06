(function () {
  "use strict";

  function slot(root, name) {
    return root.querySelector(`[data-live-slot="${name}"]`);
  }

  function show(root, frame) {
    for (const part of frame.content.children) {
      slot(root, part.dataset.livePart).replaceChildren(...part.cloneNode(true).childNodes);
    }
  }

  function tint(line) {
    line.classList.remove("risa-live__tint");
    // Without a reflow between the two, a repeat click would not restart the animation.
    void line.offsetWidth;
    line.classList.add("risa-live__tint");
  }

  function attach(root) {
    if ("liveAttached" in root.dataset) {
      return;
    }
    root.dataset.liveAttached = "";

    const frames = root.querySelectorAll("template[data-live-frame]");
    const message = slot(root, "message");
    let shown = 0;

    show(root, frames[shown]);
    // Set after the first swap, so that the upgrade of the button is not read out on load.
    message.setAttribute("aria-live", "polite");

    root.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (button === null || !message.contains(button) || shown === frames.length - 1) {
        return;
      }
      const focused = document.activeElement === button;
      shown += 1;
      show(root, frames[shown]);
      tint(root.querySelector("[data-live-tint]"));
      if (focused) {
        message.querySelector("button").focus({ preventScroll: true });
      }
    });
  }

  document$.subscribe(() => {
    document.querySelectorAll("[data-live-preview]").forEach(attach);
  });
})();
