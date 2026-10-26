// Who is signed in (GET /api/me), signing out, and the error a failed sign-in comes back with.
// Signing in is a plain link (lib/account signInUrl): the server does the GitHub round trip.
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SIGNED_OUT, asMe, takeLoginError, type Me } from "@/lib/account";
import { toast } from "@/lib/toast";
import { queryClient } from "./queryClient";

export const ME_KEY = ["me"] as const;

// A failure (or an older server without /api/me) means "signed out, no sign-in": the app works as before.
export const meQuery = {
  queryKey: ME_KEY,
  queryFn: async (): Promise<Me> => { try { return asMe(await api("/api/me")); } catch { return SIGNED_OUT; } },
  staleTime: Infinity,
};

/** The signed-in person (or null) and whether sign-in is offered; `loaded` is false until /api/me answers. */
export function useMe(): Me & { loaded: boolean } {
  const { data } = useQuery(meQuery);
  return { ...(data || SIGNED_OUT), loaded: !!data };
}

/**
 * POST /api/logout, then start over as an anonymous visitor: everything cached was this person's
 * (their list, their repositories), so it is all fetched again. Throws ApiError.
 */
export async function signOut(): Promise<void> {
  await api("/api/logout", { method: "POST", body: {} });
  queryClient.setQueryData<Me>(ME_KEY, (m) => ({ github_login: m?.github_login ?? false, user: null }));
  void queryClient.invalidateQueries({ queryKey: ME_KEY });
  void queryClient.resetQueries({ predicate: (q) => q.queryKey[0] !== ME_KEY[0] && q.queryKey[0] !== "config" });
}

let loginErrorShown = false;

/** After a failed sign-in the server redirects to "/?login_error=…": show it once, and take it off the URL. */
export function showLoginError(): void {
  if (loginErrorShown) return;
  loginErrorShown = true;
  const { message, search } = takeLoginError(location.search);
  if (message === null) return;
  history.replaceState(history.state, "", location.pathname + search + location.hash);
  toast(message, { kind: "error" });
}
