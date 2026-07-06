// Ported 1:1 from the Jinja filters in app/web/app.py (lines 107-146). Keep
// edge cases identical — src/lib/format.test.ts asserts the same outputs.

function stripTrailingZeros(s: string): string {
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}

/** null -> "n/a"; integers -> thousands-separated 0dp; non-integers -> 2dp,
 * trailing zeros/dot stripped. */
export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "n/a";
  if (n % 1 !== 0) {
    return stripTrailingZeros(
      n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    );
  }
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/** null -> "n/a"; >=1e12 $X.XXT; >=1e9 B; >=1e6 M; else $1,234. */
export function fmtCap(n: number | null | undefined): string {
  if (n === null || n === undefined) return "n/a";
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/** n*100 to 1dp + "%". */
export function pct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

export type IndicatorUnit = "pct" | "ratio" | "usd" | "count" | string;

/** null -> "n/a"; pct -> pct(); ratio -> 2dp; usd -> sign + $ + scaled
 * T/B/M/K with 1dp trailing-zero-stripped, else $1,234; count/default ->
 * thousands 0dp. */
export function fmtIndicatorValue(value: number | null | undefined, unit: IndicatorUnit): string {
  if (value === null || value === undefined) return "n/a";
  if (unit === "pct") return pct(value);
  if (unit === "ratio") return value.toFixed(2);
  if (unit === "usd") {
    const sign = value < 0 ? "-" : "";
    const n = Math.abs(value);
    const scales: [number, string][] = [
      [1e12, "T"],
      [1e9, "B"],
      [1e6, "M"],
      [1e3, "K"],
    ];
    for (const [div, suffix] of scales) {
      if (n >= div) {
        const scaled = stripTrailingZeros((n / div).toFixed(1));
        return `${sign}$${scaled}${suffix}`;
      }
    }
    return `${sign}$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  }
  // count / default
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}
