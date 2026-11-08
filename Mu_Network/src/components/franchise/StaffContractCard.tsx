// #franchise staff-contract card (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §2.5, U3; PROJECT_SPEC §5.2): each staff
// member's annual salary, contract years and a Full Control badge, from GET /api/team/{abbr}/front-office's
// staff_contracts block. On the Franchise Command Center it fetches the block itself; the Team Hub passes its
// already-loaded block as `contracts` so the tab doesn't fetch twice. Shapes: types/staffContracts.ts.
import { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { money } from "../team/format";
import "../team/frontOffice.css";
import type { StaffContract, StaffContractKind } from "../../types/staffContracts";

const ROLE_LABEL: Record<StaffContractKind, string> = {
  HC: "Head Coach",
  GM: "General Manager",
  OC: "Offensive Coordinator",
  DC: "Defensive Coordinator",
};

const SC_CSS = `
.sc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.sc-card { padding: 16px 18px; display: flex; flex-direction: column; gap: 6px; background: var(--white); border-top: 3px solid var(--gray-100); }
.sc-card.sc-full { border-top-color: #b8860b; }
.sc-card > span { color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.sc-card .sc-salary { font: 700 26px var(--font-mono); letter-spacing: -.05em; }
.sc-card .sc-years { color: #55575d; font: 600 10px var(--font-mono); }
.sc-badge { align-self: flex-start; padding: 3px 8px; border-radius: 3px; color: var(--white); background: #b8860b; font: 700 8px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
`;

function years(n: number): string {
  return `${n} yr${n === 1 ? "" : "s"}`;
}

function ContractCard({ c }: { c: StaffContract }) {
  return (
    <article className={`sc-card${c.full_control ? " sc-full" : ""}`}>
      <span>{ROLE_LABEL[c.person_kind] ?? c.person_kind}</span>
      <strong className="sc-salary">{money(c.annual_salary)}</strong>
      <small className="sc-years">
        {c.remaining_years} of {c.total_years} years left · signed {c.signed_season}
      </small>
      {c.full_control && <b className="sc-badge">Full Control</b>}
    </article>
  );
}

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; contracts: StaffContract[] | null };

export default function StaffContractCard({ teamAbbr, season, contracts }: {
  teamAbbr: string;
  season?: number;
  contracts?: StaffContract[] | null;
}) {
  const provided = contracts !== undefined;
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    if (provided) return;
    let live = true;
    setState({ status: "loading" });
    const q = season ? `?season=${season}` : "";
    fetch(`${API_BASE}/api/team/${teamAbbr}/front-office${q}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((data) => live && setState({ status: "ready", contracts: data.staff_contracts ?? null }))
      .catch((e) => live && setState({ status: "error", message: e instanceof Error ? e.message : String(e) }));
    return () => { live = false; };
  }, [provided, teamAbbr, season]);

  const list = provided ? contracts : (state.status === "ready" ? state.contracts : null);
  const loading = !provided && state.status === "loading";
  const error = !provided && state.status === "error" ? state.message : null;

  return (
    <section className="team-data-view fo-view">
      <style href="fr-staff-contracts" precedence="default">{SC_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title"><span>{teamAbbr} · Coaching staff</span><h2>Staff Contracts</h2></div>
        {loading && <p className="fo-state">Loading staff contracts…</p>}
        {error && <p className="fo-state">Staff contracts couldn't load ({error}). Try again in a moment.</p>}
        {!loading && !error && !list && <p className="fo-state">No staff contracts are on file for this franchise yet.</p>}
        {list && (
          <div className="sc-grid">
            {list.map((c) => <ContractCard key={c.person_kind} c={c} />)}
          </div>
        )}
      </section>
    </section>
  );
}
