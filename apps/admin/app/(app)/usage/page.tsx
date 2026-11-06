import Link from "next/link";

import { BarChart } from "@/components/BarChart";
import { ConfirmButton } from "@/components/ConfirmButton";
import { Empty, PageHead, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { everyDay } from "@/lib/days";
import { formatUsd } from "@/lib/format";
import type { UsageReport } from "@/lib/types";
import { clearCache } from "../../actions";

export const metadata = { title: "Usage - Mabeyn Admin" };

const RANGES = [7, 30, 90];

export default async function Usage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const { days: raw } = await searchParams;
  const days = RANGES.includes(Number(raw)) ? Number(raw) : 7;
  const report = await api<UsageReport>(`/usage?days=${days}`);
  const rows = report.days;
  const cached = rows.reduce((n, d) => n + d.cached_messages, 0);
  const llmCalls = rows.reduce((n, d) => n + d.llm_calls, 0);
  const chronological = everyDay(rows, days);

  return (
    <>
      <PageHead title="Usage" hint="Tokens, estimated cost and speed of the assistant's replies (UTC days).">
        <div className="row">
          {RANGES.map((r) => (
            <Link key={r} href={`/usage?days=${r}`} className="button" style={r === days ? undefined : { background: "transparent", color: "var(--ink)", border: "1px solid var(--line)" }}>
              {r} days
            </Link>
          ))}
        </div>
      </PageHead>
      <div className="cards">
        <Stat label="Messages" value={report.total_messages} hint={`${cached} answered from the cache`} />
        <Stat label="Estimated cost" value={formatUsd(report.total_cost_usd)} hint={`${llmCalls} model calls`} />
        <Stat label="Cost per message" value={report.total_messages ? formatUsd(report.total_cost_usd / report.total_messages) : "-"} />
      </div>
      <div className="stack">
        {rows.length ? (
          <>
            <section>
              <h2>Messages per day</h2>
              <BarChart unit="Messages" bars={chronological.map((d) => ({ label: d.day.slice(5), value: d.messages, detail: `${d.day}: ${d.messages} messages` }))} />
            </section>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Day</th><th className="num">Messages</th><th className="num">Cached</th><th className="num">Input tokens</th><th className="num">Output tokens</th><th className="num">Cost</th><th className="num">Avg latency</th></tr></thead>
                <tbody>
                  {rows.map((d) => (
                    <tr key={d.day}>
                      <td>{d.day}</td>
                      <td className="num">{d.messages}</td>
                      <td className="num">{d.cached_messages}</td>
                      <td className="num">{d.input_tokens.toLocaleString("en-GB")}</td>
                      <td className="num">{d.output_tokens.toLocaleString("en-GB")}</td>
                      <td className="num">{formatUsd(d.cost_usd)}</td>
                      <td className="num">{d.average_latency_ms === null ? "-" : `${(d.average_latency_ms / 1000).toFixed(1)} s`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <Empty>No replies in the last {days} days.</Empty>
        )}
        <section className="card">
          <h2>Answer cache</h2>
          <p className="muted">Repeated knowledge questions are answered from a cache. It is cleared automatically when documents or prompts change; clear it by hand if an answer looks stale.</p>
          <form action={clearCache}>
            <ConfirmButton message="Drop every cached answer?">Clear the cache</ConfirmButton>
          </form>
        </section>
      </div>
    </>
  );
}
