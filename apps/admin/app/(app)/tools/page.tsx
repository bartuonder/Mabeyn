import { ActionForm } from "@/components/ActionForm";
import { ConfirmButton } from "@/components/ConfirmButton";
import { Badge, Empty, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import type { Tool } from "@/lib/types";
import { createTool, deleteTool } from "../../actions";

export const metadata = { title: "Tools - Mabeyn Admin" };

const SCHEMA = `{
  "type": "object",
  "properties": {
    "order_id": { "type": "string", "description": "Order number" }
  },
  "required": ["order_id"]
}`;

const POLICIES = `[]`;

export default async function Tools() {
  const rows = await api<Tool[]>("/tools");
  return (
    <>
      <PageHead title="Tools" hint="Actions the assistant can take in your systems. Sensitive tools always wait for an approval." />
      <div className="stack">
        {rows.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Risk</th><th>Request</th><th>Policies</th><th /></tr></thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id}>
                    <td><code>{t.name}</code><div className="muted">{t.description}</div></td>
                    <td><Badge value={t.risk_level} /></td>
                    <td><code>{t.http_method} {t.url_template}</code></td>
                    <td>{t.policies.length ? t.policies.map((p) => p.type).join(", ") : "-"}</td>
                    <td>
                      <form action={deleteTool}>
                        <input type="hidden" name="id" value={t.id} />
                        <ConfirmButton message={`Delete the tool ${t.name}?`}>Delete</ConfirmButton>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No tools yet. Without tools the assistant can only answer from documents.</Empty>
        )}

        <section className="card">
          <h2>Register a tool</h2>
          <ActionForm action={createTool} submit="Create tool">
            <div className="cols">
              <label>Name (snake_case)<input type="text" name="name" pattern="[a-z][a-z0-9_]{2,63}" required /></label>
              <label>Risk
                <select name="risk_level" defaultValue="read">
                  <option value="read">read: looks something up</option>
                  <option value="write">write: changes something</option>
                  <option value="sensitive">sensitive: needs a human approval</option>
                </select>
              </label>
              <label>Method
                <select name="http_method" defaultValue="GET">
                  {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
            </div>
            <label>What it does (the model reads this)<input type="text" name="description" minLength={3} maxLength={1000} required /></label>
            <label>URL template (use {"{order_id}"} for path arguments)<input type="text" name="url_template" placeholder="https://api.example.com/orders/{order_id}" required /></label>
            <label>Input schema (JSON Schema)<textarea name="json_schema" className="mono" defaultValue={SCHEMA} required /></label>
            <label>Argument policies (JSON list, optional)<textarea name="policies" className="mono" defaultValue={POLICIES} style={{ minHeight: 70 }} /></label>
            <label className="check"><input type="checkbox" name="requires_identity" defaultChecked /> Needs a signed-in customer</label>
          </ActionForm>
        </section>
      </div>
    </>
  );
}
