import { afterEach, expect, test, vi } from "vitest";
import { fmt, plural, seconds } from "./format";
import { store } from "./store";
import { cn } from "./utils";

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string) { return this.data.has(k) ? this.data.get(k)! : null; }
  setItem(k: string, v: string) { this.data.set(k, v); }
  removeItem(k: string) { this.data.delete(k); }
}

afterEach(() => vi.unstubAllGlobals());

test("store keeps JSON values in localStorage, or sessionStorage when asked", () => {
  const local = new MemoryStorage();
  const session = new MemoryStorage();
  vi.stubGlobal("localStorage", local);
  vi.stubGlobal("sessionStorage", session);
  store.set("rs.theme", "dark");
  store.set("rs.key", "gsk_x", true);
  expect(local.getItem("rs.theme")).toBe('"dark"'); // the format the old UI wrote, so saved settings carry over
  expect(store.get("rs.theme")).toBe("dark");
  expect(store.get("rs.key")).toBeNull();
  expect(store.get("rs.key", true)).toBe("gsk_x");
  local.setItem("rs.bad", "{not json");
  expect(store.get("rs.bad")).toBeNull();
  store.del("rs.key");
  store.del("rs.theme");
  expect(store.get("rs.key", true)).toBeNull();
  expect(store.get("rs.theme")).toBeNull();
});

test("store never throws when storage is blocked", () => {
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  vi.stubGlobal("localStorage", blocked);
  vi.stubGlobal("sessionStorage", blocked);
  expect(store.get("rs.theme")).toBeNull();
  expect(() => store.set("rs.theme", "dark")).not.toThrow();
  expect(() => store.del("rs.theme")).not.toThrow();
});

test("number formats", () => {
  expect(fmt(1234)).toBe((1234).toLocaleString());
  expect(fmt(undefined)).toBe("0");
  expect(plural(1, "file")).toBe("1 file");
  expect(plural(3, "file")).toBe("3 files");
  expect(seconds(2300)).toBe("2.3s");
  expect(seconds(undefined)).toBe("0.0s");
});

test("cn joins classes and lets later Tailwind utilities win", () => {
  expect(cn("btn", false, "primary")).toBe("btn primary");
  expect(cn("tw:p-2", "tw:p-4")).toBe("tw:p-4");
});
