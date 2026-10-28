// Awards & Champions (#awards, PROJECT_SPEC §5.1/§5.2; docs/done/RECORD_BOOK_AND_AWARDS_PLAN.md phase 2), from
// GET /api/awards and /api/awards/all-pro (server/history_api.py): one champion card per season, the award history
// table, repeat winners, each franchise's titles and awards, and the All-Pro teams. The team and award filters live in
// the hash (#awards?team=car&award=MVP).
import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../api";
import AllProTeams from "./AllProTeams";
import { AWARD_LABELS, siteAbbr, teamLogo } from "./format";
import "./history.css";

type Award = {
  award_type: string;
  season: number;
  name: string | null;
  player_id: number | null;
  coach_id: number | null;
  position: string | null;
  team: string | null;
};

type Season = {
  season: number;
  champion: { game_id: number; team: string; score: number; opponent: string; opponent_score: number } | null;
  champion_coach: { coach_id: number; name: string } | null;
  sb_mvp: Award | null;
  awards: Record<string, Award | null>;
};

type Leader = { award_type: string; count: number; name: string; player_id: number | null; team: string | null; seasons: number[] };

type AwardsData = {
  award_types: string[];
  seasons: Season[];
  leaders: Leader[];
  franchise_counts: { team: string; titles: number; awards: number }[];
};

function parseQuery(query: string) {
  const q = new URLSearchParams(query.replace(/^\?/, ""));
  return { team: (q.get("team") || "").toLowerCase(), award: (q.get("award") || "").toUpperCase() };
}

function toHash(p: { team: string; award: string }): string {
  const q = new URLSearchParams();
  if (p.team) q.set("team", p.team);
  if (p.award) q.set("award", p.award);
  const s = q.toString();
  return s ? `#awards?${s}` : "#awards";
}

function Winner({ award, team }: { award: Award | null; team: string }) {
  if (!award || !award.name) return <span className="aw-none">—</span>;
  const hit = team && siteAbbr(award.team) === team;
  return (
    <span className={`aw-winner ${hit ? "hit" : ""} ${team && !hit ? "dim" : ""}`}>
      {award.team && <img src={teamLogo(award.team)} alt={award.team} />}
      <span>
        {award.player_id ? <a href={`#player-${award.player_id}`}>{award.name}</a> : <b>{award.name}</b>}
        <small>{[award.position, award.team].filter(Boolean).join(" · ")}</small>
      </span>
    </span>
  );
}

function seasonsLabel(seasons: number[]): string {
  return seasons.join(", ");
}

export default function AwardsPage({ query, teams }: { query: string; teams: { name: string; abbr: string }[] }) {
  const params = useMemo(() => parseQuery(query), [query]);
  const [data, setData] = useState<AwardsData | null>(null);
  const [error, setError] = useState(false);
  const set = (patch: Partial<{ team: string; award: string }>) => { window.location.hash = toHash({ ...params, ...patch }); };

  useEffect(() => {
    fetch(`${API_BASE}/api/awards`)
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(setData)
      .catch(() => setError(true));
  }, []);

  const types = data ? (params.award && data.award_types.includes(params.award) ? [params.award] : data.award_types) : [];
  const teamMatch = (abbr: string | null | undefined) => !!abbr && siteAbbr(abbr) === params.team;
  const seasons = (data?.seasons || []).filter(s => !params.team
    || teamMatch(s.champion?.team) || types.some(t => teamMatch(s.awards[t]?.team)));
  const leaders = (data?.leaders || []).filter(l => (!params.award || l.award_type === params.award) && (!params.team || teamMatch(l.team)));
  const counts = (data?.franchise_counts || []);
  const span = data?.seasons.length ? `${data.seasons[data.seasons.length - 1].season}–${data.seasons[0].season}` : "";

  return (
    <main className="rb-page">
      <section className="rb-masthead">
        <div>
          <span>League history</span>
          <h1>Awards &amp; Champions</h1>
          <p>{data ? `${data.seasons.length} seasons of champions and honors${span ? `, ${span}` : ""}` : "Champions, award winners and All-Pro teams"}</p>
        </div>
      </section>

      <section className="rb-controls">
        <div className="rb-filters">
          <div className="rb-chips">
            <button className={!params.award ? "active" : ""} onClick={() => set({ award: "" })}>All awards</button>
            {(data?.award_types || []).map(t => (
              <button key={t} className={params.award === t ? "active" : ""} onClick={() => set({ award: t })}>{AWARD_LABELS[t]?.short || t}</button>
            ))}
          </div>
          <select className="aw-team" value={params.team} onChange={e => set({ team: e.target.value })} aria-label="Franchise">
            <option value="">All franchises</option>
            {[...teams].sort((a, b) => a.name.localeCompare(b.name)).map(t => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
          </select>
        </div>
      </section>

      <section className="rb-body">
        {error ? (
          <p className="rb-state">Couldn't load the awards.</p>
        ) : !data ? (
          <p className="rb-state">Loading awards…</p>
        ) : (
          <>
            <div className="aw-section">
              <div className="team-section-title"><span>Super Bowl</span><h2>Champions</h2></div>
              <div className="aw-champions">
                {data.seasons.filter(s => s.champion).map(s => (
                  <article key={s.season} className={`aw-champ ${params.team && !teamMatch(s.champion!.team) ? "dim" : ""}`}>
                    <span>{s.season} season</span>
                    <div className="aw-champ-team">
                      <img src={teamLogo(s.champion!.team)} alt={s.champion!.team} />
                      <a href={`#game-${s.champion!.game_id}`}>
                        <strong>{s.champion!.score}–{s.champion!.opponent_score}</strong>
                        <small>{s.champion!.team} over {s.champion!.opponent}</small>
                      </a>
                    </div>
                    <dl>
                      <div><dt>Coach</dt><dd>{s.champion_coach?.name || "—"}</dd></div>
                      <div><dt>SB MVP</dt><dd>{s.sb_mvp?.player_id ? <a href={`#player-${s.sb_mvp.player_id}`}>{s.sb_mvp.name}</a> : s.sb_mvp?.name || "—"}</dd></div>
                    </dl>
                  </article>
                ))}
              </div>
            </div>

            <div className="aw-section">
              <div className="team-section-title"><span>Year-end honors</span><h2>Award history</h2></div>
              {seasons.length === 0 ? (
                <p className="rb-state">No titles or awards for this franchise yet.</p>
              ) : (
                <div className="rb-table-wrap">
                  <table className="rb-table aw-table">
                    <thead>
                      <tr><th>Season</th>{types.map(t => <th key={t}>{AWARD_LABELS[t]?.long || t}</th>)}</tr>
                    </thead>
                    <tbody>
                      {seasons.map(s => (
                        <tr key={s.season}>
                          <td className="rb-table-value">{s.season}</td>
                          {types.map(t => <td key={t}><Winner award={s.awards[t]} team={params.team} /></td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="aw-section aw-split">
              <div>
                <div className="team-section-title"><span>Repeat winners</span><h2>Leaders</h2></div>
                {leaders.length === 0 ? <p className="rb-state">No repeat winners for this filter.</p> : (
                  <ul className="aw-leaders">
                    {leaders.map(l => (
                      <li key={`${l.award_type}-${l.name}`}>
                        <b>{l.count}×</b>
                        <span>
                          <em>{AWARD_LABELS[l.award_type]?.long || l.award_type}</em>
                          {l.player_id ? <a href={`#player-${l.player_id}`}>{l.name}</a> : <strong>{l.name}</strong>}
                          <small>{seasonsLabel(l.seasons)}</small>
                        </span>
                        {l.team && <img src={teamLogo(l.team)} alt={l.team} />}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <div className="team-section-title"><span>Since {data.seasons.length ? data.seasons[data.seasons.length - 1].season : ""}</span><h2>By franchise</h2></div>
                <div className="rb-table-wrap">
                  <table className="rb-table aw-counts">
                    <thead><tr><th>Franchise</th><th>Titles</th><th>Awards</th></tr></thead>
                    <tbody>
                      {counts.map(c => (
                        <tr key={c.team} className={teamMatch(c.team) ? "mu" : ""}>
                          <td><span className="rb-holder"><img src={teamLogo(c.team)} alt="" /><a href={`#awards?team=${siteAbbr(c.team)}`}>{c.team}</a></span></td>
                          <td className="rb-table-value">{c.titles || "—"}</td>
                          <td className="rb-table-value">{c.awards || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <div className="aw-section">
              <div className="team-section-title"><span>Associated Press</span><h2>All-Pro teams</h2></div>
              <AllProTeams team={params.team} />
            </div>
          </>
        )}
      </section>
    </main>
  );
}
