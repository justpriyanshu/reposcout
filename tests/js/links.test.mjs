import { test } from "node:test";
import assert from "node:assert/strict";
import { parseLink, toolHash } from "../../src/reposcout/static/js/links.js";

const cases = [
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

for (const [text, owner, name, pull] of cases) {
  test(`parseLink(${JSON.stringify(text)})`, () => {
    assert.deepEqual(parseLink(text), { owner, name, pull });
  });
}

for (const text of ["not a link", "", "flask", "https://gitlab.com/a/b", "https://github.com/a", "file:///etc/passwd", null]) {
  test(`parseLink rejects ${JSON.stringify(text)}`, () => {
    assert.equal(parseLink(text), null);
  });
}

test("toolHash builds encoded tool routes", () => {
  assert.equal(toolHash("ask"), "#/ask");
  assert.equal(toolHash("ask", "o", "n"), "#/ask/o/n");
  assert.equal(toolHash("review", "o", "n", 12), "#/review/o/n/12");
  assert.equal(toolHash("review", "o w", "n", 3), "#/review/o%20w/n/3");
  assert.equal(toolHash("find", "a/b", "c#d"), "#/find/a%2Fb/c%23d");
});
