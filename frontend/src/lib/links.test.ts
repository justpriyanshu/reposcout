import { describe, expect, test } from "vitest";
import { parseLink, toolHash } from "./links";

const cases: [string, string, string, number | null][] = [
  ["https://github.com/psf/requests", "psf", "requests", null],
  ["https://github.com/pallets/flask", "pallets", "flask", null],
  ["github.com/pallets/flask.git", "pallets", "flask", null],
  ["https://github.com/pallets/flask/", "pallets", "flask", null],
  ["https://github.com/pallets/flask/tree/main/src", "pallets", "flask", null],
  ["https://github.com/pallets/flask/pull/5432", "pallets", "flask", 5432],
  ["github.com/o/r/pull/12/files", "o", "r", 12],
  ["pallets/flask", "pallets", "flask", null],
  ["o/r#7", "o", "r", 7],
  ["o/r.git", "o", "r", null],
  ["  http://www.github.com/osm-search/Nominatim  ", "osm-search", "Nominatim", null],
];

describe("parseLink", () => {
  test.each(cases)("parseLink(%j)", (text, owner, name, pull) => {
    expect(parseLink(text)).toEqual({ owner, name, pull });
  });

  test.each(["not a link", "", "flask", "https://gitlab.com/a/b", "https://github.com/a", "file:///etc/passwd", null])(
    "parseLink rejects %j", (text) => {
      expect(parseLink(text)).toBeNull();
    });
});

test("toolHash builds encoded tool routes", () => {
  expect(toolHash("ask")).toBe("#/ask");
  expect(toolHash("ask", "o", "n")).toBe("#/ask/o/n");
  expect(toolHash("review", "o", "n", 12)).toBe("#/review/o/n/12");
  expect(toolHash("review", "o w", "n", 3)).toBe("#/review/o%20w/n/3");
  expect(toolHash("find", "a/b", "c#d")).toBe("#/find/a%2Fb/c%23d");
});
