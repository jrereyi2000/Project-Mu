// #franchise Contract Room module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.5 and §3): the expiring-contracts
// watchlist (GET /{abbr}/contracts/expiring) with each player's CBA extension window (contracts_engine
// .extension_eligible) and his tag / 5th-year option values where they apply; an extension form whose answer comes
// from his agent (POST /contracts/{id}/propose, same ACCEPT / REJECT / COUNTER shape as free agency); and a
// restructure tool (POST /contracts/{id}/restructure-preview) showing cap saved this season against the dead money
// added later, year by year. Shapes: types/franchiseMarket.ts.
import { useMemo, useState } from "react";
import { money } from "../team/format";
import "../team/frontOffice.css";
import type {
  ContractTerms, ExpiringPlayer, ExpiringResponse, NegotiationResponse, ProposeRequest, RestructurePreview,
  RestructureRequest,
} from "../../types/franchiseMarket";
import { NegotiationResult, NegotiationStyles, TermsForm } from "./FreeAgencyModule";
import { SharedStyles, StateNote, franchiseFetch, toState, useFranchiseData, type FranchiseState } from "./RosterCapModule";

type Filter = "All" | "Eligible" | "Locked";
type Tool = "extend" | "restructure";

const CR_CSS = `
.fr-cr { min-width: 980px; }
.fr-cr td:nth-child(3) { color: #55575d; font-weight: 400; background: transparent; }
.fr-cr td.fr-why { max-width: 260px; text-align: left; white-space: normal; color: #55575d; font: 500 9px/1.45 var(--font-mono); }
.fr-cr td.fr-why b { margin-bottom: 4px; display: inline-block; }
.fo-tag.fr-open { color: var(--ink); background: #e6efe4; }
.fo-tag.fr-locked { color: var(--red); background: #fbe7ea; }
.fr-opt { display: block; white-space: nowrap; font: 600 10px var(--font-mono); }
.fr-opt + .fr-opt { margin-top: 3px; }
.fr-opt em { color: var(--gray-500); font-style: normal; font-size: 8px; text-transform: uppercase; }
.fo-table tr.fr-picked td { background: #f5f4f1; }
.fr-restructure { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; }
.fr-restructure label { flex: 1 1 220px; }
.fr-ry { min-width: 520px; }
.fr-ry td:nth-child(3) { color: var(--ink); font-weight: 400; background: transparent; }
.fr-ry td.neg { color: var(--red); font-weight: 700; }
.fr-ry td.pos { color: #2e7d32; font-weight: 700; }
`;

function optionLines(p: ExpiringPlayer) {
  const o = p.options;
  const lines: [string, number][] = [];
  if (o.tag_value !== undefined) lines.push(["Franchise tag", o.tag_value]);
  if (o.transition_tag_value !== undefined) lines.push(["Transition tag", o.transition_tag_value]);
  if (o.fifth_year_option) lines.push([`5th-yr option (tier ${o.fifth_year_option.tier})`, o.fifth_year_option.salary]);
  return lines;
}

function extensionSeed(p: ExpiringPlayer): ContractTerms {
  const perYear = Math.max(p.cap_hit ?? 0, p.options.tag_value ?? 0, 1_000_000);
  const total = Math.round((perYear * 3) / 100_000) * 100_000;
  const guaranteed = Math.round((total * 0.4) / 100_000) * 100_000;
  return { years: 3, total_value: total, bonus: Math.round(guaranteed / 2 / 100_000) * 100_000, guaranteed };
}

function ExtendTool({ abbr, player }: { abbr: string; player: ExpiringPlayer }) {
  const [seed, setSeed] = useState<ContractTerms>(() => extensionSeed(player));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<NegotiationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = (t: ProposeRequest) => {
    setBusy(true);
    setError(null);
    franchiseFetch<NegotiationResponse>(abbr, `/contracts/${player.player_id}/propose`, "POST", t)
      .then(setResult)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };
  return (
    <>
      <TermsForm seed={seed} submitLabel="Propose extension" busy={busy} onSubmit={submit} />
      <p className="fr-form-note">
        New years start after his current deal ends. His agent answers now; an agreed extension is written into the
        save by the league office at its next run.
      </p>
      {error && <p className="fr-warn" role="alert">The proposal didn't go through: {error}</p>}
      {result && <NegotiationResult r={result} onUseCounter={(t) => setSeed({ ...t })} />}
    </>
  );
}

function RestructureTool({ abbr, player }: { abbr: string; player: ExpiringPlayer }) {
  const [amountM, setAmountM] = useState("");
  const [preview, setPreview] = useState<FranchiseState<RestructurePreview> | null>(null);
  const amount = Math.round((parseFloat(amountM) || 0) * 1_000_000);
  const run = (dollars: number) => {
    const body: RestructureRequest = { amount: dollars };
    setPreview({ status: "loading" });
    franchiseFetch<RestructurePreview>(abbr, `/contracts/${player.player_id}/restructure-preview`, "POST", body)
      .then((p) => setPreview({ status: "ready", data: p }))
      .catch((e) => setPreview(toState<RestructurePreview>(e)));
  };
  const p = preview?.status === "ready" ? preview.data : null;
  return (
    <>
      <form className="fr-restructure" onSubmit={(e) => { e.preventDefault(); if (amount > 0) run(amount); }}>
        <div className="fr-form" style={{ gridTemplateColumns: "1fr" }}>
          <label>Base salary to convert into bonus ($M)
            <input inputMode="decimal" value={amountM} placeholder="e.g. 8.5" onChange={(e) => setAmountM(e.target.value)} />
            <small>{p ? `up to ${money(p.max_amount)} (90% of this year's base)` : "up to 90% of this year's base salary"}</small>
          </label>
        </div>
        <button type="submit" className="fr-btn primary" disabled={amount <= 0 || preview?.status === "loading"}>Preview</button>
        {p && p.max_amount > 0 && <button type="button" className="fr-btn" onClick={() => { setAmountM(String(p.max_amount / 1_000_000)); run(p.max_amount); }}>Use maximum</button>}
      </form>
      {preview && preview.status !== "ready" && <StateNote state={preview} what="the restructure preview" />}
      {p && (
        <div className="fr-result">
          <div className="fr-figures">
            <div><span>Cap saved now</span><strong className={p.cap_savings_now > 0 ? "pos" : ""}>{money(p.cap_savings_now)}</strong></div>
            <div><span>Added future dead money</span><strong className={p.added_dead_money_future > 0 ? "neg" : ""}>{money(p.added_dead_money_future)}</strong></div>
            <div><span>Cap hit now</span><strong>{money(p.cap_hit_before)} → {money(p.cap_hit_after)}</strong></div>
          </div>
          {!p.legal && <p className="fr-warn" role="alert">This restructure isn't allowed:</p>}
          {p.reasons.length > 0 && <ul className="fr-reasons">{p.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
          {p.by_year.length > 0 && (
            <div className="fo-table-scroll">
              <table className="fo-table fr-ry" aria-label="Cap hit by year, before and after the restructure">
                <thead><tr><th>Season</th><th>Cap hit before</th><th>Cap hit after</th><th>Change</th></tr></thead>
                <tbody>
                  {p.by_year.map((y) => {
                    const d = y.cap_hit_after - y.cap_hit_before;
                    return (
                      <tr key={y.season}>
                        <td><strong>{y.season}</strong></td>
                        <td>{money(y.cap_hit_before)}</td>
                        <td>{money(y.cap_hit_after)}</td>
                        <td className={d > 0 ? "neg" : d < 0 ? "pos" : undefined}>{d > 0 ? "+" : ""}{money(d)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="fo-basis">
            Converted salary becomes signing bonus prorated over the lesser of his years left and 5. It saves cap now and
            raises later cap hits; cutting him later accelerates the unpaid proration as dead money. Preview only: nothing is queued.
          </p>
        </div>
      )}
    </>
  );
}

export default function ContractRoomModule({ teamAbbr }: { teamAbbr: string }) {
  const [expiring] = useFranchiseData<ExpiringResponse>(teamAbbr, "/contracts/expiring");
  const [filter, setFilter] = useState<Filter>("All");
  const [sel, setSel] = useState<{ player: ExpiringPlayer; tool: Tool } | null>(null);

  const e = expiring.status === "ready" ? expiring.data : null;
  const rows = useMemo(() => {
    if (!e) return [];
    const list = e.players.filter((p) => filter === "All" || (filter === "Eligible") === p.eligible);
    return list.sort((a, b) => Number(b.eligible) - Number(a.eligible) || (b.overall ?? -1) - (a.overall ?? -1) || a.name.localeCompare(b.name));
  }, [e, filter]);
  const counts: Record<Filter, number> = e
    ? { All: e.players.length, Eligible: e.players.filter((p) => p.eligible).length, Locked: e.players.filter((p) => !p.eligible).length }
    : { All: 0, Eligible: 0, Locked: 0 };

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <NegotiationStyles />
      <style href="fr-cr" precedence="default">{CR_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title"><span>{teamAbbr} · {e ? `Season ${e.season} · Week ${e.week}` : "Contracts"}</span><h2>Contract Room</h2></div>
        {!e && <StateNote state={expiring} what="the expiring contracts" />}
        {e && (
          <>
            <div className="fo-chips" role="group" aria-label="Eligibility filter">
              {(["All", "Eligible", "Locked"] as Filter[]).map((f) => (
                <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f === "Locked" ? "Not yet eligible" : f}<i>{counts[f]}</i></button>
              ))}
            </div>
            {rows.length === 0 ? (
              <p className="fo-state">{e.players.length === 0 ? "No expiring contracts on the roster." : "No players in this group."}</p>
            ) : (
              <div className="fo-table-scroll">
                <table className="fo-table fr-cr" aria-label="Expiring contracts">
                  <thead>
                    <tr><th>Player</th><th>OVR</th><th>Age</th><th>Contract</th><th>Cap Hit</th><th>Extension Window</th><th>Tag / Option</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => {
                      const opts = optionLines(p);
                      return (
                        <tr key={p.player_id} className={sel?.player.player_id === p.player_id ? "fr-picked" : undefined}>
                          <td>
                            <a href={`#player-${p.player_id}`}><strong>{p.name}</strong></a>
                            <small>{p.position}{p.contract && <b className="fo-tag">{p.contract.contract_type}</b>}</small>
                          </td>
                          <td>{p.overall ?? "—"}</td>
                          <td>{p.age ?? "—"}</td>
                          <td>
                            {p.contract ? <>{p.contract.years_left} yr{p.contract.years_left === 1 ? "" : "s"} left<small>thru {p.contract.through} · {money(p.contract.total_value)}</small></> : "—"}
                          </td>
                          <td>{money(p.cap_hit)}</td>
                          <td className="fr-why">
                            <b className={`fo-tag ${p.eligible ? "fr-open" : "fr-locked"}`}>{p.eligible ? "Window open" : "Locked"}</b><br />
                            {p.eligible_reason}
                          </td>
                          <td>{opts.length === 0 ? "—" : opts.map(([label, v]) => <span key={label} className="fr-opt">{money(v)} <em>{label}</em></span>)}</td>
                          <td>
                            <div className="fr-actions">
                              <button className="fr-btn" disabled={!p.eligible} title={p.eligible ? undefined : p.eligible_reason} onClick={() => setSel({ player: p, tool: "extend" })}>Extend</button>
                              <button className="fr-btn" disabled={!p.contract} onClick={() => setSel({ player: p, tool: "restructure" })}>Restructure</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="fo-basis">
              CBA windows: drafted rookies after year 3's regular season, undrafted after year 2, veterans in their final
              year or 12 months after signing or the last restructure, 1-year deals from week 18.
            </p>
          </>
        )}
        {sel && (
          <div className="fr-panel" key={`${sel.player.player_id}-${sel.tool}`}>
            <span>{sel.tool === "extend" ? "Extension" : "Restructure"} · {sel.player.position} · {sel.player.overall ?? "—"} OVR</span>
            <h3>{sel.player.name}</h3>
            {sel.tool === "extend" ? <ExtendTool abbr={teamAbbr} player={sel.player} /> : <RestructureTool abbr={teamAbbr} player={sel.player} />}
            <footer><button className="fr-btn" onClick={() => setSel(null)}>Close</button></footer>
          </div>
        )}
      </section>
    </section>
  );
}
