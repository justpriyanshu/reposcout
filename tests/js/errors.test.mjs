import { test } from "node:test";
import assert from "node:assert/strict";
// core.js touches the DOM only inside functions, so importing it under Node is safe.
import { ApiError, friendlyError } from "../../src/reposcout/static/js/core.js";

test("rate limits with a known wait use the limit copy, rounded up to whole minutes", () => {
  assert.equal(friendlyError(new ApiError(429, "too_many_requests", "server text", 61)), "You've hit the limit. Try again in 2 min.");
  assert.equal(friendlyError(new ApiError(429, "queue_full", "server text", 60)), "You've hit the limit. Try again in 1 min.");
  assert.equal(friendlyError(new ApiError(429, "too_many_requests", "server text", 5)), "You've hit the limit. Try again in 1 min.");
});

test("other 429s keep the server's own message", () => {
  assert.equal(friendlyError(new ApiError(429, "one_at_a_time", "Wait for your previous request to finish, then try again.", 5)),
    "Wait for your previous request to finish, then try again.");
  assert.equal(friendlyError(new ApiError(429, "provider_busy", "Groq is rate limiting your key.")), "Groq is rate limiting your key.");
  assert.equal(friendlyError(new ApiError(429, "github_rate_limit", "GitHub's hourly limit was reached.")), "GitHub's hourly limit was reached.");
  assert.equal(friendlyError(new ApiError(429, "too_many_requests", "No wait given.")), "No wait given.");
});

test("busy, network and everything else", () => {
  assert.equal(friendlyError(new ApiError(503, "busy", "server text", 10)), "RepoScout is busy. Try again in a moment.");
  assert.equal(friendlyError(new ApiError(0, "network", "x")), "Can't reach the server. Check your connection and try again.");
  assert.equal(friendlyError(new ApiError(400, "bad_request", "That isn't a GitHub link.")), "That isn't a GitHub link.");
});
