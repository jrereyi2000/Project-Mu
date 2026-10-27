// Career milestones (#records, regular-season view; docs/SEASON_CONTINUITY_PLAN.md phase 4): the round marks players'
// careers in this league (since 2026, history included) crossed this season, and the next ones within reach, from
// GET /api/records/milestones. A "first" is a mark nobody in the league had reached before.
import { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { teamLogo } from "./format";

type Milestone = {
  player_id: number;
  name: string;
  team: string | null;
  position: string | null;
  stat: string;
  label: string;
  mark: number;
  career: number;
  needs?: number;
  first: "alone" | "shared" | boolean | null;
};

type MilestoneData = { season: number; in_progress: boolean; since: number; crossed: Milestone[]; chasing: Milestone[] };

const SHOWN = 10;

const fmt = (n: number) => n.toLocaleString("en-US");

function Who({ m }: { m: Milestone }) {
  return (
    <span className="rb-ms-who">
      {m.team && <img src={teamLogo(m.team)} alt="" />}
      <a href={`#player-${m.player_id}`}>{m.name}</a>
      {m.position && <small>{m.position}</small>}
    </span>
  );
}

export default function MilestoneWatch() {
  const [data, setData] = useState<MilestoneData | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/records/milestones`)
      .then(r => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, []);

  if (!data || (!data.crossed.length && !data.chasing.length)) return null;

  // Firsts reached alone lead, then shared firsts, then the rest (each group keeps the API's biggest-mark order).
  const rankFirst = (m: Milestone) => (m.first === "alone" ? 0 : m.first ? 1 : 2);
  const crossed = [...data.crossed].sort((a, b) => rankFirst(a) - rankFirst(b));
  const chasing = data.chasing;
  const cut = (rows: Milestone[]) => (all ? rows : rows.slice(0, SHOWN));

  return (
    <section className="rb-watch rb-ms" aria-label="Career milestones">
      <header>
        <span>Career Milestones · since {data.since}</span>
        <h2>{data.season}: marks reached and within reach</h2>
      </header>
      <div className="rb-ms-cols">
        <div>
          <h3>Reached in {data.season} <small>{data.crossed.length}</small></h3>
          <ul>
            {cut(crossed).map(m => (
              <li key={`x-${m.player_id}-${m.stat}-${m.mark}`} className={m.first ? "first" : ""}>
                <Who m={m} />
                <span className="rb-ms-mark">
                  <strong>{fmt(m.mark)}</strong> {m.label}
                  {m.first === "alone" && <em className="rb-result broke">First ever</em>}
                  {m.first === "shared" && <em className="rb-result tied">First, shared</em>}
                </span>
                <small>{fmt(m.career)} career</small>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>{data.in_progress ? "Within reach this season" : `Within reach in ${data.season + 1}`} <small>{chasing.length}</small></h3>
          <ul>
            {cut(chasing).map(m => (
              <li key={`c-${m.player_id}-${m.stat}`} className={m.first ? "first" : ""}>
                <Who m={m} />
                <span className="rb-ms-mark">
                  <strong>{fmt(m.mark)}</strong> {m.label}
                  {m.first && <em className="rb-result tied">Would be first</em>}
                </span>
                <small>needs {fmt(m.needs ?? 0)}</small>
              </li>
            ))}
          </ul>
        </div>
      </div>
      {(crossed.length > SHOWN || chasing.length > SHOWN) && (
        <button type="button" className="rb-ms-more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : "Show all"}
        </button>
      )}
      <p className="rb-ms-note">
        Careers count every season in this league since {data.since}, the reconstructed 2026–2034 history included;
        stats from before {data.since} aren't counted.
      </p>
    </section>
  );
}
