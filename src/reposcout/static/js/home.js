// Placeholder home view; replaced by the real home page in a later task.
import { h, stateBlock } from "./core.js";

export function render(main) {
  main.append(h("div", { class: "wrap" }, stateBlock({ icon: "home", title: "Home" })));
}
