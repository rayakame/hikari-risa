// risa's wire format after `count` clicks; tests/tools/test_render_example.py holds it against client.build.
function risaLiveCustomId(data, count) {
  const BASE85 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!#$%&()*+-;<=>?@^_`{|}~";
  const WIRE = Array.from({ length: 94 }, (_, index) => String.fromCharCode(33 + index)).filter(
    (char) => char !== '"' && char !== "\\",
  );

  function bigEndian(value, width) {
    return Array.from({ length: width }, (_, index) => Math.floor(value / 256 ** (width - 1 - index)) % 256);
  }

  function msgpackInteger(value) {
    if (value < 128) {
      return [value];
    }
    for (const [marker, width] of [[0xcc, 1], [0xcd, 2], [0xce, 4]]) {
      if (value < 256 ** width) {
        return [marker, ...bigEndian(value, width)];
      }
    }
    return [0xcf, ...bigEndian(value, 8)];
  }

  function base85(bytes) {
    let text = "";
    for (let start = 0; start < bytes.length; start += 4) {
      const chunk = bytes.slice(start, start + 4);
      let value = 0;
      for (let index = 0; index < 4; index += 1) {
        value = value * 256 + (chunk[index] ?? 0);
      }
      let group = "";
      for (let index = 0; index < 5; index += 1) {
        group = BASE85[value % 85] + group;
        value = Math.floor(value / 85);
      }
      text += group.slice(0, chunk.length + 1);
    }
    return text;
  }

  function fromHex(text) {
    return (text.match(/../g) ?? []).map((pair) => parseInt(pair, 16));
  }

  const state = [...fromHex(data.liveBefore), ...msgpackInteger(count), ...fromHex(data.liveAfter)];
  const fingerprint = Array.from(data.liveFingerprint, (char) => char.charCodeAt(0));
  const fragment = base85([
    "m".charCodeAt(0),
    ...bigEndian(1 + 2 + fingerprint.length + 3 + state.length, 2),
    ...fingerprint,
    ...bigEndian(count % 256 ** 3, 3),
    ...state,
  ]);
  return data.livePrefix + WIRE[fragment.length] + fragment;
}

(function () {
  "use strict";

  function slot(root, name) {
    return root.querySelector(`[data-live-slot="${name}"]`);
  }

  function fill(root, name, count) {
    const frame = root.querySelector("template[data-live-frame]");
    const part = frame.content.querySelector(`[data-live-part="${name}"]`).cloneNode(true);
    part.querySelectorAll("[data-live-count]").forEach((number) => {
      number.textContent = count;
    });
    slot(root, name).replaceChildren(...part.childNodes);
  }

  function markChanges(current, previous) {
    const nodes = [];
    let start = 0;
    for (let index = 1; index <= current.length; index += 1) {
      const changed = current[start] !== previous[start];
      if (index < current.length && (current[index] !== previous[index]) === changed) {
        continue;
      }
      const text = document.createTextNode(current.slice(start, index));
      if (changed) {
        const mark = document.createElement("span");
        mark.className = "risa-live__changed";
        mark.append(text);
        nodes.push(mark);
      } else {
        nodes.push(text);
      }
      start = index;
    }
    return nodes;
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

    const message = slot(root, "message");
    let count = 0;

    fill(root, "message", count);
    // Set after the first swap, so that the upgrade of the button is not read out on load.
    message.setAttribute("aria-live", "polite");

    root.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (button === null || !message.contains(button)) {
        return;
      }
      const focused = document.activeElement === button;
      const previous = risaLiveCustomId(root.dataset, count);
      count += 1;
      fill(root, "message", count);
      fill(root, "value", count);
      slot(root, "id").replaceChildren(...markChanges(risaLiveCustomId(root.dataset, count), previous));
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
