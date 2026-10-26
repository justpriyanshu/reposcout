import { expect, test } from "vitest";
import { legacyRedirect, parseRoute } from "./routes";

test("empty and unknown routes go home", () => {
  for (const hash of ["", "#", "#/", "#/zzz", "#/zzz/a/b", "#/r/o/n"]) expect(parseRoute(hash), hash).toEqual({ view: "home" });
});

test("the signed-in person's GitHub repositories", () => {
  expect(parseRoute("#/github")).toEqual({ view: "github" });
  expect(parseRoute("#/github/")).toEqual({ view: "github" });
  expect(parseRoute("#/github/x")).toEqual({ view: "home" });
});

test("tool page without a repository", () => {
  expect(parseRoute("#/ask")).toEqual({ view: "tool", tool: "ask" });
  expect(parseRoute("#/review/")).toEqual({ view: "tool", tool: "review" });
});

test("tool page with a repository", () => {
  expect(parseRoute("#/find/psf/requests")).toEqual({ view: "tool", tool: "find", owner: "psf", name: "requests" });
});

test("review with a pull number", () => {
  expect(parseRoute("#/review/o/n/12")).toEqual({ view: "tool", tool: "review", owner: "o", name: "n", pull: 12 });
});

test("invalid shapes degrade to the nearest valid route", () => {
  expect(parseRoute("#/review/o/n/abc")).toEqual({ view: "tool", tool: "review", owner: "o", name: "n" });
  expect(parseRoute("#/review/o/n/0")).toEqual({ view: "tool", tool: "review", owner: "o", name: "n" });
  expect(parseRoute("#/ask/onlyowner")).toEqual({ view: "tool", tool: "ask" });
  expect(parseRoute("#/ask/o/n/5")).toEqual({ view: "tool", tool: "ask", owner: "o", name: "n" });
});

test("parts are decoded, and bad encodings do not throw", () => {
  expect(parseRoute("#/ask/o%2Dw/n")).toEqual({ view: "tool", tool: "ask", owner: "o-w", name: "n" });
  expect(parseRoute("#/ask/%E0%A4%A/n")).toEqual({ view: "tool", tool: "ask" });
});

test("owner and name must look like GitHub ones, else the picker is shown", () => {
  const bare = { view: "tool", tool: "ask" };
  expect(parseRoute("#/ask/o%2Fx/n")).toEqual(bare);
  expect(parseRoute("#/ask/o/n%3Fx%3D1")).toEqual(bare);
  expect(parseRoute("#/find/o/..%2F..%2Fx")).toEqual({ view: "tool", tool: "find" });
  expect(parseRoute("#/review/o_w/n/3")).toEqual({ view: "tool", tool: "review" });
  expect(parseRoute(`#/ask/${"a".repeat(40)}/n`)).toEqual(bare);
  expect(parseRoute(`#/ask/o/${"n".repeat(101)}`)).toEqual(bare);
  expect(parseRoute("#/ask/Foo-Bar/my.repo_x-1")).toEqual({ view: "tool", tool: "ask", owner: "Foo-Bar", name: "my.repo_x-1" });
});

test("legacy links redirect to the new routes", () => {
  expect(legacyRedirect("#/r/o/n")).toBe("#/ask/o/n");
  expect(legacyRedirect("#/r/o/n/ask")).toBe("#/ask/o/n");
  expect(legacyRedirect("#/r/o/n/search")).toBe("#/find/o/n");
  expect(legacyRedirect("#/r/o/n/review")).toBe("#/review/o/n");
  expect(legacyRedirect("#/pr/o/n/5")).toBe("#/review/o/n/5");
  expect(legacyRedirect("#/r/o%20w/n")).toBe("#/ask/o%20w/n");
});

test("current routes are not redirected", () => {
  for (const hash of ["", "#/", "#/ask/o/n", "#/review/o/n/5", "#/zzz"]) expect(legacyRedirect(hash), hash).toBeNull();
});
