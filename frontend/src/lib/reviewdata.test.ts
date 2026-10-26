import { expect, test } from "vitest";
import { approveReview, consumeApproval, defaultSeverities, isApproved, parsePrInput, reviewCacheKey } from "./reviewdata";

test("parsePrInput takes a number, #number or a PR link", () => {
  expect(parsePrInput("1234", "o", "n")).toEqual({ owner: "o", name: "n", pull: 1234 });
  expect(parsePrInput("  #7 ", "o", "n")).toEqual({ owner: "o", name: "n", pull: 7 });
  expect(parsePrInput("https://github.com/psf/requests/pull/6721", "o", "n")).toEqual({ owner: "psf", name: "requests", pull: 6721 });
  expect(parsePrInput("psf/requests#12", "o", "n")).toEqual({ owner: "psf", name: "requests", pull: 12 });
});

test("parsePrInput rejects everything else", () => {
  for (const bad of ["", "abc", "0", "#0", "12a", "-3", "github.com/o/n", "o/n", "1.5", null, undefined]) {
    expect(parsePrInput(bad, "o", "n"), String(bad)).toBeNull();
  }
});

test("defaultSeverities hides nits unless they are the only findings", () => {
  expect([...defaultSeverities({ critical: 1, nit: 3 })]).toEqual(["critical", "major", "minor"]);
  expect([...defaultSeverities({ nit: 3 })]).toEqual(["critical", "major", "minor", "nit"]);
  expect([...defaultSeverities({})]).toEqual(["critical", "major", "minor", "nit"]);
  expect([...defaultSeverities()]).toEqual(["critical", "major", "minor", "nit"]);
});

test("reviewCacheKey is per pull request and model, lower-case", () => {
  expect(reviewCacheKey("Psf", "Requests", 12, "Llama-3.3")).toBe("rs.review.psf/requests#12.llama-3.3");
  expect(reviewCacheKey("o", "n", 1, "")).toBe("rs.review.o/n#1.");
});

test("an approval matches one review, ignoring case, until it is used", () => {
  approveReview("Psf", "Requests", 12);
  expect(isApproved("psf", "requests", 12)).toBe(true);
  expect(isApproved("psf", "requests", 13)).toBe(false);
  consumeApproval();
  expect(isApproved("psf", "requests", 12)).toBe(false);
});
