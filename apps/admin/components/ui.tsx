import Link from "next/link";
import type { ReactNode } from "react";

const TONES: Record<string, string> = {
  ready: "good", approved: "good", success: "good", closed: "good", read: "good",
  pending: "warn", processing: "warn", flagged: "warn", open: "info", write: "warn",
  failed: "bad", rejected: "bad", blocked: "bad", sensitive: "bad", escalated: "warn",
  expired: "", error: "bad",
};

export function Badge({ value }: { value: string }) {
  return <span className={`badge ${TONES[value] ?? ""}`}>{value}</span>;
}

export function PageHead({ title, hint, children }: { title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {hint && <p>{hint}</p>}
      </div>
      {children}
    </div>
  );
}

export function Stat({ label, value, hint, href }: { label: string; value: ReactNode; hint?: string; href?: string }) {
  const body = (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
  return href ? <Link href={href} style={{ color: "inherit", textDecoration: "none" }}>{body}</Link> : body;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ShortId({ id }: { id: string }) {
  return <code title={id}>{id.slice(0, 8)}</code>;
}
