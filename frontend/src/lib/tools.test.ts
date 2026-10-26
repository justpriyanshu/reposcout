import { expect, test } from "vitest";
import { CATEGORIES, EXAMPLES, TOOLS, filterTools, pickerRepos, type Tool } from "./tools";

test("nine tools, three live", () => {
  expect(TOOLS.length).toBe(9);
  expect(TOOLS.filter((t) => t.live).map((t) => t.id)).toEqual(["ask", "find", "review"]);
  for (const t of TOOLS) {
    for (const k of ["id", "name", "category", "description", "color", "glyph"] as (keyof Tool)[]) expect(t[k], `${t.id}.${k}`).toBeTruthy();
    expect(CATEGORIES, t.id).toContain(t.category);
  }
});

test("empty query returns every category in order", () => {
  const out = filterTools("");
  expect(out.map(([c]) => c)).toEqual(CATEGORIES);
  expect(out.flatMap(([, ts]) => ts).length).toBe(9);
});

test("query is case-insensitive over name and description", () => {
  const names = filterTools("REVIEW").flatMap(([, ts]) => ts.map((t) => t.name));
  expect(names).toContain("Review a PR");
  expect(names).toContain("Post a review");
  expect(filterTools("  zzz ")).toEqual([]);
});

test("picker repos: ready ones first, topped up with examples", () => {
  expect(pickerRepos([{ full_name: "a/b", ready: true }, { full_name: "c/d", ready: false }])).toEqual(["a/b", "pallets/flask", "psf/requests"]);
  expect(pickerRepos([{ full_name: "psf/requests", ready: true }])).toEqual(["psf/requests", "pallets/flask", "tiangolo/fastapi"]);
  expect(pickerRepos([], 5)).toEqual(EXAMPLES);
  expect(pickerRepos(undefined, 1)).toEqual(["pallets/flask"]);
});
