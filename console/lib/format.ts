export function fmtDate(v: unknown): string {
  if (!v) return "";
  const d = v instanceof Date ? v : new Date(String(v));
  return isNaN(d.getTime()) ? String(v) : d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function fmtDay(v: unknown): string {
  if (!v) return "";
  const d = v instanceof Date ? v : new Date(String(v));
  return isNaN(d.getTime()) ? String(v).slice(0, 10) : d.toISOString().slice(0, 10);
}

export function fmtUsd(v: unknown, digits = 2): string {
  const n = Number(v ?? 0);
  return `$${n.toFixed(digits)}`;
}

export function str(v: unknown): string {
  return v == null ? "" : String(v);
}

export function num(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function fmtInt(v: unknown): string {
  const n = num(v);
  return n == null ? "–" : Math.round(n).toLocaleString("en-US");
}

/** 1,284 / 12.9K / 4.2M */
export function fmtCompact(v: unknown): string {
  const n = num(v);
  if (n == null) return "–";
  if (Math.abs(n) < 10_000) return Math.round(n).toLocaleString("en-US");
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function fmtPct(v: unknown, digits = 1): string {
  const n = num(v);
  return n == null ? "–" : `${(n * 100).toFixed(digits)}%`;
}

export function fmtPos(v: unknown): string {
  const n = num(v);
  return n == null ? "–" : n.toFixed(1);
}

/** Signed change against a named period, or null when there is no baseline. */
export function delta(cur: unknown, prev: unknown): { text: string; dir: "up" | "down" | "flat" } | null {
  const c = num(cur);
  const p = num(prev);
  if (c == null || p == null) return null;
  if (p === 0) return c === 0 ? { text: "no change", dir: "flat" } : { text: `+${fmtCompact(c)} from 0`, dir: "up" };
  const pct = (c - p) / Math.abs(p);
  if (Math.abs(pct) < 0.005) return { text: "no change", dir: "flat" };
  return { text: `${pct > 0 ? "+" : ""}${(pct * 100).toFixed(0)}%`, dir: pct > 0 ? "up" : "down" };
}

export function truncate(s: unknown, n: number): string {
  const t = str(s);
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

export function pathOf(url: unknown): string {
  const u = str(url);
  return u.replace(/^https?:\/\/[^/]+/, "") || "/";
}
