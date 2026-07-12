/** Base URL of the Kobevoice Cloud control-plane (auth, billing, gateway). */
export const CLOUD_URL: string =
  (import.meta.env.VITE_CLOUD_URL as string | undefined)?.replace(/\/$/, '') ||
  'http://localhost:9000';

export const CLOUD_TOKEN_KEY = 'kv_cloud_token';
