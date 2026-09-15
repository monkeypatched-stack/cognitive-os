"use client";

// Talks to the same FastAPI backend living-world-explorer/src/api/client.ts
// talks to. In dev, next.config.ts's rewrites() proxies /api to the real
// backend, so relative paths work without CORS — same role Vite's own
// dev-server proxy plays there.
import { useAuthStore } from "./authStore";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1/agentos";
// /live, /health, /ready are mounted at the app root, not under
// /api/v1/agentos (see src/monkey_brain/api/main.py) — a separate base.
const ROOT_BASE = process.env.NEXT_PUBLIC_API_ROOT_URL ?? "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const token = useAuthStore.getState().token;
  const headers = new Headers(init?.headers);
  if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${base}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(`${init?.method ?? "GET"} ${path} -> ${res.status}`, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface HealthStatus {
  status?: string;
  [key: string]: unknown;
}

export function checkLive(): Promise<HealthStatus> {
  return request<HealthStatus>(ROOT_BASE, "/live");
}

export const apiClient = {
  request: <T>(path: string, init?: RequestInit) => request<T>(API_BASE, path, init),
  checkLive,
};
