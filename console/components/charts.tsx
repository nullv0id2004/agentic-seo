"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

// Charts follow the reference data-viz method: one y-axis, 2px lines, 10% area wash for a single
// series, >=8px end dot with a 2px surface ring, hairline solid grid, crosshair + tooltip on lines,
// per-column tooltip on bars, a legend for two or more series, and a table view for every chart.

export type Fmt = "int" | "pct" | "pos" | "usd" | "dec";

export interface Series {
  label: string;
  slot: 1 | 2 | 3;
  values: (number | null)[];
}

const COLOR = { 1: "var(--series-1)", 2: "var(--series-2)", 3: "var(--series-3)" } as const;

function fmt(v: number | null | undefined, f: Fmt): string {
  if (v == null || !Number.isFinite(v)) return "–";
  switch (f) {
    case "pct": return `${(v * 100).toFixed(1)}%`;
    case "pos": return v.toFixed(1);
    case "usd": return `$${v.toFixed(2)}`;
    case "dec": return v.toFixed(2);
    default: return Math.abs(v) >= 10_000
      ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v)
      : Math.round(v).toLocaleString("en-US");
  }
}

/** Round tick steps (1, 2, 2.5, 5 x 10^n; whole numbers for counts) so every tick label is exact. */
function niceScale(min: number, max: number, integer: boolean, count = 4): { lo: number; hi: number; ticks: number[] } {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  let step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  if (integer) step = Math.max(1, Math.ceil(step));
  const lo = Math.floor(min / step) * step;
  let hi = Math.ceil(max / step) * step;
  if (hi <= lo) hi = lo + step;
  const ticks: number[] = [];
  for (let t = lo; t <= hi + step / 1000; t += step) ticks.push(Number(t.toFixed(10)));
  return { lo, hi, ticks };
}

function useWidth(initial = 640) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setW(Math.max(240, Math.floor(entries[0].contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function shortDay(d: string): string {
  // "2026-10-02" -> "2 Oct"
  const t = new Date(d + "T00:00:00Z");
  return isNaN(t.getTime()) ? d : t.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

function TableView({ x, series, f, xLabel }: { x: string[]; series: Series[]; f: Fmt; xLabel: string }) {
  return (
    <details className="table-view">
      <summary>Table view</summary>
      <div className="scroll">
        <table>
          <thead><tr><th>{xLabel}</th>{series.map((s) => <th key={s.label} className="num">{s.label}</th>)}</tr></thead>
          <tbody>
            {x.map((d, i) => (
              <tr key={d}><td className="nowrap">{d}</td>{series.map((s) => <td key={s.label} className="num">{fmt(s.values[i], f)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function Legend({ series, kind }: { series: Series[]; kind: "line" | "rect" }) {
  if (series.length < 2) return null;
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.label}><span className={`key ${kind === "line" ? "line" : ""}`} style={{ background: COLOR[s.slot] }} />{s.label}</span>
      ))}
    </div>
  );
}

export function LineChart({ title, note, x, series, format = "int", invert = false, height = 180, xLabel = "Date" }: {
  title: string; note?: string; x: string[]; series: Series[]; format?: Fmt; invert?: boolean; height?: number; xLabel?: string;
}) {
  const [wrapRef, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 44, r: 16, t: 10, b: 22 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const all = series.flatMap((s) => s.values).filter((v): v is number => v != null && Number.isFinite(v));
  const hasData = all.length > 0;
  const integer = format === "int" || format === "pos";
  const scale = invert
    ? niceScale(Math.max(1, Math.min(...(hasData ? all : [1]))), Math.max(...(hasData ? all : [10])), true)
    : niceScale(0, Math.max(...(hasData ? all : [0])), integer);
  const { lo, hi } = scale;
  const xAt = (i: number) => pad.l + (x.length <= 1 ? plotW / 2 : (i / (x.length - 1)) * plotW);
  const yAt = (v: number) => {
    const t = (v - lo) / (hi - lo || 1);
    return invert ? pad.t + t * plotH : pad.t + plotH - t * plotH;
  };
  const ticks = scale.ticks;
  const xTicks = useMemo(() => {
    const n = Math.min(x.length, Math.max(2, Math.floor(plotW / 90)));
    if (x.length <= 1) return x.map((_, i) => i);
    return Array.from(new Set(Array.from({ length: n }, (_, k) => Math.round((k * (x.length - 1)) / (n - 1)))));
  }, [x, plotW]);

  // Each unbroken run of values becomes one path; a null (no collection that day) breaks the line.
  function segments(values: (number | null)[]) {
    const out: { d: string; i0: number; i1: number }[] = [];
    let start = -1;
    let d = "";
    values.forEach((v, i) => {
      if (v == null) {
        if (start >= 0) out.push({ d, i0: start, i1: i - 1 });
        start = -1; d = "";
        return;
      }
      if (start < 0) start = i;
      d += `${d ? "L" : "M"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`;
    });
    if (start >= 0) out.push({ d, i0: start, i1: values.length - 1 });
    return out;
  }

  function onMove(e: PointerEvent<SVGRectElement>) {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const px = e.clientX - rect.left - pad.l;
    const i = x.length <= 1 ? 0 : Math.round((px / plotW) * (x.length - 1));
    setHover(Math.max(0, Math.min(x.length - 1, i)));
  }
  function onKey(e: KeyboardEvent<SVGSVGElement>) {
    if (e.key === "ArrowRight") setHover((h) => Math.min(x.length - 1, (h ?? -1) + 1));
    if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? x.length) - 1));
    if (e.key === "Escape") setHover(null);
  }

  const lastIdx = (vals: (number | null)[]) => { for (let i = vals.length - 1; i >= 0; i--) if (vals[i] != null) return i; return -1; };
  const tipLeft = hover == null ? 0 : Math.min(Math.max(xAt(hover) + 10, 0), width - 170);

  return (
    <figure className="chart">
      <figcaption><strong>{title}</strong>{note ? <span className="muted small">{note}</span> : null}</figcaption>
      <Legend series={series} kind="line" />
      <div ref={wrapRef} style={{ position: "relative" }}>
        {!hasData ? <div className="empty">No data in this range yet</div> : (
          <svg height={height} role="img" aria-label={`${title}. Use the table view for exact values.`} tabIndex={0} onKeyDown={onKey}
               onBlur={() => setHover(null)}>
            {ticks.map((t) => (
              <g key={t}>
                <line className={t === (invert ? hi : 0) ? "baseline" : "gridline"} x1={pad.l} x2={width - pad.r} y1={yAt(t)} y2={yAt(t)} />
                {!(invert && t === 0) && <text className="tick" x={pad.l - 6} y={yAt(t) + 4} textAnchor="end">{fmt(t, format === "pos" ? "int" : format)}</text>}
              </g>
            ))}
            {xTicks.map((i) => (
              <text key={i} className="tick" x={xAt(i)} y={height - 4} textAnchor={i === 0 ? "start" : i === x.length - 1 ? "end" : "middle"}>{shortDay(x[i])}</text>
            ))}
            {series.length === 1 && !invert && segments(series[0].values).map((g, k) => (
              <path key={`a${k}`} d={`${g.d}L${xAt(g.i1).toFixed(1)},${yAt(lo)}L${xAt(g.i0).toFixed(1)},${yAt(lo)}Z`} fill="var(--series-1-wash)" stroke="none" />
            ))}
            {series.map((s) => segments(s.values).map((g, k) => (
              <path key={`${s.label}${k}`} d={g.d} fill="none" stroke={COLOR[s.slot]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            )))}
            {series.map((s) => segments(s.values).filter((g) => g.i0 === g.i1).map((g) => (
              <circle key={`pt${s.label}${g.i0}`} cx={xAt(g.i0)} cy={yAt(s.values[g.i0] as number)} r={3} fill={COLOR[s.slot]} />
            )))}
            {series.map((s) => {
              const i = lastIdx(s.values);
              if (i < 0) return null;
              const v = s.values[i] as number;
              return (
                <g key={`end${s.label}`}>
                  <circle cx={xAt(i)} cy={yAt(v)} r={4} fill={COLOR[s.slot]} stroke="var(--surface)" strokeWidth={2} />
                  {series.length === 1 && hover == null && (
                    <text className="tick end-label" x={xAt(i) - 8} y={yAt(v) - 8} textAnchor="end">{fmt(v, format)}</text>
                  )}
                </g>
              );
            })}
            {hover != null && (
              <g>
                <line className="crosshair" x1={xAt(hover)} x2={xAt(hover)} y1={pad.t} y2={pad.t + plotH} />
                {series.map((s) => s.values[hover] != null && (
                  <circle key={s.label} cx={xAt(hover)} cy={yAt(s.values[hover] as number)} r={4} fill={COLOR[s.slot]} stroke="var(--surface)" strokeWidth={2} />
                ))}
              </g>
            )}
            <rect x={pad.l} y={0} width={plotW} height={height} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
          </svg>
        )}
        {hover != null && hasData && (
          <div className="tooltip" style={{ left: tipLeft, top: 4 }}>
            <div className="k">{x[hover]}</div>
            {series.map((s) => (
              <div key={s.label}>
                <span className="legend"><span className="key line" style={{ background: COLOR[s.slot] }} /></span>
                <span className="v">{fmt(s.values[hover], format)}</span> <span className="k">{s.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {hasData && <TableView x={x} series={series} f={format} xLabel={xLabel} />}
    </figure>
  );
}

export function ColumnChart({ title, note, x, series, format = "int", height = 180, xLabel = "Date" }: {
  title: string; note?: string; x: string[]; series: Series[]; format?: Fmt; height?: number; xLabel?: string;
}) {
  const [wrapRef, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 44, r: 8, t: 10, b: 22 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const totals = x.map((_, i) => series.reduce((a, s) => a + (s.values[i] ?? 0), 0));
  const hasData = totals.some((t) => t > 0);
  const scale = niceScale(0, Math.max(0, ...totals), format === "int");
  const hi = scale.hi;
  const band = plotW / Math.max(1, x.length);
  const barW = Math.max(2, Math.min(24, band - 2));
  const yAt = (v: number) => pad.t + plotH - (v / hi) * plotH;
  const ticks = scale.ticks;
  const labelEvery = Math.max(1, Math.ceil(x.length / Math.max(2, Math.floor(plotW / 70))));
  const tipLeft = hover == null ? 0 : Math.min(Math.max(pad.l + band * hover + band / 2 + 10, 0), width - 180);

  return (
    <figure className="chart">
      <figcaption><strong>{title}</strong>{note ? <span className="muted small">{note}</span> : null}</figcaption>
      <Legend series={series} kind="rect" />
      <div ref={wrapRef} style={{ position: "relative" }}>
        {!hasData ? <div className="empty">No data in this range yet</div> : (
          <svg height={height} role="img" aria-label={`${title}. Use the table view for exact values.`}>
            {ticks.map((t) => (
              <g key={t}>
                <line className={t === 0 ? "baseline" : "gridline"} x1={pad.l} x2={width - pad.r} y1={yAt(t)} y2={yAt(t)} />
                <text className="tick" x={pad.l - 6} y={yAt(t) + 4} textAnchor="end">{fmt(t, format)}</text>
              </g>
            ))}
            {x.map((d, i) => {
              const cx = pad.l + band * i + band / 2;
              let base = 0;
              const segs = series.map((s) => {
                const v = s.values[i] ?? 0;
                const y0 = yAt(base);
                base += v;
                return { s, v, y0, y1: yAt(base) };
              }).filter((g) => g.v > 0);
              return (
                <g key={d} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} tabIndex={0}>
                  <rect x={pad.l + band * i} y={pad.t} width={band} height={plotH} fill="transparent" />
                  {segs.map((g, k) => {
                    const top = k === segs.length - 1;
                    const h = Math.max(0, g.y0 - g.y1 - (k > 0 ? 2 : 0));
                    const y = g.y1;
                    const r = top ? Math.min(4, h / 2, barW / 2) : 0;
                    const x0 = cx - barW / 2;
                    const d = `M${x0},${y + h}V${y + r}Q${x0},${y} ${x0 + r},${y}H${x0 + barW - r}Q${x0 + barW},${y} ${x0 + barW},${y + r}V${y + h}Z`;
                    return <path key={g.s.label} d={d} fill={COLOR[g.s.slot]} opacity={hover == null || hover === i ? 1 : 0.55} />;
                  })}
                  {i % labelEvery === 0 && <text className="tick" x={cx} y={height - 4} textAnchor="middle">{shortDay(d)}</text>}
                </g>
              );
            })}
          </svg>
        )}
        {hover != null && hasData && (
          <div className="tooltip" style={{ left: tipLeft, top: 4 }}>
            <div className="k">{x[hover]}</div>
            {series.map((s) => (
              <div key={s.label}>
                <span className="legend"><span className="key" style={{ background: COLOR[s.slot] }} /></span>
                <span className="v">{fmt(s.values[hover], format)}</span> <span className="k">{s.label}</span>
              </div>
            ))}
            {series.length > 1 && <div><span className="v">{fmt(totals[hover], format)}</span> <span className="k">total</span></div>}
          </div>
        )}
      </div>
      {hasData && <TableView x={x} series={series} f={format} xLabel={xLabel} />}
    </figure>
  );
}
