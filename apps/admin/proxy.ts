import { NextRequest, NextResponse } from "next/server";

import { ACCESS, REFRESH, setSession, type Tokens } from "./lib/session";

const API_URL = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");

function toLogin(request: NextRequest): NextResponse {
  const response = NextResponse.redirect(new URL("/login", request.url));
  response.cookies.delete(ACCESS);
  response.cookies.delete(REFRESH);
  return response;
}

// The access token is short lived. When its cookie has expired the refresh token gets a new pair,
// so a signed-in admin is never sent back to the login page by a timer.
export async function proxy(request: NextRequest): Promise<NextResponse> {
  if (request.nextUrl.pathname === "/login") return NextResponse.next();
  if (request.cookies.get(ACCESS)?.value) return NextResponse.next();

  const refresh = request.cookies.get(REFRESH)?.value;
  if (!refresh) return toLogin(request);

  const answer = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
    cache: "no-store",
  }).catch(() => null);
  if (!answer || !answer.ok) return toLogin(request);

  const tokens = (await answer.json()) as Tokens;
  const kept = request.cookies
    .getAll()
    .filter((c) => c.name !== ACCESS && c.name !== REFRESH)
    .map((c) => `${c.name}=${c.value}`);
  const headers = new Headers(request.headers);
  headers.set(
    "cookie",
    [...kept, `${ACCESS}=${tokens.access_token}`, `${REFRESH}=${tokens.refresh_token}`].join("; "),
  );
  const response = NextResponse.next({ request: { headers } });
  setSession(response.cookies, tokens);
  return response;
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
