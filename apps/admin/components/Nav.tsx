"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/approvals", label: "Approvals" },
  { href: "/conversations", label: "Conversations" },
  { href: "/documents", label: "Documents" },
  { href: "/tools", label: "Tools" },
  { href: "/security", label: "Security" },
  { href: "/usage", label: "Usage" },
  { href: "/keys", label: "API keys" },
];

export function Nav({ pending }: { pending: number }) {
  const path = usePathname();
  return (
    <nav aria-label="Main">
      {ITEMS.map((item) => {
        const active = item.href === "/" ? path === "/" : path.startsWith(item.href);
        return (
          <Link key={item.href} href={item.href} className="link" aria-current={active ? "page" : undefined}>
            <span>{item.label}</span>
            {item.href === "/approvals" && pending > 0 && <span className="badge warn">{pending}</span>}
          </Link>
        );
      })}
    </nav>
  );
}
