import { describe, expect, test } from "vitest";
import { ApiError, friendlyError } from "./api";

describe("friendlyError", () => {
  test("rate limits with a known wait use the limit copy, rounded up to whole minutes", () => {
    expect(friendlyError(new ApiError(429, "too_many_requests", "server text", 61))).toBe("You've hit the limit. Try again in 2 min.");
    expect(friendlyError(new ApiError(429, "queue_full", "server text", 60))).toBe("You've hit the limit. Try again in 1 min.");
    expect(friendlyError(new ApiError(429, "too_many_requests", "server text", 5))).toBe("You've hit the limit. Try again in 1 min.");
  });

  test("other 429s keep the server's own message", () => {
    expect(friendlyError(new ApiError(429, "one_at_a_time", "Wait for your previous request to finish, then try again.", 5)))
      .toBe("Wait for your previous request to finish, then try again.");
    expect(friendlyError(new ApiError(429, "provider_busy", "Groq is rate limiting your key."))).toBe("Groq is rate limiting your key.");
    expect(friendlyError(new ApiError(429, "github_rate_limit", "GitHub's hourly limit was reached."))).toBe("GitHub's hourly limit was reached.");
    expect(friendlyError(new ApiError(429, "too_many_requests", "No wait given."))).toBe("No wait given.");
  });

  test("busy, network and everything else", () => {
    expect(friendlyError(new ApiError(503, "busy", "server text", 10))).toBe("RepoScout is busy. Try again in a moment.");
    expect(friendlyError(new ApiError(0, "network", "x"))).toBe("Can't reach the server. Check your connection and try again.");
    expect(friendlyError(new ApiError(400, "bad_request", "That isn't a GitHub link."))).toBe("That isn't a GitHub link.");
  });
});
