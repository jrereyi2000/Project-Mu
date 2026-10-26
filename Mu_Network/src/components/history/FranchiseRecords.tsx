// A franchise's regular-season records (#records?team=ABBR), from GET /api/records/franchise/{abbr}. `compact` keeps
// only the marks set in this league, for the Team hub's Records panel.
import { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { Holder } from "./RecordCard";
import { GROUP_LABELS, recordValue, teamLogo, type Mark } from "./format";

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

  return (
    <section className="rb-franchise">
      {!compact && (
        <header className="rb-franchise-head">
          <img src={teamLogo(data.team.abbreviation)} alt="" />
          <div>
            <h2>{data.team.name}</h2>
            <p>{data.records.length} franchise records · {data.mu_count} set in this league</p>
          </div>
        </header>
      )}
      {rows.length === 0 ? (
        <p className="rb-state">{compact ? "No franchise records set in this league yet." : "No franchise records on file."}</p>
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
