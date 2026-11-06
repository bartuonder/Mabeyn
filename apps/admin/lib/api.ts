import { cookies } from "next/headers";
import { redirect, unstable_rethrow } from "next/navigation";

import { ACCESS } from "./session";

export const API_URL = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type ActionState = { ok?: string; error?: string; secret?: string };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detail(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`)
      .join("; ");
  }
  return fallback;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = (await cookies()).get(ACCESS)?.value;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(API_URL + path, { ...init, headers, cache: "no-store" });
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (response.status === 401) redirect("/login");
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, detail(body, response.statusText));
  }
  return (response.status === 204 ? null : await response.json()) as T;
}

export function json(body: unknown): RequestInit {
  return {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// Runs a form action and turns API errors into a message the form can show.
export async function run(work: () => Promise<ActionState>): Promise<ActionState> {
  try {
    return await work();
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof ApiError) return { error: error.message };
    return { error: "The panel could not reach the API." };
  }
}
