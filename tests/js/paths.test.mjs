import { test } from "node:test";
import assert from "node:assert/strict";
import { shortPath } from "../../src/reposcout/static/js/paths.js";

const DEEP = "packages/server/src/main/java/com/northwind/ingestion/reconcilers/PipelineController.java";

test("short paths are left alone", () => {
  assert.equal(shortPath("src/a.py"), "src/a.py");
  assert.equal(shortPath("README.md", 5), "README.md");
  assert.equal(shortPath(""), "");
});

test("long paths keep the first folder and the end, cut in the middle", () => {
  const out = shortPath(DEEP, 48);
  assert.equal(out, "packages/…/reconcilers/PipelineController.java");
  assert.ok(out.length <= 48);
});

test("the file name always survives, even when nothing else fits", () => {
  assert.equal(shortPath(DEEP, 30), "…/PipelineController.java");
  assert.equal(shortPath("a/b/AVeryLongFileNameThatIsLongerThanTheLimit.java", 20), "…/AVeryLongFileNameThatIsLongerThanTheLimit.java");
});

test("a long single name with no folders is returned whole (CSS truncates it)", () => {
  const name = "x".repeat(80);
  assert.equal(shortPath(name, 20), name);
});
