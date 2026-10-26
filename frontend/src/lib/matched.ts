// Which lines of a search hit to mark: the ones containing a word from the query. Pure: no DOM.

/** Distinct lower-case words of 3 or more letters or digits in the query. */
export function queryWords(query: string): string[] {
  const words = String(query).toLowerCase().match(/[\p{L}\p{N}_]+/gu) || [];
  return [...new Set(words.filter((w) => [...w].length >= 3))];
}

/** Line numbers (counting from `firstLine`) of the lines in `text` that contain any query word, ignoring case. */
export function matchedLines(text: string, query: string, firstLine = 1): number[] {
  const words = queryWords(query);
  if (!words.length) return [];
  const out: number[] = [];
  String(text).split("\n").forEach((line, i) => {
    const low = line.toLowerCase();
    if (words.some((w) => low.includes(w))) out.push(firstLine + i);
  });
  return out;
}
