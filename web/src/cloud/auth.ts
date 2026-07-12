/**
 * Kobevoice Cloud authentication for the web build.
 *
 * Persists the JWT in localStorage and mirrors it into the shared API-client
 * token holder so every engine/gateway request is authenticated.
 */
import { setAuthToken } from '@/lib/api/authToken';
import { CLOUD_TOKEN_KEY, CLOUD_URL } from './config';

export interface CloudUser {
  id: string;
  email: string;
  full_name: string | null;
  is_admin: boolean;
  is_active: boolean;
}

function loadToken(): string | null {
  return localStorage.getItem(CLOUD_TOKEN_KEY);
}

export function initAuth(): void {
  // Hydrate the API client with any persisted token on startup.
  setAuthToken(loadToken());
}

function storeToken(token: string): void {
  localStorage.setItem(CLOUD_TOKEN_KEY, token);
  setAuthToken(token);
}

async function cloudFetch<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const token = loadToken();
  const res = await fetch(`${CLOUD_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    throw new Error(
      typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `HTTP ${res.status}`,
    );
  }
  return data as T;
}

export async function login(email: string, password: string): Promise<void> {
  const { access_token } = await cloudFetch<{ access_token: string }>('/api/auth/login', {
    email,
    password,
  });
  storeToken(access_token);
}

export async function register(
  email: string,
  password: string,
  fullName?: string,
): Promise<void> {
  const { access_token } = await cloudFetch<{ access_token: string }>('/api/auth/register', {
    email,
    password,
    full_name: fullName || null,
  });
  storeToken(access_token);
}

export async function fetchMe(): Promise<CloudUser> {
  return cloudFetch<CloudUser>('/api/auth/me', undefined, 'GET');
}

export function logout(): void {
  localStorage.removeItem(CLOUD_TOKEN_KEY);
  setAuthToken(null);
}

export function hasToken(): boolean {
  return !!loadToken();
}
