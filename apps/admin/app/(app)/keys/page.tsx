import { ActionForm } from "@/components/ActionForm";
import { ConfirmButton } from "@/components/ConfirmButton";
import { Badge, Empty, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { ApiKey } from "@/lib/types";
import { createKey, revokeKey } from "../../actions";

export const metadata = { title: "API keys - Mabeyn Admin" };

export default async function Keys() {
  const rows = await api<ApiKey[]>("/api-keys");
  return (
    <>
      <PageHead title="API keys" hint="Secret keys stay on your server and open sessions for named customers. Publishable keys are safe in a web page and only open anonymous sessions." />
      <div className="stack">
        {rows.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Kind</th><th>Key</th><th>Allowed origins</th><th>Created</th><th /></tr></thead>
              <tbody>
                {rows.map((k) => (
                  <tr key={k.id}>
                    <td>{k.name}</td>
                    <td><Badge value={k.kind} /></td>
                    <td><code>{k.prefix}...</code></td>
                    <td>{k.allowed_origins.length ? k.allowed_origins.join(", ") : "any"}</td>
                    <td>{formatTime(k.created_at)}</td>
                    <td>
                      {k.revoked_at ? (
                        <span className="badge">revoked</span>
                      ) : (
                        <form action={revokeKey}>
                          <input type="hidden" name="id" value={k.id} />
                          <ConfirmButton message={`Revoke ${k.name}? Sites that use it stop working.`}>Revoke</ConfirmButton>
                        </form>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No keys yet.</Empty>
        )}
        <section className="card">
          <h2>Create a key</h2>
          <ActionForm action={createKey} submit="Create key">
            <div className="cols">
              <label>Name<input type="text" name="name" maxLength={100} required /></label>
              <label>Kind
                <select name="kind" defaultValue="publishable">
                  <option value="publishable">publishable (browser)</option>
                  <option value="secret">secret (server)</option>
                </select>
              </label>
            </div>
            <label>Allowed origins (publishable keys; one per line, empty allows any)
              <textarea name="allowed_origins" className="mono" style={{ minHeight: 70 }} placeholder="https://shop.example.com" />
            </label>
          </ActionForm>
        </section>
      </div>
    </>
  );
}
