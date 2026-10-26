import { expect, test } from "@playwright/test";
import { CONFIG, mockApi } from "./mock";

for (const [label, url] of [["public app", "/static/app.html#/"], ["local page", "/static/index.html"]] as const) {
  test(`dark mode toggle on the ${label} is saved in rs.theme and applied before first paint`, async ({ page }) => {
    const api = await mockApi(page);
    api.on("GET /api/config", { json: CONFIG });
    api.on("GET /api/repos", { json: { repos: [] } });
    api.on("GET /stats", { json: { repo: "/x/shop", llm: null, index: { files: 1, chunks: 2 } } });

    await page.goto(url);
    const html = page.locator("html");
    await expect(html).not.toHaveAttribute("data-theme", /./);
    const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(await bg()).toBe("rgb(255, 255, 255)");

    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    await expect(html).toHaveAttribute("data-theme", "dark");
    expect(await page.evaluate(() => localStorage.getItem("rs.theme"))).toBe('"dark"');
    expect(await bg()).toBe("rgb(12, 13, 16)");

    // theme-init.js (a blocking script in <head>) applies the choice before the app loads.
    await page.route("**/assets/{app,index}-*.js", (route) => route.abort());
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "dark");
    await page.unroute("**/assets/{app,index}-*.js");
    await page.reload();
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await expect(html).toHaveAttribute("data-theme", "light");
    expect(await bg()).toBe("rgb(255, 255, 255)");
  });
}

test("with no saved choice the page follows the system's dark setting", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "dark" });
  const page = await context.newPage();
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", { json: { repos: [] } });
  await page.goto("/static/app.html#/");
  await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(12, 13, 16)");
  await context.close();
});
