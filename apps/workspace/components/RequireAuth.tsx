"use client";

// App Router equivalent of living-world-explorer/src/routes/RequireAuth.tsx
// (that one uses react-router's <Navigate>; this uses next/navigation's
// useRouter, since there's no client-side <Navigate> element here).
import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "../lib/authStore";

export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);
  const hydrated = useAuthStore((s) => s.hydrated);
  const hydrate = useAuthStore((s) => s.hydrate);
  const router = useRouter();

  // Calls the SAME idempotent hydrate() NavBar.tsx also calls on mount --
  // deliberately not relying on NavBar's effect running first (sibling
  // effect ordering is an implementation detail, not a contract): this
  // component is self-sufficient regardless of mount order. Not
  // redirecting until `hydrated` is true is what actually fixes the race
  // a plain `status !== "authenticated"` check would have -- without it,
  // status still starts "anonymous" (matching the server) on every first
  // render, so this effect would fire and redirect an already-logged-in
  // user to /login before hydrate() had a chance to restore their session.
  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (hydrated && status !== "authenticated") router.replace("/login");
  }, [hydrated, status, router]);

  if (!hydrated || status !== "authenticated") return null;
  return <>{children}</>;
}
