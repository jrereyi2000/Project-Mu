// The player page's Honors row (PROJECT_SPEC §5.2 item 13; docs/RECORD_BOOK_AND_AWARDS_PLAN.md phase 3): one chip per
// award the player has won, from the `awards` list on GET /api/player/{id}. Repeat wins fold into one chip ("2× MVP");
// a single All-Pro selection names its season and slot ("2035 First Team All-Pro (QB)"). Nothing renders without awards.
import { AWARD_LABELS } from "./format";
import "./history.css";

export type PlayerAward = { season: number; award_type: string; position: string | null; team: string | null };

const ORDER = ["MVP", "SBMVP", "OPOY", "DPOY", "OROY", "DROY", "First Team All-Pro", "Second Team All-Pro"];

export function honorChips(awards: PlayerAward[]): { key: string; label: string; title: string; href: string }[] {
  const groups = new Map<string, PlayerAward[]>();
  for (const a of awards) groups.set(a.award_type, [...(groups.get(a.award_type) || []), a]);
  return [...groups.entries()]
    .sort(([a], [b]) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99))
    .map(([type, rows]) => {
      const seasons = rows.map(r => r.season).sort((a, b) => a - b);
      const allPro = type.endsWith("All-Pro");
      const name = allPro ? type : AWARD_LABELS[type]?.short || type;
      const label = rows.length > 1
        ? `${rows.length}× ${name}`
        : allPro ? `${seasons[0]} ${name}${rows[0].position ? ` (${rows[0].position})` : ""}` : `${name} ${seasons[0]}`;
      return {
        key: type,
        label,
        title: `${allPro ? type : AWARD_LABELS[type]?.long || type}: ${seasons.join(", ")}`,
        href: allPro ? "#awards" : `#awards?award=${type}`,
      };
    });
}

export default function PlayerHonors({ awards }: { awards?: PlayerAward[] | null }) {
  const chips = honorChips(awards || []);
  if (!chips.length) return null;
  return (
    <div className="player-honors" aria-label="Honors">
      <small>HONORS</small>
      <div>
        {chips.map(c => <a key={c.key} href={c.href} title={c.title}>{c.label}</a>)}
      </div>
    </div>
  );
}
