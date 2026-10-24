// Shared formatting for the Team hub's Front Office panels.

// Whole dollars as money: 31600000 -> "$31.6M", 860000 -> "$860K". null -> "—" (not recorded, never $0).
export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value < 0 ? "-" : "";
  const a = Math.abs(value);
  if (a >= 1_000_000) return `${sign}$${(a / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (a >= 1_000) return `${sign}$${(a / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return `${sign}$${a}`;
}

export function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// The database's abbreviations (AZ, WAS) as ESPN's logo names.
export function teamLogo(abbr: string): string {
  const clean = (abbr || "").toLowerCase();
  const name = clean === "az" ? "ari" : clean === "was" ? "wsh" : clean;
  return `https://a.espncdn.com/i/teamlogos/nfl/500/${name}.png`;
}
