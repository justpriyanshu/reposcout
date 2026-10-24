// Find code view: the tool page frame for now; search arrives in a later task.
import { stateBlock } from "./core.js";
import { mountTool } from "./repo.js";

export async function render(main, route, onLeave) {
  const page = await mountTool(main, route, onLeave, { index: true });
  if (!page || route.signal.aborted) return;
  page.content.replaceChildren(stateBlock({ icon: "search", title: "Find code is coming in the next step" }));
}
