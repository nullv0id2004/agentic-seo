import { num } from "@/lib/format";

const INTENTS: Record<string, [string, string]> = {
  informational: ["I", "Informational: the searcher wants to learn something"],
  navigational: ["N", "Navigational: the searcher wants a specific site"],
  commercial: ["C", "Commercial: the searcher is comparing options"],
  transactional: ["T", "Transactional: the searcher is ready to act"],
};

/** Search intent as a letter. Neutral on purpose: four hues side by side fail the palette's all-pairs floor. */
export function Intent({ value }: { value: unknown }) {
  const v = String(value ?? "").toLowerCase();
  const hit = INTENTS[v];
  if (!hit) return <span className="muted">–</span>;
  return <span className="intent" title={hit[1]} aria-label={hit[1]}>{hit[0]}</span>;
}

/** Keyword difficulty 0-100: the number plus a single-hue bar (a magnitude, so sequential, not traffic lights). */
export function Difficulty({ value }: { value: unknown }) {
  const n = num(value);
  if (n === null) return <span className="muted">–</span>;
  const word = n < 30 ? "easy" : n < 50 ? "possible" : n < 70 ? "difficult" : "very hard";
  return (
    <span className="kd" title={`Keyword difficulty ${Math.round(n)} of 100: ${word}`}>
      {Math.round(n)}<span className="bar" aria-hidden="true"><span style={{ width: `${Math.max(4, Math.min(100, n))}%` }} /></span>
    </span>
  );
}

/** A ranking position: bold inside the top 10, quiet past it, a dash when not ranking. */
export function Position({ value, title }: { value: unknown; title?: string }) {
  const n = num(value);
  if (n === null) return <span className="pos none" title={title ?? "not ranking in the data collected"}>–</span>;
  const r = Math.round(n);
  return <span className={`pos ${r <= 3 ? "top3" : r <= 10 ? "top10" : "deep"}`} title={title}>{r}</span>;
}
