import { expect, test } from "@playwright/test";
import { CONFIG, mockApi } from "./mock";

test("key dialog: check a key, save it, see it in the nav, then remove it", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", { json: { repos: [] } });
  api.on("POST /api/models", (req) => req.headers()["x-llm-key"] === "gsk_bad"
    ? { status: 401, json: { error: "Groq rejected your API key.", code: "bad_key" } }
    : { json: { models: ["openai/gpt-oss-20b", "qwen/qwen3-32b"], default: "openai/gpt-oss-20b" } });

  await page.goto("/static/app.html#/");
  const keyBtn = page.getByRole("button", { name: "Add your API key" });
  await expect(keyBtn).toHaveText("Add API key");
  await keyBtn.click();

  const dialog = page.getByRole("dialog", { name: "Your Groq API key" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAccessibleDescription("RepoScout uses your own free key to answer questions and review code.");
  const field = dialog.getByLabel("Groq API key");
  await expect(field).toBeFocused();
  await expect(field).toHaveAttribute("type", "password");
  await expect(dialog.getByRole("button", { name: "Remove key" })).toBeHidden();

  await dialog.getByRole("button", { name: "Check key" }).click();
  await expect(dialog.locator("#keyMsg")).toHaveText("Paste your key first.");
  await field.fill("gsk_bad");
  await dialog.getByRole("button", { name: "Check key" }).click();
  await expect(dialog.locator("#keyMsg")).toHaveText("Groq rejected your API key.");

  await field.fill("gsk_good");
  await dialog.getByRole("button", { name: "Show key" }).click();
  await expect(field).toHaveAttribute("type", "text");
  await dialog.getByRole("button", { name: "Check key" }).click();
  await expect(dialog.locator("#keyMsg")).toHaveText("Key works. 2 models available.");
  await dialog.getByLabel("Model").selectOption("qwen/qwen3-32b");
  await dialog.getByRole("button", { name: "Save" }).click();

  await expect(dialog).toBeHidden();
  await expect(page.getByText("Key saved.")).toBeVisible();
  const saved = page.getByRole("button", { name: "Groq key settings" });
  await expect(saved).toHaveText("Groq key");
  await expect(saved).toBeFocused(); // focus returns to what opened the dialog
  // Not remembered: kept for this tab only, under the same keys as before.
  expect(await page.evaluate(() => [sessionStorage.getItem("rs.key"), sessionStorage.getItem("rs.model"), localStorage.getItem("rs.key")]))
    .toEqual(['"gsk_good"', '"qwen/qwen3-32b"', null]);

  // Esc closes without saving.
  await saved.click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Groq API key")).toHaveValue("gsk_good");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  await saved.click();
  await dialog.getByRole("button", { name: "Remove key" }).click();
  await expect(page.getByText("Key removed from this browser.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add your API key" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("rs.key"))).toBeNull();
  api.verify();
});

test("All tools menu works by keyboard", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", { json: { repos: [] } });
  await page.goto("/static/app.html#/");
  const opener = page.locator(".navpos .menu-btn");
  await opener.click();
  await expect(opener).toHaveAttribute("aria-expanded", "true");
  const menu = page.getByRole("navigation", { name: "All tools" });
  await expect(menu.getByRole("link", { name: "Ask" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("link", { name: "Find code" })).toBeFocused();
  await page.keyboard.press("End");
  await expect(menu.getByRole("link", { name: "Review a PR" })).toBeFocused();
  await expect(menu.locator(".soon")).toHaveCount(6);
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(opener).toBeFocused();
  await opener.click();
  await menu.getByRole("link", { name: "Find code" }).click();
  await expect(page).toHaveURL(/#\/find$/);
  await expect(menu).toBeHidden();
  await expect(page.locator("#view")).toBeFocused();
  api.verify();
});
