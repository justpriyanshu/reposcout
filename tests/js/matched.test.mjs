import { test } from "node:test";
import assert from "node:assert/strict";
import { matchedLines, queryWords } from "../../src/reposcout/static/js/matched.js";

const code = "def send(self, request):\n    adapter = self.get_adapter(request.url)\n    return adapter.send(request)\n";

test("words of three or more letters, lower-cased and de-duplicated", () => {
  assert.deepEqual(queryWords("Send a Request, send!"), ["send", "request"]);
  assert.deepEqual(queryWords("a to of"), []);
});

test("lines containing any query word, numbered from firstLine", () => {
  assert.deepEqual(matchedLines(code, "adapter", 10), [11, 12]);
  assert.deepEqual(matchedLines(code, "ADAPTER"), [2, 3]);
  assert.deepEqual(matchedLines(code, "where is the retry logic", 1), []);
});

test("nothing is marked when the query has no usable word", () => {
  assert.deepEqual(matchedLines(code, "a b"), []);
  assert.deepEqual(matchedLines("", "send"), []);
});

test("regex characters in the query are plain text", () => {
  assert.deepEqual(matchedLines("x = (a+b)\nfoo.*", ".*(", 1), []);
  assert.deepEqual(matchedLines("x = foo.bar", "foo.bar", 1), [1]);
});
