export const fmt = (n: number | null | undefined): string => Number(n || 0).toLocaleString();
export const plural = (n: number, word: string): string => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;
export const seconds = (ms: number | null | undefined): string => `${((ms || 0) / 1000).toFixed(1)}s`;
