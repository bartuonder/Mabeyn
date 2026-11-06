import type { ReactNode } from "react";

import { Nav } from "@/components/Nav";
import { api } from "@/lib/api";
import type { Approval } from "@/lib/types";
import { logout } from "../actions";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: ReactNode }) {
  const pending = await api<Approval[]>("/approvals?status=pending");
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          Mabeyn
          <small>Admin</small>
        </div>
        <Nav pending={pending.length} />
        <div className="spacer" />
        <form action={logout}>
          <button type="submit" className="ghost" style={{ width: "100%" }}>Sign out</button>
        </form>
      </aside>
      <main>{children}</main>
    </div>
  );
}
