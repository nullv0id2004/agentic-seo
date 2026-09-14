export function fmtDate(v: unknown): string {
  if (!v) return "";
  const d = v instanceof Date ? v : new Date(String(v));
  return isNaN(d.getTime()) ? String(v) : d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function fmtUsd(v: unknown): string {
  const n = Number(v ?? 0);
  return `$${n.toFixed(2)}`;
}

export function str(v: unknown): string {
  return v == null ? "" : String(v);
}
