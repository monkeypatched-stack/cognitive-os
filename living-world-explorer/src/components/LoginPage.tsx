import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import { useLiveTotpCode } from '../lib/useLiveTotpCode'
import './LoginPage.css'

function LoginForm() {
  const login = useAuthStore((s) => s.login)
  const error = useAuthStore((s) => s.error)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      await login(email, password)
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="lwe-login-card" onSubmit={submit}>
      <h1>CognitiveOS</h1>
      <p className="lwe-login-subtitle">Sign in to Living World Explorer</p>
      <label>
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
      </label>
      <label>
        Password
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      {error && <div className="lwe-login-error">{error}</div>}
      <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  )
}

function MfaChallengeForm() {
  const verifyMfaChallenge = useAuthStore((s) => s.verifyMfaChallenge)
  const mfaSecret = useAuthStore((s) => s.mfaSecret)
  const error = useAuthStore((s) => s.error)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  // Showing the live code here (not just once during setup) is a
  // deliberate, temporary convenience for this personal/dev cluster's own
  // admin — see authStore.ts's MFA_SECRET_STORAGE_KEY comment for why the
  // secret is persisted at all. A real multi-user product would never do
  // this; it would only ever prompt for a code from a separate device.
  const { code: liveCode, countdown } = useLiveTotpCode(mfaSecret)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      await verifyMfaChallenge(code || liveCode)
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="lwe-login-card" onSubmit={submit}>
      <h1>Verification code</h1>
      <p className="lwe-login-subtitle">Enter the 6-digit code from your authenticator app</p>
      {mfaSecret && (
        <div className="lwe-mfa-live">
          <span className="lwe-mfa-label">Current code</span>
          <span className="lwe-mfa-live-code">{liveCode || '——————'}</span>
          <span className="lwe-mfa-live-countdown">refreshes in {countdown}s</span>
        </div>
      )}
      <label>
        Code
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={liveCode}
          inputMode="numeric"
          maxLength={6}
          autoFocus
        />
      </label>
      {error && <div className="lwe-login-error">{error}</div>}
      <button type="submit" disabled={busy}>{busy ? 'Verifying…' : 'Verify'}</button>
    </form>
  )
}

function MfaSetupForm() {
  const enrollMfa = useAuthStore((s) => s.enrollMfa)
  const enableMfa = useAuthStore((s) => s.enableMfa)
  const mfaEnrollment = useAuthStore((s) => s.mfaEnrollment)
  const error = useAuthStore((s) => s.error)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  // Ticks the live-computed code every second so the setup screen is
  // transparent about what it's doing rather than a black box — this is
  // a personal/dev cluster's own admin bootstrapping their own MFA
  // secret, not a shared screen where showing the code would matter.
  const { code: liveCode, countdown } = useLiveTotpCode(mfaEnrollment?.secret ?? null)

  const submitPassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      await enrollMfa(password)
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false)
    }
  }

  const submitCode = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      await enableMfa(code || liveCode)
    } catch {
      // error is surfaced from the store below
    } finally {
      setBusy(false)
    }
  }

  if (!mfaEnrollment) {
    return (
      <form className="lwe-login-card" onSubmit={submitPassword}>
        <h1>Set up two-factor auth</h1>
        <p className="lwe-login-subtitle">This account hasn't enrolled in MFA yet — confirm your password to start.</p>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus required />
        </label>
        {error && <div className="lwe-login-error">{error}</div>}
        <button type="submit" disabled={busy}>{busy ? 'Starting…' : 'Continue'}</button>
      </form>
    )
  }

  return (
    <form className="lwe-login-card lwe-login-card-wide" onSubmit={submitCode}>
      <h1>Scan or copy this secret</h1>
      <p className="lwe-login-subtitle">Add it to an authenticator app, or use the live code below to finish setup now.</p>
      <div className="lwe-mfa-secret">
        <div>
          <span className="lwe-mfa-label">Secret</span>
          <code>{mfaEnrollment.secret}</code>
        </div>
        <div>
          <span className="lwe-mfa-label">otpauth URI</span>
          <code className="lwe-mfa-uri">{mfaEnrollment.otpauthUri}</code>
        </div>
      </div>
      <div className="lwe-mfa-live">
        <span className="lwe-mfa-label">Current code</span>
        <span className="lwe-mfa-live-code">{liveCode || '——————'}</span>
        <span className="lwe-mfa-live-countdown">refreshes in {countdown}s</span>
      </div>
      <label>
        Confirm code
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={liveCode}
          inputMode="numeric"
          maxLength={6}
        />
      </label>
      {error && <div className="lwe-login-error">{error}</div>}
      <button type="submit" disabled={busy}>{busy ? 'Enabling…' : 'Enable MFA and continue'}</button>
      <details className="lwe-mfa-backup">
        <summary>Backup codes ({mfaEnrollment.backupCodes.length})</summary>
        <ul>{mfaEnrollment.backupCodes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
      </details>
    </form>
  )
}

export function LoginPage() {
  const status = useAuthStore((s) => s.status)

  if (status === 'authenticated') return <Navigate to="/" replace />

  return (
    <div className="lwe-login-page">
      {status === 'anonymous' && <LoginForm />}
      {status === 'needs_mfa_challenge' && <MfaChallengeForm />}
      {status === 'needs_mfa_setup' && <MfaSetupForm />}
    </div>
  )
}
