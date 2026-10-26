// Shorten a file path for a one-line label. Pure: no DOM.

/**
 * Cut a long path in the middle so the file name (where paths differ) stays readable:
 * "packages/…/reconcilers/Controller.java". Keeps the first folder when it fits, then as many
 * trailing folders as fit in `max` characters. The file name is never cut; CSS truncates what is left.
 */
export function shortPath(path: string | null | undefined, max = 48): string {
  const text = String(path || "");
  if (text.length <= max) return text;
  const parts = text.split("/");
  if (parts.length < 2) return text;
  const file = parts.pop() as string;
  const head = parts.shift() as string;
  let tail = file;
  while (parts.length && `${head}/…/${parts[parts.length - 1]}/${tail}`.length <= max) tail = `${parts.pop()}/${tail}`;
  const cut = `${head}/…/${tail}`;
  return cut.length <= max ? cut : `…/${tail}`;
}
