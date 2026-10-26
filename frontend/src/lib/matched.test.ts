import { expect, test } from "vitest";
import { matchedLines, queryWords } from "./matched";

const code = "def send(self, request):\n    adapter = self.get_adapter(request.url)\n    return adapter.send(request)\n";

test("words of three or more letters, lower-cased and de-duplicated", () => {
  expect(queryWords("Send a Request, send!")).toEqual(["send", "request"]);
  expect(queryWords("a to of")).toEqual([]);
});

test("lines containing any query word, numbered from firstLine", () => {
  expect(matchedLines(code, "adapter", 10)).toEqual([11, 12]);
  expect(matchedLines(code, "ADAPTER")).toEqual([2, 3]);
  expect(matchedLines(code, "where is the retry logic", 1)).toEqual([]);
});

test("nothing is marked when the query has no usable word", () => {
  expect(matchedLines(code, "a b")).toEqual([]);
  expect(matchedLines("", "send")).toEqual([]);
});

test("regex characters in the query are plain text", () => {
  expect(matchedLines("x = (a+b)\nfoo.*", ".*(", 1)).toEqual([]);
  expect(matchedLines("x = foo.bar", "foo.bar", 1)).toEqual([1]);
});
