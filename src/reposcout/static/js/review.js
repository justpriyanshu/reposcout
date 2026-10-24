// Review a PR view: the tool page frame for now (no index needed); the review arrives in a later task.
import { stateBlock } from "./core.js";
import { mountTool } from "./repo.js";

export async function render(main, route, onLeave) {
  const page = await mountTool(main, route, onLeave, { index: false });
  if (!page || route.signal.aborted) return;
  page.content.replaceChildren(stateBlock({ icon: "review", title: "Review a PR is coming in the next step" }));
}
