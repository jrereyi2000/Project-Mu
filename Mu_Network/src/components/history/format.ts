// Shared formatting for the Record Book and Awards pages (components/history).
export { ordinal, teamLogo } from "../team/format";

export type Mark = {
  value: number;
  holder: string;
  player_id: number | null;
  team: string | null;
  season: number | null;
  game_date: string | null;
  source: "NFL" | "BOOK" | "LEAGUE";
  mu: boolean;
  in_progress: boolean;
  rank?: number;
};

// A record value with its unit, from the category key: 5497 -> "5,497 yds", 30.5 sacks -> "30.5 sacks".
export function recordValue(category: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(1);
  return `${n} ${recordUnit(category, value)}`.trim();
}

export function recordUnit(category: string, value = 2): string {
  const one = value === 1;
  if (category.endsWith("fg_long")) return "yd";
  if (category.includes("yards")) return one ? "yd" : "yds";
  if (category.includes("tds")) return one ? "TD" : "TDs";
  if (category.includes("receptions")) return one ? "catch" : "catches";
  if (category.includes("sacks")) return one ? "sack" : "sacks";
  if (category.includes("interceptions") || category.includes("def_ints")) return one ? "INT" : "INTs";
  if (category.includes("tackles")) return "tackles";
  if (category.endsWith("_fgm")) return "FGs";
  if (category.includes("points")) return "pts";
  if (category.includes("wins")) return one ? "win" : "wins";
  return "";
}

// Where a mark comes from, as the badge reads.
export function sourceLabel(source: Mark["source"]): string {
  return source === "NFL" ? "NFL history" : source === "BOOK" ? "Record book" : "Mu season";
}

export const GROUP_LABELS: Record<string, string> = {
  SEASON: "Single season",
  GAME: "Single game",
  ROOKIE: "Rookie",
  SUPER_BOWL: "Super Bowl",
  CAREER: "Career",
};

export const SUBJECT_LABELS: Record<string, string> = { PLAYER: "Player", TEAM: "Team", COACH: "Coach" };

// The database's abbreviations (AZ, WAS) as the site's team routes (ari, wsh), for matching the ?team= filter.
export function siteAbbr(abbr: string | null | undefined): string {
  const clean = (abbr || "").toLowerCase();
  return clean === "az" ? "ari" : clean === "was" ? "wsh" : clean;
}

export const AWARD_LABELS: Record<string, { short: string; long: string }> = {
  MVP: { short: "MVP", long: "MVP" },
  OPOY: { short: "OPOY", long: "Offensive Player" },
  DPOY: { short: "DPOY", long: "Defensive Player" },
  OROY: { short: "OROY", long: "Offensive Rookie" },
  DROY: { short: "DROY", long: "Defensive Rookie" },
  COTY: { short: "COTY", long: "Coach of the Year" },
  SBMVP: { short: "SB MVP", long: "Super Bowl MVP" },
};
