import { expect, test } from "@playwright/test";
import { CONFIG, mockApi, saveKey } from "./mock";

const REVIEW = {
  pull: { repo: "psf/requests", number: 12, title: "Average invoice totals", state: "open", author: "octo", url: "https://github.com/psf/requests/pull/12", changed_files: 2, additions: 40, deletions: 3 },
  findings: [
    { path: "billing/invoice.py", line: 14, severity: "critical", category: "bug", title: "Division by zero", message: "`total / count` fails when **count** is 0.", suggestion: "if not count:\n    return 0", source: "llm", url: "https://github.com/psf/requests/pull/12/files#diff-1R14" },
    { path: "billing/invoice.py", line: 3, severity: "minor", category: "style", title: "Unused import", message: "os is never used.", source: "rule:unused-import" },
    { path: "billing/tax.py", line: 8, severity: "nit", category: "style", title: "Long line", message: "Wrap it.", source: "rule:line-length" },
  ],
  files_reviewed: ["billing/invoice.py", "billing/tax.py"],
  files_skipped: { "poetry.lock": "generated" },
  file_summaries: { "billing/invoice.py": "Adds an average." },
  counts: { critical: 1, minor: 1, nit: 1 },
  used_repo_context: false,
  stats: { llm_errors: 0 },
  markdown: "## RepoScout review",
};

test("review a PR: pick it, run it, filter the findings, and reuse the cached result", async ({ page }) => {
  await saveKey(page);
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", { json: { repos: [] } });
  api.on("GET /api/repos/psf/requests", { status: 404, json: { error: "This repository hasn't been added yet.", code: "not_indexed" } });
  api.on("POST /api/review", { json: REVIEW });

  await page.goto("/static/app.html#/review");
  await expect(page.getByRole("heading", { name: "Which pull request?" })).toBeVisible();
  await page.getByRole("textbox", { name: "GitHub link" }).fill("github.com/psf/requests/pull/12");
  await page.getByRole("button", { name: "Continue" }).click();

  // A PR link the visitor typed starts at once (no "Start the review?" step).
  await expect(page).toHaveURL(/#\/review\/psf\/requests\/12$/);
  await expect(page).toHaveTitle("Review #12 · psf/requests · RepoScout");
  await expect(page.getByRole("heading", { name: "Average invoice totals" })).toBeVisible();
  await expect(page.locator(".repobar .meta")).toHaveText("Not indexed. Reviews read only the changed files.");
  await expect(page.locator(".verdict strong")).toHaveText("3 things to look at");
  await expect(page.locator(".prmeta")).toContainText("#12 by octo");
  await expect(page.locator(".prmeta")).toContainText("+40 −3");
  await expect(page.getByText("Not reviewed: poetry.lock (generated)")).toBeVisible();
  await expect(page.getByRole("link", { name: "index psf/requests" })).toHaveAttribute("href", "#/ask/psf/requests");
  await expect(page.locator("[role=status]").filter({ hasText: "Review ready." })).toHaveCount(1);

  const chips = page.getByRole("group", { name: "Filter by severity" }).getByRole("button");
  await expect(chips).toHaveText(["1 Critical", "1 Minor", "1 Nit"]);
  await expect(chips.nth(2)).toHaveAttribute("aria-pressed", "false"); // nits hidden at first
  await expect(page.locator(".finding")).toHaveCount(2);
  const first = page.locator(".finding").first();
  await expect(first.locator(".t")).toHaveText("Unused import"); // sorted by line within the file
  const critical = page.locator(".finding.s-critical");
  await expect(critical.locator("p").first()).toContainText("total / count fails when count is 0.");
  await expect(critical.locator("code")).toHaveText("total / count");
  await expect(critical.locator("a.where")).toHaveText("line 14");
  await expect(critical.locator(".by")).toHaveText("AI review · bug");
  await expect(first.locator(".by")).toHaveText("Fixed check (unused-import) · style");

  await chips.nth(2).click();
  await expect(chips.nth(2)).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".finding")).toHaveCount(3);
  for (const i of [0, 1, 2]) await chips.nth(i).click();
  await expect(page.getByRole("heading", { name: "Every finding is filtered out" })).toBeVisible();
  await page.getByRole("button", { name: "Show all findings" }).click();
  await expect(page.locator(".finding")).toHaveCount(3);
  await expect(chips.first()).toBeFocused();

  const req = api.requests("POST /api/review")[0];
  expect(req.postDataJSON()).toEqual({ url: "https://github.com/psf/requests/pull/12", min_severity: "nit" });
  expect(req.headers()["x-llm-key"]).toBe("gsk_test");

  // The result is cached for this tab: a reload shows it without spending quota again.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Average invoice totals" })).toBeVisible();
  expect(api.requests("POST /api/review")).toHaveLength(1);

  // Index it from the bar.
  api.on("POST /api/repos", { status: 202, json: { repo: { owner: "psf", name: "requests", full_name: "psf/requests", status: "queued", ready: false } } });
  await page.getByRole("button", { name: "Index it" }).click();
  await expect(page.getByText("Indexing started. You can keep reviewing meanwhile.")).toBeVisible();
  await expect(page.locator(".repobar .meta")).toHaveText("Indexing now");
  api.verify();
});

test("an opened review link asks before spending quota", async ({ page }) => {
  await saveKey(page);
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos/o/n", { status: 404, json: { error: "nope", code: "not_indexed" } });
  api.on("POST /api/review", { status: 401, json: { error: "Groq rejected your API key.", code: "bad_key" } });

  await page.goto("/static/app.html#/review/o/n/7");
  await expect(page.getByRole("heading", { name: "Start the review?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start review" })).toBeFocused();
  expect(api.requests("POST /api/review")).toHaveLength(0);
  await page.getByRole("button", { name: "Start review" }).click();
  // A key error reopens the key dialog with the server's message.
  const dialog = page.getByRole("dialog", { name: "Your Groq API key" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("#keyMsg")).toHaveText("Groq rejected your API key.");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "The review didn't finish" })).toBeVisible();
  api.verify();
});

test("without a key the review asks for one", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos/o/n", { status: 404, json: { error: "nope", code: "not_indexed" } });
  await page.goto("/static/app.html#/review/o/n/7");
  await expect(page.getByRole("heading", { name: "Add your API key to start" })).toBeVisible();
  api.verify();
});
