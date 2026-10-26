import { expect, test } from "@playwright/test";
import { CONFIG, mockApi, readyRepo, saveKey } from "./mock";

test("home → add a repository → indexing progress → ask a question with sources", async ({ page }) => {
  await saveKey(page);
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", { json: { repos: [readyRepo("psf", "requests", { description: "HTTP for humans" })] } });
  // Not added yet; then queued (second in line), indexing 3 of 10 files, and ready.
  const states = [
    { status: "indexing", ready: false, files_total: 10, files_done: 3, stage: "Indexing" },
    { status: "indexing", ready: false, files_total: 10, files_done: 3, stage: "Indexing" },
    readyRepo("pallets", "flask"),
  ];
  let added = false;
  api.on("GET /api/repos/pallets/flask", () => {
    if (!added) return { status: 404, json: { error: "This repository hasn't been added yet.", code: "not_indexed" } };
    const next = states.length > 1 ? states.shift()! : states[0];
    return { json: { repo: { owner: "pallets", name: "flask", full_name: "pallets/flask", ...next } } };
  });
  api.on("POST /api/repos", () => {
    added = true;
    return { status: 202, json: { repo: { owner: "pallets", name: "flask", full_name: "pallets/flask", status: "queued", ready: false, queue_position: 2 } } };
  });
  api.on("POST /api/repos/pallets/flask/ask", {
    json: {
      answer: "Flask builds the app in **`create_app`** [1]. Config comes from the environment [2].",
      sources: [
        { ref: 1, path: "src/flask/app.py", start_line: 10, end_line: 30, kind: "function", name: "create_app", url: "https://github.com/pallets/flask/blob/c0ffee/src/flask/app.py#L10-L30" },
        { ref: 2, path: "src/flask/config.py", start_line: 1, end_line: 9, url: "https://github.com/pallets/flask/blob/c0ffee/src/flask/config.py#L1-L9" },
        { ref: 3, path: "src/flask/cli.py", start_line: 5, end_line: 6, url: "https://github.com/pallets/flask/blob/c0ffee/src/flask/cli.py#L5-L6" },
      ],
      cited: [1, 2],
      model: "openai/gpt-oss-20b",
      latency_ms: 2300,
    },
  });

  await page.goto("/static/app.html#/");
  await expect(page.getByRole("heading", { level: 1, name: "Tools for understanding and reviewing GitHub code" })).toBeVisible();
  await expect(page.locator(".recent .row")).toContainText("psf/requests");

  // The tool filter announces a short count.
  await page.getByRole("searchbox", { name: "Search tools" }).fill("review");
  await expect(page.locator(".search [role=status]")).toHaveText("2 tools");
  await page.getByRole("searchbox", { name: "Search tools" }).fill("");

  await page.locator(".tiles").getByRole("link", { name: /^Ask/ }).click();
  await expect(page).toHaveTitle("Ask · RepoScout");
  await expect(page.getByRole("heading", { name: "Which repository?" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Try one of these" }).getByRole("link")).toHaveText(["psf/requests", "pallets/flask", "tiangolo/fastapi"]);

  const link = page.getByRole("textbox", { name: "GitHub link" });
  await link.fill("not a link");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toHaveText("Paste a link like github.com/owner/repo");
  await expect(link).toHaveAttribute("aria-invalid", "true");
  await link.fill("https://github.com/pallets/flask");
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page).toHaveURL(/#\/ask\/pallets\/flask$/);
  await expect(page.getByRole("heading", { name: "Getting pallets/flask ready" })).toBeVisible();
  await expect(page.locator(".steps li").first()).toHaveText("Waiting for a free slot (number 2 in line)");
  await expect(page.locator(".repobar .meta")).toHaveText("Indexing now");
  const bar = page.getByRole("progressbar", { name: "Indexing progress" });
  await expect(bar).toHaveAttribute("aria-valuenow", "30");
  await expect(page.locator(".steps li[aria-current=step]")).toHaveText("Reading and indexing files (3 of 10)");
  await expect(page.locator(".progress [role=status]")).toHaveText("Reading and indexing files");

  // Ready: the chat replaces the card.
  await expect(page.getByRole("heading", { name: "Ask anything about flask" })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".repobar .meta")).toHaveText("main @ c0ffee1 · 42 files indexed");
  await expect(page.getByRole("link", { name: "Refresh index" }).or(page.getByRole("button", { name: "Refresh index" }))).toBeVisible();

  const box = page.getByRole("textbox", { name: "Your question" });
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
  await box.fill("How is the app created?");
  await box.press("Enter");

  await expect(page.locator(".q")).toHaveText("How is the app created?");
  await expect(page.locator(".a .md")).toContainText("Flask builds the app in create_app");
  const cite = page.locator(".a .md a.cite").first();
  await expect(cite).toHaveText("1");
  await expect(cite).toHaveAttribute("href", "https://github.com/pallets/flask/blob/c0ffee/src/flask/app.py#L10-L30");
  await expect(cite).toHaveAttribute("title", "src/flask/app.py:10-30");
  const sources = page.getByLabel("Sources").getByRole("link");
  await expect(sources).toHaveCount(3);
  await expect(sources.nth(2)).toHaveClass(/uncited/);
  await expect(page.locator(".afoot")).toContainText("openai/gpt-oss-20b");
  await expect(page.locator(".afoot")).toContainText("2.3s");
  await expect(page.locator(".chat + [role=status]")).toHaveText("Answer ready.");
  await expect(box).toBeFocused();

  const ask = api.requests("POST /api/repos/pallets/flask/ask")[0];
  expect(ask.headers()["x-llm-key"]).toBe("gsk_test");
  expect(ask.headers()["x-llm-model"]).toBe("openai/gpt-oss-20b");
  expect(ask.postDataJSON()).toEqual({ question: "How is the app created?", history: [] });

  // The chat is kept per repository (rs.chat.*) and comes back after a reload.
  await page.reload();
  await expect(page.locator(".q")).toHaveText("How is the app created?");
  await page.getByRole("button", { name: "Clear chat" }).click();
  await expect(page.getByRole("heading", { name: "Ask anything about flask" })).toBeVisible();
  api.verify();
});

test("asking without a key opens the key dialog, then sends after the key is saved", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos/o/n", { json: { repo: readyRepo("o", "n") } });
  api.on("POST /api/models", { json: { models: ["openai/gpt-oss-20b", "qwen/qwen3-32b"], default: "openai/gpt-oss-20b" } });
  api.on("POST /api/repos/o/n/ask", { json: { answer: "It is fine.", sources: [], cited: [], model: "openai/gpt-oss-20b", latency_ms: 900 } });

  await page.goto("/static/app.html#/ask/o/n");
  await page.getByRole("button", { name: "What does this project do, and how is it organised?" }).click();
  const dialog = page.getByRole("dialog", { name: "Your Groq API key" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("#keyMsg")).toHaveText("Add your API key to ask questions.");
  await dialog.getByLabel("Groq API key").fill("gsk_fresh");
  await dialog.getByRole("button", { name: "Save" }).click(); // loads the models first, then saves
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Key saved.")).toBeVisible();
  await expect(page.locator(".a .md")).toHaveText("It is fine.");
  expect(api.requests("POST /api/repos/o/n/ask")[0].headers()["x-llm-key"]).toBe("gsk_fresh");
  api.verify();
});

test("old links redirect and unknown routes go home", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos/o/n", { json: { repo: readyRepo("o", "n") } });
  api.on("GET /api/repos", { json: { repos: [] } });
  await page.goto("/static/app.html#/r/o/n/search");
  await expect(page).toHaveURL(/#\/find\/o\/n$/);
  await expect(page).toHaveTitle("Find code · o/n · RepoScout");
  await expect(page.locator(".navlink.direct[aria-current=page]")).toHaveText("Find code");
  await page.goto("/static/app.html#/nowhere");
  await expect(page.getByRole("heading", { name: "Nothing explored yet" })).toBeVisible();
  api.verify();
});
