import Link from "next/link";

import { Badge, Empty, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { Approval } from "@/lib/types";

export const metadata = { title: "Approvals - Mabeyn Admin" };

const FILTERS = [
  { value: "pending", label: "Pending" },
  { value: "", label: "All" },
];

export default async function Approvals({ searchParams }: { searchParams: Promise<{ status?: string }> }) {
  const { status = "pending" } = await searchParams;
  const rows = await api<Approval[]>(`/approvals${status ? `?status=${encodeURIComponent(status)}` : ""}`);

  return (
    <>
      <PageHead title="Approvals" hint="Sensitive actions the assistant wants to take wait here for a human decision.">
        <div className="row">
          {FILTERS.map((f) => (
            <Link key={f.label} className={f.value === status ? "button" : "button ghost"} href={f.value ? `/approvals?status=${f.value}` : "/approvals?status="} style={f.value === status ? undefined : { background: "transparent", color: "var(--ink)", border: "1px solid var(--line)" }}>
              {f.label}
            </Link>
          ))}
        </div>
      </PageHead>
      {rows.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Requested</th><th>Action</th><th>Customer</th><th>Summary</th><th>Status</th><th>Expires</th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td><Link href={`/approvals/${a.id}`}>{formatTime(a.created_at)}</Link></td>
                  <td>{a.payload.tool}</td>
                  <td>{a.payload.customer_id}</td>
                  <td>{a.payload.summary}</td>
                  <td><Badge value={a.status} /></td>
                  <td>{a.status === "pending" ? formatTime(a.expires_at) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>{status === "pending" ? "No approvals are waiting." : "No approvals yet."}</Empty>
      )}
    </>
  );
}
