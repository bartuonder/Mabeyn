import type { UsageDay } from "./types";

// The API returns only days that had messages; a chart needs every day of the range, oldest first.
export function everyDay(days: UsageDay[], count: number, today = new Date()): UsageDay[] {
  const byDay = new Map(days.map((d) => [d.day, d]));
  const result: UsageDay[] = [];
  for (let back = count - 1; back >= 0; back--) {
    const date = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - back));
    const key = date.toISOString().slice(0, 10);
    result.push(
      byDay.get(key) ?? {
        day: key, messages: 0, input_tokens: 0, output_tokens: 0, llm_calls: 0,
        cost_usd: 0, cached_messages: 0, average_latency_ms: null,
      },
    );
  }
  return result;
}
