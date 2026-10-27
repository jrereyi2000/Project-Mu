import { useContext, useEffect, useState, type CSSProperties } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getDivision, logo } from "../../lib/gridiron";

export type PowerRankingEntry = {
  rank: number;
  previous_rank: number | null;
  movement: number | null;
  record: string | null;
  kicker: string | null;
  blurb: string | null;
  team_id: number;
  abbr: string;
  name: string;
  city: string;
  team_color: string | null;
  division: string | null;
};

export type PowerRankingsResponse = {
  season: number;
  week: number | null;
  available_weeks: number[];
  article: { id: number; headline: string; excerpt: string | null; persona: string | null } | null;
  rankings: PowerRankingEntry[];
};

export const writerName = (persona: string | null | undefined) =>
  (persona || "").toLowerCase().split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

export function usePowerRankings(week?: number | null) {
  const systemStatus = useContext(SystemContext);
  const [data, setData] = useState<PowerRankingsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const weekParam = week ? `&week=${week}` : "";
    fetch(`${API_BASE}/api/power_rankings?season=${systemStatus.season}${weekParam}`)
      .then((res) => res.json())
      .then((json) => setData(json))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [systemStatus.season, week]);

  return { data, loading };
}

export function PowerRankingsPage() {
  const [rankingWeek, setRankingWeek] = useState<number | null>(null);
  const { data, loading } = usePowerRankings(rankingWeek);
  const rankings = data?.rankings || [];
  const shownWeek = data?.week ?? rankingWeek;

  return (
    <main className="rankings-page">
      <section className="rankings-masthead">
        <div>
          <span>GRIDIRON POWER INDEX</span>
          <h1>Power Rankings</h1>
          <p>{data?.article?.headline || "Every team, every week—ranked by form, underlying performance and postseason outlook."}</p>
        </div>
      </section>

      <section className="rankings-navigation">
        <div className="rankings-week-nav">
          <span>{(data?.available_weeks.length || 0) > 0 ? "Published rankings" : "No rankings published yet"}</span>
          {(data?.available_weeks || []).map((week) => (
            <button className={shownWeek === week ? "active" : ""} onClick={() => setRankingWeek(week)} key={week}>Week {week}</button>
          ))}
        </div>
        {rankings.length > 0 && (
          <div className="rankings-jump">
            <span>Jump to a team</span>
            <div>
              {rankings.map((team) => (
                <button onClick={() => document.getElementById(`rank-${team.abbr.toLowerCase()}`)?.scrollIntoView({ behavior: "smooth", block: "start" })} key={team.abbr}>
                  <img src={logo(team.abbr)} alt="" />{team.abbr}
                </button>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="rankings-list">
        <div className="rankings-list-head">
          <span>{shownWeek ? `WEEK ${shownWeek}` : "POWER RANKINGS"}</span>
          <strong>THE FULL 32</strong>
          <small>{data?.article?.persona ? `By ${writerName(data.article.persona)}` : ""}</small>
        </div>
        {loading && <article className="ranking-entry"><div className="ranking-copy"><p>Loading rankings…</p></div></article>}
        {!loading && rankings.length === 0 && (
          <article className="ranking-entry">
            <div className="ranking-copy">
              <h3>No Power Rankings for this week yet</h3>
              <p>Rankings appear here once the weekly Power Rankings column is published with all 32 teams.</p>
            </div>
          </article>
        )}
        {!loading && rankings.map((team) => {
          const abbr = team.abbr.toLowerCase();
          const fullName = `${team.city} ${team.name}`;
          const movement = team.movement;
          return (
            <article className="ranking-entry" id={`rank-${abbr}`} key={team.team_id} style={team.team_color ? ({ "--team-color": team.team_color } as CSSProperties) : undefined}>
              <div className="ranking-number">{String(team.rank).padStart(2, "0")}</div>
              <div className="ranking-team-lockup">
                <img src={logo(team.abbr)} alt={`${fullName} logo`} />
                <div>
                  <a href={`#team-${abbr}`}><h2>{fullName}</h2></a>
                  <span>{team.record || "—"} · {team.division || getDivision(abbr)}</span>
                </div>
                <div className={`ranking-movement ${(movement ?? 0) >= 0 ? "up" : "down"}`}>
                  <small>LAST WEEK</small>
                  <strong>{team.previous_rank ?? "—"}</strong>
                  <i>{movement == null ? "NEW" : movement === 0 ? "—" : movement > 0 ? `↑ ${movement}` : `↓ ${Math.abs(movement)}`}</i>
                </div>
              </div>
              <div className="ranking-result">
                <span>RECORD ENTERING WEEK {shownWeek}</span>
                <strong>{team.record || "—"}</strong>
              </div>
              <div className="ranking-copy">
                {team.kicker && <h3>{team.kicker}</h3>}
                <p>{team.blurb}</p>
                {data?.article?.persona && <small>Analysis by {writerName(data.article.persona)} · National Reporter</small>}
              </div>
            </article>
          );
        })}
      </section>
    </main>
  );
}
