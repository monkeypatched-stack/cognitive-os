"use client";

// Port of living-world-explorer/src/store/authStore.ts's pattern for this
// app. One deliberate simplification: that version also displays a live,
// locally-computed TOTP code during MFA challenge/setup — its own comment
// flags that as "a deliberate, temporary, personal/dev-cluster
// convenience... a real multi-user product would never do this." This is
// meant to be exactly that kind of product, so this port always prompts
// for a code from a separate authenticator app/device instead.
import { create } from "zustand";

const STORAGE_KEY = "workspace.auth";
const AUTH_BASE = "/api/v1/auth";

export type AuthStatus = "anonymous" | "needs_mfa_challenge" | "needs_mfa_setup" | "authenticated";

export interface AuthUser {
  email: string;
  role: string;
}

export interface MfaEnrollment {
  secret: string;
  otpauthUri: string;
  backupCodes: string[];
}

interface AuthState {
  status: AuthStatus;
  token: string | null;
  user: AuthUser | null;
  mfaChallengeToken: string | null;
  mfaEnrollment: MfaEnrollment | null;
  error: string;
  login: (email: string, password: string) => Promise<void>;
  verifyMfaChallenge: (code: string) => Promise<void>;
  enrollMfa: (password: string) => Promise<void>;
  enableMfa: (code: string) => Promise<void>;
  logout: () => void;
}

let pendingEmail = "";
let pendingPassword = "";

function decodeJwtClaims(token: string): Record<string, unknown> {
  const payload = token.split(".")[1] ?? "";
  const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
  return JSON.parse(json) as Record<string, unknown>;
}

function loadPersisted(): { token: string; user: AuthUser } | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as { token: string; user: AuthUser }) : null;
  } catch {
    return null;
  }
}

// Same real-probe rationale as living-world-explorer/src/store/authStore.ts:
// a JWT's own mfa_status claim only says whether THIS token would satisfy
// the platform's MFA gate IF it's on — not whether the gate is actually
// enabled right now (an operator-controlled, runtime-toggleable setting).
async function probeMfaGate(token: string): Promise<boolean> {
  try {
    const res = await fetch("/api/v1/agentos/actors", { headers: { Authorization: `Bearer ${token}` } });
    if (res.status !== 403) return false;
    const data: unknown = await res.json().catch(() => ({}));
    return (data as { detail?: string })?.detail === "MFA evidence required";
  } catch {
    return false;
  }
}

async function authPost<T>(path: string, body: unknown, bearerToken?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (bearerToken) headers.Authorization = `Bearer ${bearerToken}`;
  const res = await fetch(`${AUTH_BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  const data: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string })?.detail;
    throw new Error(detail || `${path} failed (${res.status})`);
  }
  return data as T;
}

const persisted = loadPersisted();

export const useAuthStore = create<AuthState>((set, get) => ({
  status: persisted ? "authenticated" : "anonymous",
  token: persisted?.token ?? null,
  user: persisted?.user ?? null,
  mfaChallengeToken: null,
  mfaEnrollment: null,
  error: "",

  login: async (email, password) => {
    pendingEmail = email;
    pendingPassword = password;
    set({ error: "" });
    try {
      const data = await authPost<{
        access_token: string | null;
        mfa_required: boolean;
        mfa_challenge_token: string | null;
      }>("/login", { email, password });

      if (data.mfa_required && data.mfa_challenge_token) {
        set({ status: "needs_mfa_challenge", mfaChallengeToken: data.mfa_challenge_token });
        return;
      }
      if (!data.access_token) throw new Error("Login response carried no token");

      const claims = decodeJwtClaims(data.access_token) as { email?: string; role?: string; mfa_status?: string };
      const user: AuthUser = { email: claims.email ?? email, role: claims.role ?? "" };

      const mfaActuallyRequired = claims.mfa_status !== "satisfied" && (await probeMfaGate(data.access_token));

      if (!mfaActuallyRequired) {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: data.access_token, user }));
        set({ status: "authenticated", token: data.access_token, user });
      } else {
        set({ status: "needs_mfa_setup", token: data.access_token, user });
      }
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "Login failed" });
      throw err;
    }
  },

  verifyMfaChallenge: async (code) => {
    const { mfaChallengeToken } = get();
    if (!mfaChallengeToken) throw new Error("No MFA challenge in progress");
    set({ error: "" });
    try {
      const data = await authPost<{ access_token: string }>("/mfa/verify", {
        mfa_challenge_token: mfaChallengeToken,
        code,
      });
      const claims = decodeJwtClaims(data.access_token) as { email?: string; role?: string };
      const user: AuthUser = { email: claims.email ?? pendingEmail, role: claims.role ?? "" };
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: data.access_token, user }));
      pendingEmail = "";
      pendingPassword = "";
      set({ status: "authenticated", token: data.access_token, user, mfaChallengeToken: null });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "Invalid code" });
      throw err;
    }
  },

  enrollMfa: async (password) => {
    const { token } = get();
    if (!token) throw new Error("Not logged in");
    set({ error: "" });
    try {
      const data = await authPost<{ secret: string; otpauth_uri: string; backup_codes: string[] }>(
        "/mfa/enroll",
        { password },
        token,
      );
      set({ mfaEnrollment: { secret: data.secret, otpauthUri: data.otpauth_uri, backupCodes: data.backup_codes } });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "Enrollment failed" });
      throw err;
    }
  },

  enableMfa: async (code) => {
    const { token, mfaEnrollment: enrolling } = get();
    if (!token) throw new Error("Not logged in");
    set({ error: "" });
    try {
      await authPost("/mfa/enable", { code }, token);
      // Pre-enrollment token can never become satisfied (JWTs are
      // immutable) — log in again now that mfa_enabled=true server-side.
      // Unlike living-world-explorer's port, there's no locally-computed
      // fresh code to auto-answer the resulting challenge with here, so
      // the user re-enters a code from their authenticator app.
      if (enrolling) {
        await get().login(pendingEmail, pendingPassword);
      }
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "Could not enable MFA" });
      throw err;
    }
  },

  logout: () => {
    window.localStorage.removeItem(STORAGE_KEY);
    pendingEmail = "";
    pendingPassword = "";
    set({ status: "anonymous", token: null, user: null, mfaChallengeToken: null, mfaEnrollment: null, error: "" });
  },
}));
