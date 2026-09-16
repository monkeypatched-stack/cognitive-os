"use client";

// Port of living-world-explorer/src/components/LoginPage.tsx's flow (see
// lib/authStore.ts's own comment for the one deliberate simplification:
// no locally-computed "live" TOTP code convenience here).
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "../../lib/authStore";

function LoginForm() {
  const login = useAuthStore((s) => s.login);
  const error = useAuthStore((s) => s.error);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await login(email, password);
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="login-card" onSubmit={submit}>
      <h1>CognitiveOS</h1>
      <p className="login-subtitle">Sign in to the workspace</p>
      <label>
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
      </label>
      <label>
        Password
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      {error && <div className="login-error">{error}</div>}
      <button type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

function MfaChallengeForm() {
  const verifyMfaChallenge = useAuthStore((s) => s.verifyMfaChallenge);
  const error = useAuthStore((s) => s.error);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await verifyMfaChallenge(code);
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="login-card" onSubmit={submit}>
      <h1>Verification code</h1>
      <p className="login-subtitle">Enter the 6-digit code from your authenticator app</p>
      <label>
        Code
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode="numeric"
          maxLength={6}
          autoFocus
          required
        />
      </label>
      {error && <div className="login-error">{error}</div>}
      <button type="submit" disabled={busy}>
        {busy ? "Verifying…" : "Verify"}
      </button>
    </form>
  );
}

function MfaSetupForm() {
  const enrollMfa = useAuthStore((s) => s.enrollMfa);
  const enableMfa = useAuthStore((s) => s.enableMfa);
  const mfaEnrollment = useAuthStore((s) => s.mfaEnrollment);
  const error = useAuthStore((s) => s.error);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  const submitPassword = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await enrollMfa(password);
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await enableMfa(code);
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false);
    }
  };

  if (!mfaEnrollment) {
    return (
      <form className="login-card" onSubmit={submitPassword}>
        <h1>Set up two-factor auth</h1>
        <p className="login-subtitle">This account hasn&apos;t enrolled in MFA yet — confirm your password to start.</p>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus required />
        </label>
        {error && <div className="login-error">{error}</div>}
        <button type="submit" disabled={busy}>
          {busy ? "Starting…" : "Continue"}
        </button>
      </form>
    );
  }

  return (
    <form className="login-card" onSubmit={submitCode}>
      <h1>Scan this secret</h1>
      <p className="login-subtitle">Add it to an authenticator app, then confirm with a generated code.</p>
      <div>
        <span className="login-subtitle">Secret</span>
        <code>{mfaEnrollment.secret}</code>
      </div>
      <label>
        Confirm code
        <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" maxLength={6} required />
      </label>
      {error && <div className="login-error">{error}</div>}
      <button type="submit" disabled={busy}>
        {busy ? "Enabling…" : "Enable MFA and continue"}
      </button>
      <details className="mfa-backup">
        <summary>Backup codes ({mfaEnrollment.backupCodes.length})</summary>
        <ul>
          {mfaEnrollment.backupCodes.map((c) => (
            <li key={c}>
              <code>{c}</code>
            </li>
          ))}
        </ul>
      </details>
    </form>
  );
}

export default function LoginPage() {
  const status = useAuthStore((s) => s.status);
  const router = useRouter();

  useEffect(() => {
    if (status === "authenticated") router.replace("/drone-flight");
  }, [status, router]);

  if (status === "authenticated") return null;

  return (
    <div className="login-page">
      {status === "anonymous" && <LoginForm />}
      {status === "needs_mfa_challenge" && <MfaChallengeForm />}
      {status === "needs_mfa_setup" && <MfaSetupForm />}
    </div>
  );
}
