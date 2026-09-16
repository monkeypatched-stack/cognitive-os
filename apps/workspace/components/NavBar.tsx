"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useAuthStore } from "../lib/authStore";

// Routes that use the Foxglove-style dark telemetry shell (globals.css's
// .fg-shell) -- the nav bar switches to match so there's no light-header-
// over-dark-content seam. Every other route keeps the app's normal
// light/auto theme untouched.
const DARK_SHELL_ROUTES = ["/drone-flight"];

export function NavBar() {
  const status = useAuthStore((s) => s.status);
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const hydrate = useAuthStore((s) => s.hydrate);
  const pathname = usePathname();
  const isDarkShell = DARK_SHELL_ROUTES.some((route) => pathname?.startsWith(route));

  // NavBar is the one component every page (including /login) always
  // renders via layout.tsx -- the single, reliable post-mount point to
  // read localStorage's persisted session, exactly once, after hydration
  // has already matched the server's render (see authStore.ts's own
  // comment on why this can't happen at module-eval time instead).
  useEffect(() => {
    hydrate();
  }, [hydrate]);

  if (status !== "authenticated") return null;

  return (
    <nav className={isDarkShell ? "navbar navbar--dark" : "navbar"}>
      <span className="navbar-brand">CognitiveOS Workspace</span>
      <div className="navbar-user">
        <span>{user?.email}</span>
        <button type="button" onClick={logout}>
          Sign out
        </button>
      </div>
    </nav>
  );
}
