// All-Pro teams on #awards (PROJECT_SPEC §5.2 item 13), from GET /api/awards/all-pro: a season picker over the seasons
// with stored teams, and First / Second Team grouped Offense / Defense / Special Teams in slot order.
import { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { siteAbbr, teamLogo } from "./format";

type Selection = { slot: string; unit: string; name: string; player_id: number | null; team: string | null };
type AllProData = { seasons_available: number[]; season: number | null; first_team: Selection[]; second_team: Selection[] };

const UNITS: [string, string][] = [["OFFENSE", "Offense"], ["DEFENSE", "Defense"], ["SPECIAL_TEAMS", "Special teams"]];

export default function AllProTeams({ team }: { team: string }) {
  const [season, setSeason] = useState<number | null>(null);
  const [data, setData] = useState<AllProData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);
    fetch(`${API_BASE}/api/awards/all-pro${season ? `?season=${season}` : ""}`)
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(setData)
      .catch(() => setError(true));
  }, [season]);

  if (error) return <p className="rb-state">Couldn't load the All-Pro teams.</p>;
  if (!data) return <p className="rb-state">Loading All-Pro teams…</p>;
  if (!data.season) return <p className="rb-state">No All-Pro teams are stored yet.</p>;

  const first = data.seasons_available[data.seasons_available.length - 1];
  return (
    <div className="aw-allpro">
      <div className="aw-allpro-head">
        <div className="rb-chips">
          {data.seasons_available.map(s => (
            <button key={s} className={s === data.season ? "active" : ""} onClick={() => setSeason(s)}>{s}</button>
          ))}
        </div>
        <p className="rb-note">All-Pro teams are recorded from {first}. Earlier seasons' teams aren't stored.</p>
      </div>
      <div className="aw-allpro-grid">
        {([["First Team", data.first_team], ["Second Team", data.second_team]] as [string, Selection[]][]).map(([label, rows]) => (
          <section key={label} className="aw-allpro-team">
            <h3>{label} <small>{rows.length} selections</small></h3>
            {UNITS.map(([unit, unitLabel]) => {
              const unitRows = rows.filter(r => r.unit === unit);
              if (!unitRows.length) return null;
              return (
                <div key={unit} className="aw-allpro-unit">
                  <h4>{unitLabel}</h4>
                  <ul>
                    {unitRows.map((r, i) => (
                      <li key={`${r.slot}-${i}`} className={team && siteAbbr(r.team) === team ? "hit" : ""}>
                        <b>{r.slot}</b>
                        {r.team && <img src={teamLogo(r.team)} alt={r.team} />}
                        {r.player_id ? <a href={`#player-${r.player_id}`}>{r.name}</a> : <span>{r.name}</span>}
                        <small>{r.team}</small>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </section>
        ))}
      </div>
    </div>
  );
}
