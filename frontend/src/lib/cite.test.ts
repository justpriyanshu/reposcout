import { expect, test } from "vitest";
import { addCitations } from "./cite";
import { markdown } from "./markdown";

const sources = [
  { ref: 1, path: "auth/session.py", start_line: 3, end_line: 9, url: "https://github.com/o/n/blob/c/auth/session.py#L3-L9" },
  { ref: 2, path: "a&b.py", start_line: 1, end_line: 2 },
];

test("known refs in text become links to the code", () => {
  expect(addCitations(markdown("Tokens are checked [1]."), sources)).toBe(
    '<p>Tokens are checked <a class="cite" href="https://github.com/o/n/blob/c/auth/session.py#L3-L9" target="_blank" rel="noopener" title="auth/session.py:3-9">1</a>.</p>');
});

test("unknown refs, code and tags are left alone", () => {
  const html = markdown("See [7] and `x[1]`\n\n```\na[1]\n```");
  expect(addCitations(html, sources)).toBe(html);
  expect(addCitations("<p>[1]</p>", [])).toBe("<p>[1]</p>");
});

test("without a URL (the local page) the badge is a span, and titles are escaped", () => {
  expect(addCitations("<p>[2]</p>", sources, false)).toBe('<p><span class="cite" title="a&amp;b.py:1-2">2</span></p>');
  expect(addCitations("<p>[1]</p>", sources, false)).toBe('<p><span class="cite" title="auth/session.py:3-9">1</span></p>');
});
