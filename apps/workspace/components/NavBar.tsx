"use client";

import Link from "next/link";
import { useAuthStore } from "../lib/authStore";

export function NavBar() {
  const status = useAuthStore((s) => s.status);
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  if (status !== "authenticated") return null;

  return (
    <nav className="navbar">
      <span className="navbar-brand">CognitiveOS Workspace</span>
      <div className="navbar-links">
        <Link href="/goals">Goals</Link>
        <Link href="/approvals">Approvals</Link>
        <Link href="/voice">Voice</Link>
        <Link href="/drone-flight">Drone Flight</Link>
        <Link href="/drone-mission">Drone Mission</Link>
      </div>
      <div className="navbar-user">
        <span>{user?.email}</span>
        <button type="button" onClick={logout}>
          Sign out
        </button>
      </div>
    </nav>
  );
}
