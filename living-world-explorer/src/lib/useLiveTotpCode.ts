import { useEffect, useState } from 'react'
import { computeTotpCode, secondsUntilNextTotpStep } from './totp'

/** Ticks a live TOTP code (and the seconds left until it rolls over) once
 * per second for as long as `secret` is set. Shared by the MFA setup and
 * challenge screens — see LoginPage.tsx's own comment on why showing the
 * code at all is a deliberate, temporary personal/dev-cluster convenience. */
export function useLiveTotpCode(secret: string | null): { code: string; countdown: number } {
  const [code, setCode] = useState('')
  const [countdown, setCountdown] = useState(0)

  useEffect(() => {
    if (!secret) {
      setCode('')
      return
    }
    let cancelled = false
    const tick = async () => {
      const c = await computeTotpCode(secret)
      if (!cancelled) {
        setCode(c)
        setCountdown(Math.ceil(secondsUntilNextTotpStep()))
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [secret])

  return { code, countdown }
}
