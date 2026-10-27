// The Team hub's History tab (docs/HISTORY_BACKFILL_PLAN.md phase 4; PROJECT_SPEC §3.3j): every season this league
// records, from GET /api/team/{abbr}/history. 2026–2034 are reconstructed league history (marked "hist"); 2035 on
// come from the season archive; the current season shows its record so far.
import { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { ordinal } from "./format";

type Season = {
  season: number;
  wins: number | null;
  losses: number | null;
  ties: number | null;
  points_for?: number | null;
  points_against?: number | null;
  division_place?: number | null;
  conference_seed?: number | null;
  playoff_result?: string | null;
  playoff_wins?: number | null;
  playoff_losses?: number | null;
  coach_name: string | null;
  gm_name?: string | null;
  history: boolean;
  in_progress?: boolean;
  awards: { award: string; name: string }[];
};
type History = {
  seasons: Season[];
  summary: { since: number | null; wins: number; losses: number; playoffs: number; titles: number[]; super_bowls: number[] };
};

const RESULT: Record<string, string> = {
  CHAMPION: "Won Super Bowl", SUPER_BOWL_LOSS: "Lost Super Bowl", CONFERENCE: "Lost conference title game",
  DIVISIONAL: "Lost in Divisional round", WILD_CARD: "Lost in Wild Card round", MISSED: "Missed playoffs",
};

function record(s: Season): string {
  if (s.wins == null || s.losses == null) return "—";
  return s.ties ? `${s.wins}-${s.losses}-${s.ties}` : `${s.wins}-${s.losses}`;
}

export default function TeamHistory({ abbr }: { abbr: string }) {
  const [data, setData] = useState<History | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setData(null);
    fetch(`${API_BASE}/api/team/${abbr}/history`)
      .then((res) => res.json())
      .then((d) => (d.error ? setError(true) : setData(d)))
      .catch(() => setError(true));
  }, [abbr]);

  if (error) return <section className="team-history"><p className="player-log-note">History isn't available.</p></section>;
  if (!data) return <section className="team-history"><p className="player-log-note">Loading history…</p></section>;
  const { seasons, summary } = data;
  const hasHistory = seasons.some((s) => s.history);

  return (
    <section className="team-history">
      <div className="team-section-title"><span>Since {summary.since}</span><h2>Franchise History</h2></div>
      <div className="team-history-summary">
        <div><b>{summary.wins}-{summary.losses}</b><span>Regular season</span></div>
        <div><b>{summary.playoffs}</b><span>Playoff trips</span></div>
        <div><b>{summary.super_bowls.length}</b><span>Super Bowl trips</span></div>
        <div><b>{summary.titles.length}</b><span>{summary.titles.length ? `Titles (${summary.titles.join(", ")})` : "Titles"}</span></div>
      </div>
      <div className="player-table-scroll">
        <table className="player-history-table team-history-table">
          <thead>
            <tr>
              <th>SEASON</th><th>RECORD</th><th>DIV</th><th>PF</th><th>PA</th><th>PLAYOFFS</th><th>HEAD COACH</th><th>GM</th><th>AWARDS</th>
            </tr>
          </thead>
          <tbody>
            {seasons.map((s) => (
              <tr key={s.season} className={s.playoff_result === "CHAMPION" ? "team-history-champion" : ""}>
                <td>
                  <strong>{s.season}</strong>
                  {s.history && <span className="career-history-tag" title="Reconstructed league history (2026–2034)">hist</span>}
                </td>
                <td>{record(s)}{s.in_progress && <span className="career-since">so far</span>}</td>
                <td>{s.division_place ? ordinal(s.division_place) : "—"}</td>
                <td>{s.points_for ?? "—"}</td>
                <td>{s.points_against ?? "—"}</td>
                <td>
                  {s.in_progress ? "In progress" : RESULT[s.playoff_result ?? ""] ?? "—"}
                  {s.conference_seed ? <span className="career-since">{ordinal(s.conference_seed)} seed</span> : null}
                </td>
                <td>{s.coach_name ?? "—"}</td>
                <td>{s.gm_name ?? "—"}</td>
                <td>{s.awards.length ? s.awards.map((a) => `${a.award}: ${a.name}`).join(" · ") : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hasHistory && (
        <p className="player-log-note">
          <span className="career-history-tag">hist</span> seasons are reconstructed league history (2026–2034): records,
          playoffs and staff are canon; games weren't kept, so there are no box scores.
        </p>
      )}
    </section>
  );
}
