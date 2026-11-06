export const ACCESS = "mb_access";
export const REFRESH = "mb_refresh";
const WEEK = 7 * 24 * 60 * 60;

export type Tokens = { access_token: string; refresh_token: string; expires_in: number };

// Tokens live in httpOnly cookies: the browser never runs code that can read them, and every call
// to the API is made by this server.
type CookieStore = {
  set(name: string, value: string, options: Record<string, unknown>): unknown;
};

export function setSession(store: CookieStore, tokens: Tokens): void {
  const base = {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.COOKIE_SECURE === "true",
    path: "/",
  };
  store.set(ACCESS, tokens.access_token, { ...base, maxAge: Math.max(tokens.expires_in - 30, 60) });
  store.set(REFRESH, tokens.refresh_token, { ...base, maxAge: WEEK });
}
