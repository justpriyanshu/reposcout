import { expect, test } from "vitest";
import { filterGithubRepos, timeAgo, type GithubRepo } from "./github";

const REPOS: GithubRepo[] = [
  { full_name: "octocat/Hello-World", description: "My first repository", language: "Python" },
  { full_name: "octocat/spoon-knife", description: null, language: "HTML", fork: true },
  { full_name: "octocat/linguist", description: "Language Savant", language: null },
];

test("no query keeps every repository in order", () => {
  expect(filterGithubRepos(REPOS, "")).toEqual(REPOS);
  expect(filterGithubRepos(REPOS, "   ")).toEqual(REPOS);
  expect(filterGithubRepos(null, "x")).toEqual([]);
});

test("the filter matches name, description and language, ignoring case", () => {
  expect(filterGithubRepos(REPOS, "hello").map((r) => r.full_name)).toEqual(["octocat/Hello-World"]);
  expect(filterGithubRepos(REPOS, "SAVANT").map((r) => r.full_name)).toEqual(["octocat/linguist"]);
  expect(filterGithubRepos(REPOS, "html").map((r) => r.full_name)).toEqual(["octocat/spoon-knife"]);
  expect(filterGithubRepos(REPOS, "octocat/")).toHaveLength(3);
  expect(filterGithubRepos(REPOS, "zzz")).toEqual([]);
});

test("time ago, in words", () => {
  const now = Date.parse("2026-10-26T12:00:00Z");
  expect(timeAgo("2026-10-26T01:00:00Z", now)).toBe("today");
  expect(timeAgo("2026-10-25T06:00:00Z", now)).toBe("yesterday");
  expect(timeAgo("2026-10-21T12:00:00Z", now)).toBe("5 days ago");
  expect(timeAgo("2026-10-05T12:00:00Z", now)).toBe("3 weeks ago");
  expect(timeAgo("2026-06-01T12:00:00Z", now)).toBe("4 months ago");
  expect(timeAgo("2025-10-01T12:00:00Z", now)).toBe("1 year ago");
  expect(timeAgo("2027-01-01T00:00:00Z", now)).toBe("today"); // clock skew: never "in the future"
  expect(timeAgo("not a date", now)).toBe("");
  expect(timeAgo(null, now)).toBe("");
});
