// Record Watch (#records, regular-season view; docs/done/RECORD_BOOK_AND_AWARDS_PLAN.md phase 4): while a regular season is
// being played, this season's leader in each fully tracked single-season player category against the record, from
// GET /api/records/watch. Renders nothing once the regular season is complete (the endpoint returns no rows then).
import { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { recordValue, teamLogo } from "./format";

type WatchRow = {
  category: string;
  label: string;
  record: { value: number; holders: { holder: string; season: number | null }[] };
  leaders: { holder: string; player_id: number | null; team: string | null; games: number }[];
  value: number;
  pct: number | null;
  pace_17: number | null;
  on_pace: boolean;
  rank: number;
};

type WatchData = { season: number | null; week: number | null; in_progress: boolean; categories: WatchRow[] };

export default function RecordWatch() {
  const [data, setData] = useState<WatchData | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/records/watch`)
      .then(r => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, []);

  if (!data?.in_progress || !data.categories.length) return null;

  return (
    <section className="rb-watch" aria-label="Record Watch">
      <header>
        <span>Record Watch</span>
        <h2>{data.season} leaders against the record{data.week ? ` · through Week ${data.week}` : ""}</h2>
      </header>
      <ul>
        {data.categories.map(r => {
          const past = r.pct !== null && r.pct >= 100;
          const holders = r.record.holders;
          return (
            <li key={r.category} className={past ? "past" : r.on_pace ? "on-pace" : ""}>
              <div className="rb-watch-label">
                <small>{r.label}</small>
                <span className="rb-watch-leader">
                  {r.leaders.map((l, i) => (
                    <span key={`${l.holder}-${i}`}>
                      {l.team && <img src={teamLogo(l.team)} alt="" />}
                      {l.player_id ? <a href={`#player-${l.player_id}`}>{l.holder}</a> : l.holder}
                    </span>
                  ))}
                </span>
              </div>
              <div className="rb-watch-bar" title={`${r.pct ?? "—"}% of the record`}>
                <i style={{ width: `${Math.min(r.pct ?? 0, 100)}%` }} />
              </div>
              <div className="rb-watch-nums">
                <strong>{recordValue(r.category, r.value)}</strong>
                <small>
                  {r.pct !== null ? `${r.pct}% of ` : "Record "}{recordValue(r.category, r.record.value)}
                  {holders.length ? ` (${holders[0].holder}${holders[0].season ? ` ${holders[0].season}` : ""}${holders.length > 1 ? ` +${holders.length - 1}` : ""})` : ""}
                </small>
              </div>
              <div className="rb-watch-pace">
                {past ? (
                  <em className="rb-result broke">{r.value === r.record.value ? "Tied" : "Past the record"}</em>
                ) : r.pace_17 !== null ? (
                  <>
                    <small>17-game pace</small>
                    <strong>{recordValue(r.category, r.pace_17)}</strong>
                    {r.on_pace && <em className="rb-result tied">On record pace</em>}
                  </>
                ) : (
                  <small>All-time #{r.rank}</small>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
