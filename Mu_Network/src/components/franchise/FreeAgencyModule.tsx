// #franchise Free Agency module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.4 and §4): the free-agent market
// (GET /api/franchise/free-agents, filtered by position, min OVR and max age, sorted by scheme fit, OVR, worth or
// age) and an offer form (years, total, bonus, guaranteed). POST /{abbr}/offers scores the offer with
// free_agency_engine.offer_utility and stores it; the answer (ACCEPT / REJECT / COUNTER) is drawn as a utility
// breakdown bar against his acceptance bar, and a counter can be loaded back into the form. Offers are never instant
// signings: the league office writes accepted deals into the save on its next run. Shapes: types/franchiseMarket.ts.
//
// Also exports the negotiation pieces the Contract Room reuses: `TermsForm`, `NegotiationResult`, `UtilityBar`.
import { useEffect, useMemo, useState } from "react";
import { money } from "../team/format";
import "../team/frontOffice.css";
import {
  OFFER_WEIGHTS,
  type ContractTerms, type FreeAgent, type FreeAgentsResponse, type NegotiationResponse, type OfferRequest,
  type UtilityParts,
} from "../../types/franchiseMarket";
import { SharedStyles, StateNote, franchiseFetch, useFranchiseData } from "./RosterCapModule";

// ---- Shared negotiation pieces ---------------------------------------------------------------------------------

export const NEG_CSS = `
.fr-panel { margin-top: 18px; padding: 20px 22px; background: var(--white); border-top: 3px solid var(--team-color, var(--red)); box-shadow: 0 10px 30px #11111109; }
.fr-panel > span { color: var(--team-color, var(--red)); font: 700 8px var(--font-mono); letter-spacing: .1em; text-transform: uppercase; }
.fr-panel h3 { margin: 6px 0 14px; font: 700 22px var(--font-sans); letter-spacing: -.03em; }
.fr-panel h4 { margin: 18px 0 8px; color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.fr-panel footer { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.fr-form { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.fr-form label, .fr-filters label { display: flex; flex-direction: column; gap: 5px; color: var(--gray-500); font: 700 7px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-form input, .fr-form select, .fr-filters input, .fr-filters select { height: 32px; padding: 0 10px; border: 1px solid var(--gray-100); border-radius: 3px; color: var(--ink); background: var(--white); font: 600 12px var(--font-mono); min-width: 0; }
.fr-form input:focus, .fr-form select:focus, .fr-filters input:focus, .fr-filters select:focus { outline: 2px solid var(--team-color, var(--red)); outline-offset: -1px; }
.fr-form small { color: var(--gray-500); font: 500 9px var(--font-mono); letter-spacing: 0; text-transform: none; }
.fr-form-note { margin: 10px 0 0; color: #55575d; font: 500 10px/1.5 var(--font-mono); }
.fr-verdict { display: inline-block; padding: 4px 10px; border-radius: 3px; font: 700 9px var(--font-mono); letter-spacing: .08em; }
.fr-verdict.ACCEPT { color: var(--white); background: #2e7d32; }
.fr-verdict.REJECT { color: var(--white); background: var(--red); }
.fr-verdict.COUNTER { color: var(--ink); background: #fff0b8; }
.fr-result { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--gray-100); }
.fr-result header { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 12px; }
.fr-result header strong { font: 700 15px var(--font-mono); }
.fr-result header small { color: var(--gray-500); font: 600 9px var(--font-mono); }
.fr-quote { margin: 0 0 12px; padding: 10px 14px; color: var(--ink); background: var(--paper); border-left: 3px solid var(--ink); font: italic 500 12px/1.5 var(--font-sans); }
.fr-ubar { position: relative; height: 18px; display: flex; background: var(--gray-100); }
.fr-ubar i { display: block; height: 100%; }
.fr-ubar b { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--ink); }
.fr-ubar b::after { content: attr(data-label); position: absolute; top: -14px; left: 50%; transform: translateX(-50%); color: var(--ink); font: 700 7px var(--font-mono); white-space: nowrap; }
.fr-ulegend { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px 18px; color: #55575d; font: 600 9px var(--font-mono); }
.fr-ulegend span { display: flex; align-items: center; gap: 6px; }
.fr-ulegend i { display: block; width: 10px; height: 10px; }
.fr-u-financial { background: var(--ink); }
.fr-u-opportunity { background: var(--team-color, var(--red)); }
.fr-u-contention { background: #8d8a83; }
.fr-u-scheme { background: #c9b26b; }
.fr-u-persona { background: #6b8fc9; }
.fr-terms { margin: 0 0 12px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.fr-terms div { padding: 10px 12px; background: var(--paper); display: flex; flex-direction: column; gap: 4px; }
.fr-terms span { color: var(--gray-500); font: 700 7px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-terms strong { font: 700 15px var(--font-mono); letter-spacing: -.03em; }
@media (max-width: 640px) { .fr-form, .fr-terms { grid-template-columns: 1fr 1fr; } }
`;

export function NegotiationStyles() {
  return <style href="fr-neg" precedence="default">{NEG_CSS}</style>;
}

/** Dollars <-> the $M text the form edits. */
const toM = (v: number) => String(Math.round(v / 10_000) / 100);
const fromM = (s: string) => Math.round((parseFloat(s) || 0) * 1_000_000);

type TermsText = { years: number; total: string; bonus: string; guaranteed: string };
export const termsToText = (t: ContractTerms): TermsText =>
  ({ years: t.years, total: toM(t.total_value), bonus: toM(t.bonus), guaranteed: toM(t.guaranteed) });
const textToTerms = (t: TermsText): ContractTerms =>
  ({ years: t.years, total_value: fromM(t.total), bonus: fromM(t.bonus), guaranteed: fromM(t.guaranteed) });

function termsProblem(t: ContractTerms): string | null {
  if (t.total_value <= 0) return "Enter a total value.";
  if (t.bonus > t.total_value) return "The signing bonus can't be more than the total.";
  if (t.guaranteed > t.total_value) return "Guaranteed money can't be more than the total.";
  if (t.bonus < 0 || t.guaranteed < 0) return "Money can't be negative.";
  return null;
}

/** Years / total / bonus / guaranteed in $M; calls onSubmit with whole-dollar terms. `seed` resets the fields. */
export function TermsForm({ seed, submitLabel, busy, onSubmit, onCancel }: {
  seed: ContractTerms;
  submitLabel: string;
  busy: boolean;
  onSubmit: (t: ContractTerms) => void;
  onCancel?: () => void;
}) {
  const [text, setText] = useState<TermsText>(() => termsToText(seed));
  useEffect(() => setText(termsToText(seed)), [seed]);
  const terms = textToTerms(text);
  const problem = termsProblem(terms);
  const aav = terms.years > 0 ? terms.total_value / terms.years : 0;
  const set = (k: keyof TermsText) => (e: { target: { value: string } }) =>
    setText((t) => ({ ...t, [k]: k === "years" ? Number(e.target.value) : e.target.value }));
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!problem) onSubmit(terms); }}>
      <div className="fr-form">
        <label>Years
          <select value={text.years} onChange={set("years")}>
            {[1, 2, 3, 4, 5].map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
        <label>Total ($M)<input inputMode="decimal" value={text.total} onChange={set("total")} /><small>{money(aav)} per year</small></label>
        <label>Signing bonus ($M)<input inputMode="decimal" value={text.bonus} onChange={set("bonus")} /><small>prorated over {Math.min(text.years, 5)} yr{text.years === 1 ? "" : "s"}</small></label>
        <label>Guaranteed ($M)<input inputMode="decimal" value={text.guaranteed} onChange={set("guaranteed")} />
          <small>{terms.total_value > 0 ? `${Math.round((Math.max(terms.guaranteed, terms.bonus) / terms.total_value) * 100)}% of total` : "—"}</small>
        </label>
      </div>
      {problem && <p className="fr-form-note">{problem}</p>}
      <footer>
        {onCancel && <button type="button" className="fr-btn" onClick={onCancel}>Close</button>}
        <button type="submit" className="fr-btn primary" disabled={!!problem || busy}>{busy ? "Sending…" : submitLabel}</button>
      </footer>
    </form>
  );
}

const PART_LABEL: Record<keyof UtilityParts, string> = {
  financial: "Money", opportunity: "Role", contention: "Contender", scheme: "Scheme fit", persona: "Persona",
};

/** Stacked weighted parts of U (0..1 across), with his acceptance bar as a marker. */
export function UtilityBar({ parts, utility, bar }: { parts: UtilityParts; utility: number; bar: number | null }) {
  const segs = (Object.keys(OFFER_WEIGHTS) as (keyof typeof OFFER_WEIGHTS)[]).map((k) => ({
    k, score: parts[k], width: Math.max(0, parts[k] * OFFER_WEIGHTS[k]),
  }));
  const persona = parts.persona;
  const pct = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`;
  return (
    <div>
      <div className="fr-ubar" role="img" aria-label={`Offer utility ${utility.toFixed(2)}${bar === null ? "" : ` against a bar of ${bar.toFixed(2)}`}`}>
        {segs.map((s) => <i key={s.k} className={`fr-u-${s.k}`} style={{ width: pct(s.width) }} title={`${PART_LABEL[s.k]} ${s.score.toFixed(2)} × ${OFFER_WEIGHTS[s.k]}`} />)}
        {persona > 0 && <i className="fr-u-persona" style={{ width: pct(persona) }} title={`Persona +${persona.toFixed(2)}`} />}
        {bar !== null && <b style={{ left: pct(bar) }} data-label={`bar ${bar.toFixed(2)}`} />}
      </div>
      <div className="fr-ulegend">
        {segs.map((s) => (
          <span key={s.k}><i className={`fr-u-${s.k}`} />{PART_LABEL[s.k]} {s.score.toFixed(2)} <em>×{OFFER_WEIGHTS[s.k]}</em></span>
        ))}
        <span><i className="fr-u-persona" />Persona {persona >= 0 ? "+" : ""}{persona.toFixed(2)}</span>
      </div>
    </div>
  );
}

export function TermsFigures({ t }: { t: ContractTerms }) {
  return (
    <div className="fr-terms">
      <div><span>Years</span><strong>{t.years}</strong></div>
      <div><span>Total</span><strong>{money(t.total_value)}</strong></div>
      <div><span>Bonus</span><strong>{money(t.bonus)}</strong></div>
      <div><span>Guaranteed</span><strong>{money(t.guaranteed)}</strong></div>
    </div>
  );
}

const STATUS_TEXT = { ACCEPT: "Accepted", REJECT: "Rejected", COUNTER: "Counter-offer" } as const;

/** The agent's answer: verdict, utility breakdown, his note, and a counter that can be loaded into the form. */
export function NegotiationResult({ r, onUseCounter }: { r: NegotiationResponse; onUseCounter: (t: ContractTerms) => void }) {
  return (
    <div className="fr-result" aria-live="polite">
      <header>
        <span className={`fr-verdict ${r.status}`}>{STATUS_TEXT[r.status]}</span>
        <strong>U = {r.utility.toFixed(2)}</strong>
        {r.bar !== null && <small>needs {r.bar.toFixed(2)} to accept</small>}
        <small>offer #{r.offer_id}</small>
      </header>
      {r.note && <p className="fr-quote">“{r.note}”</p>}
      <UtilityBar parts={r.parts} utility={r.utility} bar={r.bar} />
      {r.counter && (
        <>
          <h4>His counter</h4>
          <TermsFigures t={r.counter} />
          <footer><button className="fr-btn" onClick={() => onUseCounter(r.counter!)}>Load counter into the form</button></footer>
        </>
      )}
    </div>
  );
}

// ---- Free agency -----------------------------------------------------------------------------------------------

const POSITIONS = ["QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT", "LE", "RE", "DT", "LOLB", "MLB", "ROLB", "CB", "FS", "SS", "K", "P"];
type SortKey = "scheme_fit" | "overall" | "market_worth" | "age";
const SORT_LABEL: Record<SortKey, string> = { scheme_fit: "Scheme fit", overall: "OVR", market_worth: "Market worth", age: "Youngest" };

const FA_CSS = `
.fr-filters { display: grid; grid-template-columns: 2fr repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 16px; }
.fr-fa { min-width: 860px; }
.fr-fa td:nth-child(3) { color: #55575d; font-weight: 400; background: transparent; }
.fr-fit { display: inline-flex; align-items: center; gap: 6px; }
.fr-fit i { display: block; width: 46px; height: 6px; background: var(--gray-100); }
.fr-fit i b { display: block; height: 100%; background: var(--team-color, var(--red)); }
.fo-table tr.fr-picked td { background: #f5f4f1; }
@media (max-width: 760px) { .fr-filters { grid-template-columns: 1fr 1fr; } }
`;

function defaultOffer(p: FreeAgent): ContractTerms {
  const a = p.asking;
  const total = a?.total_value ?? (p.market_worth ?? 1_000_000) * 2;
  const years = a?.years ?? 2;
  const guaranteed = a?.guaranteed ?? Math.round(total * 0.3);
  return { years, total_value: total, bonus: Math.round(guaranteed / 2 / 100_000) * 100_000, guaranteed };
}

function sortAgents(list: FreeAgent[], key: SortKey): FreeAgent[] {
  const v = (p: FreeAgent): number => {
    const x = key === "age" ? p.age : p[key];
    if (x === null) return key === "age" ? 99 : -1;
    return x;
  };
  return [...list].sort((a, b) => (key === "age" ? v(a) - v(b) : v(b) - v(a)) || (b.overall ?? 0) - (a.overall ?? 0) || a.name.localeCompare(b.name));
}

export default function FreeAgencyModule({ teamAbbr }: { teamAbbr: string }) {
  const [position, setPosition] = useState("");
  const [minOvr, setMinOvr] = useState("");
  const [maxAge, setMaxAge] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("scheme_fit");
  const [query, setQuery] = useState("");

  // Debounce the server-side filters so typing an OVR doesn't fire a fetch per keystroke.
  useEffect(() => {
    const qs = new URLSearchParams({ team: teamAbbr });
    if (position) qs.set("position", position);
    if (minOvr.trim()) qs.set("min_ovr", String(parseInt(minOvr, 10) || 0));
    if (maxAge.trim()) qs.set("max_age", String(parseInt(maxAge, 10) || 0));
    const t = setTimeout(() => setQuery(`?${qs}`), 300);
    return () => clearTimeout(t);
  }, [teamAbbr, position, minOvr, maxAge]);

  // GET /api/franchise/free-agents is league-wide (not under /{abbr}); franchiseFetch joins "free-agents" + query.
  const [market] = useFranchiseData<FreeAgentsResponse>("free-agents", query || `?team=${teamAbbr}`);
  const m = market.status === "ready" ? market.data : null;

  const rows = useMemo(() => {
    if (!m) return [];
    const s = search.trim().toLowerCase();
    return sortAgents(s ? m.players.filter((p) => p.name.toLowerCase().includes(s)) : m.players, sort);
  }, [m, search, sort]);

  const [picked, setPicked] = useState<FreeAgent | null>(null);
  const [seed, setSeed] = useState<ContractTerms | null>(null);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<NegotiationResponse | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  const pick = (p: FreeAgent) => { setPicked(p); setSeed(defaultOffer(p)); setResult(null); setSendError(null); };
  const submit = (t: ContractTerms) => {
    if (!picked) return;
    const body: OfferRequest = { player_id: picked.player_id, ...t };
    setSending(true);
    setSendError(null);
    franchiseFetch<NegotiationResponse>(teamAbbr, "/offers", "POST", body)
      .then(setResult)
      .catch((e) => setSendError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSending(false));
  };

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <NegotiationStyles />
      <style href="fr-fa" precedence="default">{FA_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title">
          <span>{teamAbbr} · {m ? `Season ${m.season} · Week ${m.week} · ${m.in_season ? "street market" : "offseason market"}` : "Market"}</span>
          <h2>Free Agency</h2>
        </div>
        <div className="fr-filters" role="search">
          <label>Search<input value={search} placeholder="Player name" onChange={(e) => setSearch(e.target.value)} /></label>
          <label>Position
            <select value={position} onChange={(e) => setPosition(e.target.value)}>
              <option value="">All</option>
              {POSITIONS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label>Min OVR<input inputMode="numeric" value={minOvr} placeholder="any" onChange={(e) => setMinOvr(e.target.value)} /></label>
          <label>Max age<input inputMode="numeric" value={maxAge} placeholder="any" onChange={(e) => setMaxAge(e.target.value)} /></label>
          <label>Sort by
            <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => <option key={k} value={k}>{SORT_LABEL[k]}</option>)}
            </select>
          </label>
        </div>
        {!m && <StateNote state={market} what="the free-agent market" />}
        {m && rows.length === 0 && <p className="fo-state">No free agents match these filters.</p>}
        {m && rows.length > 0 && (
          <div className="fo-table-scroll">
            <table className="fo-table fr-fa" aria-label="Free agents">
              <thead>
                <tr><th>Player</th><th>OVR</th><th>Age</th><th>Scheme Fit</th><th>Market Worth</th><th>Asking</th><th></th></tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.player_id} className={picked?.player_id === p.player_id ? "fr-picked" : undefined}>
                    <td>
                      <a href={`#player-${p.player_id}`}><strong>{p.name}</strong></a>
                      <small>{p.position}{p.dev_trait && p.dev_trait !== "Normal" && <b className="fo-tag">{p.dev_trait}</b>}</small>
                    </td>
                    <td>{p.overall ?? "—"}</td>
                    <td>{p.age ?? "—"}</td>
                    <td>
                      {p.scheme_fit === null ? "—" : (
                        <span className="fr-fit"><i><b style={{ width: `${Math.round(p.scheme_fit * 100)}%` }} /></i>{Math.round(p.scheme_fit * 100)}%</span>
                      )}
                    </td>
                    <td>{money(p.market_worth)}<small>per year</small></td>
                    <td>
                      {p.asking ? <>{money(p.asking.total_value)}<small>{p.asking.years} yr{p.asking.years === 1 ? "" : "s"} · {money(p.asking.guaranteed)} gtd</small></> : "—"}
                    </td>
                    <td><div className="fr-actions"><button className="fr-btn" onClick={() => pick(p)}>Make offer</button></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {m && <p className="fo-basis">{rows.length} of {m.players.length} free agents. Scheme fit is measured against {teamAbbr}'s scheme.</p>}

        {picked && seed && (
          <div className="fr-panel">
            <span>Offer · {picked.position} · {picked.overall ?? "—"} OVR</span>
            <h3>{picked.name}</h3>
            <TermsForm seed={seed} submitLabel="Send offer" busy={sending} onSubmit={submit} onCancel={() => setPicked(null)} />
            <p className="fr-form-note">
              Offers aren't instant signings. He weighs money, his role on your depth chart, how close you are to
              contending and scheme fit. An accepted offer is written into the save by the league office at its next
              run, if your cap and roster still allow it; other teams may bid in the meantime.
            </p>
            {sendError && <p className="fr-warn" role="alert">The offer didn't go through: {sendError}</p>}
            {result && <NegotiationResult r={result} onUseCounter={(t) => setSeed({ ...t })} />}
          </div>
        )}
      </section>
    </section>
  );
}
