import type { ReactNode } from "react";
import Link from "next/link";
import "./globals.css";

export const metadata = { title: "SEO Console" };
export const viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="top">
          <Link href="/" className="brand"><span className="logo" aria-hidden="true">S</span><strong>SEO Console</strong></Link>
          <span className="muted small">your sites, competitors and AI search in one place · read-only except approvals</span>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
