import { expect, test } from "vitest";
import { escapeHtml, inline, markdown } from "./markdown";

test("raw HTML is escaped", () => {
  const out = markdown("<img src=x onerror=alert(1)>");
  expect(out).toMatch(/&lt;img/);
  expect(out).not.toMatch(/<img/);
});

test("only http(s) links become anchors", () => {
  expect(markdown("[x](javascript:alert(1))")).not.toMatch(/href="javascript/);
  expect(markdown("[x](https://example.com)")).toMatch(/<a href="https:\/\/example.com" target="_blank" rel="noopener">x<\/a>/);
});

test("fenced code becomes an escaped pre block", () => {
  expect(markdown("```py\nif a < b:\n    pass\n```")).toBe("<pre><code>if a &lt; b:\n    pass</code></pre>");
});

test("pipe tables become tables", () => {
  expect(markdown("| a | b |\n|---|---|\n| 1 | 2 |"))
    .toBe("<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>");
});

test("inline formatting, headings and lists", () => {
  expect(inline(escapeHtml("**b** and `x<y`"))).toBe("<strong>b</strong> and <code>x&lt;y</code>");
  expect(markdown("# Title")).toBe("<h3>Title</h3>");
  expect(markdown("- one\n- two")).toBe("<ul><li>one</li><li>two</li></ul>");
  expect(escapeHtml(`&<>"'`)).toBe("&amp;&lt;&gt;&quot;&#39;");
});
