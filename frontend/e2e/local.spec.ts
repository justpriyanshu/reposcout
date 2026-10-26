import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

test("local page: ask, review a diff and find code in tabs", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /stats", { json: { repo: "/home/me/code/shop", llm: "qwen3", index: { files: 12, chunks: 340 } } });
  api.on("POST /ask", { json: { answer: "Tokens are checked in `verify` [1].", sources: [{ ref: 1, path: "auth/session.py", start_line: 3, end_line: 9, name: "verify" }, { ref: 2, path: "auth/x.py", start_line: 1, end_line: 2 }], cited: [1], latency_ms: 1200 } });
  api.on("POST /review", (req) => {
    expect(req.postDataJSON()).toEqual({ diff: "diff --git a/x b/x", min_severity: "major" });
    return { json: { headline: "1 problem found", file_summaries: { "auth/session.py": "Logs the password." }, stats: { latency_ms: 3400, llm_calls: 1 },
      findings: [{ path: "auth/session.py", line: 11, severity: "critical", category: "security", title: "Password logged", message: "Never log passwords.", source: "llm" }] } };
  });
  let searches = 0;
  api.on("POST /search", () => (++searches === 1
    ? { status: 500, json: { error: "The index is locked." } }
    : { json: { results: [{ path: "billing/invoice.py", start_line: 5, end_line: 6, name: "total", text: "def total():\n    return 1" }] } }));

  await page.goto("/static/index.html");
  await expect(page.locator("#stats")).toContainText("shop");
  await expect(page.locator("#stats .meta")).toHaveText("12 files · 340 sections · model qwen3");

  const tabs = page.getByRole("tablist", { name: "Tools" });
  await expect(tabs.getByRole("tab", { name: "Ask" })).toHaveAttribute("aria-selected", "true");
  const question = page.getByRole("textbox", { name: "Your question" });
  await question.fill("How are tokens verified?");
  await question.press("Control+Enter");
  await expect(page.locator("#ask-out .md")).toContainText("Tokens are checked in verify");
  await expect(page.locator("#ask-out span.cite")).toHaveAttribute("title", "auth/session.py:3-9");
  await expect(page.locator("#ask-out .src")).toHaveCount(2);
  await expect(page.locator("#ask-out .afoot")).toHaveText("1.2s");

  // Arrow keys move between tabs and select them; a click puts the cursor in the panel's field.
  await tabs.getByRole("tab", { name: "Ask" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(tabs.getByRole("tab", { name: "Review a diff" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel", { name: "Review a diff" })).toBeVisible();
  await expect(page.locator("#ask-out")).toBeHidden(); // kept, just hidden
  await tabs.getByRole("tab", { name: "Review a diff" }).click();
  const diff = page.getByRole("textbox", { name: "Unified diff" });
  await expect(diff).toBeFocused();
  await diff.fill("diff --git a/x b/x");
  await page.getByLabel("Minimum severity").selectOption("major");
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await expect(page.locator("#review-out .verdict strong")).toHaveText("1 problem found");
  await expect(page.locator("#review-out .verdict .note")).toHaveText("3.4s · 1 LLM call");
  await expect(page.locator("#review-out .finding .t")).toHaveText("Password logged");

  await tabs.getByRole("tab", { name: "Find code" }).click();
  const query = page.getByRole("searchbox", { name: "Search the code" });
  await expect(query).toBeFocused();
  await query.fill("total");
  await query.press("Enter");
  await expect(page.locator("#find-out [role=alert]")).toContainText("The index is locked.");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator("#find-out .hit header .path")).toHaveText("billing/invoice.py:5-6");

  await tabs.getByRole("tab", { name: "Ask" }).click();
  await expect(page.locator("#ask-out .md")).toContainText("Tokens are checked"); // the answer survived the tab switches
  api.verify();
});
