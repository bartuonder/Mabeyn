import Link from "next/link";
import { notFound } from "next/navigation";

import { ConfirmButton } from "@/components/ConfirmButton";
import { Badge, Empty, PageHead } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { ConversationDetail, Memory, SecurityEvent } from "@/lib/types";
import { eraseMemory } from "../../../actions";

export const metadata = { title: "Conversation - Mabeyn Admin" };

export default async function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const conversation = await api<ConversationDetail>(`/conversations/${id}`).catch((error) => {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  });
  const anonymous = conversation.customer_id.startsWith("anon:");
  const [memories, events] = await Promise.all([
    anonymous ? Promise.resolve<Memory[]>([]) : api<Memory[]>(`/customers/${encodeURIComponent(conversation.customer_id)}/memory`),
    api<SecurityEvent[]>("/security-events?limit=500"),
  ]);
  const related = events.filter((e) => e.conversation_id === id);
  const messages = conversation.messages.filter((m) => m.role === "user" || m.role === "assistant");

  return (
    <>
      <PageHead title="Conversation" hint={`${conversation.customer_id}, started ${formatTime(conversation.created_at)}`}>
        <Badge value={conversation.status} />
      </PageHead>
      <div className="stack">
        <section className="thread" aria-label="Messages">
          {messages.length ? messages.map((m) => (
            <div key={m.id} className={`bubble ${m.role === "user" ? "user" : ""}`}>
              <div className="meta">{m.role === "user" ? "Customer" : "Assistant"} · {formatTime(m.created_at)}</div>
              {m.content}
              {m.citations.length > 0 && (
                <div className="cites">
                  {[...new Set(m.citations.map((c) => c.cite))].map((cite) => <span key={cite} className="badge info">{cite}</span>)}
                </div>
              )}
            </div>
          )) : <Empty>No messages.</Empty>}
        </section>

        {related.length > 0 && (
          <section>
            <h2>Security events in this conversation</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>When</th><th>Layer</th><th>Category</th><th>Action</th></tr></thead>
                <tbody>
                  {related.map((e) => (
                    <tr key={e.id}><td>{formatTime(e.created_at)}</td><td>{e.layer}</td><td>{e.category}</td><td><Badge value={e.action} /></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <section>
          <h2>What the assistant remembers about this customer</h2>
          {anonymous ? (
            <p className="muted">Anonymous visitors have no memory.</p>
          ) : memories.length ? (
            <>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Category</th><th>Note</th><th>Updated</th></tr></thead>
                  <tbody>
                    {memories.map((m) => (
                      <tr key={m.key}><td><Badge value={m.category} /></td><td>{m.text}</td><td>{formatTime(m.updated_at)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <form action={eraseMemory} style={{ marginTop: 10 }}>
                <input type="hidden" name="customer" value={conversation.customer_id} />
                <input type="hidden" name="conversation" value={id} />
                <ConfirmButton message="Erase everything the assistant remembers about this customer?">Erase memory</ConfirmButton>
              </form>
            </>
          ) : (
            <p className="muted">Nothing is stored for this customer.</p>
          )}
        </section>
        <p><Link href="/conversations">Back to all conversations</Link></p>
      </div>
    </>
  );
}
