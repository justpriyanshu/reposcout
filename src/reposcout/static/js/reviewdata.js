// Review a PR: the pure parts (input parsing, filters, cache key) and the one-shot approval. No DOM, tested under Node.
import { parseLink } from "./links.js";

export const SEVERITIES = ["critical", "major", "minor", "nit"];

/** "1234", "#1234" or a PR link -> {owner, name, pull}; the number forms use this repository. Otherwise null. */
export function parsePrInput(text, owner, name) {
  const v = String(text || "").trim();
  const num = v.match(/^#?(\d+)$/);
  if (num) return +num[1] > 0 ? { owner, name, pull: +num[1] } : null;
  const link = parseLink(v);
  return link && link.pull ? link : null;
}

/** The severities shown at first: everything but nits, unless nits are the only findings. */
export function defaultSeverities(counts = {}) {
  const total = SEVERITIES.reduce((a, s) => a + (counts[s] || 0), 0);
  return new Set(SEVERITIES.filter((s) => s !== "nit" || total === (counts.nit || 0)));
}

/** Where a finished review is kept in sessionStorage: one per pull request and model. */
export const reviewCacheKey = (owner, name, pull, model) => `rs.review.${owner}/${name}#${pull}.${model || ""}`.toLowerCase();

// Reviews spend the visitor's API quota, so they only start on the visitor's own click: a form in
// this app, or "Start review" when a link was opened directly. The approval lives in memory and is
// used once.
let approved = "";
const idOf = (owner, name, pull) => `${owner}/${name}#${pull}`.toLowerCase();

/** Approve one review for this tab (the visitor just asked for it). */
export function approveReview(owner, name, pull) { approved = idOf(owner, name, pull); }

/** True when this review was approved, and still is (does not use it up). */
export const isApproved = (owner, name, pull) => approved === idOf(owner, name, pull);

/** Use up the approval. */
export function consumeApproval() { approved = ""; }
