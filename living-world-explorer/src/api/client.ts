// FastAPI integration. In dev, requests go through Vite's proxy
// (vite.config.ts) to http://localhost:8031, so relative paths work
// without CORS. The env vars override this for a build served from a
// different origin than the API.
import { useAuthStore } from '../store/authStore'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1/agentos'
// /live, /health, /ready are mounted at the app root, not under the
// /api/v1/agentos prefix (see src/monkey_brain/api/main.py) — a
// separate base, not a relative "../" hack off API_BASE.
const ROOT_BASE = import.meta.env.VITE_API_ROOT_URL ?? ''

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const token = useAuthStore.getState().token
  const headers = new Headers(init?.headers)
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${base}${path}`, { ...init, headers })
  if (!res.ok) {
    throw new ApiError(`${init?.method ?? 'GET'} ${path} -> ${res.status}`, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export interface HealthStatus {
  status?: string
  [key: string]: unknown
}

// The minimal, dependency-free liveness check the backend itself
// documents as the correct one for "is the server up" — see
// deploy/k8s/deployment.yaml's livenessProbe comment.
export function checkLive(): Promise<HealthStatus> {
  return request<HealthStatus>(ROOT_BASE, '/live')
}

export const apiClient = {
  request: <T>(path: string, init?: RequestInit) => request<T>(API_BASE, path, init),
  checkLive,
}
