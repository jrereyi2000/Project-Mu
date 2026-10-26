// One Record Book category (PROJECT_SPEC §5.2): the record, its holder(s), the NFL record a league mark replaced and
// a Top 10 expander. Marks set in this league are badged, and a current-season mark reads "in progress" (D3).
import { useState } from "react";
import { GROUP_LABELS, SUBJECT_LABELS, recordValue, sourceLabel, teamLogo, type Mark } from "./format";

export type RecordCategory = {
  category: string;
  label: string;
  scope: string;
  subject: string;
  better: "HIGH" | "LOW";
  season_type: "REGULAR" | "POSTSEASON";
  coverage: string;
  group: string;
  basis: "nfl_list" | "franchise" | "mu" | "none";
  record: Mark[];
  top10: Mark[];
  result: "broke" | "tied" | null;
  previous_nfl_record: { value: number; holder: string; team: string | null; season: number | null } | null;
};

const SHOWN_HOLDERS = 3;

export function Holder({ mark, week }: { mark: Mark; week: number | null }) {
  const name = mark.player_id
    ? <a href={`#player-${mark.player_id}`}>{mark.holder}</a>
    : <span>{mark.holder}</span>;
  return (
    <span className="rb-holder">
      {mark.team && <img src={teamLogo(mark.team)} alt={mark.team} />}
      {name}
      {mark.season && <small>{mark.season}</small>}
      <MarkBadges mark={mark} week={week} />
    </span>
  );
}

export function MarkBadges({ mark, week }: { mark: Mark; week: number | null }) {
  return (
    <>
      {mark.mu
        ? <em className="rb-badge mu" title={sourceLabel(mark.source)}>Set in this league</em>
        : null}
      {mark.in_progress && <em className="rb-badge live">In progress{week ? ` · through Week ${week}` : ""}</em>}
    </>
  );
}

export default function RecordCard({ cat, week }: { cat: RecordCategory; week: number | null }) {
  const [open, setOpen] = useState(false);
  const holders = cat.record;
  const extra = holders.length - SHOWN_HOLDERS;
  const lead = holders[0];
  return (
    <article className={`rb-card ${holders.some(h => h.mu) ? "mu" : ""}`}>
      <header>
        <span>{GROUP_LABELS[cat.group] || cat.scope} · {SUBJECT_LABELS[cat.subject] || cat.subject}</span>
        <h3>{cat.label}</h3>
      </header>
      {lead ? (
        <>
          <div className="rb-value">
            <strong>{recordValue(cat.category, lead.value)}</strong>
            {cat.result === "broke" && <em className="rb-result broke">NFL record</em>}
            {cat.result === "tied" && <em className="rb-result tied">Tied NFL record</em>}
          </div>
          <div className="rb-holders">
            {holders.slice(0, SHOWN_HOLDERS).map((h, i) => <Holder key={`${h.holder}-${h.season}-${i}`} mark={h} week={week} />)}
            {extra > 0 && (
              <button className="rb-more" onClick={() => setOpen(true)}>+{extra} tied</button>
            )}
          </div>
          {cat.previous_nfl_record && (
            <p className="rb-previous">
              Previous NFL record: {recordValue(cat.category, cat.previous_nfl_record.value)} · {cat.previous_nfl_record.holder}
              {cat.previous_nfl_record.season ? ` (${cat.previous_nfl_record.season})` : ""}
            </p>
          )}
        </>
      ) : (
        <p className="rb-empty">No mark yet. The first league season to post one sets it.</p>
      )}
      {cat.top10.length > 1 && (
        <>
          <button className="rb-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Hide top 10" : "Top 10"} <i>{open ? "−" : "+"}</i>
          </button>
          {open && (
            <ol className="rb-top10">
              {cat.top10.map((m, i) => (
                <li key={`${m.holder}-${m.season}-${i}`} className={m.mu ? "mu" : ""}>
                  <b>{m.rank}</b>
                  <Holder mark={m} week={week} />
                  <span className="rb-top10-value">{recordValue(cat.category, m.value)}</span>
                </li>
              ))}
            </ol>
          )}
          {open && cat.basis === "franchise" && (
            <p className="rb-note">No NFL-wide list is kept for this category: ranked from franchise records.</p>
          )}
        </>
      )}
    </article>
  );
}
