// #franchise Trade Machine module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.6): a two-way builder. Pick players
// from your roster and the chosen team's (GET /{abbr}/roster, §2.7a) and picks from both teams' next 3 drafts
// (GET /{abbr}/picks?seasons=3), then POST /{abbr}/trades/evaluate. The answer is drawn as a surplus value bar for
// both sides (trade_value V_T and pick curves, in dollars), cap legality for both teams (contracts_engine), and the
// AI GM's verdict and dialogue (gm_negotiation.judge); a counter can be loaded back into the builder. Evaluating
// never makes the trade. Shapes: types/franchiseMarket.ts.
import { useEffect, useMemo, useState } from "react";
import { money, ordinal } from "../team/format";
import "../team/frontOffice.css";
import type { RosterPlayer, RosterResponse } from "../../types/franchiseRoster";
import {
  TEAM_ABBRS,
  type OwnedPick, type PicksResponse, type TradeEvaluation, type TradePick, type TradeRequest, type TradeSide,
} from "../../types/franchiseMarket";
import { NegotiationStyles } from "./FreeAgencyModule";
import { SharedStyles, StateNote, franchiseFetch, toState, useFranchiseData, type FranchiseState } from "./RosterCapModule";

const pickKey = (p: TradePick) => `${p.season}-${p.round}-${p.original_team}`;
const pickLabel = (p: TradePick | OwnedPick, owner: string) => {
  const overall = "overall_pick" in p && p.overall_pick !== null ? ` (#${p.overall_pick})` : "";
  return `${p.season} ${ordinal(p.round)} round${p.original_team !== owner ? ` (via ${p.original_team})` : ""}${overall}`;
};

type Side = { players: Set<number>; picks: Set<string> };
const emptySide = (): Side => ({ players: new Set(), picks: new Set() });

const TM_CSS = `
.fr-tm-bar { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; margin-bottom: 16px; }
.fr-tm-bar label { display: flex; flex-direction: column; gap: 5px; color: var(--gray-500); font: 700 7px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-tm-bar select { height: 32px; padding: 0 10px; border: 1px solid var(--gray-100); border-radius: 3px; color: var(--ink); background: var(--white); font: 600 12px var(--font-mono); }
.fr-tm-bar .fr-grow { flex: 1; color: var(--gray-500); font: 600 9px var(--font-mono); }
.fr-tm { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.fr-tm-side { background: var(--white); border-top: 3px solid var(--ink); min-width: 0; }
.fr-tm-side.them { border-top-color: var(--gray-300); }
.fr-tm-side header { padding: 12px 14px 8px; display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.fr-tm-side header strong { font: 700 16px var(--font-sans); letter-spacing: -.02em; }
.fr-tm-side header small { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; letter-spacing: .05em; }
.fr-tm-side input[type=search] { width: calc(100% - 28px); margin: 0 14px 8px; height: 30px; padding: 0 10px; border: 1px solid var(--gray-100); border-radius: 3px; font: 600 11px var(--font-mono); box-sizing: border-box; }
.fr-tm-side h4 { margin: 8px 14px 4px; color: var(--gray-500); font: 700 7px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.fr-tm-list { max-height: 340px; overflow: auto; margin: 0; padding: 0 0 6px; list-style: none; }
.fr-tm-list label { display: grid; grid-template-columns: 16px 1fr auto; align-items: center; gap: 8px; min-height: 32px; padding: 0 14px; border-top: 1px solid var(--gray-100); cursor: pointer; }
.fr-tm-list label:hover { background: #f5f4f1; }
.fr-tm-list label.on { background: #f1efe9; }
.fr-tm-list span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 600 11px var(--font-sans); }
.fr-tm-list small { color: var(--gray-500); font: 600 8px var(--font-mono); white-space: nowrap; }
.fr-tm-list .fr-note { padding: 8px 14px; color: var(--gray-500); font: 600 9px var(--font-mono); }
.fr-tm-sum { margin: 16px 0 0; display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.fr-tm-sum div { padding: 10px 14px; background: var(--paper); font: 500 10px/1.6 var(--font-mono); color: #55575d; }
.fr-tm-sum b { display: block; color: var(--ink); font: 700 8px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; margin-bottom: 4px; }
.fr-sbar { display: grid; grid-template-columns: 70px 1fr 90px; align-items: center; gap: 10px; margin-bottom: 8px; font: 700 10px var(--font-mono); }
.fr-sbar .track { position: relative; height: 16px; background: var(--gray-100); }
.fr-sbar .track::before { content: ""; position: absolute; left: 50%; top: -3px; bottom: -3px; width: 1px; background: var(--ink); }
.fr-sbar .track i { position: absolute; top: 0; bottom: 0; display: block; }
.fr-sbar .track i.pos { left: 50%; background: #2e7d32; }
.fr-sbar .track i.neg { right: 50%; background: var(--red); }
.fr-sbar strong { text-align: right; }
.fr-sbar strong.pos { color: #2e7d32; }
.fr-sbar strong.neg { color: var(--red); }
@media (max-width: 760px) { .fr-tm, .fr-tm-sum { grid-template-columns: 1fr; } }
`;

function rosterPlayers(r: RosterResponse): RosterPlayer[] {
  return [...r.active, ...r.injured_reserve, ...r.practice_squad]
    .sort((a, b) => (b.overall ?? -1) - (a.overall ?? -1) || a.name.localeCompare(b.name));
}

function SideColumn({ abbr, label, them, roster, picks, side, onToggle }: {
  abbr: string;
  label: string;
  them: boolean;
  roster: FranchiseState<RosterResponse>;
  picks: FranchiseState<PicksResponse>;
  side: Side;
  onToggle: (kind: "players" | "picks", key: number | string) => void;
}) {
  const [q, setQ] = useState("");
  const players = roster.status === "ready" ? rosterPlayers(roster.data) : [];
  const shown = q.trim() ? players.filter((p) => `${p.name} ${p.position}`.toLowerCase().includes(q.trim().toLowerCase())) : players;
  return (
    <div className={`fr-tm-side${them ? " them" : ""}`}>
      <header><strong>{abbr}</strong><small>{label} · {side.players.size} player{side.players.size === 1 ? "" : "s"}, {side.picks.size} pick{side.picks.size === 1 ? "" : "s"}</small></header>
      {roster.status !== "ready" ? <StateNote state={roster} what={`${abbr}'s roster`} /> : (
        <>
          <input type="search" value={q} placeholder="Filter players" aria-label={`Filter ${abbr} players`} onChange={(e) => setQ(e.target.value)} />
          <h4>Players</h4>
          <ul className="fr-tm-list">
            {shown.length === 0 && <li className="fr-note">No players.</li>}
            {shown.map((p) => {
              const on = side.players.has(p.player_id);
              return (
                <li key={p.player_id}>
                  <label className={on ? "on" : undefined}>
                    <input type="checkbox" checked={on} onChange={() => onToggle("players", p.player_id)} />
                    <span>{p.name}</span>
                    <small>{p.position} · {p.overall ?? "—"} · {money(p.cap_hit)}</small>
                  </label>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <h4>Draft picks · next 3 drafts</h4>
      <ul className="fr-tm-list">
        {picks.status === "loading" && <li className="fr-note">Loading picks…</li>}
        {picks.status === "unavailable" && <li className="fr-note">The pick list isn't available yet.</li>}
        {picks.status === "error" && <li className="fr-note">Picks couldn't load ({picks.message}).</li>}
        {picks.status === "ready" && picks.data.picks.length === 0 && <li className="fr-note">No picks owned.</li>}
        {picks.status === "ready" && picks.data.picks.map((p) => {
          const k = pickKey(p);
          const on = side.picks.has(k);
          return (
            <li key={k}>
              <label className={on ? "on" : undefined}>
                <input type="checkbox" checked={on} onChange={() => onToggle("picks", k)} />
                <span>{pickLabel(p, abbr)}</span>
                <small>{p.original_team}</small>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SurplusRow({ label, value, scale }: { label: string; value: number; scale: number }) {
  const w = `${Math.min(50, (Math.abs(value) / scale) * 50)}%`;
  const cls = value > 0 ? "pos" : value < 0 ? "neg" : "";
  return (
    <div className="fr-sbar">
      <span>{label}</span>
      <div className="track" role="img" aria-label={`${label} surplus ${money(value)}`}>
        {value !== 0 && <i className={cls} style={{ width: w }} />}
      </div>
      <strong className={cls}>{value > 0 ? "+" : ""}{money(value)}</strong>
    </div>
  );
}

const VERDICT_TEXT = { ACCEPT: "Accepts", REJECT: "Rejects", COUNTER: "Counters" } as const;

/** GET /{abbr}/picks?seasons=3; a fetch that doesn't fail the module when only the picks endpoint is missing. */
function usePicks(abbr: string): FranchiseState<PicksResponse> {
  const [state, setState] = useState<FranchiseState<PicksResponse>>({ status: "loading" });
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    franchiseFetch<PicksResponse>(abbr, "/picks?seasons=3")
      .then((d) => live && setState({ status: "ready", data: d }))
      .catch((e) => live && setState(toState<PicksResponse>(e)));
    return () => { live = false; };
  }, [abbr]);
  return state;
}

export default function TradeMachineModule({ teamAbbr }: { teamAbbr: string }) {
  const others = useMemo(() => TEAM_ABBRS.filter((t) => t !== teamAbbr), [teamAbbr]);
  const [other, setOther] = useState<string>(others[0]);
  useEffect(() => { if (other === teamAbbr) setOther(others[0]); }, [teamAbbr, other, others]);

  const [ourRoster] = useFranchiseData<RosterResponse>(teamAbbr, "/roster");
  const [theirRoster] = useFranchiseData<RosterResponse>(other, "/roster");
  const ourPicks = usePicks(teamAbbr);
  const theirPicks = usePicks(other);

  const [send, setSend] = useState<Side>(emptySide);
  const [receive, setReceive] = useState<Side>(emptySide);
  const [evaluation, setEvaluation] = useState<FranchiseState<TradeEvaluation> | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => { setReceive(emptySide()); setEvaluation(null); }, [other]);

  const toggle = (setter: typeof setSend) => (kind: "players" | "picks", key: number | string) => {
    setter((s) => {
      const next: Side = { players: new Set(s.players), picks: new Set(s.picks) };
      const set = next[kind] as Set<number | string>;
      if (set.has(key)) set.delete(key); else set.add(key);
      return next;
    });
    setStale(true);
  };

  // Look-ups for naming assets in the summary and in a counter.
  const names = useMemo(() => {
    const m = new Map<number, string>();
    for (const r of [ourRoster, theirRoster]) if (r.status === "ready") for (const p of rosterPlayers(r.data)) m.set(p.player_id, `${p.name} (${p.position})`);
    return m;
  }, [ourRoster, theirRoster]);
  const pickIndex = useMemo(() => {
    const m = new Map<string, TradePick>();
    for (const s of [ourPicks, theirPicks]) if (s.status === "ready") for (const p of s.data.picks) m.set(pickKey(p), p);
    return m;
  }, [ourPicks, theirPicks]);

  const toWire = (s: Side): TradeSide => ({
    players: [...s.players],
    picks: [...s.picks].map((k) => pickIndex.get(k)).filter((p): p is TradePick => !!p)
      .map(({ season, round, original_team }) => ({ season, round, original_team })),
  });
  const fromWire = (s: TradeSide): Side => ({ players: new Set(s.players), picks: new Set(s.picks.map(pickKey)) });
  const describe = (s: TradeSide, owner: string) => [
    ...s.players.map((id) => names.get(id) ?? `Player #${id}`),
    ...s.picks.map((p) => pickLabel(p, owner)),
  ];

  const empty = send.players.size + send.picks.size === 0 || receive.players.size + receive.picks.size === 0;
  const evaluate = () => {
    const body: TradeRequest = { other_team: other, send: toWire(send), receive: toWire(receive) };
    setEvaluation({ status: "loading" });
    setStale(false);
    franchiseFetch<TradeEvaluation>(teamAbbr, "/trades/evaluate", "POST", body)
      .then((d) => setEvaluation({ status: "ready", data: d }))
      .catch((e) => setEvaluation(toState<TradeEvaluation>(e)));
  };
  const clear = () => { setSend(emptySide()); setReceive(emptySide()); setEvaluation(null); setStale(false); };

  const ev = evaluation?.status === "ready" ? evaluation.data : null;
  const scale = ev ? Math.max(Math.abs(ev.surplus_us), Math.abs(ev.surplus_them), 1) : 1;
  const sentList = describe(toWire(send), teamAbbr);
  const recvList = describe(toWire(receive), other);

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <NegotiationStyles />
      <style href="fr-tm" precedence="default">{TM_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title"><span>{teamAbbr} · Two-way trade</span><h2>Trade Machine</h2></div>
        {ourRoster.status === "unavailable" ? <StateNote state={ourRoster} what="the trade machine" /> : (
          <>
            <div className="fr-tm-bar">
              <label>Trade partner
                <select value={other} onChange={(e) => setOther(e.target.value)}>
                  {others.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <span className="fr-grow">Tick what you send and what you want back, then ask {other}'s GM.</span>
              <button className="fr-btn" onClick={clear} disabled={empty && !ev}>Clear</button>
              <button className="fr-btn primary" onClick={evaluate} disabled={empty || evaluation?.status === "loading"}>
                {evaluation?.status === "loading" ? "Asking…" : "Evaluate trade"}
              </button>
            </div>
            <div className="fr-tm">
              <SideColumn abbr={teamAbbr} label="You send" them={false} roster={ourRoster} picks={ourPicks} side={send} onToggle={toggle(setSend)} />
              <SideColumn abbr={other} label="You receive" them roster={theirRoster} picks={theirPicks} side={receive} onToggle={toggle(setReceive)} />
            </div>
            <div className="fr-tm-sum">
              <div><b>{teamAbbr} sends</b>{sentList.length ? sentList.join(" · ") : "Nothing yet."}</div>
              <div><b>{teamAbbr} receives</b>{recvList.length ? recvList.join(" · ") : "Nothing yet."}</div>
            </div>

            {evaluation && evaluation.status !== "ready" && <StateNote state={evaluation} what="the trade evaluation" />}
            {ev && (
              <div className="fr-panel" aria-live="polite">
                <span>{other} GM{stale ? " · the trade changed since this answer" : ""}</span>
                <h3><span className={`fr-verdict ${ev.verdict}`}>{VERDICT_TEXT[ev.verdict]}</span></h3>
                {ev.dialogue && <p className="fr-quote">“{ev.dialogue}”</p>}
                <h4>Surplus value (value in − value out)</h4>
                <SurplusRow label={teamAbbr} value={ev.surplus_us} scale={scale} />
                <SurplusRow label={other} value={ev.surplus_them} scale={scale} />
                <h4>Cap check</h4>
                <div className="fr-figures">
                  <div><span>{teamAbbr} cap</span><strong className={ev.cap_legal_us ? "pos" : "neg"}>{ev.cap_legal_us ? "Legal" : "Over"}</strong></div>
                  <div><span>{teamAbbr} room after</span><strong className={(ev.cap_room_after_us ?? 0) < 0 ? "neg" : ""}>{money(ev.cap_room_after_us)}</strong></div>
                  <div><span>{other} cap</span><strong className={ev.cap_legal_them ? "pos" : "neg"}>{ev.cap_legal_them ? "Legal" : "Over"}</strong></div>
                  <div><span>{other} room after</span><strong className={(ev.cap_room_after_them ?? 0) < 0 ? "neg" : ""}>{money(ev.cap_room_after_them)}</strong></div>
                </div>
                {(!ev.cap_legal_us || !ev.cap_legal_them) && <p className="fr-warn" role="alert">At least one team can't absorb the incoming contracts.</p>}
                {ev.reasons.length > 0 && <ul className="fr-reasons">{ev.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
                {ev.counter && (
                  <>
                    <h4>Their counter</h4>
                    <div className="fr-tm-sum" style={{ marginTop: 0 }}>
                      <div><b>{teamAbbr} sends</b>{describe(ev.counter.send, teamAbbr).join(" · ") || "Nothing"}</div>
                      <div><b>{teamAbbr} receives</b>{describe(ev.counter.receive, other).join(" · ") || "Nothing"}</div>
                    </div>
                    <footer>
                      <button className="fr-btn" onClick={() => { setSend(fromWire(ev.counter!.send)); setReceive(fromWire(ev.counter!.receive)); setStale(true); }}>
                        Load counter into the builder
                      </button>
                    </footer>
                  </>
                )}
                <p className="fo-basis">Evaluating doesn't make the trade. Surplus is trade_value's V_T and pick curves in dollars, each side by its own valuation.</p>
              </div>
            )}
          </>
        )}
      </section>
    </section>
  );
}
