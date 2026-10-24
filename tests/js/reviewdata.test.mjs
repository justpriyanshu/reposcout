import { test } from "node:test";
import assert from "node:assert/strict";
import {
  approveReview, consumeApproval, defaultSeverities, isApproved, parsePrInput, reviewCacheKey,
} from "../../src/reposcout/static/js/reviewdata.js";

test("parsePrInput takes a number, #number or a PR link", () => {
  assert.deepEqual(parsePrInput("1234", "o", "n"), { owner: "o", name: "n", pull: 1234 });
  assert.deepEqual(parsePrInput("  #7 ", "o", "n"), { owner: "o", name: "n", pull: 7 });
  assert.deepEqual(parsePrInput("https://github.com/psf/requests/pull/6721", "o", "n"), { owner: "psf", name: "requests", pull: 6721 });
  assert.deepEqual(parsePrInput("psf/requests#12", "o", "n"), { owner: "psf", name: "requests", pull: 12 });
});

test("parsePrInput rejects everything else", () => {
  for (const bad of ["", "abc", "0", "#0", "12a", "-3", "github.com/o/n", "o/n", "1.5", null, undefined]) {
    assert.equal(parsePrInput(bad, "o", "n"), null, String(bad));
  }
});

test("defaultSeverities hides nits unless they are the only findings", () => {
  assert.deepEqual([...defaultSeverities({ critical: 1, nit: 3 })], ["critical", "major", "minor"]);
  assert.deepEqual([...defaultSeverities({ nit: 3 })], ["critical", "major", "minor", "nit"]);
  assert.deepEqual([...defaultSeverities({})], ["critical", "major", "minor", "nit"]);
  assert.deepEqual([...defaultSeverities()], ["critical", "major", "minor", "nit"]);
});

test("reviewCacheKey is per pull request and model, lower-case", () => {
  assert.equal(reviewCacheKey("Psf", "Requests", 12, "Llama-3.3"), "rs.review.psf/requests#12.llama-3.3");
  assert.equal(reviewCacheKey("o", "n", 1, ""), "rs.review.o/n#1.");
});

test("an approval matches one review, ignoring case, until it is used", () => {
  approveReview("Psf", "Requests", 12);
  assert.equal(isApproved("psf", "requests", 12), true);
  assert.equal(isApproved("psf", "requests", 13), false);
  consumeApproval();
  assert.equal(isApproved("psf", "requests", 12), false);
});
