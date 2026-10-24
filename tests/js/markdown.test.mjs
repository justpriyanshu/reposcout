import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeHtml, inline, markdown } from "../../src/reposcout/static/js/markdown.js";

test("raw HTML is escaped", () => {
  const out = markdown("<img src=x onerror=alert(1)>");
  assert.match(out, /&lt;img/);
  assert.doesNotMatch(out, /<img/);
});

test("only http(s) links become anchors", () => {
  assert.doesNotMatch(markdown("[x](javascript:alert(1))"), /href="javascript/);
  assert.match(markdown("[x](https://example.com)"), /<a href="https:\/\/example.com" target="_blank" rel="noopener">x<\/a>/);
});

test("fenced code becomes an escaped pre block", () => {
  const out = markdown("```py\nif a < b:\n    pass\n```");
  assert.equal(out, "<pre><code>if a &lt; b:\n    pass</code></pre>");
});

test("pipe tables become tables", () => {
  const out = markdown("| a | b |\n|---|---|\n| 1 | 2 |");
  assert.equal(out, "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>");
});

test("inline formatting, headings and lists", () => {
  assert.equal(inline(escapeHtml("**b** and `x<y`")), "<strong>b</strong> and <code>x&lt;y</code>");
  assert.equal(markdown("# Title"), "<h3>Title</h3>");
  assert.equal(markdown("- one\n- two"), "<ul><li>one</li><li>two</li></ul>");
  assert.equal(escapeHtml(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});
