"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Re-renders the page's server data on an interval while something is still in progress.
export function AutoRefresh({ seconds, active }: { seconds: number; active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), seconds * 1000);
    return () => clearInterval(timer);
  }, [active, seconds, router]);
  return null;
}
