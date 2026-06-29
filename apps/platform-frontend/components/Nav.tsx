"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Deploy", icon: "▲" },
  { href: "/services", label: "Running Services", icon: "◆" },
  { href: "/history", label: "Deployment History", icon: "↺" },
  { href: "/monitoring", label: "Monitoring", icon: "◷" },
];

export default function Nav() {
  const path = usePathname();
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-900/40 p-4">
      <div className="mb-8 px-2">
        <div className="text-lg font-bold text-white">AI Platform</div>
        <div className="text-xs text-slate-500">GitOps control plane</div>
      </div>
      <nav className="flex flex-col gap-1">
        {LINKS.map((l) => {
          const active = path === l.href;
          return (
            <Link
              key={l.href}
              href={l.href}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
                active
                  ? "bg-accent/15 font-medium text-white"
                  : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
              }`}
            >
              <span className="text-accent">{l.icon}</span>
              {l.label}
            </Link>
          );
        })}
      </nav>
      {/* ponytail: Login/role badge goes here when JWT auth lands (deferred in v1). */}
      <div className="mt-auto px-3 text-xs text-slate-600">v1 · local kind</div>
    </aside>
  );
}
