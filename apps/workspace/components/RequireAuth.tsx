"use client";

// App Router equivalent of living-world-explorer/src/routes/RequireAuth.tsx
// (that one uses react-router's <Navigate>; this uses next/navigation's
// useRouter, since there's no client-side <Navigate> element here).
import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "../lib/authStore";

export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);
  const router = useRouter();

  useEffect(() => {
    if (status !== "authenticated") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated") return null;
  return <>{children}</>;
}
