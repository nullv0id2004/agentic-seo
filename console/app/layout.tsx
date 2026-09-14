import type { ReactNode } from "react";
import Link from "next/link";
import "./globals.css";

export const metadata = { title: "SEO Console" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav>
          <Link href="/"><strong>SEO Console</strong></Link>
          <span className="muted">read-only, approvals only</span>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
