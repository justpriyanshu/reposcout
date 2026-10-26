// The top bar (spec §6.2): All tools menu, direct tool links, key status, theme, and the account (sign in with GitHub).
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Icon, LogoMark, ToolIcon } from "@/components/Icon";
import { ThemeButton } from "@/components/ThemeButton";
import { Button } from "@/components/ui/button";
import { CATEGORIES, TOOLS } from "@/lib/tools";
import { Account } from "./Account";
import { openKeyDialog, useConfig, useSavedKey } from "./settings";

const DIRECT = [["ask", "Ask"], ["find", "Find code"], ["review", "Review a PR"]] as const;

function Menu({ currentTool, onKeyDown, menuRef, close }: {
  currentTool: string | null; onKeyDown: (e: ReactKeyboardEvent) => void; menuRef: React.RefObject<HTMLElement | null>; close: () => void;
}) {
  return (
    // Picking the tool already open changes no hash, so close on the click itself.
    <nav className="menu" id="toolsMenu" aria-label="All tools" ref={menuRef} onKeyDown={onKeyDown}
      onClick={(e) => { if ((e.target as Element).closest("a[href]")) close(); }}>
      {CATEGORIES.map((cat) => (
        <div key={cat}>
          <h4>{cat}</h4>
          {TOOLS.filter((t) => t.category === cat).map((t) => t.live
            // aria-current here too: on phones the direct links that carry it are hidden.
            ? <a key={t.id} href={`#/${t.id}`} data-nav={t.id} aria-current={t.id === currentTool ? "page" : undefined}><ToolIcon tool={t} size="sm" />{t.name}</a>
            : <a key={t.id} className="soon" aria-disabled="true"><ToolIcon tool={t} size="sm" />{t.name}<span className="pill">Soon</span></a>)}
        </div>
      ))}
    </nav>
  );
}

export function Nav({ currentTool }: { currentTool: string | null }) {
  const config = useConfig();
  const hasKey = !!useSavedKey();
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLElement>(null);
  const burger = useRef<HTMLButtonElement>(null);
  const menuBtn = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const restore = useRef(false);

  const isMenuButton = (el: EventTarget | null) => el instanceof Node && (!!burger.current?.contains(el) || !!menuBtn.current?.contains(el));
  const close = (restoreFocus = false) => { restore.current = restoreFocus; setOpen(false); };
  const toggle = (btn: HTMLButtonElement | null) => {
    if (open) { close(); return; }
    opener.current = btn;
    setOpen(true);
  };

  useEffect(() => {
    if (!open) {
      if (restore.current) opener.current?.focus();
      restore.current = false;
      return;
    }
    menuRef.current?.querySelector<HTMLElement>("a[href]")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); close(true); } };
    const outside = (e: Event) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node) && !isMenuButton(e.target)) close();
    };
    // Leaving the menu by Tab closes it; so does any navigation.
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
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  function onMenuKey(e: ReactKeyboardEvent) {
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>("a[href]") || [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); items[(n + items.length) % items.length]?.focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(items.length - 1);
  }

  return (
    <header className="nav"><div className="wrap">
      <Button ref={burger} variant="quiet" size="icon" className="burger" data-menu-btn="" aria-label="All tools" aria-expanded={open} aria-controls="toolsMenu"
        onClick={() => toggle(burger.current)}>
        <Icon name="menu" size={20} />
      </Button>
      <a className="logo" href="#/" aria-label="RepoScout home"><LogoMark />RepoScout</a>
      <div className="navpos" id="navpos">
        <button ref={menuBtn} className="navlink menu-btn" type="button" data-menu-btn="" aria-expanded={open} aria-controls="toolsMenu"
          onClick={() => toggle(menuBtn.current)}>
          All tools <Icon name="chevron" size={14} strokeWidth={2.2} />
        </button>
        {open ? <Menu currentTool={currentTool} onKeyDown={onMenuKey} menuRef={menuRef} close={() => close()} /> : null}
      </div>
      {DIRECT.map(([id, label]) => (
        <a key={id} className="navlink direct" href={`#/${id}`} data-nav={id} aria-current={currentTool === id ? "page" : undefined}>{label}</a>
      ))}
      <div className="right">
        <Button variant="quiet" className="keystate" id="keyBtn" aria-label={hasKey ? `${config.provider} key settings` : "Add your API key"}
          onClick={() => openKeyDialog()}>
          <span className={hasKey ? "dot" : "dot off"} id="keyDot" /><span className="lbl" id="keyLabel">{hasKey ? `${config.provider} key` : "Add API key"}</span>
        </Button>
        <ThemeButton />
        <Account />
      </div>
    </div></header>
  );
}
