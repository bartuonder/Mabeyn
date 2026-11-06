"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { API_URL, api, apiFetch, ApiError, json, run, type ActionState } from "@/lib/api";
import { ACCESS, REFRESH, setSession, type Tokens } from "@/lib/session";

type Previous = ActionState;

const text = (form: FormData, name: string): string => String(form.get(name) ?? "").trim();
const lines = (value: string): string[] =>
  value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);

function parseJson(value: string, label: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    throw new ApiError(422, `${label} is not valid JSON.`);
  }
}

// ---- session ----------------------------------------------------------------------------------

export async function login(_: Previous, form: FormData): Promise<ActionState> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      ...json({ email: text(form, "email"), password: String(form.get("password") ?? "") }),
      cache: "no-store",
    });
  } catch {
    return { error: "The panel could not reach the API." };
  }
  if (response.status === 401) return { error: "Wrong e-mail or password." };
  if (!response.ok) return { error: "Sign-in failed. Try again." };
  setSession(await cookies(), (await response.json()) as Tokens);
  redirect("/");
}

export async function logout(): Promise<void> {
  const store = await cookies();
  store.delete(ACCESS);
  store.delete(REFRESH);
  redirect("/login");
}

// ---- approvals --------------------------------------------------------------------------------

export async function decideApproval(_: Previous, form: FormData): Promise<ActionState> {
  const id = text(form, "id");
  const decision = text(form, "decision");
  const result = await run(async () => {
    await api(`/approvals/${id}`, {
      method: "POST",
      ...json({ decision, note: text(form, "note") || null }),
    });
    return { ok: "recorded" };
  });
  if (result.error) return result;
  revalidatePath("/", "layout");
  redirect(`/approvals/${id}?decided=${decision}`);
}

// ---- documents --------------------------------------------------------------------------------

export async function uploadDocument(_: Previous, form: FormData): Promise<ActionState> {
  return run(async () => {
    const file = form.get("file");
    if (!(file instanceof File) || file.size === 0) return { error: "Choose a PDF, Markdown or HTML file." };
    const body = new FormData();
    body.set("file", file);
    if (text(form, "title")) body.set("title", text(form, "title"));
    if (text(form, "effective_date")) body.set("effective_date", text(form, "effective_date"));
    await api("/documents", { method: "POST", body });
    revalidatePath("/documents");
    return { ok: "Uploaded. It is being processed." };
  });
}

export async function addUrlDocument(_: Previous, form: FormData): Promise<ActionState> {
  return run(async () => {
    await api("/documents/url", {
      method: "POST",
      ...json({
        url: text(form, "url"),
        title: text(form, "title") || null,
        effective_date: text(form, "effective_date") || null,
      }),
    });
    revalidatePath("/documents");
    return { ok: "Added. It is being processed." };
  });
}

export async function deleteDocument(form: FormData): Promise<void> {
  await apiFetch(`/documents/${text(form, "id")}`, { method: "DELETE" });
  revalidatePath("/documents");
}

// ---- tools ------------------------------------------------------------------------------------

export async function createTool(_: Previous, form: FormData): Promise<ActionState> {
  return run(async () => {
    await api("/tools", {
      method: "POST",
      ...json({
        name: text(form, "name"),
        description: text(form, "description"),
        risk_level: text(form, "risk_level"),
        http_method: text(form, "http_method"),
        url_template: text(form, "url_template"),
        requires_identity: form.get("requires_identity") === "on",
        json_schema: parseJson(text(form, "json_schema"), "The input schema"),
        policies: parseJson(text(form, "policies") || "[]", "The policies"),
      }),
    });
    revalidatePath("/tools");
    return { ok: "Tool created." };
  });
}

export async function deleteTool(form: FormData): Promise<void> {
  await apiFetch(`/tools/${text(form, "id")}`, { method: "DELETE" });
  revalidatePath("/tools");
}

// ---- security ---------------------------------------------------------------------------------

export async function saveFirewall(_: Previous, form: FormData): Promise<ActionState> {
  return run(async () => {
    const allowlist = lines(text(form, "tool_allowlist"));
    await api("/settings/firewall", {
      method: "PUT",
      ...json({
        firewall_enabled: form.get("firewall_enabled") === "on",
        injection_threshold: Number(text(form, "injection_threshold")),
        write_rate_limit: Number(text(form, "write_rate_limit")),
        write_rate_window_minutes: Number(text(form, "write_rate_window_minutes")),
        tool_allowlist: allowlist.length ? allowlist : null,
      }),
    });
    revalidatePath("/security");
    return { ok: "Firewall settings saved." };
  });
}

export async function saveWebhook(_: Previous, form: FormData): Promise<ActionState> {
  return run(async () => {
    const url = text(form, "approval_webhook_url");
    const saved = await api<{ signing_secret: string | null }>("/settings/notifications", {
      method: "PUT",
      ...json({ approval_webhook_url: url || null }),
    });
    revalidatePath("/security");
    return saved.signing_secret
      ? { ok: "Webhook saved. Verify X-Mabeyn-Signature with this secret.", secret: saved.signing_secret }
      : { ok: "Webhook removed." };
  });
}

// ---- customers, cache, keys -------------------------------------------------------------------

export async function eraseMemory(form: FormData): Promise<void> {
  const customer = encodeURIComponent(text(form, "customer"));
  await apiFetch(`/customers/${customer}/memory`, { method: "DELETE" });
  revalidatePath(`/conversations/${text(form, "conversation")}`);
}

export async function clearCache(): Promise<void> {
  await apiFetch("/cache", { method: "DELETE" });
  revalidatePath("/usage");
}

export async function createKey(_: Previous, form: FormData): Promise<ActionState> {
  return run(async () => {
    const created = await api<{ key: string }>("/api-keys", {
      method: "POST",
      ...json({
        name: text(form, "name"),
        kind: text(form, "kind"),
        allowed_origins: lines(text(form, "allowed_origins")),
      }),
    });
    revalidatePath("/keys");
    return { ok: "Key created.", secret: created.key };
  });
}

export async function revokeKey(form: FormData): Promise<void> {
  await apiFetch(`/api-keys/${text(form, "id")}`, { method: "DELETE" });
  revalidatePath("/keys");
}
