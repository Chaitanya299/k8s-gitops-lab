import "./globals.css";
import type { Metadata } from "next";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "AI Platform",
  description: "Deploy AI services to Kubernetes via GitOps",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen">
          <Nav />
          <main className="flex-1 px-8 py-7">
            <div className="mx-auto max-w-5xl">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
