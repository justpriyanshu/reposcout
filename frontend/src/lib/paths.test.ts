import { expect, test } from "vitest";
import { shortPath } from "./paths";

const DEEP = "packages/server/src/main/java/com/northwind/ingestion/reconcilers/PipelineController.java";

test("short paths are left alone", () => {
  expect(shortPath("src/a.py")).toBe("src/a.py");
  expect(shortPath("README.md", 5)).toBe("README.md");
  expect(shortPath("")).toBe("");
});

test("long paths keep the first folder and the end, cut in the middle", () => {
  const out = shortPath(DEEP, 48);
  expect(out).toBe("packages/…/reconcilers/PipelineController.java");
  expect(out.length).toBeLessThanOrEqual(48);
});

test("the file name always survives, even when nothing else fits", () => {
  expect(shortPath(DEEP, 30)).toBe("…/PipelineController.java");
  expect(shortPath("a/b/AVeryLongFileNameThatIsLongerThanTheLimit.java", 20)).toBe("…/AVeryLongFileNameThatIsLongerThanTheLimit.java");
});

test("a long single name with no folders is returned whole (CSS truncates it)", () => {
  const name = "x".repeat(80);
  expect(shortPath(name, 20)).toBe(name);
});
