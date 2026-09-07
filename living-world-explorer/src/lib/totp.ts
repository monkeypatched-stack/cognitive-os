// RFC 6238 TOTP — matches the auth service's own implementation exactly
// (services/auth/routers/auth.py::_totp: SHA-1, 6 digits, 30s step, base32
// secret) so a code computed here verifies correctly server-side. Used to
// drive MFA enrollment without requiring a separate authenticator app —
// this is a personal/dev cluster's own admin bootstrapping their own MFA,
// not a multi-tenant product where showing the live code would be a real
// vulnerability. The otpauth_uri is still shown as copyable text so a real
// authenticator app can be used instead if preferred.

const STEP_SECONDS = 30
const DIGITS = 6

function base32Decode(secret: string): Uint8Array {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  const clean = secret.toUpperCase().replace(/=+$/, '')
  const bytes: number[] = []
  let bits = 0
  let value = 0
  for (const char of clean) {
    const idx = alphabet.indexOf(char)
    if (idx === -1) continue
    value = (value << 5) | idx
    bits += 5
    if (bits >= 8) {
      bits -= 8
      bytes.push((value >> bits) & 0xff)
    }
  }
  return new Uint8Array(bytes)
}

function counterBytes(counter: number): Uint8Array {
  const buf = new ArrayBuffer(8)
  const view = new DataView(buf)
  // JS numbers are safe integers well past any real TOTP counter value —
  // the high 32 bits are always 0 for decades to come.
  view.setUint32(0, 0)
  view.setUint32(4, counter)
  return new Uint8Array(buf)
}

async function hmacSha1(key: Uint8Array, message: Uint8Array): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey(
    'raw', key as BufferSource, { name: 'HMAC', hash: 'SHA-1' }, false, ['sign'],
  )
  const signature = await crypto.subtle.sign('HMAC', cryptoKey, message as BufferSource)
  return new Uint8Array(signature)
}

async function totpAt(secret: string, counter: number): Promise<string> {
  const key = base32Decode(secret)
  const digest = await hmacSha1(key, counterBytes(counter))
  const offset = digest[digest.length - 1] & 0x0f
  const code =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff)
  return String(code % 10 ** DIGITS).padStart(DIGITS, '0')
}

export async function computeTotpCode(secret: string): Promise<string> {
  const counter = Math.floor(Date.now() / 1000 / STEP_SECONDS)
  return totpAt(secret, counter)
}

/** Seconds remaining until the current TOTP code expires — drives a live countdown. */
export function secondsUntilNextTotpStep(): number {
  const nowSeconds = Date.now() / 1000
  return STEP_SECONDS - (nowSeconds % STEP_SECONDS)
}
