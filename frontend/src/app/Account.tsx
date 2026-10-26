// The nav's account control: "Sign in with GitHub" when signed out (and the server offers it), or
// the avatar and login opening a small menu with "Your GitHub repositories" and "Sign out".
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { GitHubMark, Icon } from "@/components/Icon";
import { Button } from "@/components/ui/button";
import { initialsOf, signInUrl } from "@/lib/account";
import { friendlyError } from "@/lib/api";
import { toast } from "@/lib/toast";
import { signOut, useMe } from "./account";

/** The person's GitHub avatar; their initials if it doesn't load (offline, or blocked). */
export function Avatar({ src, login, size = 24 }: { src: string; login: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return src && !failed
    ? <img className="avatar" src={src} alt="" width={size} height={size} referrerPolicy="no-referrer" onError={() => setFailed(true)} />
    : <span className="avatar" aria-hidden="true" style={{ width: size, height: size }}>{initialsOf(login)}</span>;
}

/** A link that signs in with GitHub and comes back to this page and route. Read at render: the nav re-renders on every navigation. */
export function SignInLink({ className, linkRef, label = "Sign in with GitHub" }: {
  className?: string; linkRef?: React.Ref<HTMLAnchorElement>; label?: string;
}) {
  return (
    <Button asChild className={className}>
      <a ref={linkRef} href={signInUrl(location.pathname, location.hash)} aria-label={label}><GitHubMark size={16} /><span className="lbl">{label}</span></a>
    </Button>
  );
}

export function Account() {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const signIn = useRef<HTMLAnchorElement>(null);
  const restore = useRef(false);
  const focusSignIn = useRef(false);
  const user = me.user;

  const close = (restoreFocus = false) => { restore.current = restoreFocus; setOpen(false); };

  useEffect(() => {
    if (!open) {
      if (restore.current) btn.current?.focus();
      restore.current = false;
      return;
    }
    menu.current?.querySelector<HTMLElement>("a[href], button")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); close(true); } };
    const outside = (e: Event) => {
      const t = e.target as Node;
      if (menu.current && !menu.current.contains(t) && !btn.current?.contains(t)) close();
    };
    const onHash = () => close();
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside);
    window.addEventListener("hashchange", onHash);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", outside);
      window.removeEventListener("hashchange", onHash);
    };
  }, [open]);

  // After signing out the account button is gone: keep keyboard focus in the same place.
  useEffect(() => {
    if (user || !focusSignIn.current) return;
    focusSignIn.current = false;
    (signIn.current || document.getElementById("view"))?.focus();
  }, [user]);

  function onMenuKey(e: ReactKeyboardEvent) {
    const items = [...(menu.current?.querySelectorAll<HTMLElement>("a[href], button") || [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); items[(n + items.length) % items.length]?.focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(items.length - 1);
  }

  async function doSignOut() {
    setBusy(true);
    focusSignIn.current = true;
    try {
      await signOut();
      close();
      toast("Signed out.");
    } catch (err) {
      focusSignIn.current = false;
      toast(friendlyError(err), { kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  if (!user) return me.github_login ? <SignInLink className="signin" linkRef={signIn} /> : null;

  return (
    <div className="acctpos">
      <Button ref={btn} variant="quiet" className="acct" aria-label={`Account: ${user.login}`} aria-expanded={open} aria-controls="accountMenu"
        onClick={() => (open ? close() : setOpen(true))}>
        <Avatar src={user.avatar_url} login={user.login} /><span className="lbl">{user.login}</span>
        <Icon name="chevron" size={14} strokeWidth={2.2} />
      </Button>
      {open ? (
        // Picking "Your GitHub repositories" while on it changes no hash, so close on the click itself.
        <div className="acctmenu" id="accountMenu" ref={menu} onKeyDown={onMenuKey}
          onClick={(e) => { if ((e.target as Element).closest("a[href]")) close(); }}>
          <div className="who">
            <Avatar src={user.avatar_url} login={user.login} size={36} />
            <span><b>{user.name || user.login}</b><span>{`@${user.login}`}</span></span>
          </div>
          <a href="#/github"><GitHubMark size={16} />Your GitHub repositories</a>
          <button type="button" disabled={busy} onClick={() => void doSignOut()}><Icon name="logout" size={16} />Sign out</button>
        </div>
      ) : null}
    </div>
  );
}
