"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Commit = { commit: string; author: string; date: string; message: string };

export default function HistoryPage() {
  const [commits, setCommits] = useState<Commit[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Commit[]>("/history")
      .then(setCommits)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-white">Deployment history</h1>
      <p className="mt-1 text-sm text-slate-400">
        Git is the source of truth — every deploy is a commit. Rollback = revert.
      </p>

      {error && (
        <div className="card mt-5 border-red-900/60 bg-red-950/30 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="card mt-5 overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead className="bg-slate-900/80 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Commit</th>
              <th className="px-4 py-3">Message</th>
              <th className="px-4 py-3">Author</th>
              <th className="px-4 py-3">When</th>
            </tr>
          </thead>
          <tbody>
            {commits.map((c) => (
              <tr key={c.commit} className="border-t border-slate-800">
                <td className="px-4 py-3 font-mono text-accent">{c.commit}</td>
                <td className="px-4 py-3 text-slate-200">{c.message}</td>
                <td className="px-4 py-3 text-slate-400">{c.author}</td>
                <td className="px-4 py-3 text-slate-400">
                  {new Date(c.date).toLocaleString()}
                </td>
              </tr>
            ))}
            {commits.length === 0 && !error && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-slate-500">
                  No deployments yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
