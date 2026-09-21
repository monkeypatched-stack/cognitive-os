import { create } from 'zustand'
import { computeTotpCode } from '../lib/totp'

const STORAGE_KEY = 'lwe.auth'
// Persisted (not just kept in memory like the rest of MfaEnrollment) so a
// later login's challenge screen can also show a live code, not only the
// one-time setup screen — see MfaChallengeForm's own comment for why this
// is a deliberate, temporary, personal/dev-cluster convenience rather than
// how a real multi-tenant product would work.
const MFA_SECRET_STORAGE_KEY = 'lwe.mfaSecret'
const AUTH_BASE = '/api/v1/auth'

export type AuthStatus = 'anonymous' | 'needs_mfa_challenge' | 'needs_mfa_setup' | 'authenticated'

export interface AuthUser {
  email: string
  role: string
}

export interface MfaEnrollment {
  secret: string
  otpauthUri: string
  backupCodes: string[]
}

interface AuthState {
  status: AuthStatus
  token: string | null
  user: AuthUser | null
  mfaChallengeToken: string | null
  mfaEnrollment: MfaEnrollment | null
  mfaSecret: string | null
  error: string
  login: (email: string, password: string) => Promise<void>
  verifyMfaChallenge: (code: string) => Promise<void>
  enrollMfa: (password: string) => Promise<void>
  enableMfa: (code: string) => Promise<void>
  logout: () => void
}

// Held outside the store (never rendered, never in Zustand's inspectable
// state) purely so enableMfa() can silently replay the login the user
// already typed once, without asking them to type their password twice —
// see enableMfa's own comment for why a replay is necessary at all.
let pendingEmail = ''
let pendingPassword = ''

function decodeJwtClaims(token: string): Record<string, unknown> {
  const payload = token.split('.')[1] ?? ''
  const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
  return JSON.parse(json) as Record<string, unknown>
}

function loadPersisted(): { token: string; user: AuthUser } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as { token: string; user: AuthUser }) : null
  } catch {
    return null
  }
}

// Cheap, side-effect-free authenticated GET, used only to detect whether
// the platform's MFA gate (src/monkey_brain/api/dependencies.py::
// require_permission -> mfa_allows_operation) is actually enforced right
// now — a fresh token's own mfa_status claim can't tell us that.
async function probeMfaGate(token: string): Promise<boolean> {
  try {
    const res = await fetch('/api/v1/agentos/actors', { headers: { Authorization: `Bearer ${token}` } })
    if (res.status !== 403) return false
    const data: unknown = await res.json().catch(() => ({}))
    return (data as { detail?: string })?.detail === 'MFA evidence required'
  } catch {
    return false
  }
}

async function authPost<T>(path: string, body: unknown, bearerToken?: string): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (bearerToken) headers.Authorization = `Bearer ${bearerToken}`
  const res = await fetch(`${AUTH_BASE}${path}`, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'include' })
  const data: unknown = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || `${path} failed (${res.status})`)
  }
  return data as T
}

const persisted = loadPersisted()

export const useAuthStore = create<AuthState>((set, get) => ({
  status: persisted ? 'authenticated' : 'anonymous',
  token: persisted?.token ?? null,
  user: persisted?.user ?? null,
  mfaChallengeToken: null,
  mfaEnrollment: null,
  mfaSecret: localStorage.getItem(MFA_SECRET_STORAGE_KEY),
  error: '',

  login: async (email, password) => {
    pendingEmail = email
    pendingPassword = password
    set({ error: '' })
    try {
      const data = await authPost<{
        access_token: string | null
        mfa_required: boolean
        mfa_challenge_token: string | null
      }>('/login', { email, password })

      if (data.mfa_required && data.mfa_challenge_token) {
        set({ status: 'needs_mfa_challenge', mfaChallengeToken: data.mfa_challenge_token })
        return
      }
      if (!data.access_token) throw new Error('Login response carried no token')

      const claims = decodeJwtClaims(data.access_token) as { email?: string; role?: string; mfa_status?: string }
      const user: AuthUser = { email: claims.email ?? email, role: claims.role ?? '' }

      // A JWT's own mfa_status claim only says whether THIS token would
      // satisfy the platform gate IF that gate is on — it says nothing
      // about whether the gate (kernel/production_gates.py::
      // mfa_required(), COGNITIVEOS_MFA_REQUIRED) is actually enabled
      // right now. That's an operator-controlled, runtime-toggleable
      // setting (currently off), so a not_satisfied token can be
      // perfectly usable. Rather than hardcode either assumption client-
      // side (and drift from whatever the operator has it set to), probe
      // reality with one real authenticated call: if it 403s specifically
      // because of MFA, THEN force enrollment; any other outcome means
      // the token already works.
      const mfaActuallyRequired = claims.mfa_status !== 'satisfied' && await probeMfaGate(data.access_token)

      if (!mfaActuallyRequired) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: data.access_token, user }))
        set({ status: 'authenticated', token: data.access_token, user })
      } else {
        // Real platform rule when the gate IS on (confirmed live): a
        // token minted for a user who hasn't completed MFA enrollment is
        // permanently mfa_status=not_satisfied and every real API call
        // 403s. This token is still usable against the auth service's
        // own /mfa/* routes (services/common/auth.py::get_current_user
        // does not check mfa_status), so keep it in memory to drive
        // enrollment, but don't persist it — it can never become useful
        // without
        // completing setup.
        set({ status: 'needs_mfa_setup', token: data.access_token, user })
      }
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Login failed' })
      throw err
    }
  },

  verifyMfaChallenge: async (code) => {
    const { mfaChallengeToken } = get()
    if (!mfaChallengeToken) throw new Error('No MFA challenge in progress')
    set({ error: '' })
    try {
      const data = await authPost<{ access_token: string }>('/mfa/verify', { mfa_challenge_token: mfaChallengeToken, code })
      const claims = decodeJwtClaims(data.access_token) as { email?: string; role?: string }
      const user: AuthUser = { email: claims.email ?? pendingEmail, role: claims.role ?? '' }
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: data.access_token, user }))
      pendingEmail = ''
      pendingPassword = ''
      set({ status: 'authenticated', token: data.access_token, user, mfaChallengeToken: null })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Invalid code' })
      throw err
    }
  },

  enrollMfa: async (password) => {
    const { token } = get()
    if (!token) throw new Error('Not logged in')
    set({ error: '' })
    try {
      const data = await authPost<{ secret: string; otpauth_uri: string; backup_codes: string[] }>(
        '/mfa/enroll', { password }, token,
      )
      set({ mfaEnrollment: { secret: data.secret, otpauthUri: data.otpauth_uri, backupCodes: data.backup_codes } })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Enrollment failed' })
      throw err
    }
  },

  enableMfa: async (code) => {
    const { token, mfaEnrollment: enrolling } = get()
    if (!token) throw new Error('Not logged in')
    set({ error: '' })
    try {
      await authPost('/mfa/enable', { code }, token)
      // Only persist now that the server has confirmed this secret is the
      // truly-active one (a failed /enable, e.g. wrong code, means the
      // user may re-enroll and get a different secret — persisting
      // eagerly on enroll would risk saving one that's never activated).
      if (enrolling) {
        localStorage.setItem(MFA_SECRET_STORAGE_KEY, enrolling.secret)
        set({ mfaSecret: enrolling.secret })
      }
      // The pre-enrollment token can never become satisfied (JWTs are
      // immutable) — log in again now that mfa_enabled=true server-side,
      // then immediately answer the resulting challenge with a freshly
      // computed code, so the user lands on the dashboard instead of
      // being sent back to a bare login form after finishing setup.
      await get().login(pendingEmail, pendingPassword)
      const { mfaChallengeToken, mfaEnrollment } = get()
      if (mfaChallengeToken && mfaEnrollment) {
        const freshCode = await computeTotpCode(mfaEnrollment.secret)
        await get().verifyMfaChallenge(freshCode)
      }
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Could not enable MFA' })
      throw err
    }
  },

  logout: () => {
    localStorage.removeItem(STORAGE_KEY)
    pendingEmail = ''
    pendingPassword = ''
    set({ status: 'anonymous', token: null, user: null, mfaChallengeToken: null, mfaEnrollment: null, error: '' })
  },
}))
