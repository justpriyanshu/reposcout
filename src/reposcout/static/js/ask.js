// Ask view: the tool page frame for now; the question box and answers arrive in a later task.
import { stateBlock } from "./core.js";
import { mountTool } from "./repo.js";

export async function render(main, route, onLeave) {
  const page = await mountTool(main, route, onLeave, { index: true });
  if (!page || route.signal.aborted) return;
  page.content.replaceChildren(stateBlock({ icon: "chat", title: "Ask is coming in the next step" }));
}
