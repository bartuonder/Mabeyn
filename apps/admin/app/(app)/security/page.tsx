import Link from "next/link";

import { ActionForm } from "@/components/ActionForm";
import { Badge, Empty, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { FirewallSettings, SecurityEvent } from "@/lib/types";
import { saveFirewall, saveWebhook } from "../../actions";

export const metadata = { title: "Security - Mabeyn Admin" };

type Search = { layer?: string; action?: string };

export default async function Security({ searchParams }: { searchParams: Promise<Search> }) {
  const { layer = "", action = "" } = await searchParams;
  const query = new URLSearchParams({ limit: "100" });
  if (layer) query.set("layer", layer);
  if (action) query.set("action", action);
  const [events, settings, notifications] = await Promise.all([
    api<SecurityEvent[]>(`/security-events?${query}`),
    api<FirewallSettings>("/settings/firewall"),
    api<{ approval_webhook_url: string | null }>("/settings/notifications"),
  ]);

  return (
    <>
      <PageHead title="Security" hint="Everything the firewall blocked or flagged, and how it is configured." />
      <div className="stack">
        <section>
          <h2>Events</h2>
          <form className="row" style={{ marginBottom: 12 }}>
            <label>Layer
              <select name="layer" defaultValue={layer}>
                <option value="">All</option>
                {["input", "document", "tool", "output", "memory"].map((l) => <option key={l}>{l}</option>)}
              </select>
            </label>
            <label>Action
              <select name="action" defaultValue={action}>
                <option value="">All</option>
                <option>blocked</option>
                <option>flagged</option>
              </select>
            </label>
            <div style={{ alignSelf: "flex-end" }}>
              <button type="submit" className="ghost">Filter</button>{" "}
              <Link href="/security">Reset</Link>
            </div>
          </form>
          {events.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>When</th><th>Layer</th><th>Category</th><th>Action</th><th>Detail</th><th /></tr></thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.id}>
                      <td>{formatTime(e.created_at)}</td>
                      <td>{e.layer}</td>
                      <td>{e.category}</td>
                      <td><Badge value={e.action} /></td>
                      <td><code>{JSON.stringify(e.detail).slice(0, 140)}</code></td>
                      <td>{e.conversation_id && <Link href={`/conversations/${e.conversation_id}`}>Conversation</Link>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>No events match. Try an injection attempt in the storefront chat to see one appear.</Empty>
          )}
        </section>

        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
          <section className="card">
            <h2>Firewall</h2>
            <ActionForm action={saveFirewall} submit="Save settings" resetOnSuccess={false}>
              <label className="check"><input type="checkbox" name="firewall_enabled" defaultChecked={settings.firewall_enabled} /> Firewall enabled</label>
              <div className="cols">
                <label>Injection threshold (0.05 to 1)<input type="number" name="injection_threshold" step="0.05" min="0.05" max="1" defaultValue={settings.injection_threshold} required /></label>
                <label>Write limit per customer<input type="number" name="write_rate_limit" min="0" max="1000" defaultValue={settings.write_rate_limit} required /></label>
                <label>Window (minutes)<input type="number" name="write_rate_window_minutes" min="1" max="1440" defaultValue={settings.write_rate_window_minutes} required /></label>
              </div>
              <label>Tool allowlist (one name per line; empty allows every tool)
                <textarea name="tool_allowlist" className="mono" style={{ minHeight: 80 }} defaultValue={(settings.tool_allowlist ?? []).join("\n")} />
              </label>
            </ActionForm>
          </section>
          <section className="card">
            <h2>Approval webhook</h2>
            <p className="muted">A signed <code>approval.pending</code> event is sent here for every new request. The URL must be public.</p>
            <ActionForm action={saveWebhook} submit="Save webhook" resetOnSuccess={false}>
              <label>URL (empty removes it)<input type="url" name="approval_webhook_url" defaultValue={notifications.approval_webhook_url ?? ""} placeholder="https://hooks.example.com/approvals" /></label>
            </ActionForm>
          </section>
        </div>
      </div>
    </>
  );
}
