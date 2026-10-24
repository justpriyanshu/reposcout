import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, CATEGORIES, EXAMPLES, filterTools, pickerRepos } from "../../src/reposcout/static/js/tools.js";

test("nine tools, three live", () => {
  assert.equal(TOOLS.length, 9);
  assert.deepEqual(TOOLS.filter((t) => t.live).map((t) => t.id), ["ask", "find", "review"]);
  for (const t of TOOLS) {
    for (const k of ["id", "name", "category", "description", "color", "glyph"]) assert.ok(t[k], `${t.id}.${k}`);
    assert.ok(CATEGORIES.includes(t.category), t.id);
  }
});

test("empty query returns every category in order", () => {
  const out = filterTools("");
  assert.deepEqual(out.map(([c]) => c), CATEGORIES);
  assert.equal(out.flatMap(([, ts]) => ts).length, 9);
});

test("query is case-insensitive over name and description", () => {
  const names = filterTools("REVIEW").flatMap(([, ts]) => ts.map((t) => t.name));
  assert.ok(names.includes("Review a PR"));
  assert.ok(names.includes("Post a review"));
  assert.deepEqual(filterTools("  zzz "), []);
});

test("picker repos: ready ones first, topped up with examples", () => {
  assert.deepEqual(pickerRepos([{ full_name: "a/b", ready: true }, { full_name: "c/d", ready: false }]), ["a/b", "pallets/flask", "psf/requests"]);
  assert.deepEqual(pickerRepos([{ full_name: "psf/requests", ready: true }]), ["psf/requests", "pallets/flask", "tiangolo/fastapi"]);
  assert.deepEqual(pickerRepos([], 5), EXAMPLES);
  assert.deepEqual(pickerRepos(undefined, 1), ["pallets/flask"]);
});
