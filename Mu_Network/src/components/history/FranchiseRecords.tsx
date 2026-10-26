// A franchise's regular-season records (#records?team=ABBR), from GET /api/records/franchise/{abbr}. `compact` is the
// Team hub's Records panel (Stats tab): the titles won in this league, then only the records set in this league, and a
// link to the full franchise view.
import { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { Holder } from "./RecordCard";
import { GROUP_LABELS, recordValue, siteAbbr, teamLogo, type Mark } from "./format";

type FranchiseRecord = {
  category: string;
  label: string;
  group: string;
  record: Mark[];
  league_record: boolean;
  mu: boolean;
};

type FranchiseData = {
  team: { id: number; abbreviation: string; name: string; color: string | null };
  as_of: { season: number | null; week: number | null; in_progress: boolean };
  mu_count: number;
  records: FranchiseRecord[];
  titles: { season: number; game_id: number; score: number; opponent: string; opponent_score: number }[];
};

export default function FranchiseRecords({ abbr, compact = false }: { abbr: string; compact?: boolean }) {
  const [data, setData] = useState<FranchiseData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setData(null);
    setError(false);
    fetch(`${API_BASE}/api/records/franchise/${abbr}`)
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(setData)
      .catch(() => setError(true));
  }, [abbr]);

  if (error) return <p className="rb-state">Couldn't load the {abbr.toUpperCase()} record book.</p>;
  if (!data) return <p className="rb-state">Loading franchise records…</p>;

  const week = data.as_of.in_progress ? data.as_of.week : null;
  const rows = compact ? data.records.filter(r => r.mu) : data.records;

  if (compact) {
    const others = data.records.length - rows.length;
    return (
      <section className="rb-franchise compact">
        <div className="rb-titles">
          {data.titles.length ? data.titles.map(t => (
            <a key={t.season} href={`#game-${t.game_id}`} className="rb-title">
              <span>Super Bowl champions</span>
              <strong>{t.season}</strong>
              <small>{t.score}–{t.opponent_score} over {t.opponent}</small>
            </a>
          )) : <p className="rb-state">No Super Bowl titles in this league yet.</p>}
        </div>
        {rows.length === 0 ? (
          <p className="rb-state">No franchise records set in this league yet.</p>
        ) : (
          <ul className="rb-compact-list">
            {rows.map(r => (
              <li key={r.category}>
                <span><small>{GROUP_LABELS[r.group] || r.group}</small>{r.label}</span>
                <strong>{recordValue(r.category, r.record[0]?.value)}</strong>
                <span className="rb-compact-holders">
                  {r.record.map((m, i) => <Holder key={`${m.holder}-${m.season}-${i}`} mark={{ ...m, mu: false }} week={week} />)}
                  {r.league_record && <em className="rb-result broke">League record</em>}
                </span>
              </li>
            ))}
          </ul>
        )}
        <a className="rb-full-link" href={`#records?team=${siteAbbr(data.team.abbreviation)}`}>
          Full record book{others > 0 ? ` · ${others} more franchise record${others === 1 ? "" : "s"}` : ""} →
        </a>
      </section>
    );
  }

  return (
    <section className="rb-franchise">
      <header className="rb-franchise-head">
        <img src={teamLogo(data.team.abbreviation)} alt="" />
        <div>
          <h2>{data.team.name}</h2>
          <p>{data.records.length} franchise records · {data.mu_count} set in this league{data.titles.length ? ` · ${data.titles.length} Super Bowl title${data.titles.length === 1 ? "" : "s"} (${data.titles.map(t => t.season).join(", ")})` : ""}</p>
        </div>
      </header>
      {rows.length === 0 ? (
        <p className="rb-state">No franchise records on file.</p>
      ) : (
        <div className="rb-table-wrap">
          <table className="rb-table">
            <thead>
              <tr><th>Record</th><th>Mark</th><th>Holder</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.category} className={r.mu ? "mu" : ""}>
                  <td><small>{GROUP_LABELS[r.group] || r.group}</small>{r.label}</td>
                  <td className="rb-table-value">{recordValue(r.category, r.record[0]?.value)}</td>
                  <td>{r.record.map((m, i) => <Holder key={`${m.holder}-${m.season}-${i}`} mark={m} week={week} />)}</td>
                  <td>{r.league_record && <em className="rb-result broke">League record</em>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
