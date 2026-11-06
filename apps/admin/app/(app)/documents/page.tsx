import { ActionForm } from "@/components/ActionForm";
import { AutoRefresh } from "@/components/AutoRefresh";
import { ConfirmButton } from "@/components/ConfirmButton";
import { Badge, Empty, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { formatBytes, formatTime } from "@/lib/format";
import type { Document } from "@/lib/types";
import { addUrlDocument, deleteDocument, uploadDocument } from "../../actions";

export const metadata = { title: "Documents - Mabeyn Admin" };

export default async function Documents() {
  const rows = await api<Document[]>("/documents");
  const busy = rows.some((d) => d.status === "pending" || d.status === "processing");

  return (
    <>
      <AutoRefresh seconds={3} active={busy} />
      <PageHead title="Documents" hint="The knowledge base the assistant answers from. Passages that look like instructions to an AI are flagged and never retrieved." />
      <div className="stack">
        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          <section className="card">
            <h2>Upload a file</h2>
            <ActionForm action={uploadDocument} submit="Upload">
              <label>File (PDF, Markdown or HTML)<input type="file" name="file" accept=".pdf,.md,.markdown,.html,.htm" required /></label>
              <div className="cols">
                <label>Title (optional)<input type="text" name="title" maxLength={500} /></label>
                <label>Effective date (optional)<input type="date" name="effective_date" /></label>
              </div>
            </ActionForm>
          </section>
          <section className="card">
            <h2>Add a web page</h2>
            <ActionForm action={addUrlDocument} submit="Add">
              <label>Public URL<input type="url" name="url" placeholder="https://example.com/returns" required /></label>
              <div className="cols">
                <label>Title (optional)<input type="text" name="title" maxLength={500} /></label>
                <label>Effective date (optional)<input type="date" name="effective_date" /></label>
              </div>
            </ActionForm>
          </section>
        </div>

        {rows.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Title</th><th>Type</th><th>Language</th><th>Size</th><th>Added</th><th>Status</th><th /></tr></thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.id}>
                    <td>{d.title}{d.error && <div className="muted">{d.error}</div>}</td>
                    <td>{d.source_type}</td>
                    <td>{d.language}</td>
                    <td className="num">{formatBytes(d.size_bytes)}</td>
                    <td>{formatTime(d.created_at)}</td>
                    <td><Badge value={d.status} /></td>
                    <td>
                      <form action={deleteDocument}>
                        <input type="hidden" name="id" value={d.id} />
                        <ConfirmButton message={`Delete "${d.title}" and its passages?`}>Delete</ConfirmButton>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No documents yet. Upload one above; answers come only from documents.</Empty>
        )}
      </div>
    </>
  );
}
