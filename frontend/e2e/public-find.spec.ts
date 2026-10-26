import { expect, test } from "@playwright/test";
import { CONFIG, mockApi, readyRepo } from "./mock";

test("find code: results with matched lines, no matches, and an error with Try again", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos/psf/requests", { json: { repo: readyRepo("psf", "requests") } });
  let booms = 0;
  api.on("POST /api/repos/psf/requests/search", (req) => {
    const { query } = req.postDataJSON() as { query: string };
    if (query === "boom") return ++booms < 3 ? { status: 503, json: { error: "busy", code: "busy" } } : { json: { results: [] } };
    if (query === "zzz") return { json: { results: [] } };
    return {
      json: {
        results: [{
          path: "src/requests/adapters.py", start_line: 100, end_line: 103, kind: "method", name: "send", language: "python",
          url: "https://github.com/psf/requests/blob/c0ffee/src/requests/adapters.py#L100-L103",
          text: "def send(self, request):\n    conn = self.get_connection(request.url)\n    return conn.urlopen()\n",
        }],
      },
    };
  });

  await page.goto("/static/app.html#/find/psf/requests");
  const box = page.getByRole("searchbox", { name: "Search the code" });
  await expect(box).toBeFocused(); // desktop: the field takes focus
  await box.fill("send request");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const hit = page.locator(".hit");
  await expect(hit).toHaveCount(1);
  await expect(hit.locator("header a")).toHaveText("src/requests/adapters.py:100-103");
  await expect(hit.locator("header .pill")).toHaveText("method send");
  await expect(hit.locator(".ln.hl")).toHaveCount(2); // lines 100 and 101 mention "send" / "request"
  await expect(hit.locator(".ln").first()).toHaveAttribute("data-n", "100");
  expect(api.requests("POST /api/repos/psf/requests/search")[0].headers()["x-llm-key"]).toBeUndefined();

  await box.fill("zzz");
  await box.press("Enter");
  await expect(page.getByRole("heading", { name: "No matches for “zzz”" })).toBeVisible();

  await box.fill("boom");
  await box.press("Enter");
  await expect(page.getByRole("alert")).toContainText("RepoScout is busy. Try again in a moment.");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("alert")).toContainText("RepoScout is busy");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No matches for “boom”" })).toBeVisible();
  api.verify();
});

test("an indexing failure offers Try again", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  let failed = true;
  api.on("GET /api/repos/o/broken", () => ({ json: { repo: failed
    ? { owner: "o", name: "broken", full_name: "o/broken", status: "failed", ready: false, error: "The repository is too big." }
    : readyRepo("o", "broken") } }));
  api.on("POST /api/repos", (req) => {
    expect(req.postDataJSON()).toEqual({ url: "o/broken", refresh: true });
    failed = false;
    return { status: 202, json: { repo: readyRepo("o", "broken") } };
  });
  await page.goto("/static/app.html#/find/o/broken");
  await expect(page.getByRole("heading", { name: "Couldn't index o/broken" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("The repository is too big.");
  await expect(page.locator(".repobar .meta")).toHaveText("Indexing failed");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("searchbox", { name: "Search the code" })).toBeVisible();
  api.verify();
});
