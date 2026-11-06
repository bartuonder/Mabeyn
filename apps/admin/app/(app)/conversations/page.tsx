import Link from "next/link";

import { Badge, Empty, PageHead, ShortId } from "@/components/ui";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { Conversation } from "@/lib/types";

export const metadata = { title: "Conversations - Mabeyn Admin" };

export default async function Conversations() {
  const rows = (await api<Conversation[]>("/conversations")).slice(0, 100);
  return (
    <>
      <PageHead title="Conversations" hint="The 100 most recent conversations of your customers." />
      {rows.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Started</th><th>Customer</th><th>Status</th><th>Reason</th><th>Id</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td><Link href={`/conversations/${c.id}`}>{formatTime(c.created_at)}</Link></td>
                  <td>{c.customer_id}</td>
                  <td><Badge value={c.status} /></td>
                  <td>{c.escalation_reason ?? "-"}</td>
                  <td><ShortId id={c.id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No conversations yet. Chat in the demo storefront to create one.</Empty>
      )}
    </>
  );
}
