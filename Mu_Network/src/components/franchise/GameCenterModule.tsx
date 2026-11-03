// #franchise Game Center module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.1): the team's overview (record, cap at
// the league year, roster counts, pending moves) from GET /api/franchise/{abbr}/overview, the next opponent's dossier
// (records, team_seasons ratings, per-game ranks) and the unit matchups with their edges from GET /game-center, and
// the "Play Game" button: POST /game/launch writes the game's mod pack and starts Madden through the MMC Mod Manager
// (pipeline/game_launch.py), or on a dry run only names the pack and the command. Shapes: types/franchiseGame.ts.
import { useState } from "react";
import "../team/frontOffice.css";
import { money } from "../team/format";
import type {
  FranchiseOverview, GameCenterResponse, GameLaunchRequest, GameLaunchResponse, MatchupEdge, NextGame, RankStat,
  TeamRatings, TeamRecord,
} from "../../types/franchiseGame";
import { RANK_STATS } from "../../types/franchiseGame";
import { MOVE_LABEL, SharedStyles, StateNote, franchiseFetch, useFranchiseData } from "./RosterCapModule";

const GC_CSS = `
.gc-next { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 24px; margin-bottom: 22px; background: var(--white); border-top: 3px solid var(--team-color, var(--red)); }
.gc-next > div > span { color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.gc-next h3 { margin: 6px 0 0; font: 700 26px var(--font-sans); letter-spacing: -.03em; }
.gc-next h3 a { color: inherit; }
.gc-next small { display: block; margin-top: 4px; color: #55575d; font: 600 10px var(--font-mono); }
.gc-launch { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.gc-launch .fr-btn { height: 40px; padding: 0 22px; font-size: 10px; border-radius: 20px; }
.gc-launch label { display: flex; align-items: center; gap: 6px; color: #55575d; font: 600 10px var(--font-mono); }
.gc-result { margin: 0 0 22px; }
.gc-result code { display: block; margin-top: 6px; padding: 8px 10px; overflow-x: auto; color: var(--ink); background: var(--white); font: 500 10px/1.5 var(--font-mono); white-space: pre; }
.gc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.gc-table { width: 100%; border-collapse: collapse; background: var(--white); font-family: var(--font-mono); border-top: 3px solid var(--ink); }
.gc-table th, .gc-table td { padding: 9px 12px; border-bottom: 1px solid var(--gray-100); font-size: 10px; text-align: right; white-space: nowrap; }
.gc-table th { color: var(--gray-500); background: #ebeae7; font-size: 8px; letter-spacing: .04em; text-transform: uppercase; }
.gc-table th:first-child, .gc-table td:first-child { text-align: left; color: var(--ink); }
.gc-table td small { margin-left: 6px; color: var(--gray-500); font-size: 8px; }
.gc-table td.better { color: var(--ink); font-weight: 700; }
.gc-edge { padding: 3px 8px; border-radius: 3px; font: 700 8px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.gc-edge.team { color: var(--ink); background: #e6efe4; }
.gc-edge.opponent { color: var(--white); background: var(--red); }
.gc-edge.even { color: var(--gray-500); background: var(--gray-100); }
.gc-pending { margin: 0; padding: 0; list-style: none; background: var(--white); border-top: 3px solid var(--ink); }
.gc-pending li { display: flex; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--gray-100); font: 600 10px var(--font-mono); }
.gc-pending li span { color: var(--gray-500); }
.gc-h3 { margin: 0 0 10px; color: var(--gray-500); font: 700 9px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
@media (max-width: 800px) { .gc-grid { grid-template-columns: 1fr; } .fo-cap-tiles.gc-tiles { grid-template-columns: 1fr 1fr; } .gc-launch { align-items: flex-start; } }
`;

const STAT_LABEL: Record<RankStat, string> = {
  points_for: "Points scored", points_against: "Points allowed", yards_for: "Yards gained", yards_against: "Yards allowed",
  pass_yards_for: "Pass yards", pass_yards_against: "Pass yards allowed", rush_yards_for: "Rush yards",
  rush_yards_against: "Rush yards allowed", turnovers: "Giveaways", takeaways: "Takeaways",
};

const RATING_ROWS: Array<[keyof TeamRatings, string, boolean]> = [   // [key, label, is a rank (lower is better)]
  ["team_ovr", "Team OVR", false], ["off_ovr", "Offense OVR", false], ["def_ovr", "Defense OVR", false],
  ["pass_off_rank", "Pass offense rank", true], ["rush_off_rank", "Rush offense rank", true],
  ["pass_rush_rank", "Pass rush rank", true], ["coverage_rank", "Coverage rank", true],
];

const EDGE_LABEL: Record<MatchupEdge, string> = { team: "Our edge", opponent: "Their edge", even: "Even" };

const recordText = (r: TeamRecord) => `${r.wins}-${r.losses}${r.ties ? `-${r.ties}` : ""}`;
/** #team-{abbr} uses lib/gridiron's lowercase ESPN-style abbreviations (ari, wsh). */
const teamHref = (abbr: string) => `#team-${({ AZ: "ari", WAS: "wsh" } as Record<string, string>)[abbr] ?? abbr.toLowerCase()}`;
const dash = (v: number | null | undefined) => (v === null || v === undefined ? "—" : String(v));
const gameType = (t: string | null) => (!t || t === "REGULAR_SEASON" ? "Regular season" : t.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()));

/** "Week 6 · Thu 8:20 PM · Regular season" */
function nextGameLine(g: NextGame): string {
  return [`Week ${g.week}`, g.game_time, gameType(g.game_type)].filter(Boolean).join(" · ");
}

/** Which side's number is better, for bolding: "team" | "opponent" | null. */
function better(a: number | null, b: number | null, lowerIsBetter: boolean): "team" | "opponent" | null {
  if (a === null || b === null || a === b) return null;
  return (a < b) === lowerIsBetter ? "team" : "opponent";
}

function LaunchPanel({ abbr, pending, next }: { abbr: string; pending: number; next: NextGame | null }) {
  const [dryRun, setDryRun] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GameLaunchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const launch = async () => {
    if (!dryRun && !window.confirm(`Write the mod pack and start Madden 27 for ${abbr}'s game?`)) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body: GameLaunchRequest = { dry_run: dryRun };
      setResult(await franchiseFetch<GameLaunchResponse>(abbr, "/game/launch", "POST", body));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gc-launch">
      <button className="fr-btn primary" disabled={busy} onClick={launch}>
        {busy ? "Working…" : dryRun ? "Play Game (dry run)" : "Play Game"}
      </button>
      <label>
        <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
        Dry run: build the pack and command, start nothing
      </label>
      {pending > 0 && <small>{pending} pending move{pending === 1 ? "" : "s"}: the launch refuses until the league office writes them.</small>}
      {!next && <small>No unplayed game on the schedule.</small>}
      <LaunchResult result={result} error={error} />
    </div>
  );
}

function LaunchResult({ result, error }: { result: GameLaunchResponse | null; error: string | null }) {
  if (error) return <p className="fr-warn gc-result">Launch failed: {error}</p>;
  if (!result) return null;
  const g = result.game;
  const game = g ? `${g.away} at ${g.home}, ${g.season} week ${g.week}${g.game_time ? ` (${g.game_time})` : ""}` : null;
  if (result.refused) {
    return (
      <div className="fr-warn gc-result" role="alert">
        <strong>Refused: {result.refused}</strong>
        {game && <div>Game: {game}</div>}
        {result.pack && <div>Pack: {result.pack}</div>}
      </div>
    );
  }
  return (
    <div className="fr-ok gc-result" role="status">
      <strong>{result.started ? "Madden is starting" : "Dry run: nothing written or started"}</strong>
      {game && <div>Game: {game}</div>}
      <div>Pack: {result.pack ?? "—"}</div>
      {result.command && <code>{result.command}</code>}
    </div>
  );
}

function OverviewTiles({ o }: { o: FranchiseOverview }) {
  const rc = o.roster_counts;
  const est = o.cap.estimated ? <em>est.</em> : null;
  return (
    <div className="fo-cap-tiles gc-tiles">
      <article>
        <span>Record</span>
        <strong>{recordText(o.record)}</strong>
        <small>Season {o.season} · Week {o.week}</small>
      </article>
      <article className={o.cap.room < 0 ? "over" : ""}>
        <span>Cap room ({o.league_year})</span>
        <strong>{money(o.cap.room)}{est}</strong>
        <small>{money(o.cap.committed)} committed · {money(o.cap.dead_money)} dead of {money(o.cap.cap)}</small>
      </article>
      <article className={rc.active > rc.limits.active ? "over" : ""}>
        <span>Active roster</span>
        <strong>{rc.active}/{rc.limits.active}</strong>
        <small>{rc.injured_reserve} on IR</small>
      </article>
      <article className={rc.practice_squad > rc.limits.practice_squad ? "over" : ""}>
        <span>Practice squad</span>
        <strong>{rc.practice_squad}/{rc.limits.practice_squad}</strong>
        <small>{o.pending_moves.length} pending move{o.pending_moves.length === 1 ? "" : "s"}</small>
      </article>
    </div>
  );
}

function PendingMoves({ o }: { o: FranchiseOverview }) {
  if (o.pending_moves.length === 0) return <p className="fo-basis">No pending moves: the save is up to date with your front office.</p>;
  return (
    <ul className="gc-pending" aria-label="Pending moves">
      {o.pending_moves.map((m) => {
        const name = typeof (m.payload?.preview as { name?: unknown } | undefined)?.name === "string" ? (m.payload.preview as { name: string }).name : null;
        return (
          <li key={m.move_id}>
            <strong>{m.kind === "DEPTH_CHART" ? "Depth chart" : MOVE_LABEL[m.kind]}{name ? `: ${name}` : m.player_id ? ` (#${m.player_id})` : ""}</strong>
            <span>{m.status} · {new Date(m.created_at).toLocaleString()}</span>
          </li>
        );
      })}
    </ul>
  );
}

function Dossier({ gc }: { gc: GameCenterResponse }) {
  const opp = gc.opponent;
  if (!opp) return <p className="fo-state">No opponent to scout: there's no unplayed game on {gc.team}'s schedule.</p>;
  const oppAbbr = opp.abbr ?? "Opponent";
  const mine = gc.ranks.team;
  const theirs = gc.ranks.opponent;
  return (
    <div className="gc-grid">
      <div>
        <h3 className="gc-h3">Ratings</h3>
        <table className="gc-table" aria-label="Team ratings">
          <thead><tr><th>Rating</th><th>{gc.team}</th><th>{oppAbbr}</th></tr></thead>
          <tbody>
            <tr><td>Record</td><td>{recordText(gc.record)}</td><td>{recordText(opp.record)}</td></tr>
            {RATING_ROWS.map(([key, label, isRank]) => {
              const a = gc.ratings?.[key] ?? null;
              const b = opp.ratings?.[key] ?? null;
              const win = better(a, b, isRank);
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td className={win === "team" ? "better" : ""}>{dash(a)}</td>
                  <td className={win === "opponent" ? "better" : ""}>{dash(b)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {(!gc.ratings || !opp.ratings) && <p className="fo-basis">Ratings missing for a team: no team_seasons row this season.</p>}
      </div>
      <div>
        <h3 className="gc-h3">Per game ({mine?.games ?? 0} v {theirs?.games ?? 0} games)</h3>
        {!mine || !theirs ? (
          <p className="fo-state">No completed regular-season box scores to rank yet.</p>
        ) : (
          <table className="gc-table" aria-label="Per-game ranks">
            <thead><tr><th>Stat</th><th>{gc.team}</th><th>{oppAbbr}</th></tr></thead>
            <tbody>
              {RANK_STATS.map((s) => {
                const a = mine[s];
                const b = theirs[s];
                const win = better(a.rank, b.rank, true);
                return (
                  <tr key={s}>
                    <td>{STAT_LABEL[s]}</td>
                    <td className={win === "team" ? "better" : ""}>{dash(a.value)}<small>{a.rank ? `#${a.rank}` : ""}</small></td>
                    <td className={win === "opponent" ? "better" : ""}>{dash(b.value)}<small>{b.rank ? `#${b.rank}` : ""}</small></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Matchups({ gc }: { gc: GameCenterResponse }) {
  if (gc.matchups.length === 0) return <p className="fo-state">No unit matchups yet: both teams need completed games.</p>;
  const oppAbbr = gc.opponent?.abbr ?? "Opponent";
  return (
    <table className="gc-table" aria-label="Unit matchups">
      <thead><tr><th>Unit</th><th>{gc.team}</th><th>{oppAbbr}</th><th>Edge</th></tr></thead>
      <tbody>
        {gc.matchups.map((m) => (
          <tr key={m.unit}>
            <td>{m.unit}</td>
            <td>{STAT_LABEL[m.team_stat] ?? m.team_stat}: {dash(m.team_value)}<small>{m.team_rank ? `#${m.team_rank}` : ""}</small></td>
            <td>{STAT_LABEL[m.opponent_stat] ?? m.opponent_stat}: {dash(m.opponent_value)}<small>{m.opponent_rank ? `#${m.opponent_rank}` : ""}</small></td>
            <td><span className={`gc-edge ${m.edge}`}>{EDGE_LABEL[m.edge]}</span></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function GameCenterModule({ teamAbbr }: { teamAbbr: string }) {
  const [overview] = useFranchiseData<FranchiseOverview>(teamAbbr, "/overview");
  const [center] = useFranchiseData<GameCenterResponse>(teamAbbr, "/game-center");
  const o = overview.status === "ready" ? overview.data : null;
  const gc = center.status === "ready" ? center.data : null;
  const next = gc?.next_game ?? o?.next_game ?? null;

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <style href="fr-gc" precedence="default">{GC_CSS}</style>

      <section className="fo-block">
        <div className="team-section-title">
          <span>{teamAbbr} · {o ? `Season ${o.season} · Week ${o.week}` : "This week"}</span>
          <h2>Game Center</h2>
        </div>
        <div className="gc-next">
          <div>
            <span>Next game</span>
            {next ? (
              <>
                <h3>
                  {next.home ? "vs " : "at "}
                  {next.opponent.abbr ? <a href={teamHref(next.opponent.abbr)}>{next.opponent.name ?? next.opponent.abbr}</a> : "TBD"}
                </h3>
                <small>{nextGameLine(next)}{next.home ? " · Home" : " · Away"}</small>
              </>
            ) : (
              <h3>{overview.status === "loading" || center.status === "loading" ? "…" : "None scheduled"}</h3>
            )}
          </div>
          <LaunchPanel abbr={teamAbbr} pending={o?.pending_moves.length ?? 0} next={next} />
        </div>
        {o && !o.user_controlled && (
          <p className="fr-warn">{teamAbbr} isn't one of your franchise's user teams: launching and every move will be refused (403).</p>
        )}
        {!o && <StateNote state={overview} what="the team overview" />}
        {o && <OverviewTiles o={o} />}
      </section>

      {o && (
        <section className="fo-block">
          <div className="team-section-title"><span>Front office</span><h2>Pending Moves</h2></div>
          <PendingMoves o={o} />
        </section>
      )}

      <section className="fo-block">
        <div className="team-section-title"><span>{gc?.opponent ? `${gc.opponent.abbr} · ${recordText(gc.opponent.record)}` : "Opponent"}</span><h2>Scouting Dossier</h2></div>
        {!gc && <StateNote state={center} what="the opponent dossier" />}
        {gc && <Dossier gc={gc} />}
      </section>

      {gc && gc.opponent && (
        <section className="fo-block">
          <div className="team-section-title"><span>Unit v unit</span><h2>Matchups</h2></div>
          <Matchups gc={gc} />
          <p className="fo-basis">Per-game values over the season's completed regular-season games; rank 1 is the league's best. An edge needs the better rank by more than 3 places.</p>
        </section>
      )}
    </section>
  );
}
