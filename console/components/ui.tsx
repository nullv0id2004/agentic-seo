import Link from "next/link";
import type { ReactNode } from "react";
import { delta } from "@/lib/format";

export const TABS = [
  ["overview", "Overview"],
  ["changes", "Changes"],
  ["search", "Search"],
  ["traffic", "Traffic"],
  ["keywords", "Keywords"],
  ["rankings", "Rankings"],
  ["technical", "Technical"],
  ["ai", "AI visibility"],
  ["content", "Content & outreach"],
  ["operations", "Operations"],
] as const;
export type TabKey = (typeof TABS)[number][0];

export const RANGES = [7, 28, 90] as const;

export function Tabs({ slug, active, range }: { slug: string; active: TabKey; range: number }) {
  return (
    <div className="tabs" role="navigation" aria-label="Project sections">
      {TABS.map(([key, label]) => (
        <Link key={key} href={`/projects/${slug}?tab=${key}&range=${range}`} aria-current={key === active ? "page" : undefined}>{label}</Link>
      ))}
    </div>
  );
}

export function RangeFilter({ slug, tab, range, note }: { slug: string; tab: TabKey; range: number; note?: ReactNode }) {
  return (
    <div className="filters">
      <span className="muted small">Range</span>
      {RANGES.map((r) => (
        <Link key={r} href={`/projects/${slug}?tab=${tab}&range=${r}`} aria-current={r === range ? "true" : undefined}>Last {r} days</Link>
      ))}
      {note ? <span className="muted small">{note}</span> : null}
    </div>
  );
}

/** A 12-ish point sparkline: de-emphasis line, current point in the accent. */
function Sparkline({ values }: { values: (number | null)[] }) {
  const pts = values.map((v, i) => ({ v, i })).filter((p): p is { v: number; i: number } => p.v != null);
  if (pts.length < 2) return null;
  const max = Math.max(...pts.map((p) => p.v));
  const min = Math.min(...pts.map((p) => p.v));
  const w = 120;
  const h = 28;
  const x = (i: number) => 2 + (i / Math.max(1, values.length - 1)) * (w - 4);
  const y = (v: number) => (max === min ? h / 2 : 3 + (1 - (v - min) / (max - min)) * (h - 6));
  const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={d} fill="none" stroke="var(--axis)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      <line x1={x(last.i)} y1={y(last.v)} x2={x(last.i)} y2={y(last.v)} stroke="var(--series-1)" strokeWidth={6} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function StatTile({ label, value, prev, current, period, upIsGood = true, trend, foot }: {
  label: string; value: string; prev?: unknown; current?: unknown; period?: string; upIsGood?: boolean;
  trend?: (number | null)[]; foot?: ReactNode;
}) {
  const d = prev !== undefined ? delta(current, prev) : null;
  const good = d && d.dir !== "flat" ? (d.dir === "up") === upIsGood : null;
  return (
    <div className="tile">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {d ? (
        <span className={`delta ${good === true ? "up-good" : good === false ? "down-bad" : ""}`}>
          {d.dir === "up" ? "▲ " : d.dir === "down" ? "▼ " : ""}{d.text}{period ? ` vs ${period}` : ""}
        </span>
      ) : foot ? null : <span className="delta">{period ? `no data in the ${period} to compare` : " "}</span>}
      {foot ? <span className="delta">{foot}</span> : null}
      {trend ? <Sparkline values={trend} /> : null}
    </div>
  );
}

const SEVERITY_CLASS: Record<string, string> = { critical: "critical", high: "serious", medium: "warning", low: "" };

export function Severity({ level }: { level: unknown }) {
  const s = String(level ?? "");
  return <span className={`pill ${SEVERITY_CLASS[s] ?? ""}`}>{s}</span>;
}

export function Status({ value }: { value: unknown }) {
  const s = String(value ?? "");
  const cls = ["done", "executed", "approved", "ok", "pass", "indexed", "yes"].includes(s) ? "ok"
    : s.startsWith("halted") || ["failed", "rejected", "critical", "fail", "no"].includes(s) ? "critical"
    : ["pending", "paused_for_approval", "running", "partial", "draft", "proposed"].includes(s) ? "warning" : "";
  return <span className={`pill ${cls}`}>{s.replaceAll("_", " ")}</span>;
}

export function Empty({ cols, children }: { cols: number; children: ReactNode }) {
  return <tr><td colSpan={cols} className="muted">{children}</td></tr>;
}

export function Meter({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(1, value / max) : 0;
  const cls = pct >= 1 ? "over" : pct >= 0.8 ? "warn" : "";
  return (
    <div className={`meter ${cls}`} role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <span style={{ width: `${(pct * 100).toFixed(1)}%` }} />
    </div>
  );
}

export function Section({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <h2>{title}</h2>
      {note ? <p className="muted small">{note}</p> : null}
      {children}
    </section>
  );
}
