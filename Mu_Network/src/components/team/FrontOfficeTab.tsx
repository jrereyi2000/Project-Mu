// The Team hub's Front Office tab (PROJECT_SPEC §5.1/§5.2): the cap sheet and the active contracts, from
// GET /api/team/{abbr}/front-office (server/front_office_api.py). Every figure keeps its basis: projected years
// read "est.", an estimated cap row says so, and a term that isn't recorded shows "—" (never $0).
import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../api";
import "./frontOffice.css";

type ContractYear = { year: number; cap_hit: number | null; projected: boolean };
type Contract = {
  contract_id: number;
  player_id: number;
  name: string;
  position: string | null;
  length: number | null;
  years_remaining: number | null;
  through: number;
  expiring: boolean;
  cap_hit: number | null;
  total_value: number | null;
  bonus: number | null;
  cut_savings: number | null;
  cut_penalty: number | null;
  no_trade_clause: boolean;
  incentive: boolean;
  source: "SCAN" | "TRANSACTION";
  basis: string;
  years: ContractYear[];
  new_deal: { years: number; total_value: number | null; starts: number } | null;
};
type Cap = {
  cap: number | null;
  adjusted_cap: number | null;
  cap_space: number | null;
  cap_space_rank: number;
  teams: number;
  salaries: number | null;
  dead_money: number | null;
  total_spend: number | null;
  next_rookie_reserve: number | null;
  estimated: boolean;
  basis: string;
};
type Committed = { year: number; scanned: number; projected: number; players: number };
type FrontOffice = {
  season: number;
  year_columns: number[];
  cap: Cap | null;
  contracts: Contract[] | null;
  committed: Committed[] | null;
};

const GROUPS: Record<string, string[]> = {
  Offense: ["QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT"],
  Defense: ["LE", "RE", "DT", "LEDG", "REDG", "LOLB", "MLB", "ROLB", "SAM", "MIKE", "WILL", "CB", "FS", "SS"],
  "Special Teams": ["K", "P", "LS"],
};
type SortKey = "cap_hit" | "total_value" | "years_remaining" | "name";

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value < 0 ? "-" : "";
  const a = Math.abs(value);
  if (a >= 1_000_000) return `${sign}$${(a / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (a >= 1_000) return `${sign}$${(a / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return `${sign}$${a}`;
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function CapSheet({ cap }: { cap: Cap }) {
  const base = cap.adjusted_cap || cap.cap || 0;
  const pct = (v: number | null) => (base > 0 && v ? Math.max(0, (v / base) * 100) : 0);
  const over = (cap.cap_space ?? 0) < 0;
  const tiles: [string, string, string][] = [
    ["Cap Space", money(cap.cap_space), `${ordinal(cap.cap_space_rank)} of ${cap.teams}`],
    ["Total Spend", money(cap.total_spend), `of ${money(base)} adjusted cap`],
    ["Dead Money", money(cap.dead_money), "charged this season"],
    ["Rookie Reserve", money(cap.next_rookie_reserve), "set aside for the next class"],
  ];
  return (
    <section className="fo-block">
      <div className="team-section-title"><span>Salary Cap{cap.estimated ? " · Estimated" : ""}</span><h2>Cap Sheet</h2></div>
      <div className="fo-cap-tiles">
        {tiles.map(([label, value, note]) => (
          <article key={label} className={label === "Cap Space" && over ? "over" : ""}>
            <span>{label}</span>
            <strong>{value}{cap.estimated && label !== "Rookie Reserve" ? <em>est.</em> : null}</strong>
            <small>{note}</small>
          </article>
        ))}
      </div>
      <div className="fo-cap-bar" role="img"
        aria-label={`Salaries ${money(cap.salaries)}, dead money ${money(cap.dead_money)}, cap space ${money(cap.cap_space)}`}>
        <i className="salaries" style={{ width: `${pct(cap.salaries)}%` }} />
        <i className="dead" style={{ width: `${pct(cap.dead_money)}%` }} />
        {!over && <i className="space" style={{ width: `${pct(cap.cap_space)}%` }} />}
      </div>
      <div className="fo-cap-legend">
        <span><i className="salaries" />Salaries {money(cap.salaries)}</span>
        <span><i className="dead" />Dead money {money(cap.dead_money)}</span>
        <span><i className="space" />{over ? "Over the cap by " + money(-(cap.cap_space ?? 0)) : "Space " + money(cap.cap_space)}</span>
      </div>
      <p className="fo-basis">{cap.basis}</p>
    </section>
  );
}

function YearCell({ y }: { y: ContractYear | undefined }) {
  if (!y || !y.cap_hit) return <td className="fo-empty">—</td>;
  return y.projected
    ? <td className="fo-projected" title="Projected year of a new deal">{money(y.cap_hit)} <em>est.</em></td>
    : <td>{money(y.cap_hit)}</td>;
}

function Contracts({ contracts, committed, years }: { contracts: Contract[]; committed: Committed[]; years: number[] }) {
  const [group, setGroup] = useState<string>("All");
  const [sort, setSort] = useState<SortKey>("cap_hit");
  const [desc, setDesc] = useState(true);

  const rows = useMemo(() => {
    const list = group === "All" ? contracts : contracts.filter((c) => GROUPS[group]?.includes(c.position || ""));
    const val = (c: Contract) => (sort === "name" ? c.name : (c[sort] ?? -1));
    return [...list].sort((a, b) => {
      const x = val(a), y = val(b);
      const cmp = typeof x === "string" ? x.localeCompare(y as string) : (x as number) - (y as number);
      return desc ? -cmp : cmp;
    });
  }, [contracts, group, sort, desc]);

  const header = (key: SortKey, label: string, className?: string) => (
    <th key={label} className={className} aria-sort={sort === key ? (desc ? "descending" : "ascending") : "none"}>
      <button onClick={() => (sort === key ? setDesc(!desc) : (setSort(key), setDesc(key !== "name")))}>
        {label}<i>{sort === key ? (desc ? "▼" : "▲") : "↕"}</i>
      </button>
    </th>
  );

  return (
    <section className="fo-block">
      <div className="team-section-title"><span>{contracts.length} Active Deals</span><h2>Contracts</h2></div>
      <div className="fo-chips" role="group" aria-label="Position group">
        {["All", ...Object.keys(GROUPS)].map((g) => (
          <button key={g} className={group === g ? "active" : ""} onClick={() => setGroup(g)}>{g}</button>
        ))}
      </div>
      <div className="fo-table-scroll">
        <table className="fo-table">
          <thead>
            <tr>
              {header("name", "Player")}
              {header("years_remaining", "Yrs Left")}
              {years.map((y, i) => (i === 0 ? header("cap_hit", String(y), "fo-current") : <th key={y}>{y}</th>))}
              {header("total_value", "Total")}
              <th>Bonus</th>
              <th>Cut Saves</th>
              <th>Dead If Cut</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.contract_id}>
                <td>
                  <a href={`#player-${c.player_id}`}><strong>{c.name}</strong></a>
                  <small>
                    {c.position || "?"}
                    {c.expiring && <b className="fo-tag contract-year">Contract year</b>}
                    {c.no_trade_clause && <b className="fo-tag">No-trade</b>}
                    {c.incentive && <b className="fo-tag">Incentives</b>}
                    {c.new_deal && <b className="fo-tag new-deal" title={`${c.new_deal.years} years, ${money(c.new_deal.total_value)} total`}>New deal from {c.new_deal.starts}</b>}
                    {c.source === "TRANSACTION" && <b className="fo-tag signing" title={c.basis}>Signing</b>}
                  </small>
                </td>
                <td>{c.years_remaining ?? "—"}<small>thru {c.through}</small></td>
                {years.map((y) => <YearCell key={y} y={c.years.find((x) => x.year === y)} />)}
                <td>{money(c.total_value)}</td>
                <td>{money(c.bonus)}</td>
                <td>{money(c.cut_savings)}</td>
                <td>{money(c.cut_penalty)}</td>
              </tr>
            ))}
          </tbody>
          {group === "All" && (
            <tfoot>
              <tr>
                <td><strong>Committed</strong><small>all active deals</small></td>
                <td />
                {committed.map((r) => (
                  <td key={r.year}>
                    {money(r.scanned)}
                    {r.projected > 0 && <em className="fo-projected">+{money(r.projected)} est.</em>}
                    <small>{r.players} players</small>
                  </td>
                ))}
                <td colSpan={4} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <p className="fo-basis">
        Cap hits come from the team's books; <em>est.</em> marks a projected year of a new deal. "—" means the term
        isn't recorded. In-season signings carry only their length and amount.
      </p>
    </section>
  );
}

export default function FrontOfficeTab({ abbr, season }: { abbr: string; season: number }) {
  const [data, setData] = useState<FrontOffice | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setData(null);
    setError(false);
    fetch(`${API_BASE}/api/team/${abbr}/front-office?season=${season}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then(setData)
      .catch(() => setError(true));
  }, [abbr, season]);

  if (error) return <section className="team-data-view"><p className="fo-state">The front office couldn't load. Try again in a moment.</p></section>;
  if (!data) return <section className="team-data-view"><p className="fo-state">Loading the books…</p></section>;
  return (
    <section className="team-data-view fo-view">
      {data.cap
        ? <CapSheet cap={data.cap} />
        : <p className="fo-state">No cap sheet recorded for {data.season}.</p>}
      {data.contracts && data.committed
        ? <Contracts contracts={data.contracts} committed={data.committed} years={data.year_columns} />
        : <p className="fo-state">No contracts recorded for {data.season}.</p>}
    </section>
  );
}
