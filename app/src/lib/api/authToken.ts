/**
 * Optional bearer token for authenticated (cloud) deployments.
 *
 * The desktop app never sets this, so `getAuthToken()` returns null and the
 * API client sends no Authorization header — behaviour is unchanged. The web
 * (cloud) build sets it after the user signs in to Kobevoice Cloud.
 */

let authToken: string | null = null;
type Listener = (token: string | null) => void;
const listeners = new Set<Listener>();

export function getAuthToken(): string | null {
  return authToken;
}

export function setAuthToken(token: string | null): void {
  authToken = token;
  for (const l of listeners) l(token);
}

export function onAuthTokenChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
