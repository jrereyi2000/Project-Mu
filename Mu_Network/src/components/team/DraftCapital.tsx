// Front Office tab: the team's picks in the next three drafts (draft_picks), round by round. Its own pick is kept
// or traded away ("to DET"); an acquired pick reads "via ATL". The overall number shows once the order is set.
import { teamLogo } from "./format";

export type Pick = {
  draft_season: number;
  round: number;
  overall_pick: number | null;
  original_team: string;
  owner_team: string;
  status: "OWN" | "ACQUIRED" | "TRADED_AWAY";
  source: "SEED" | "TRADE" | "JR";
  trade_week: number | null;
};
export type DraftPicks = { drafts: number[]; picks: Pick[] };

const ROUNDS = [1, 2, 3, 4, 5, 6, 7];

function PickChip({ pick }: { pick: Pick }) {
  const when = pick.trade_week ? ` · Wk ${pick.trade_week}` : "";
  const no = pick.overall_pick ? <em>#{pick.overall_pick}</em> : null;
  if (pick.status === "OWN") return <span className="fo-pick own">Own{no}</span>;
  if (pick.status === "ACQUIRED") {
    return (
      <span className="fo-pick acquired" title={`Acquired from ${pick.original_team}${when}`}>
        <img src={teamLogo(pick.original_team)} alt="" />via {pick.original_team}{no}
      </span>
    );
  }
  return (
    <span className="fo-pick traded" title={`Traded to ${pick.owner_team}${when}`}>
      <img src={teamLogo(pick.owner_team)} alt="" />to {pick.owner_team}{when}
    </span>
  );
}

export default function DraftCapital({ data, abbr }: { data: DraftPicks; abbr: string }) {
  const cell = (draft: number, round: number) =>
    data.picks.filter((p) => p.draft_season === draft && p.round === round)
      .sort((a, b) => ["OWN", "ACQUIRED", "TRADED_AWAY"].indexOf(a.status) - ["OWN", "ACQUIRED", "TRADED_AWAY"].indexOf(b.status));
  const summary = (draft: number) => {
    const ps = data.picks.filter((p) => p.draft_season === draft);
    const held = ps.filter((p) => p.status !== "TRADED_AWAY").length;
    const extra = ps.filter((p) => p.status === "ACQUIRED").length;
    const gone = ps.filter((p) => p.status === "TRADED_AWAY").length;
    return { held, extra, gone };
  };
  const held = data.picks.filter((p) => p.status !== "TRADED_AWAY").length;

  return (
    <section className="fo-block">
      <div className="team-section-title"><span>{held} Picks Held · Next {data.drafts.length} Drafts</span><h2>Draft Capital</h2></div>
      <div className="fo-table-scroll">
        <table className="fo-draft" aria-label={`${abbr} draft picks by round`}>
          <thead>
            <tr>
              <th>Round</th>
              {data.drafts.map((d) => {
                const s = summary(d);
                return (
                  <th key={d}>
                    <strong>{d}</strong>
                    <small>{s.held} picks{s.extra ? ` · +${s.extra} acquired` : ""}{s.gone ? ` · ${s.gone} traded` : ""}</small>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {ROUNDS.map((r) => (
              <tr key={r}>
                <td>R{r}</td>
                {data.drafts.map((d) => (
                  <td key={d}>
                    <div className="fo-pick-cell">
                      {cell(d, r).map((p) => <PickChip pick={p} key={`${p.original_team}-${p.status}`} />)}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fo-basis">Pick ownership after every trade made this season. Overall numbers appear once the draft order is set.</p>
    </section>
  );
}
