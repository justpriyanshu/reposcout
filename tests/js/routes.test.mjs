import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoute, legacyRedirect } from "../../src/reposcout/static/js/routes.js";

test("empty and unknown routes go home", () => {
  for (const hash of ["", "#", "#/", "#/zzz", "#/zzz/a/b", "#/r/o/n"]) assert.deepEqual(parseRoute(hash), { view: "home" }, hash);
});

test("tool page without a repository", () => {
  assert.deepEqual(parseRoute("#/ask"), { view: "tool", tool: "ask" });
  assert.deepEqual(parseRoute("#/review/"), { view: "tool", tool: "review" });
});

test("tool page with a repository", () => {
  assert.deepEqual(parseRoute("#/find/psf/requests"), { view: "tool", tool: "find", owner: "psf", name: "requests" });
});

test("review with a pull number", () => {
  assert.deepEqual(parseRoute("#/review/o/n/12"), { view: "tool", tool: "review", owner: "o", name: "n", pull: 12 });
});

test("invalid shapes degrade to the nearest valid route", () => {
  assert.deepEqual(parseRoute("#/review/o/n/abc"), { view: "tool", tool: "review", owner: "o", name: "n" });
  assert.deepEqual(parseRoute("#/review/o/n/0"), { view: "tool", tool: "review", owner: "o", name: "n" });
  assert.deepEqual(parseRoute("#/ask/onlyowner"), { view: "tool", tool: "ask" });
  assert.deepEqual(parseRoute("#/ask/o/n/5"), { view: "tool", tool: "ask", owner: "o", name: "n" });
});

test("parts are decoded, and bad encodings do not throw", () => {
  assert.deepEqual(parseRoute("#/ask/o%2Dw/n"), { view: "tool", tool: "ask", owner: "o-w", name: "n" });
  assert.deepEqual(parseRoute("#/ask/%E0%A4%A/n"), { view: "tool", tool: "ask" });
});

test("owner and name must look like GitHub ones, else the picker is shown", () => {
  const bare = { view: "tool", tool: "ask" };
  assert.deepEqual(parseRoute("#/ask/o%2Fx/n"), bare);
  assert.deepEqual(parseRoute("#/ask/o/n%3Fx%3D1"), bare);
  assert.deepEqual(parseRoute("#/find/o/..%2F..%2Fx"), { view: "tool", tool: "find" });
  assert.deepEqual(parseRoute("#/review/o_w/n/3"), { view: "tool", tool: "review" });
  assert.deepEqual(parseRoute(`#/ask/${"a".repeat(40)}/n`), bare);
  assert.deepEqual(parseRoute(`#/ask/o/${"n".repeat(101)}`), bare);
  assert.deepEqual(parseRoute("#/ask/Foo-Bar/my.repo_x-1"), { view: "tool", tool: "ask", owner: "Foo-Bar", name: "my.repo_x-1" });
});

test("legacy links redirect to the new routes", () => {
  assert.equal(legacyRedirect("#/r/o/n"), "#/ask/o/n");
  assert.equal(legacyRedirect("#/r/o/n/ask"), "#/ask/o/n");
  assert.equal(legacyRedirect("#/r/o/n/search"), "#/find/o/n");
  assert.equal(legacyRedirect("#/r/o/n/review"), "#/review/o/n");
  assert.equal(legacyRedirect("#/pr/o/n/5"), "#/review/o/n/5");
  assert.equal(legacyRedirect("#/r/o%20w/n"), "#/ask/o%20w/n");
});

test("current routes are not redirected", () => {
  for (const hash of ["", "#/", "#/ask/o/n", "#/review/o/n/5", "#/zzz"]) assert.equal(legacyRedirect(hash), null, hash);
});
