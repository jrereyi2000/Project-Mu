// Front Office tab: the season's roster moves and executed trades, grouped by league week (newest first). A move
// row comes from League Transactions (a CPU signing, release, re-signing, extension or restructure); a trade card
// is one executed GM trade, both sides (server/front_office_api.py shape_move / shape_trade).
import { useMemo, useState } from "react";
import { money, teamLogo } from "./format";

export type Move = {
  id: number;
  week: number | null;
  type: "SIGNED" | "RELEASED" | "RE_SIGNED" | "EXTENDED" | "RESTRUCTURED" | "OTHER";
  direction: "in" | "out";
  player_id: number | null;
  name: string;
  position: string | null;
  from_team: string | null;
  contract_years: number | null;
  contract_value: number | null;
  dead_money: number | null;
  depth_slot: number | null;
  is_starter: boolean;
  practice_squad: boolean;
  new_deal_start: number | null;
  later_team: string | null;
  later_week: number | null;
  notable: boolean;
};
type TradeLeg =
  | { type: "PLAYER"; player_id: number; name: string; position: string | null; cap_hit: number | null }
  | { type: "PICK"; draft_season: number; round: number; original_team: string };
export type Trade = { proposal_id: number; week: number | null; partner: string; received: TradeLeg[]; sent: TradeLeg[] };
export type Transactions = { moves: Move[]; trades: Trade[] };

const TYPE_LABEL: Record<string, string> = {
  SIGNED: "Signed", RELEASED: "Released", RE_SIGNED: "Re-signed", EXTENDED: "Extended",
  RESTRUCTURED: "Restructured", OTHER: "Other", TRADE: "Trade",
};

function terms(years: number | null, value: number | null): string | null {
  if (!years && !value) return null;
  return [years ? `${years} yr${years === 1 ? "" : "s"}` : null, value ? money(value) : null].filter(Boolean).join(", ");
}

function detail(m: Move): string[] {
  const out: string[] = [];
  const t = terms(m.contract_years, m.contract_value);
  if (m.type === "SIGNED") {
    out.push(m.from_team ? `from ${m.from_team}` : "free agent");
    if (t) out.push(t);
    if (m.depth_slot && m.position) out.push(`${m.position}${m.depth_slot} on the depth chart`);
  } else if (m.type === "RELEASED") {
    if (m.dead_money) out.push(`${money(m.dead_money)} dead money`);
    if (m.later_team) out.push(`later signed by the ${m.later_team}${m.later_week ? `, Week ${m.later_week}` : ""}`);
  } else if (m.type === "RE_SIGNED" || m.type === "EXTENDED") {
    if (t) out.push(t);
    if (m.new_deal_start) out.push(`new deal starts ${m.new_deal_start}`);
  } else if (m.type === "RESTRUCTURED") {
    if (m.contract_value) out.push(money(m.contract_value));
  }
  return out;
}

function Leg({ leg }: { leg: TradeLeg }) {
  if (leg.type === "PICK") {
    return <li><b className="fo-pick-chip">{leg.draft_season} R{leg.round}</b><small>{leg.original_team}'s pick</small></li>;
  }
  return (
    <li>
      <a href={`#player-${leg.player_id}`}><strong>{leg.name}</strong></a>
      <small>{leg.position || "?"}{leg.cap_hit ? ` · ${money(leg.cap_hit)} cap hit` : ""}</small>
    </li>
  );
}

function TradeCard({ trade }: { trade: Trade }) {
  return (
    <article className="fo-trade">
      <header>
        <span className="fo-move-type trade">Trade</span>
        <span>with</span>
        <img src={teamLogo(trade.partner)} alt="" />
        <strong>{trade.partner}</strong>
      </header>
      <div className="fo-trade-sides">
        <div><h4>Received</h4><ul>{trade.received.length ? trade.received.map((l, i) => <Leg leg={l} key={i} />) : <li><small>Nothing</small></li>}</ul></div>
        <div><h4>Sent</h4><ul>{trade.sent.length ? trade.sent.map((l, i) => <Leg leg={l} key={i} />) : <li><small>Nothing</small></li>}</ul></div>
      </div>
    </article>
  );
}

export default function TransactionsTimeline({ data, season }: { data: Transactions; season: number }) {
  const [filter, setFilter] = useState("ALL");

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    data.moves.forEach((m) => { c[m.type] = (c[m.type] || 0) + 1; });
    if (data.trades.length) c.TRADE = data.trades.length;
    return c;
  }, [data]);

  // Week groups, newest first; offseason moves (no week) last.
  const groups = useMemo(() => {
    const byWeek = new Map<number, { moves: Move[]; trades: Trade[] }>();
    const key = (w: number | null) => (w === null ? -1 : w);
    const slot = (w: number | null) => {
      if (!byWeek.has(key(w))) byWeek.set(key(w), { moves: [], trades: [] });
      return byWeek.get(key(w))!;
    };
    if (filter === "ALL" || filter === "TRADE") data.trades.forEach((t) => slot(t.week).trades.push(t));
    data.moves.filter((m) => filter === "ALL" || m.type === filter).forEach((m) => slot(m.week).moves.push(m));
    return [...byWeek.entries()].sort((a, b) => b[0] - a[0]);
  }, [data, filter]);

  const total = data.moves.length + data.trades.length;
  return (
    <section className="fo-block">
      <div className="team-section-title"><span>{total} Moves in {season}</span><h2>Transactions</h2></div>
      {total === 0 ? <p className="fo-state">No roster moves yet this season.</p> : (
        <>
          <div className="fo-chips" role="group" aria-label="Move type">
            {["ALL", "SIGNED", "RELEASED", "RE_SIGNED", "EXTENDED", "RESTRUCTURED", "TRADE"]
              .filter((t) => t === "ALL" || counts[t])
              .map((t) => (
                <button key={t} className={filter === t ? "active" : ""} onClick={() => setFilter(t)}>
                  {t === "ALL" ? "All" : TYPE_LABEL[t]} <i>{t === "ALL" ? total : counts[t]}</i>
                </button>
              ))}
          </div>
          <ol className="fo-timeline">
            {groups.map(([week, g]) => (
              <li key={week}>
                <h3>{week === -1 ? "Offseason" : `Week ${week}`}</h3>
                <div className="fo-week">
                  {g.trades.map((t) => <TradeCard trade={t} key={`t${t.proposal_id}`} />)}
                  {g.moves.map((m) => (
                    <article className={`fo-move ${m.direction}`} key={m.id}>
                      <span className={`fo-move-type ${m.type.toLowerCase()}`}>{TYPE_LABEL[m.type]}</span>
                      <div>
                        {m.player_id
                          ? <a href={`#player-${m.player_id}`}><strong>{m.name}</strong></a>
                          : <strong>{m.name}</strong>}
                        <small>
                          {m.position || "?"}
                          {m.notable && <b className="fo-tag contract-year">Notable</b>}
                          {m.is_starter && m.type !== "RELEASED" && <b className="fo-tag">Starter</b>}
                          {m.practice_squad && <b className="fo-tag">Practice squad</b>}
                        </small>
                      </div>
                      <p>{detail(m).join(" · ")}</p>
                    </article>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
