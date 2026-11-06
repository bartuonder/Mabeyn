"use client";

import { useState } from "react";

export type Bar = { label: string; value: number; detail: string };

const W = 640;
const H = 200;
const PAD = { top: 12, right: 8, bottom: 26, left: 36 };

// One series, one colour: the title names it, so there is no legend. Bars are thin with a rounded
// top, the grid is a single recessive baseline plus a few ticks, and each bar has a hover tooltip
// and a keyboard-visible label; the table below the chart carries the same numbers.
export function BarChart({ bars, unit }: { bars: Bar[]; unit: string }) {
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(1, ...bars.map((b) => b.value));
  const top = Math.ceil(max / 4) * 4 || 4;
  const inner = { w: W - PAD.left - PAD.right, h: H - PAD.top - PAD.bottom };
  const slot = inner.w / Math.max(bars.length, 1);
  const width = Math.min(28, slot * 0.6);
  const y = (v: number) => PAD.top + inner.h - (v / top) * inner.h;
  const ticks = [0, top / 2, top];

  return (
    <div className="chart" role="img" aria-label={`${unit} per day`} style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="axis" x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end">{Math.round(t)}</text>
          </g>
        ))}
        {bars.map((bar, i) => {
          const x = PAD.left + slot * i + (slot - width) / 2;
          const h = Math.max((bar.value / top) * inner.h, bar.value > 0 ? 2 : 0);
          return (
            <g key={bar.label} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)}>
              <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={inner.h} fill="transparent" />
              {h > 0 && <rect className="bar" x={x} y={y(bar.value)} width={width} height={h} rx={4} ry={4} />}
              {(bars.length <= 10 || i % Math.ceil(bars.length / 10) === 0) && (
                <text x={x + width / 2} y={H - 8} textAnchor="middle">{bar.label}</text>
              )}
            </g>
          );
        })}
      </svg>
      {active !== null && (
        <div
          role="tooltip"
          style={{
            position: "absolute",
            top: 8,
            left: `${((PAD.left + slot * active + slot / 2) / W) * 100}%`,
            transform: "translateX(-50%)",
            background: "var(--ink)",
            color: "var(--bg)",
            padding: "4px 8px",
            borderRadius: 6,
            fontSize: 12,
            whiteSpace: "nowrap",
            pointerEvents: "none",
          }}
        >
          {bars[active].detail}
        </div>
      )}
    </div>
  );
}
