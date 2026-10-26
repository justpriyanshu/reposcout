import { expect, test } from "vitest";
import { SIGNED_OUT, asMe, initialsOf, signInUrl, takeLoginError } from "./account";

test("the sign-in link carries the current path and route as next", () => {
  expect(signInUrl("/", "#/ask/pallets/flask")).toBe("/auth/github/login?next=%2F%23%2Fask%2Fpallets%2Fflask");
  expect(signInUrl("/static/app.html", "")).toBe("/auth/github/login?next=%2Fstatic%2Fapp.html");
  expect(signInUrl("", "")).toBe("/auth/github/login?next=%2F");
});

test("login_error is taken off the query, other parameters stay", () => {
  expect(takeLoginError("?login_error=GitHub%20said%20no")).toEqual({ message: "GitHub said no", search: "" });
  expect(takeLoginError("?a=1&login_error=Nope&b=2")).toEqual({ message: "Nope", search: "?a=1&b=2" });
  expect(takeLoginError("?a=1")).toEqual({ message: null, search: "?a=1" });
  expect(takeLoginError("")).toEqual({ message: null, search: "" });
});

test("an empty or overlong login_error still gives a sensible message", () => {
  expect(takeLoginError("?login_error=").message).toBe("Couldn't sign in with GitHub. Try again.");
  expect(takeLoginError("?login_error=+++").message).toBe("Couldn't sign in with GitHub. Try again.");
  expect(takeLoginError(`?login_error=${"x".repeat(500)}`).message).toHaveLength(200);
});

test("asMe accepts the server's reply and tolerates anything else", () => {
  const user = { login: "octocat", name: "The Octocat", avatar_url: "https://a/x.png", html_url: "https://github.com/octocat" };
  expect(asMe({ user, github_login: true })).toEqual({ user, github_login: true });
  expect(asMe({ user: null, github_login: true })).toEqual({ user: null, github_login: true });
  expect(asMe({ user: { login: "x" } })).toEqual({ user: { login: "x", name: "", avatar_url: "", html_url: "https://github.com/x" }, github_login: false });
  expect(asMe({ user: { name: "no login" }, github_login: "yes" })).toEqual(SIGNED_OUT);
  expect(asMe(null)).toEqual(SIGNED_OUT);
  expect(asMe("nope")).toEqual(SIGNED_OUT);
});

test("initials for an avatar that didn't load", () => {
  expect(initialsOf("octocat")).toBe("OC");
  expect(initialsOf("a-b")).toBe("AB");
  expect(initialsOf("--")).toBe("?");
});
