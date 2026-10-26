import { expect, test } from "@playwright/test";
import { CONFIG, SIGNED_IN, SIGNED_OUT, mockApi, readyRepo } from "./mock";

const signInHref = (next: string) => `/auth/github/login?next=${encodeURIComponent(next)}`;

test("signed out: Sign in with GitHub comes back to the page the visitor was on", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/me", { json: SIGNED_OUT });
  api.on("GET /api/repos", { json: { repos: [] } });

  await page.goto("/static/app.html#/");
  const nav = page.locator("header.nav");
  const signIn = nav.getByRole("link", { name: "Sign in with GitHub" });
  await expect(signIn).toHaveAttribute("href", signInHref("/static/app.html#/"));
  await expect(nav.getByRole("button", { name: /^Account/ })).toHaveCount(0);
  // The list is private, and the empty state offers sign-in too.
  await expect(page.getByRole("heading", { name: "Your repositories" })).toBeVisible();
  await expect(page.locator(".split")).toContainText("Only you can see this list. Sign in with GitHub to keep it on every device.");
  await expect(page.getByRole("heading", { name: "Nothing here yet" })).toBeVisible();
  await expect(page.locator(".recent").getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute("href", signInHref("/static/app.html#/"));

  await page.locator(".tiles").getByRole("link", { name: /^Find code/ }).click();
  await expect(page).toHaveURL(/#\/find$/);
  await expect(signIn).toHaveAttribute("href", signInHref("/static/app.html#/find"));

  // Phones: the button keeps its name but shows only the GitHub mark; nothing scrolls sideways.
  await page.setViewportSize({ width: 360, height: 740 });
  await expect(signIn).toBeVisible();
  await expect(signIn.locator(".lbl")).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  api.verify();
});

test("signed in: the account menu by keyboard, then sign out starts a fresh anonymous list", async ({ page }) => {
  const api = await mockApi(page);
  let signedIn = true;
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/me", () => ({ json: signedIn ? SIGNED_IN : SIGNED_OUT }));
  api.on("GET /api/repos", () => ({ json: { repos: signedIn ? [readyRepo("octocat", "hello")] : [] } }));
  api.on("POST /api/logout", () => { signedIn = false; return { json: { ok: true } }; });

  await page.goto("/static/app.html#/");
  const nav = page.locator("header.nav");
  const acct = nav.getByRole("button", { name: "Account: octocat" });
  await expect(acct).toHaveText("octocat");
  await expect(acct.locator("img.avatar")).toBeVisible();
  await expect(nav.getByRole("link", { name: "Sign in with GitHub" })).toHaveCount(0);
  await expect(page.locator(".recent .row")).toHaveText([/octocat\/hello/]);
  await expect(page.locator(".split")).toContainText("Repositories you've explored, most recent first. Only you can see this list.");
  await expect(page.getByRole("link", { name: "Explore your GitHub repositories" })).toHaveAttribute("href", "#/github");

  await acct.click();
  await expect(acct).toHaveAttribute("aria-expanded", "true");
  const menu = page.locator("#accountMenu");
  await expect(menu).toContainText("The Octocat");
  await expect(menu).toContainText("@octocat");
  await expect(menu.getByRole("link", { name: "Your GitHub repositories" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("button", { name: "Sign out" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("link", { name: "Your GitHub repositories" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(acct).toBeFocused();

  // Outside clicks close it too.
  await acct.click();
  await expect(menu).toBeVisible();
  await page.locator(".hero h1").click();
  await expect(menu).toBeHidden();

  await acct.click();
  await menu.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByText("Signed out.")).toBeVisible();
  const signIn = nav.getByRole("link", { name: "Sign in with GitHub" });
  await expect(signIn).toBeFocused();
  await expect(acct).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Nothing here yet" })).toBeVisible();
  expect(api.requests("POST /api/logout")[0].postDataJSON()).toEqual({});
  expect(api.calls.filter((c) => c === "GET /api/me")).toHaveLength(2);
  expect(api.calls.filter((c) => c === "GET /api/repos")).toHaveLength(2);
  api.verify();
});

test("home lists only your repositories; remove takes one off and keeps focus in the list", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", { json: { repos: [readyRepo("a", "one", { description: "First" }), readyRepo("b", "two"), readyRepo("c", "three")] } });
  for (const full of ["a/one", "b/two", "c/three"]) api.on(`DELETE /api/repos/${full}`, { json: { ok: true } });

  await page.goto("/static/app.html#/");
  await expect(page.getByRole("heading", { name: "Recently explored" })).toHaveCount(0);
  const list = page.getByRole("list", { name: "Your repositories" });
  const rows = list.getByRole("listitem");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0).getByRole("link", { name: "a/one" })).toHaveAttribute("href", "#/ask/a/one");
  await expect(rows.nth(0)).toContainText("First");
  await expect(rows.nth(1)).toContainText("42 files indexed");
  await expect(rows.nth(1).locator(".pill")).toHaveText("Ready");

  // The picker suggests the visitor's own ready repositories, then the built-in examples.
  await page.goto("/static/app.html#/ask");
  await expect(page.getByRole("group", { name: "Try one of these" }).getByRole("link")).toHaveText(["a/one", "b/two", "c/three"]);
  await page.goto("/static/app.html#/");

  await page.getByRole("button", { name: "Remove b/two from my list" }).click();
  await expect(page.getByText("Removed b/two from your list.")).toBeVisible();
  await expect(rows).toHaveCount(2);
  await expect(list.getByRole("link", { name: "c/three" })).toBeFocused(); // the row now in its place

  await page.getByRole("button", { name: "Remove c/three from my list" }).click();
  await expect(rows).toHaveCount(1);
  await expect(list.getByRole("link", { name: "a/one" })).toBeFocused(); // the last row: the one before

  await page.getByRole("button", { name: "Remove a/one from my list" }).click();
  await expect(page.getByRole("heading", { name: "Nothing here yet" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Your repositories" })).toBeFocused();
  expect(api.calls.filter((c) => c.startsWith("DELETE "))).toEqual(["DELETE /api/repos/b/two", "DELETE /api/repos/c/three", "DELETE /api/repos/a/one"]);
  api.verify();
});

test("an expired repository: Index again starts indexing, and the list follows it to Ready", async ({ page }) => {
  const api = await mockApi(page);
  let reindexed = false;
  const expired = { owner: "x", name: "old", full_name: "x/old", key: "x/old", status: "expired", ready: false, files_total: 0, files_done: 0 };
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", () => ({ json: { repos: [reindexed ? readyRepo("x", "old") : expired, readyRepo("me", "mine")] } }));
  api.on("POST /api/repos", () => {
    reindexed = true;
    return { status: 202, json: { repo: { ...expired, status: "queued", queue_position: 1 } } };
  });

  await page.goto("/static/app.html#/");
  const row = page.getByRole("listitem").filter({ hasText: "x/old" });
  await expect(row.locator(".pill")).toHaveText("Expired");
  await expect(row).toContainText("Its index was removed to save space. Index it again to use it.");
  // Ready rows have no "Index again".
  await expect(page.getByRole("listitem").filter({ hasText: "me/mine" }).getByRole("button", { name: "Index again" })).toHaveCount(0);

  await row.getByRole("button", { name: "Index again" }).click();
  await expect(page.getByText("Indexing x/old again. It takes a minute or two.")).toBeVisible();
  await expect(row.locator(".pill")).toHaveText("Indexing");
  await expect(row.getByRole("link", { name: "x/old" })).toBeFocused();
  expect(api.requests("POST /api/repos")[0].postDataJSON()).toEqual({ url: "x/old" });
  await expect(row.locator(".pill")).toHaveText("Ready", { timeout: 10_000 }); // the list polls while indexing
  api.verify();
});

test("opening an expired repository in a tool indexes it again", async ({ page }) => {
  const api = await mockApi(page);
  let reindexed = false;
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos/x/old", () => ({
    json: { repo: reindexed ? readyRepo("x", "old") : { owner: "x", name: "old", full_name: "x/old", status: "expired", ready: false } },
  }));
  api.on("POST /api/repos", () => {
    reindexed = true;
    return { status: 202, json: { repo: { owner: "x", name: "old", full_name: "x/old", status: "queued", ready: false, queue_position: 1 } } };
  });

  await page.goto("/static/app.html#/find/x/old");
  await expect(page.locator(".repobar .meta")).toHaveText("main @ c0ffee1 · 42 files indexed", { timeout: 10_000 });
  expect(api.requests("POST /api/repos").map((r) => r.postDataJSON())).toEqual([{ url: "x/old" }]);
  api.verify();
});

test("your GitHub repositories: filter, then Explore indexes one and opens Ask", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/me", { json: SIGNED_IN });
  api.on("GET /api/repos", { json: { repos: [] } });
  const day = 86_400_000;
  api.on("GET /api/me/github-repos", {
    json: {
      repos: [
        { full_name: "octocat/Hello-World", description: "My first repository", language: "Python", stars: 2100, pushed_at: new Date(Date.now() - 3 * day).toISOString(), html_url: "https://github.com/octocat/Hello-World", fork: false },
        { full_name: "octocat/Spoon-Knife", description: null, language: "HTML", stars: 1, pushed_at: null, html_url: "https://github.com/octocat/Spoon-Knife", fork: true },
        { full_name: "octocat/linguist", description: "Language Savant", language: null, stars: 0, pushed_at: "2020-01-01T00:00:00Z", html_url: "https://github.com/octocat/linguist", fork: false },
      ],
    },
  });
  api.on("POST /api/repos", { status: 202, json: { repo: { owner: "octocat", name: "Hello-World", full_name: "octocat/Hello-World", status: "queued", ready: false } } });
  api.on("GET /api/repos/octocat/Hello-World", { json: { repo: readyRepo("octocat", "Hello-World") } });

  await page.goto("/static/app.html#/");
  await page.getByRole("button", { name: "Account: octocat" }).click();
  await page.locator("#accountMenu").getByRole("link", { name: "Your GitHub repositories" }).click();
  await expect(page).toHaveURL(/#\/github$/);
  await expect(page).toHaveTitle("Your GitHub repositories · RepoScout");
  await expect(page.getByRole("heading", { level: 1, name: "Your GitHub repositories" })).toBeVisible();
  await expect(page.locator("#accountMenu")).toBeHidden();

  const list = page.getByRole("list", { name: "Your public repositories" });
  const rows = list.getByRole("listitem");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("My first repository");
  await expect(rows.nth(0)).toContainText("Python");
  await expect(rows.nth(0)).toContainText("2,100 stars");
  await expect(rows.nth(0)).toContainText("Updated 3 days ago");
  await expect(rows.nth(1).locator(".pill")).toHaveText("Fork");
  await expect(rows.nth(1)).toContainText("No description");
  await expect(rows.nth(0).getByRole("link", { name: "Open octocat/Hello-World on GitHub" })).toHaveAttribute("href", "https://github.com/octocat/Hello-World");

  const filter = page.getByRole("searchbox", { name: "Filter your repositories" });
  await expect(filter).toHaveAttribute("placeholder", "Filter 3 repositories");
  await filter.fill("savant");
  await expect(rows).toHaveCount(1);
  await expect(page.locator(".ghfilter [role=status]")).toHaveText("1 repository");
  await filter.fill("zzz");
  await expect(page.getByText("No repository matches “zzz”.")).toBeVisible();
  await expect(page.locator(".ghfilter [role=status]")).toHaveText("No repositories match");
  await filter.fill("");
  await expect(rows).toHaveCount(3);

  // Phones: the account shows just the avatar, and nothing scrolls sideways.
  await page.setViewportSize({ width: 360, height: 740 });
  await expect(page.getByRole("button", { name: "Account: octocat" }).locator(".lbl")).toBeHidden();
  await expect(page.getByRole("button", { name: "Account: octocat" })).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.getByRole("button", { name: "Explore octocat/Hello-World" }).click();
  await expect(page).toHaveURL(/#\/ask\/octocat\/Hello-World$/);
  await expect(page.getByRole("heading", { name: "Ask anything about Hello-World" })).toBeVisible();
  expect(api.requests("POST /api/repos")[0].postDataJSON()).toEqual({ url: "octocat/Hello-World" });
  api.verify();
});

test("your GitHub repositories when signed out, or when the session has ended", async ({ page }) => {
  const api = await mockApi(page);
  let me: unknown = SIGNED_OUT;
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/me", () => ({ json: me }));
  api.on("GET /api/me/github-repos", { status: 401, json: { error: "Sign in with GitHub first.", code: "sign_in" } });

  await page.goto("/static/app.html#/github");
  await expect(page.getByRole("heading", { name: "Sign in to see your repositories" })).toBeVisible();
  await expect(page.locator(".ghbody").getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute("href", signInHref("/static/app.html#/github"));

  me = SIGNED_IN; // /api/me says signed in, but GitHub repositories answer 401
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in to see your repositories" })).toBeVisible();
  api.verify();
});

test("a failed sign-in shows the server's message once and cleans the URL", async ({ page }) => {
  const api = await mockApi(page);
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/me", { json: SIGNED_OUT });
  api.on("GET /api/repos", { json: { repos: [] } });

  await page.goto("/static/app.html?login_error=GitHub%20sign-in%20was%20cancelled.#/");
  await expect(page.locator(".toast.error")).toHaveText("GitHub sign-in was cancelled.");
  await expect(page.locator(".toast.error")).toHaveCount(1);
  expect(new URL(page.url()).search).toBe("");
  await expect(page).toHaveURL(/\/static\/app\.html#\/$/);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Your repositories" })).toBeVisible();
  await expect(page.locator(".toast")).toHaveCount(0);
  api.verify();
});

test("no GitHub sign-in on this server: no sign-in anywhere", async ({ page }) => {
  const api = await mockApi(page); // /api/me: github_login false by default
  api.on("GET /api/config", { json: CONFIG });
  api.on("GET /api/repos", { json: { repos: [] } });

  await page.goto("/static/app.html#/");
  await expect(page.getByRole("heading", { name: "Nothing here yet" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in with GitHub" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Account/ })).toHaveCount(0);
  await expect(page.locator(".split")).toContainText("Repositories you've explored in this browser, most recent first. Only you can see this list.");
  await expect(page.locator(".split")).not.toContainText("Sign in");

  await page.goto("/static/app.html#/github");
  await expect(page.getByRole("heading", { name: "Sign-in isn't available here" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in with GitHub" })).toHaveCount(0);
  api.verify();
});
