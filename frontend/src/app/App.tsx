// The public app: start-up, the hash router (old-link redirects, lazy views, focus and titles on navigation).
import { Component, Suspense, lazy, useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { StateBlock } from "@/components/blocks";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { legacyRedirect, parseRoute, type Route } from "@/lib/routes";
import { tool } from "@/lib/tools";
import { showLoginError, useMe } from "./account";
import { KeyDialog } from "./KeyDialog";
import { Nav } from "./Nav";
import { useConfig } from "./settings";

const Home = lazy(() => import("./views/Home"));
const Ask = lazy(() => import("./views/Ask"));
const Find = lazy(() => import("./views/Find"));
const Review = lazy(() => import("./views/Review"));
const GitHub = lazy(() => import("./views/GitHub"));

function titleFor(route: Route): string {
  if (route.view === "home") return "RepoScout: free tools for GitHub repositories";
  if (route.view === "github") return "Your GitHub repositories · RepoScout";
  const name = tool(route.tool)!.name;
  if (route.tool === "review" && route.pull) return `Review #${route.pull} · ${route.owner}/${route.name} · RepoScout`;
  return route.owner ? `${name} · ${route.owner}/${route.name} · RepoScout` : `${name} · RepoScout`;
}

/** A view that failed to load or render (e.g. RepoScout was updated while the page was open). */
class ViewBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(err: unknown) { console.error(err); }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="wrap">
        <StateBlock icon="alert" tone="error" title="This page didn't load"
          text="Something went wrong on our side, or RepoScout was just updated. Reload to try again."
          actions={<Button onClick={() => location.reload()}>Reload</Button>} />
      </div>
    );
  }
}

/** Marks <main> busy while a view's code loads. */
function Busy() {
  useEffect(() => {
    const main = document.getElementById("view");
    main?.setAttribute("aria-busy", "true");
    return () => main?.removeAttribute("aria-busy");
  }, []);
  return null;
}

function View({ route }: { route: Route }) {
  if (route.view === "home") return <Home />;
  if (route.view === "github") return <GitHub />;
  if (!route.owner || !route.name) {
    const Tool = route.tool === "ask" ? Ask : route.tool === "find" ? Find : Review;
    return <Tool route={{ tool: route.tool }} />;
  }
  const r = { tool: route.tool, owner: route.owner, name: route.name };
  if (route.tool === "ask") return <Ask route={r} />;
  if (route.tool === "find") return <Find route={r} />;
  return <Review route={{ ...r, pull: route.pull }} />;
}

/** The current hash and a counter bumped on every hashchange (also the same-hash "reload"). */
function useHashNav() {
  const [nav, setNav] = useState({ hash: location.hash, count: 0 });
  useEffect(() => {
    const go = () => {
      const redirect = legacyRedirect(location.hash);
      if (redirect) { location.replace(redirect); return; } // fires hashchange; Back skips the old link
      setNav((n) => ({ hash: location.hash, count: n.count + 1 }));
    };
    const first = legacyRedirect(location.hash);
    if (first) location.replace(first);
    window.addEventListener("hashchange", go);
    return () => window.removeEventListener("hashchange", go);
  }, []);
  return nav;
}

export function App() {
  useConfig(); // load the provider name and key link early
  useMe(); // and who is signed in
  useEffect(() => { showLoginError(); }, []); // after <Toaster /> has mounted (child effects run first)
  const { hash, count } = useHashNav();
  const route = parseRoute(hash);

  useLayoutEffect(() => { document.title = titleFor(route); });
  useEffect(() => {
    if (!count) return;
    document.getElementById("view")?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [count]);

  return (
    <>
      {/* A button, not a fragment link to #view: in a hash router that would navigate home. */}
      <button className="skip" type="button" id="skipLink" onClick={() => document.getElementById("view")?.focus()}>Skip to content</button>
      <Nav currentTool={route.view === "tool" ? route.tool : null} />
      <main id="view" tabIndex={-1}>
        <ViewBoundary key={count}>
          <Suspense fallback={<Busy />}>
            <View route={route} />
          </Suspense>
        </ViewBoundary>
      </main>
      <footer className="site"><div className="wrap">RepoScout reads public code. Your API key stays in your browser and is sent only with your own requests.</div></footer>
      <Toaster />
      <KeyDialog />
    </>
  );
}
