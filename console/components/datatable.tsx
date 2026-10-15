"use client";

import { useMemo, useState, type ReactNode } from "react";

/** A column. `num` right-aligns and sorts high-to-low first; `sort: false` disables sorting on it. */
export interface Col { label: string; num?: boolean; sort?: boolean; title?: string }

/**
 * One row. `cells` is what is shown (any JSX, built on the server); `values` is what sorting, search and the
 * CSV export use, one per column, so a link or badge still sorts and exports as its plain value.
 */
export interface DRow { key: string; cells: ReactNode[]; values: (string | number | null)[]; facet?: string }

export interface Facet { key: string; label: string }

function cmp(a: string | number | null, b: string | number | null): number {
  if (a === b) return 0;
  if (a === null || a === "") return 1;         // empty values sort last in both directions
  if (b === null || b === "") return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

function csvCell(v: string | number | null): string {
  const s = v === null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

export function DataTable({ columns, rows, facets, defaultFacet, defaultSort, filename = "export", pageSize = 50, empty = "Nothing to show.", searchLabel = "Filter rows" }: {
  columns: Col[]; rows: DRow[]; facets?: Facet[]; defaultFacet?: string; defaultSort?: { col: number; desc: boolean };
  filename?: string; pageSize?: number; empty?: ReactNode; searchLabel?: string;
}) {
  const [query, setQuery] = useState("");
  const [facet, setFacet] = useState<string>(defaultFacet ?? "all");
  const [sort, setSort] = useState<{ col: number; desc: boolean } | null>(defaultSort ?? null);
  const [shown, setShown] = useState(pageSize);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) if (r.facet) c[r.facet] = (c[r.facet] ?? 0) + 1;
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = rows.filter((r) => (facet === "all" || r.facet === facet) && (!q || r.values.some((v) => v !== null && String(v).toLowerCase().includes(q))));
    if (sort) {
      out = [...out].sort((a, b) => {
        const x = a.values[sort.col] ?? null;
        const y = b.values[sort.col] ?? null;
        if (x === null || x === "" || y === null || y === "") return cmp(x, y);
        return sort.desc ? cmp(y, x) : cmp(x, y);
      });
    }
    return out;
  }, [rows, query, facet, sort]);

  function toggle(i: number) {
    setShown(pageSize);
    setSort((s) => (s && s.col === i ? { col: i, desc: !s.desc } : { col: i, desc: !!columns[i].num }));
  }

  function download() {
    const lines = [columns.map((c) => csvCell(c.label)).join(","), ...visible.map((r) => r.values.map(csvCell).join(","))];
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filename}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <div className="dt-toolbar">
        <input type="search" placeholder={`${searchLabel}…`} aria-label={searchLabel} value={query} onChange={(e) => { setQuery(e.target.value); setShown(pageSize); }} />
        {facets && facets.length > 0 ? (
          <div className="chips" role="group" aria-label="Show">
            {[{ key: "all", label: "All" }, ...facets].map((f) => (
              <button key={f.key} type="button" aria-pressed={facet === f.key} onClick={() => { setFacet(f.key); setShown(pageSize); }}>
                {f.label}<span className="n">{f.key === "all" ? rows.length : counts[f.key] ?? 0}</span>
              </button>
            ))}
          </div>
        ) : null}
        <span className="spacer" />
        <span className="muted small">{visible.length.toLocaleString("en-IN")} row(s)</span>
        <button type="button" onClick={download} disabled={visible.length === 0}>Export CSV</button>
      </div>
      <div className="scroll dt"><table>
        <thead><tr>
          {columns.map((c, i) => {
            const active = sort?.col === i;
            const aria = active ? (sort!.desc ? "descending" : "ascending") : "none";
            return (
              <th key={i} className={c.num ? "num" : undefined} aria-sort={c.sort === false ? undefined : aria} title={c.title}>
                {c.sort === false ? c.label : (
                  <button type="button" className="sort" onClick={() => toggle(i)}>
                    {c.label}<span className="arrow" aria-hidden="true">{active ? (sort!.desc ? "▼" : "▲") : "↕"}</span>
                  </button>
                )}
              </th>
            );
          })}
        </tr></thead>
        <tbody>
          {visible.slice(0, shown).map((r) => (
            <tr key={r.key}>{r.cells.map((cell, i) => <td key={i} className={columns[i]?.num ? "num" : undefined}>{cell}</td>)}</tr>
          ))}
          {visible.length === 0 ? <tr><td colSpan={columns.length} className="muted">{empty}</td></tr> : null}
        </tbody>
      </table></div>
      {visible.length > shown ? (
        <div className="dt-foot">
          <span>Showing {shown.toLocaleString("en-IN")} of {visible.length.toLocaleString("en-IN")}</span>
          <button type="button" onClick={() => setShown((n) => n + pageSize * 2)}>Show more</button>
        </div>
      ) : null}
    </div>
  );
}
