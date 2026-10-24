// Placeholder Review a PR view; replaced by the real tool page in a later task.
import { h, stateBlock } from "./core.js";

export function render(main) {
  main.append(h("div", { class: "wrap" }, stateBlock({ icon: "review", title: "Review a PR" })));
}
