import Link from "next/link";

import { BarChart } from "@/components/BarChart";
import { Badge, Empty, PageHead, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { everyDay } from "@/lib/days";
import { formatTime, formatUsd } from "@/lib/format";
import type { Approval, Document, SecurityEvent, UsageReport } from "@/lib/types";

export const metadata = { title: "Overview - Mabeyn Admin" };

export default async function Overview() {
  const [pending, documents, events, usage] = await Promise.all([
    api<Approval[]>("/approvals?status=pending"),
    api<Document[]>("/documents"),
    api<SecurityEvent[]>("/security-events?limit=500"),
    api<UsageReport>("/usage?days=7"),
  ]);
  const weekAgo = Date.now() - 7 * 24 * 3600 * 1000;
  const recent = events.filter((e) => new Date(e.created_at).getTime() >= weekAgo);
  const blocked = recent.filter((e) => e.action === "blocked").length;
  const ready = documents.filter((d) => d.status === "ready").length;
  const failed = documents.filter((d) => d.status === "failed").length;
  const days = everyDay(usage.days, 7);
  const cached = usage.days.reduce((n, d) => n + d.cached_messages, 0);

  return (
    <>
      <PageHead title="Overview" hint="What needs attention and how the assistant did in the last 7 days." />
      <div className="cards">
        <Stat label="Pending approvals" value={pending.length} hint={pending.length ? "Customers are waiting" : "Nothing waiting"} href="/approvals" />
        <Stat label="Messages answered" value={usage.total_messages} hint={`${cached} from the cache`} href="/usage" />
        <Stat label="Estimated cost" value={formatUsd(usage.total_cost_usd)} hint="Last 7 days" href="/usage" />
        <Stat label="Blocked or flagged" value={recent.length} hint={`${blocked} blocked`} href="/security" />
        <Stat label="Documents ready" value={`${ready}/${documents.length}`} hint={failed ? `${failed} failed` : "None failed"} href="/documents" />
      </div>

      <div className="stack">
        <section>
          <h2>Messages per day</h2>
          {usage.total_messages > 0 ? (
            <BarChart
              unit="Messages"
              bars={days.map((d) => ({ label: d.day.slice(5), value: d.messages, detail: `${d.day}: ${d.messages} messages, ${formatUsd(d.cost_usd)}` }))}
            />
          ) : (
            <Empty>No conversations in the last 7 days.</Empty>
          )}
        </section>

        <section>
          <h2>Waiting for a decision</h2>
          {pending.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Requested</th><th>Action</th><th>Customer</th><th>Expires</th></tr></thead>
                <tbody>
                  {pending.slice(0, 5).map((a) => (
                    <tr key={a.id}>
                      <td><Link href={`/approvals/${a.id}`}>{formatTime(a.created_at)}</Link></td>
                      <td><Badge value="sensitive" /> {a.payload.tool}</td>
                      <td>{a.payload.customer_id}</td>
                      <td>{formatTime(a.expires_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>No approvals are waiting.</Empty>
          )}
        </section>
      </div>
    </>
  );
}
