// The player page's Career Path row (docs/in_progress/SEASON_CONTINUITY_PLAN.md phase 4): his moves between captured seasons from
// the player season ledger (`career_path` on GET /api/player/{id}): moved, drafted, joined, free agent, retired, and
// how his career stands now when he isn't on a roster. Seasons he simply stayed are left out; nothing renders when
// there's nothing to say.

type Move = {
  season: number;
  status: "MOVED" | "RETIRED" | "FREE_AGENT" | "DRAFTED" | "NEW" | "RETAINED";
  source: string | null;
  flags: string | null;
  from_team: string | null;
  to_team: string | null;
};
type Now = { status: "ACTIVE" | "RETIRED" | "FREE_AGENT"; team?: string; after_season?: number; basis?: string };
export type CareerPath = { moves: Move[]; now: Now } | null;

function signedWeek(flags: string | null): string | null {
  const m = (flags || "").match(/signed_in_season:week (\d+)/);
  return m ? m[1] : null;
}

function moveLabel(m: Move): string {
  const week = signedWeek(m.flags);
  switch (m.status) {
    case "MOVED":
      return `${m.season} · ${m.from_team ?? "?"} → ${m.to_team ?? "?"}${m.source === "TRADE" ? " (trade)" : ""}`;
    case "DRAFTED":
      return `${m.season} · Drafted by ${m.to_team ?? "?"}`;
    case "NEW":
      return `${m.season} · Joined ${m.to_team ?? "?"}${(m.flags || "").includes("undrafted_rookie") ? " (undrafted)" : ""}${week ? `, Week ${week}` : ""}`;
    case "FREE_AGENT":
      return `${m.season} · Free agent after ${m.from_team ?? "?"}${week ? ` · signed Week ${week}` : ""}`;
    case "RETIRED":
      return `Retired after ${m.season - 1}`;
    default:
      return `${m.season}`;
  }
}

export default function PlayerCareerPath({ path }: { path: CareerPath }) {
  if (!path) return null;
  const chips = path.moves.map((m) => ({ key: `${m.season}-${m.status}`, label: moveLabel(m) }));
  const { now } = path;
  if (now.status === "RETIRED" && !path.moves.some((m) => m.status === "RETIRED")) {
    chips.push({ key: "now", label: `Retired after ${now.after_season}` });
  } else if (now.status === "FREE_AGENT") {
    chips.push({ key: "now", label: `Free agent since ${(now.after_season ?? 0) + 1}` });
  }
  if (!chips.length) return null;
  return (
    <div className="player-honors player-career-path" aria-label="Career path">
      <small>CAREER PATH</small>
      <div>
        {chips.map((c) => <span key={c.key}>{c.label}</span>)}
      </div>
    </div>
  );
}
