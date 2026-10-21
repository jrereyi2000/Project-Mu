import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { API_BASE } from "./api";

// #debug (reached from the header's LIVE badge; also from a phone on the home network): league state,
// every table (sortable, filterable, paginated, cells editable), the API's endpoints, the spec docs, and
// buttons that start the weekly runs. Backend: server/debug_api.py. Writes need the DEBUG_TOKEN from .env.

const DEBUG_API = `${API_BASE}/api/debug`;
const TOKEN_KEY = "mu_debug_token";

type Column = { column: string; type: string; nullable: boolean; generated: boolean; pk: boolean };
type TableRows = { table: string; kind: string; columns: Column[]; pk: string[]; total: number; page: number; page_size: number; rows: Record<string, unknown>[] };
type Job = { id: string; kind: string; kind_label: string; status: string; exit_code?: number | null; started_at: string; resume?: string | null; log_tail?: string; url_hash?: string; headline?: string; article_id?: number; target_desk?: string };
type Capture = { id: number; folder: string; mode: string; season: number; week: number | null; status: string; error: string | null; loaded_at: string | null };
type Resumable = { folder: string; mode: string; stage: string };
type DebugState = { state: { current_season: number; current_week: number }; database: string; captures: Capture[]; writes_enabled: boolean; running_job: Job | null; resumable: Resumable[] };
type NewsWeekSummary = { season: number; week: number; articles: number; tweets: number };
type NewsSummary = { current_season: number; current_week: number; summary: NewsWeekSummary[]; total_articles: number; total_tweets: number };

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

async function call<T>(path: string, init?: RequestInit, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["X-Debug-Token"] = token;
  const r = await fetch(`${DEBUG_API}${path}`, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string> || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body?.detail ? String(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : `HTTP ${r.status}`);
  return body as T;
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const btn = "px-3 py-1.5 text-xs font-bold uppercase tracking-wider border-2 border-[var(--ink)] bg-white hover:bg-[var(--ink)] hover:text-white disabled:opacity-40 disabled:hover:bg-white disabled:hover:text-[var(--ink)]";
const btnRed = "px-3 py-2 text-xs font-bold uppercase tracking-wider text-white bg-[var(--red)] border-2 border-[var(--red)] hover:bg-[var(--ink)] hover:border-[var(--ink)] disabled:opacity-40";
const card = "bg-white border-t-4 border-[var(--ink)] p-4";
const label = "block text-[10px] font-bold uppercase tracking-widest text-[var(--gray-500)] mb-1";
const input = "border border-[var(--gray-300)] px-2 py-1 text-sm bg-white w-full";

// ------------------------------------------------------------- control ----
const RUNS: { kind: string; title: string; note: string }[] = [
  { kind: "scan_week", title: "Scan week", note: "Full weekly run: capture Season Stats, Team Stats and last week's schedule, extract, load, engines, articles. Playoffs (week 19 on): last round's schedule, next round's matchups and every game's box score (~7 min a game) instead of Season Stats. Week 22 (Pro Bowl week): no console, just the Super Bowl week's articles." },
  { kind: "publish_articles", title: "Publish articles", note: "Runs the newsroom swarm for the next unpublished week (national, fantasy, and Agent Wire slates)." },
  { kind: "backfill_week", title: "Backfill previous week", note: "The same capture, extract, load and engines. No articles." },
  { kind: "game_scan", title: "Game scan", note: "Every game of last week: box scores and both teams' player stats (~7 min a game), loaded over the backfill's lines. No articles." },
  { kind: "season_scan", title: "Season scan", note: "Annual start-of-season cadence: captures the 18-week schedule, all 32 team rosters, every team's contracts (Team Salary, right after rosters), and the draft prospect board (in that order; prospects last). No articles." },
  { kind: "roster_scan", title: "Roster scan", note: "Captures all 32 team rosters, extracts player cards, and updates players/seasons in the database (normally run once a season with schedule and draft board). No articles." },
  { kind: "contract_scan", title: "Contract scan", note: "Captures every team's Team Salary screen (contracts and cap), extracts and loads it; a re-scan supersedes the old contracts. ~40 min. Normally part of the Season scan; only loads in-season (the year on screen must match the league season). No articles." },
  { kind: "transactions_scan", title: "League transactions scan", note: "Reads League > League Transactions from the newest week already loaded (from Week 1 the first time; preseason moves are already in the Week 1 roster), loads signings, releases and re-signings (team moves, contracts and cap kept current from them; no Team Salary re-scan). Trade rows are skipped: trades go through the GM agents' approval flow. Scan week does this too. No articles." },
  { kind: "gm_proposals", title: "GM trade proposals", note: "One run of the GM agents' trade engine for this week (no console): expires stale proposals, updates directions and needs, and adds this week's proposals to the Front Office card. Scan week runs it too, after the engines." },
  { kind: "gm_offseason_stage", title: "Next offseason stage", note: "After the Super Bowl: a League transactions scan (Remote Play takes the console; the CPU's re-signings, signings and releases are loaded), then the GM agents' trade proposals for the next offseason stage: re-signing, free agency, draft, preseason (stage 1, post-Super Bowl, runs with the Super Bowl scan). New proposals land on the Front office card." },
  { kind: "draft_scan", title: "Draft prospect scan", note: "Scans the scouting draft board line-by-line, extracts prospects via Apple Vision OCR, and syncs data/draft/ prospects board (normally run once a season with schedule and roster scans, after both). No articles." },
];

// League Transactions (team management step 2): rows to review
type TxRow = { id: number; txn_date: string; type: string; player_name: string | null; position: string | null; from_team: string | null; to_team: string | null; raw_text: string; flags: string | null; applied: boolean };
type TxState = { season: number; installed: boolean; rows: TxRow[]; unparsed: TxRow[]; ambiguous: TxRow[] };

function TransactionsCard() {
  const [tx, setTx] = useState<TxState | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    call<TxState>("/transactions?limit=25").then(setTx).catch(e => setError(String(e.message || e)));
  }, []);
  const line = (r: TxRow) => (
    <div key={r.id} className="border-b border-[var(--gray-100)] pb-1">
      <span className="font-mono text-[var(--gray-500)] mr-2">{r.txn_date}</span>
      <b className="mr-2">{r.type}</b>{r.raw_text}
      {!r.applied && <span className="ml-2 text-[var(--gray-500)]">(history)</span>}
    </div>
  );
  return (
    <section className={card}>
      <h3 className="font-black uppercase tracking-wide mb-3">League transactions{tx ? ` · ${tx.season}` : ""}</h3>
      {error && <p className="text-xs text-[var(--red)]">{error}</p>}
      {tx && !tx.installed && <p className="text-xs text-[var(--gray-500)]">The transactions migration isn't applied to this database yet.</p>}
      {tx?.installed && (
        <div className="grid gap-3 text-xs">
          <div>
            <span className={label}>Unparsed rows ({tx.unparsed.length})</span>
            {tx.unparsed.length ? tx.unparsed.map(line) : <span className="text-[var(--gray-500)]">None.</span>}
          </div>
          <div>
            <span className={label}>Ambiguous players ({tx.ambiguous.length})</span>
            {tx.ambiguous.length ? tx.ambiguous.map(line) : <span className="text-[var(--gray-500)]">None.</span>}
          </div>
          <div>
            <span className={label}>Latest rows</span>
            {tx.rows.length ? tx.rows.map(line) : <span className="text-[var(--gray-500)]">Nothing loaded yet.</span>}
          </div>
        </div>
      )}
    </section>
  );
}

// Front Office (team management step 3): the GM agents' trade proposals, JR's approve / confirm, recorded trades
type FoLeg = { type: string; from: string; to: string; player_id?: number; player_name?: string; position?: string; ovr?: number; cap_hit_now?: number; label?: string };
type FoCheck = { ok: boolean; detail: string };
type FoProposal = { id: number; week: number; status: string; status_reason: string | null; chip: string | null; origin: string; trigger: string; window: string; team_a: string; team_b: string; headline: string | null; rationale_a: string | null; rationale_b: string | null; rationale_source: string | null; value_a: number | null; value_b: number | null; cap: { a: { before: number | null; after: number | null; dead: number | null; derived: boolean | null }; b: { before: number | null; after: number | null; dead: number | null; derived: boolean | null } }; legality: Record<string, FoCheck>; flags: string[]; legs: FoLeg[]; steps: string[]; executed_as: string | null };
type FoTeam = { team_id: number; abbr: string; team: string; gm_name: string | null; direction: string | null; direction_score: number | null; needs: { group: string; score: number }[]; urgent_need: { group: string } | null; user_controlled: boolean; suggestions_on: boolean };
type FoSighting = { id: number; txn_date: string; raw_text: string; match_status: string; proposal_id: number | null; resolved?: Record<string, unknown>[]; unresolved?: string[] };
type FoFlag = { kind: string; detail: string; proposal_id?: number; sighting_id?: number };
type FoState = { season: number; installed: boolean; week?: number; window?: string | null; budget?: { executed: number; target: number; season_target: number; open: number; max_open: number; deadline_week: number; p_exec: number; offseason_target: number; offseason_executed: number; offseason_stage?: { done: number | null; done_name: string | null; next: number | null; next_name: string | null } }; teams?: FoTeam[]; proposals?: FoProposal[]; flags?: FoFlag[]; sightings?: FoSighting[] };

const money = (v: number | null | undefined) => v === null || v === undefined ? "?" : `$${(v / 1e6).toFixed(2)}M`;

// "PLAYER 1234 CAR NO" or "PICK 2037 1 CAR CAR NO" (season, round, original team, from, to): one leg a line
function parseLegs(text: string): Record<string, unknown>[] {
  return text.split("\n").map(l => l.trim().split(/\s+/)).filter(w => w[0]).map(w => w[0].toUpperCase() === "PICK"
    ? { type: "PICK", draft_season: Number(w[1]), round: Number(w[2]), original_team: w[3]?.toUpperCase(), from: w[4]?.toUpperCase(), to: w[5]?.toUpperCase() }
    : { type: "PLAYER", player_id: Number(w[1]), from: w[2]?.toUpperCase(), to: w[3]?.toUpperCase() });
}

// Draft Room (team management step 4): the GM agents choose picks 1-32 live, JR makes each one in Madden and records it.
type DrProspect = { prospect_key: string; full_name: string; position: string; college: string; age: number; consensus_rank: number; grade: number; round_label: string; team_grade?: number | null; team_rank?: number | null; choice_source?: string; madden_match?: string | null };
type DrSlot = { pick: number; team: string; original_team: string; user_controlled: boolean; selection: DrProspect | null };
type DrClock = { pick: number; team: string; team_name: string; gm: string | null; original_team: string; user_controlled: boolean; recommendation: DrProspect | null; alternatives: DrProspect[]; rationale?: string; steps: string[] };
type DrState = { installed: boolean; draft_season: number; error?: string; step?: number; locked?: boolean; before?: string; after?: string; slots?: DrSlot[]; on_clock?: DrClock | null; projected?: (DrProspect & { pick: number; team: string })[]; consensus?: DrProspect[]; team_board?: { team: string; rows: DrProspect[] }; trades?: { id: number; status: string; headline: string | null; pick: number | null }[] };

function DraftRoomCard({ token }: { token: string }) {
  const [dr, setDr] = useState<DrState | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [view, setView] = useState("");                 // "" = consensus board, else a team abbreviation
  const [search, setSearch] = useState("");
  const [fixes, setFixes] = useState("");
  const load = useCallback(() => {
    call<DrState>(`/draft-room${view ? `?team=${view}` : ""}`).then(setDr).catch(e => setError(String(e.message || e)));
  }, [view]);
  useEffect(() => { load(); }, [load]);
  const act = async (path: string, body?: unknown, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setMessage("");
    try { setDr(await call<DrState>(path, { method: "POST", body: JSON.stringify(body || {}) }, token)); setMessage("Done."); load(); }
    catch (e) { setMessage(String((e as Error).message || e)); }
  };
  const lock = () => {
    const parsed: Record<string, string> = {};
    fixes.split(",").map(x => x.trim()).filter(Boolean).forEach(x => { const [p, t] = x.split("="); if (p && t) parsed[p.trim()] = t.trim().toUpperCase(); });
    act("/draft-room/lock-order", { fixes: Object.keys(parsed).length ? parsed : undefined }, "Lock the draft order? Check the Round 1 owners against Madden's draft order screen first.");
  };
  const oc = dr?.on_clock;
  const recorded = (dr?.slots || []).filter(s => s.selection);
  const last = recorded.length ? recorded[recorded.length - 1] : null;
  const pool = (view && dr?.team_board ? dr.team_board.rows : dr?.consensus || []);
  const hits = search.trim() ? (dr?.consensus || []).filter(p => `${p.full_name} ${p.position}`.toLowerCase().includes(search.trim().toLowerCase())).slice(0, 8) : [];
  const line = (p: DrProspect) => `${p.full_name} · ${p.position} · ${p.college} · #${p.consensus_rank} (${p.grade})`;
  return (
    <section className={card}>
      <h3 className="font-black uppercase tracking-wide mb-3">Draft room{dr ? ` · ${dr.draft_season}` : ""}</h3>
      {error && <p className="text-xs text-[var(--red)]">{error}</p>}
      {dr && !dr.installed && <p className="text-xs text-[var(--gray-500)]">The scouting migration (2026_12_12_scouting.sql) isn't applied to this database yet.</p>}
      {dr?.error && <p className="text-xs text-[var(--gray-500)]">{dr.error}</p>}
      {dr?.installed && dr.slots && (
        <div className="grid gap-3 text-xs">
          {message && <p className="text-[var(--red)]">{message}</p>}
          {!dr.locked && (
            <div className="grid gap-2">
              <p>{dr.before}</p>
              <label className={label}>Owner fixes before locking (pick=TEAM, comma-separated)</label>
              <input className={input} value={fixes} onChange={e => setFixes(e.target.value)} placeholder="5=DEN, 18=GB" />
              <button className={btnRed} disabled={!token} onClick={lock}>Lock draft order</button>
            </div>
          )}
          {oc && (
            <div className="border-2 border-[var(--ink)] p-2 grid gap-1">
              <div className="text-sm"><b>Pick {oc.pick}: {oc.team_name}</b>{oc.original_team !== oc.team ? ` (via ${oc.original_team})` : ""} · {oc.gm || "GM"}{oc.user_controlled ? " · your team (advice only)" : ""}</div>
              {oc.recommendation && <div className="text-sm">GM's pick: <b>{line(oc.recommendation)}</b>{oc.recommendation.team_rank ? ` · their board #${oc.recommendation.team_rank}` : ""}</div>}
              {oc.rationale && <p><i>{oc.rationale}</i></p>}
              {oc.alternatives.length > 0 && <div>Next: {oc.alternatives.map(line).join(" | ")}</div>}
              <ol className="list-decimal pl-5">{oc.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
              <div className="flex flex-wrap gap-2">
                {oc.recommendation && <button className={btnRed} disabled={!token} onClick={() => act(`/draft-room/picks/${oc.pick}`, { prospect_key: oc.recommendation!.prospect_key, source: "GM" })}>Made it</button>}
                {last && <button className={btn} disabled={!token} onClick={() => act(`/draft-room/picks/${last.pick}/undo`, {}, `Undo pick ${last.pick}?`)}>Undo pick {last.pick}</button>}
              </div>
              <label className={label}>Different player (name or position)</label>
              <input className={input} value={search} onChange={e => setSearch(e.target.value)} placeholder="e.g. Bodden or CB" />
              {hits.map(p => <button key={p.prospect_key} className="text-left underline" disabled={!token} onClick={() => act(`/draft-room/picks/${oc.pick}`, { prospect_key: p.prospect_key, source: "OFF_SCRIPT" }, `Record ${p.full_name} at pick ${oc.pick}?`).then(() => setSearch(""))}>{line(p)}</button>)}
            </div>
          )}
          {dr.locked && !oc && <p>{dr.after}</p>}
          {(dr.trades || []).length > 0 && <div><span className={label}>Draft-day trades (approve and confirm them in Front office)</span>{dr.trades!.map(t => <div key={t.id}>#{t.id} pick {t.pick ?? "?"} · <b>{t.status}</b> · {t.headline}</div>)}</div>}
          <div>
            <span className={label}>Round 1</span>
            {dr.slots.map(s => (
              <div key={s.pick} className={`border-b border-[var(--gray-100)] py-0.5 ${oc?.pick === s.pick ? "font-bold" : ""}`}>
                <span className="font-mono mr-2">{s.pick}</span><b>{s.team}</b>{s.original_team !== s.team ? ` (via ${s.original_team})` : ""}{s.user_controlled ? " · JR" : ""}
                {s.selection && <span> · {s.selection.full_name} {s.selection.position} · {s.selection.choice_source}{s.selection.madden_match ? ` · ${s.selection.madden_match}` : ""}</span>}
              </div>
            ))}
          </div>
          {(dr.projected || []).length > 0 && <div><span className={label}>Projected rest of Round 1</span>{dr.projected!.map(p => <div key={p.pick}>{p.pick} {p.team}: {p.full_name} {p.position}</div>)}</div>}
          <div>
            <span className={label}>Board</span>
            <select className={input} value={view} onChange={e => setView(e.target.value)}>
              <option value="">Consensus</option>
              {Array.from(new Set((dr.slots || []).map(s => s.team))).sort().map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            {pool.slice(0, 40).map(p => <div key={p.prospect_key}>{p.team_rank ? `${p.team_rank}. ` : `${p.consensus_rank}. `}{p.full_name} · {p.position} · {p.college} · {p.team_grade ?? p.grade}</div>)}
          </div>
        </div>
      )}
    </section>
  );
}

function FrontOfficeCard({ token }: { token: string }) {
  const [fo, setFo] = useState<FoState | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [rec, setRec] = useState({ team_a: "", team_b: "", legs: "", proposal_id: "" });
  const load = useCallback(() => {
    call<FoState>("/front-office").then(setFo).catch(e => setError(String(e.message || e)));
  }, []);
  useEffect(() => { load(); }, [load]);
  const act = async (path: string, body?: unknown, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setMessage("");
    try {
      const r = await call<{ warnings?: string[]; legality?: { legal: boolean } }>(path, { method: "POST", body: JSON.stringify(body || {}) }, token);
      const warn = [...(r.warnings || []), ...(r.legality && !r.legality.legal ? ["legality warning (see the proposal's checks)"] : [])];
      setMessage(warn.length ? `Done, with warnings: ${warn.join("; ")}` : "Done.");
      load();
    } catch (e) { setMessage(String((e as Error).message || e)); }
  };
  const setControl = async (abbr: string, body: { user_controlled?: boolean; suggestions_on?: boolean }) => {
    try { await call(`/front-office/teams/${abbr}`, { method: "PUT", body: JSON.stringify(body) }, token); load(); }
    catch (e) { setMessage(String((e as Error).message || e)); }
  };
  const legText = (l: FoLeg) => l.type === "PICK" ? l.label : `${l.position} ${l.player_name} ${l.ovr ?? ""}`;
  const b = fo?.budget;
  return (
    <section className={card}>
      <h3 className="font-black uppercase tracking-wide mb-3">Front office{fo ? ` · ${fo.season}` : ""}</h3>
      {error && <p className="text-xs text-[var(--red)]">{error}</p>}
      {fo && !fo.installed && <p className="text-xs text-[var(--gray-500)]">The GM agents migration (2026_12_10_gm_agents.sql) isn't applied to this database yet.</p>}
      {fo?.installed && b && (
        <div className="grid gap-3 text-xs">
          <div className="flex flex-wrap gap-3">
            <span><b>{b.executed}</b> / {b.target} executed (season target {b.season_target})</span>
            <span>{b.open} open (max {b.max_open})</span>
            <span>deadline: week {b.deadline_week}</span>
            <span>window: {fo.window || "closed"}</span>
            <span>JR approval rate {Math.round(b.p_exec * 100)}%</span>
            <span>offseason {b.offseason_executed} / {b.offseason_target}{b.offseason_stage?.done ? ` · stage ${b.offseason_stage.done} (${b.offseason_stage.done_name}) done` : ""}{b.offseason_stage?.next && fo.window === "OFFSEASON" ? ` · next: ${b.offseason_stage.next_name}` : ""}</span>
          </div>
          {message && <p className="text-[var(--red)]">{message}</p>}
          <div>
            <span className={label}>Proposals</span>
            {(fo.proposals || []).length === 0 && <span className="text-[var(--gray-500)]">None yet.</span>}
            {(fo.proposals || []).map(p => (
              <div key={p.id} className="border-b border-[var(--gray-100)] py-1">
                <button className="text-left w-full" onClick={() => setOpen(open === p.id ? null : p.id)}>
                  <span className="font-mono text-[var(--gray-500)] mr-2">#{p.id} wk {p.week}</span>
                  <b className="mr-2">{p.status}</b>{p.chip && <span className="mr-2 px-1 bg-[var(--gray-100)]">{p.chip}</span>}
                  <b>{p.team_a} ↔ {p.team_b}</b>: {p.headline || p.origin}
                  {p.flags.length > 0 && <span className="ml-2 text-[var(--gray-500)]">[{p.flags.join(", ")}]</span>}
                </button>
                {open === p.id && (
                  <div className="grid gap-1 mt-1 pl-2 border-l-2 border-[var(--gray-300)]">
                    {[p.team_a, p.team_b].map((t, i) => (
                      <div key={t}><b>{t}</b> gets {p.legs.filter(l => l.to === t).map(legText).join(", ") || "nothing"} · cap {money((i ? p.cap.b : p.cap.a).before)} → {money((i ? p.cap.b : p.cap.a).after)}{(i ? p.cap.b : p.cap.a).dead ? ` (dead ${money((i ? p.cap.b : p.cap.a).dead)})` : ""}{(i ? p.cap.b : p.cap.a).derived ? " · derived cap row" : ""} · surplus {money(i ? p.value_b : p.value_a)}</div>
                    ))}
                    {p.rationale_a && <p><i>{p.rationale_a}</i></p>}
                    {p.rationale_b && <p><i>{p.rationale_b}</i></p>}
                    <div>{Object.entries(p.legality || {}).map(([k, c]) => <span key={k} className={`mr-2 ${c.ok ? "" : "text-[var(--red)] font-bold"}`}>{k}: {c.detail}</span>)}</div>
                    {p.status_reason && <div className="text-[var(--gray-500)]">{p.status_reason}</div>}
                    {(p.status === "APPROVED" || p.status === "PROPOSED") && <ol className="list-decimal pl-5">{p.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
                    <div className="flex flex-wrap gap-2 mt-1">
                      {p.status === "PROPOSED" && <button className={btn} disabled={!token} onClick={() => act(`/front-office/proposals/${p.id}/approve`)}>Approve</button>}
                      {(p.status === "PROPOSED" || p.status === "APPROVED") && <button className={btn} disabled={!token} onClick={() => act(`/front-office/proposals/${p.id}/reject`, { reason: window.prompt("Why? (optional)") || undefined })}>Reject</button>}
                      {["APPROVED", "EXPIRED"].includes(p.status) && <button className={btnRed} disabled={!token} onClick={() => act(`/front-office/proposals/${p.id}/confirm`, {}, "You made exactly this trade in Madden? Its players, contracts, cap rows and picks will move.")}>Made it</button>}
                      {p.status === "APPROVED" && <button className={btn} disabled={!token} onClick={() => act(`/front-office/proposals/${p.id}/could-not-make`, { reason: window.prompt("What did Madden say?") || undefined })}>Couldn't make it</button>}
                      {["APPROVED", "EXPIRED", "PROPOSED"].includes(p.status) && <button className={btn} disabled={!token} onClick={() => setRec({ team_a: p.team_a, team_b: p.team_b, legs: "", proposal_id: String(p.id) })}>Made a different trade</button>}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div>
            <span className={label}>{rec.proposal_id ? `Made a different trade (proposal #${rec.proposal_id})` : "Record a trade (one you made yourself)"}</span>
            <div className="grid grid-cols-2 gap-2">
              <input className={input} placeholder="Team A (CAR)" value={rec.team_a} onChange={e => setRec({ ...rec, team_a: e.target.value })} />
              <input className={input} placeholder="Team B (IND)" value={rec.team_b} onChange={e => setRec({ ...rec, team_b: e.target.value })} />
            </div>
            <textarea className={`${input} mt-2 font-mono`} rows={3} placeholder={"PLAYER <player_id> <FROM> <TO>\nPICK <season> <round> <ORIGINAL> <FROM> <TO>"} value={rec.legs} onChange={e => setRec({ ...rec, legs: e.target.value })} />
            <div className="flex gap-2 mt-1">
              <button className={btnRed} disabled={!token || !rec.team_a || !rec.team_b || !rec.legs.trim()} onClick={() => act("/front-office/record", { team_a: rec.team_a.toUpperCase(), team_b: rec.team_b.toUpperCase(), assets: parseLegs(rec.legs), proposal_id: rec.proposal_id ? Number(rec.proposal_id) : undefined }, "Record this trade? Its players, contracts, cap rows and picks will move.").then(() => setRec({ team_a: "", team_b: "", legs: "", proposal_id: "" }))}>Record trade</button>
              {rec.proposal_id && <button className={btn} onClick={() => setRec({ team_a: "", team_b: "", legs: "", proposal_id: "" })}>Cancel</button>}
            </div>
          </div>
          <div>
            <span className={label}>Flags ({(fo.flags || []).length})</span>
            {(fo.flags || []).length ? fo.flags!.map((f, i) => <div key={i}><b className="mr-2">{f.kind}</b>{f.detail}</div>) : <span className="text-[var(--gray-500)]">None.</span>}
          </div>
          <div>
            <span className={label}>Trade rows seen in Madden's log</span>
            {(fo.sightings || []).length === 0 && <span className="text-[var(--gray-500)]">None.</span>}
            {(fo.sightings || []).map(s => (
              <div key={s.id} className="border-b border-[var(--gray-100)] pb-1">
                <span className="font-mono text-[var(--gray-500)] mr-2">{s.txn_date}</span><b className="mr-2">{s.match_status}</b>{s.raw_text}
                {s.match_status !== "MATCHED" && (
                  <div className="pl-2">
                    <div>Parsed legs: {(s.resolved || []).map(x => JSON.stringify(x)).join(" · ") || "none"}{(s.unresolved || []).length ? ` · unresolved: ${s.unresolved!.join(", ")}` : ""}</div>
                    <button className={btnRed} disabled={!token || !(s.resolved || []).length || (s.unresolved || []).length > 0} onClick={() => act(`/front-office/sightings/${s.id}/apply`, {}, "Apply this trade? Check the parsed legs first: its players, contracts, cap rows and picks will move.")}>Apply this trade</button>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div>
            <span className={label}>Directions, needs and who runs each team</span>
            {(fo.teams || []).map(t => (
              <div key={t.team_id} className="flex flex-wrap gap-2 items-center border-b border-[var(--gray-100)]">
                <b className="w-10">{t.abbr}</b><span className="w-20">{t.direction || "-"}</span>
                <span className="w-40 truncate">{t.gm_name}</span>
                <span className="flex-1">{t.needs.slice(0, 3).map(n => n.group).join(", ")}{t.urgent_need ? ` · urgent ${t.urgent_need.group}` : ""}</span>
                <label><input type="checkbox" disabled={!token} checked={t.user_controlled} onChange={e => setControl(t.abbr, { user_controlled: e.target.checked })} /> JR controls</label>
                <label><input type="checkbox" disabled={!token} checked={t.suggestions_on} onChange={e => setControl(t.abbr, { suggestions_on: e.target.checked })} /> suggestions</label>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// Trade finder (team management step 3): every package the GM engine's rules allow for one player, read-only
type TfPlayer = { player_id: number; name: string; team: string; pos: string; ovr: number | null; age: number | null; dev: string | null };
type TfDeal = { shape: string | null; buyer_gets: string[]; seller_gets: string[]; buyer_surplus: number; seller_surplus: number; cap_after: Record<string, number | null>; dead_money: Record<string, number>; failed: string[]; checks: Record<string, { ok: boolean; detail: string }> };
type TfTeam = { team: string; direction: string; record: string | null; tried: number; n_viable: number; viable: TfDeal[]; best_miss: TfDeal | null; misses: Record<string, number>; notes: string[] };
type TfResult = { season: number; week: number; window: string; notes: string[]; player: TfPlayer & { years_left: number | null; cap_hit: number | null; ladder: [number, number] | null; seller_record: string | null; seller_direction: string }; teams: TfTeam[] };

function TradeFinderCard() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<TfPlayer[]>([]);
  const [team, setTeam] = useState("");
  const [res, setRes] = useState<TfResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showMisses, setShowMisses] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = window.setTimeout(() => {
      call<TfPlayer[]>(`/front-office/players?q=${encodeURIComponent(q.trim())}`).then(setHits).catch(() => setHits([]));
    }, 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const find = (pid: number) => {
    setBusy(true); setError(""); setRes(null); setHits([]);
    call<TfResult>(`/front-office/trade-finder?player_id=${pid}${team ? `&team=${encodeURIComponent(team.toUpperCase())}` : ""}`)
      .then(setRes).catch(e => setError(String(e.message || e))).finally(() => setBusy(false));
  };
  const deal = (d: TfDeal, buyer: string, i: number) => (
    <div key={i} className="pl-2 border-l-2 border-[var(--gray-300)] mb-1">
      {d.shape && <b className="mr-2">{d.shape}</b>}
      <span>{buyer} gets {d.buyer_gets.join(", ")} · {res?.player.team} gets {d.seller_gets.join(", ") || "nothing"}</span>
      <div className="text-[var(--gray-500)]">surplus {buyer} {money(d.buyer_surplus)}, {res?.player.team} {money(d.seller_surplus)} · cap after {Object.entries(d.cap_after).map(([t, v]) => `${t} ${money(v)}`).join(", ")}</div>
      {d.failed.length > 0 && <div className="text-[var(--red)]">fails: {d.failed.map(f => d.checks[f] ? `${f} (${d.checks[f].detail})` : f).join("; ")}</div>}
    </div>
  );
  const p = res?.player;
  return (
    <section className={card}>
      <h3 className="font-black uppercase tracking-wide mb-3">Trade finder</h3>
      <p className="text-xs text-[var(--gray-500)] mb-2">Pick a player: every package the GM engine's rules allow for him from each other team, with both GMs' view and every check. Read-only: nothing is proposed or saved.</p>
      <div className="grid grid-cols-3 gap-2">
        <input className={`${input} col-span-2`} placeholder="Player name" value={q} onChange={e => setQ(e.target.value)} />
        <input className={input} placeholder="Buyer (optional, e.g. CAR)" value={team} onChange={e => setTeam(e.target.value)} />
      </div>
      {hits.length > 0 && (
        <div className="border border-[var(--gray-300)] mt-1 text-xs max-h-48 overflow-auto">
          {hits.map(h => (
            <button key={h.player_id} className="block w-full text-left px-2 py-1 hover:bg-[var(--gray-100)]" onClick={() => { setQ(h.name); find(h.player_id); }}>
              <b>{h.name}</b> · {h.team} {h.pos} {h.ovr} · age {h.age}{h.dev ? ` · ${h.dev}` : ""}
            </button>
          ))}
        </div>
      )}
      {busy && <p className="text-xs mt-2">Searching…</p>}
      {error && <p className="text-xs text-[var(--red)] mt-2">{error}</p>}
      {res && p && (
        <div className="grid gap-2 text-xs mt-3">
          <div><b>{p.name}</b> · {p.team} {p.pos} {p.ovr} · age {p.age}{p.dev ? ` · ${p.dev}` : ""} · {p.years_left ?? "?"} yr left · hit {money(p.cap_hit)} · {p.team} {p.seller_record} {p.seller_direction}{p.ladder ? ` · ladder R${p.ladder[0]}${p.ladder[0] === p.ladder[1] ? "" : `–R${p.ladder[1]}`}` : p.ovr && p.ovr >= 88 && res.window === "IN_SEASON" ? " · blockbuster only (weeks 7–8)" : ""} · {res.window} week {res.week}</div>
          {res.notes.map((n, i) => <div key={i} className="text-[var(--red)]">{n}</div>)}
          <label><input type="checkbox" checked={showMisses} onChange={e => setShowMisses(e.target.checked)} /> show teams with no viable package (and why)</label>
          {res.teams.filter(t => showMisses || t.n_viable > 0).map(t => (
            <div key={t.team} className="border-b border-[var(--gray-100)] pb-1">
              <b>{t.team}</b> <span className="text-[var(--gray-500)]">{t.direction} {t.record} · {t.n_viable} of {t.tried} packages work</span>
              {t.notes.map((n, i) => <span key={i} className="ml-2 text-[var(--gray-500)]">({n})</span>)}
              {t.viable.map((d, i) => deal(d, t.team, i))}
              {t.n_viable === 0 && t.best_miss && <div><span className="text-[var(--gray-500)]">closest miss:</span>{deal(t.best_miss, t.team, 0)}<span className="text-[var(--gray-500)]">all misses: {Object.entries(t.misses).map(([k, v]) => `${k} ${v}`).join(", ")}</span></div>}
            </div>
          ))}
          {!showMisses && res.teams.every(t => t.n_viable === 0) && <div className="text-[var(--gray-500)]">No team has a package that passes every check and both GMs. Tick the box to see each team's closest miss.</div>}
        </div>
      )}
    </section>
  );
}

// Negotiate (team management step 3): JR offers a trade from a team he controls to another team's GM, who
// accepts, counters, rejects or walks away (engines/gm_negotiation.py). An agreed deal lands on the Front office
// card as an APPROVED proposal.
type NgPlayer = { player_id: number; name: string; pos: string; ovr: number | null; age: number | null; starter: boolean; cap_hit: number; years_left: number | null; tradeable: boolean; why_not: string | null };
type NgPick = { draft_season: number; round: number; original_team: string; label: string; booked: boolean };
type NgTeam = { abbr: string; name: string; gm_name: string | null; direction: string; needs: string[]; record: string | null; cap_space: number | null; players: NgPlayer[]; picks: NgPick[] };
type NgAssets = { window: string; week: number; teams: Record<string, NgTeam> };
type NgAsset = { type: string; player_id?: number; draft_season?: number; round?: number; original_team?: string; from: string; to: string };
type NgTurn = { turn_no: number; actor: string; action: string; legs: (NgAsset & { label: string })[] | null; assets: NgAsset[] | null; surplus_user: number | null; mood: string | null; failed_checks: Record<string, string>; message: string | null; message_source: string | null };
type Negotiation = { id: number; week: number; window: string; status: string; status_reason: string | null; user_team: string; gm_team: string; gm_name: string | null; patience: number; patience_left: number; proposal_id: number | null; on_table: NgTurn | null; turns: NgTurn[] };

const assetKey = (a: NgAsset) => a.type === "PLAYER" ? `P:${a.player_id}` : `K:${a.draft_season}:${a.round}:${a.original_team}`;

function NegotiateCard({ token }: { token: string }) {
  const [teams, setTeams] = useState<FoTeam[]>([]);
  const [userTeam, setUserTeam] = useState("");
  const [gmTeam, setGmTeam] = useState("");
  const [assets, setAssets] = useState<NgAssets | null>(null);
  const [picked, setPicked] = useState<Record<string, NgAsset>>({});
  const [note, setNote] = useState("");
  const [negs, setNegs] = useState<Negotiation[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const loadNegs = useCallback(() => { call<Negotiation[]>("/front-office/negotiations").then(setNegs).catch(e => setMessage(String(e.message || e))); }, []);
  useEffect(() => {
    call<FoState>("/front-office").then(s => {
      const ts = s.teams || [];
      setTeams(ts);
      setUserTeam(prev => prev || ts.find(t => t.user_controlled)?.abbr || "");
    }).catch(() => {});
    loadNegs();
  }, [loadNegs]);
  useEffect(() => {
    setAssets(null); setPicked({});
    if (!userTeam || !gmTeam) return;
    call<NgAssets>(`/front-office/negotiations/assets?user_team=${userTeam}&gm_team=${gmTeam}`).then(setAssets).catch(e => setMessage(String(e.message || e)));
  }, [userTeam, gmTeam]);
  const open = negs.find(n => n.status === "OPEN" && n.user_team === userTeam && n.gm_team === gmTeam);
  const thread = open || negs.find(n => n.user_team === userTeam && n.gm_team === gmTeam);
  const toggle = (a: NgAsset) => setPicked(p => { const k = assetKey(a); const n = { ...p }; if (n[k]) delete n[k]; else n[k] = a; return n; });
  const send = async () => {
    setBusy(true); setMessage("");
    try {
      const body = { assets: Object.values(picked), note: note.trim() || undefined };
      const n = open
        ? await call<Negotiation>(`/front-office/negotiations/${open.id}/offer`, { method: "POST", body: JSON.stringify(body) }, token)
        : await call<Negotiation>("/front-office/negotiations", { method: "POST", body: JSON.stringify({ ...body, user_team: userTeam, gm_team: gmTeam }) }, token);
      setNote("");
      if (n.status === "AGREED") setMessage(`Agreed: proposal #${n.proposal_id} is on the Front office card, approved and ready to make in Madden.`);
      loadNegs();
    } catch (e) { setMessage(String((e as Error).message || e)); }
    setBusy(false);
  };
  const act = async (path: string, body?: unknown) => {
    setBusy(true); setMessage("");
    try {
      const n = await call<Negotiation>(path, { method: "POST", body: JSON.stringify(body || {}) }, token);
      if (n.status === "AGREED") setMessage(`Agreed: proposal #${n.proposal_id} is on the Front office card, approved and ready to make in Madden.`);
      loadNegs();
    } catch (e) { setMessage(String((e as Error).message || e)); }
    setBusy(false);
  };
  const loadCounter = (t: NgTurn) => setPicked(Object.fromEntries((t.assets || []).map(a => [assetKey(a), a])));
  const side = (t: NgTeam | undefined, from: string, to: string) => t && (
    <div className="min-w-0">
      <span className={label}>{from === userTeam ? `You send (${from})` : `You get (${from}, ${t.gm_name || "GM"})`}</span>
      <div className="text-[var(--gray-500)] mb-1">{t.direction} {t.record ?? ""} · needs {t.needs.join(", ") || "-"} · cap {money(t.cap_space)}</div>
      <div className="max-h-56 overflow-auto border border-[var(--gray-100)]">
        {t.players.map(p => {
          const a: NgAsset = { type: "PLAYER", player_id: p.player_id, from, to };
          return (
            <label key={p.player_id} className={`flex gap-1 px-1 ${p.tradeable ? "" : "text-[var(--gray-500)]"}`} title={p.why_not || ""}>
              <input type="checkbox" disabled={!p.tradeable} checked={!!picked[assetKey(a)]} onChange={() => toggle(a)} />
              <span className="truncate">{p.pos} {p.name} {p.ovr} · {p.age} · {money(p.cap_hit)} {p.years_left ?? "?"}y{p.starter ? " · starter" : ""}{p.why_not ? ` · ${p.why_not}` : ""}</span>
            </label>
          );
        })}
        {t.picks.map(k => {
          const a: NgAsset = { type: "PICK", draft_season: k.draft_season, round: k.round, original_team: k.original_team, from, to };
          return (
            <label key={k.label} className={`flex gap-1 px-1 ${k.booked ? "text-[var(--gray-500)]" : ""}`}>
              <input type="checkbox" disabled={k.booked} checked={!!picked[assetKey(a)]} onChange={() => toggle(a)} />
              <span>{k.label}{k.booked ? " · in an open proposal" : ""}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
  const gmTeams = teams.filter(t => !t.user_controlled && t.gm_name);
  return (
    <section className={card}>
      <h3 className="font-black uppercase tracking-wide mb-3">Negotiate with a GM</h3>
      <p className="text-xs text-[var(--gray-500)] mb-2">Offer a trade from a team you control. The GM accepts, counters, or turns it down, and walks away if you keep wasting his time. A deal you both agree on lands on the Front office card, approved.</p>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <select className={input} value={userTeam} onChange={e => setUserTeam(e.target.value)}>
          <option value="">Your team</option>
          {teams.filter(t => t.user_controlled).map(t => <option key={t.abbr} value={t.abbr}>{t.abbr} · {t.team}</option>)}
        </select>
        <select className={input} value={gmTeam} onChange={e => setGmTeam(e.target.value)}>
          <option value="">GM to call</option>
          {gmTeams.map(t => <option key={t.abbr} value={t.abbr}>{t.abbr} · {t.gm_name} ({t.direction || "-"})</option>)}
        </select>
      </div>
      {message && <p className="text-xs text-[var(--red)] mt-2">{message}</p>}
      {thread && (
        <div className="text-xs mt-3 grid gap-1">
          <div><b>#{thread.id} {thread.status}</b> · week {thread.week} · {thread.gm_name}'s patience {"●".repeat(thread.patience_left)}{"○".repeat(Math.max(0, thread.patience - thread.patience_left))}{thread.status_reason ? ` · ${thread.status_reason}` : ""}{thread.proposal_id ? ` · proposal #${thread.proposal_id}` : ""}</div>
          {thread.turns.map(t => (
            <div key={t.turn_no} className={`pl-2 border-l-2 ${t.actor === "GM" ? "border-[var(--red)]" : "border-[var(--gray-300)]"}`}>
              <b className="mr-2">{t.actor === "GM" ? thread.gm_name : "You"} · {t.action}</b>
              {t.mood && t.actor === "JR" && <span className="mr-2 px-1 bg-[var(--gray-100)]">{t.mood === "INSULTED" ? "insulting" : t.mood === "APART" ? "far apart" : "close"}</span>}
              {t.legs && <span>you get {t.legs.filter(l => l.to === thread.user_team).map(l => l.label).join(", ") || "nothing"} · they get {t.legs.filter(l => l.to === thread.gm_team).map(l => l.label).join(", ") || "nothing"}</span>}
              {t.surplus_user !== null && t.legs && <span className="text-[var(--gray-500)]"> · your surplus {money(t.surplus_user)}</span>}
              {t.message && <p className={t.actor === "GM" ? "" : "italic text-[var(--gray-500)]"}>{t.message}</p>}
              {Object.keys(t.failed_checks).length > 0 && t.actor === "JR" && <div className="text-[var(--red)]">{Object.entries(t.failed_checks).map(([k, v]) => `${k}: ${v}`).join("; ")}</div>}
            </div>
          ))}
          {thread.on_table && (
            <div className="flex flex-wrap gap-2 mt-1">
              <button className={btnRed} disabled={!token || busy} onClick={() => act(`/front-office/negotiations/${thread.id}/accept`)}>Accept counter</button>
              <button className={btn} disabled={busy} onClick={() => loadCounter(thread.on_table!)}>Edit the counter</button>
            </div>
          )}
          {thread.status === "OPEN" && <button className={`${btn} justify-self-start`} disabled={!token || busy} onClick={() => act(`/front-office/negotiations/${thread.id}/close`)}>Walk away</button>}
        </div>
      )}
      {assets && !(thread?.status === "WALKED_AWAY" && thread.week === assets.week) && (
        <div className="text-xs mt-3 grid gap-2">
          <div className="grid gap-2">
            {side(assets.teams[userTeam], userTeam, gmTeam)}
            {side(assets.teams[gmTeam], gmTeam, userTeam)}
          </div>
          <input className={input} placeholder="What you tell him (optional)" value={note} onChange={e => setNote(e.target.value)} />
          <button className={`${btnRed} justify-self-start`} disabled={!token || busy || Object.keys(picked).length === 0} onClick={send}>
            {busy ? "Waiting for the GM…" : open ? "Make another offer" : "Make offer"}
          </button>
        </div>
      )}
    </section>
  );
}

function StatusPill({ status }: { status: string }) {
  const color = status === "running" ? "bg-amber-400 text-black" : status === "done" ? "bg-emerald-600 text-white" : status === "stopped" ? "bg-[var(--gray-500)] text-white" : "bg-[var(--red)] text-white";
  return <span className={`px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${color}`}>{status}</span>;
}

function Control({ token, data, reload }: { token: string; data: DebugState | null; reload: () => void }) {
  const [season, setSeason] = useState("");
  const [week, setWeek] = useState("");
  const [resume, setResume] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [log, setLog] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (data) {
      const s = String(data.state.current_season);
      setSeason(s);
      setWeek(String(data.state.current_week));
      setOffseasonSeason(prev => prev || s);
      setOffseasonManifestPath(prev => prev || `captures/offseason/${s}/manifest.json`);
    }
  }, [data?.state.current_season, data?.state.current_week]);

  const loadJobs = useCallback(() => {
    call<Job[]>("/jobs").then(js => {
      setJobs(js);
      const current = js.find(j => j.status === "running") || js[0];
      if (current) call<{ log: string }>(`/jobs/${current.id}/log?lines=300`).then(r => setLog(r.log)).catch(() => {});
    }).catch(e => setMessage(String(e.message || e)));
  }, []);

  useEffect(() => {
    loadJobs();
    const t = window.setInterval(loadJobs, 3000);
    return () => window.clearInterval(t);
  }, [loadJobs]);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log]);

  const running = jobs.find(j => j.status === "running");
  const latest = running || jobs[0];

  const saveState = async () => {
    setBusy(true); setMessage("");
    try {
      await call("/state", { method: "PUT", body: JSON.stringify({ current_season: Number(season), current_week: Number(week) }) }, token);
      setMessage("League state saved."); reload();
    } catch (e) { setMessage(`Not saved: ${(e as Error).message}`); }
    setBusy(false);
  };

  const start = async (kind: string, title: string) => {
    const what = resume ? `${title}, resuming ${resume}` : title;
    if (!window.confirm(`${what}?\n\nRemote Play takes over the console and your controller disconnects. Your phone gets a notice when it's your turn again.`)) return;
    setBusy(true); setMessage("");
    try {
      await call("/jobs", { method: "POST", body: JSON.stringify({ kind, resume: resume || null }) }, token);
      setMessage(`${what} started.`); setResume(""); loadJobs(); reload();
    } catch (e) { setMessage(`Not started: ${(e as Error).message}`); }
    setBusy(false);
  };

  const stop = async (id: string) => {
    if (!window.confirm("Stop this run? The capture stops where it is (every key is released); resume it later from its folder.")) return;
    try { await call(`/jobs/${id}/stop`, { method: "POST" }, token); setMessage("Stopping…"); loadJobs(); }
    catch (e) { setMessage(`Not stopped: ${(e as Error).message}`); }
  };

  const [news, setNews] = useState<NewsSummary | null>(null);
  const [targetWeek, setTargetWeek] = useState<string>("current");
  const [newsScope, setNewsScope] = useState<"ARTICLE" | "ALL">("ARTICLE");
  const [clearingNews, setClearingNews] = useState(false);

  // Custom story commissioning state (Two-Step Flow)
  const [promptText, setPromptText] = useState("");
  const [promptSeason, setPromptSeason] = useState("");
  const [promptWeek, setPromptWeek] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [commissioning, setCommissioning] = useState(false);
  const [customPlan, setCustomPlan] = useState<{
    classification: {
      target_desk: "NATIONAL" | "FANTASY";
      article_type_id: string;
      article_type_name: string;
      writer_archetype: string;
      team_id: number | null;
      desk_note: string;
      rationale: string;
    };
    catalog: {
      national_types: { id: string; name: string; allowed_writers: string[] }[];
      fantasy_types: { id: string; name: string; allowed_writers: string[] }[];
      national_writers: { id: string; name: string }[];
      fantasy_writers: { id: string; name: string }[];
      teams: { id: number; city: string; name: string; abbr: string }[];
    };
  } | null>(null);

  const [formDesk, setFormDesk] = useState<"NATIONAL" | "FANTASY">("NATIONAL");
  const [formType, setFormType] = useState("");
  const [formWriter, setFormWriter] = useState("");
  const [formTeam, setFormTeam] = useState<string>("");
  const [formDeskNote, setFormDeskNote] = useState("");

  // Offseason Package state
  const [offseasonSeason, setOffseasonSeason] = useState("");
  const [offseasonManifestPath, setOffseasonManifestPath] = useState("");
  const [offseasonDryRun, setOffseasonDryRun] = useState(false);
  const [offseasonOnly, setOffseasonOnly] = useState("");
  const [previewingOffseason, setPreviewingOffseason] = useState(false);
  const [runningOffseason, setRunningOffseason] = useState(false);
  const [offseasonPreviewData, setOffseasonPreviewData] = useState<any>(null);

  const loadNews = useCallback(() => {
    call<NewsSummary>("/news").then(setNews).catch(() => {});
  }, []);

  useEffect(() => {
    loadNews();
  }, [loadNews]);

  const curWeek = data?.state.current_week ?? Number(week) ?? 1;
  const curSeason = data?.state.current_season ?? Number(season) ?? 2035;

  let targetWeekNum: number | null = null;
  let targetSeasonNum: number | null = curSeason;
  let targetLabel = "";
  let targetArticles = 0;
  let targetTweets = 0;

  if (targetWeek === "current") {
    targetWeekNum = curWeek;
    targetLabel = `Week ${curWeek}`;
    const row = news?.summary.find(s => s.season === curSeason && s.week === curWeek);
    targetArticles = row?.articles || 0;
    targetTweets = row?.tweets || 0;
  } else if (targetWeek === "all_season") {
    targetWeekNum = null;
    targetLabel = `Season ${curSeason} (all weeks)`;
    const rows = (news?.summary || []).filter(s => s.season === curSeason);
    targetArticles = rows.reduce((acc, r) => acc + r.articles, 0);
    targetTweets = rows.reduce((acc, r) => acc + r.tweets, 0);
  } else if (targetWeek === "all_time") {
    targetWeekNum = null;
    targetSeasonNum = null;
    targetLabel = "All seasons & weeks";
    targetArticles = news?.total_articles || 0;
    targetTweets = news?.total_tweets || 0;
  } else {
    const w = Number(targetWeek);
    targetWeekNum = w;
    targetLabel = `Week ${w}`;
    const row = news?.summary.find(s => s.season === curSeason && s.week === w);
    targetArticles = row?.articles || 0;
    targetTweets = row?.tweets || 0;
  }

  const itemsToClear = newsScope === "ALL" ? (targetArticles + targetTweets) : targetArticles;

  const handleClearNews = async () => {
    if (!token) return;
    const desc = newsScope === "ALL" ? "articles & Agent Wire posts" : "articles";
    const confirmMsg = `Clear ${itemsToClear} ${desc} for ${targetLabel}?\n\nThis removes articles from news_feed, cascading to tags, power rankings, and agent memory entries so the slate can be rerun cleanly.`;
    if (!window.confirm(confirmMsg)) return;

    setClearingNews(true);
    setMessage("");
    try {
      const res = await call<{ deleted: number; remaining_articles: number; backup_file: string | null }>(
        "/news/clear",
        {
          method: "POST",
          body: JSON.stringify({
            season: targetSeasonNum,
            week: targetWeekNum,
            content_type: newsScope,
            backup: true,
          }),
        },
        token
      );
      setMessage(`Cleared ${res.deleted} items for ${targetLabel}. ${res.remaining_articles} articles remain in DB.${res.backup_file ? ` (Backup: ${res.backup_file})` : ""}`);
      loadNews();
      reload();
    } catch (e) {
      setMessage(`Clear failed: ${(e as Error).message}`);
    } finally {
      setClearingNews(false);
    }
  };

  const handlePublishNews = async () => {
    if (!token || !!running) return;
    const w = targetWeekNum !== null ? targetWeekNum : curWeek;
    if (!window.confirm(`Publish articles for Week ${w}?\n\nStarts the newsroom swarm (national, fantasy, and Agent Wire slates).`)) return;
    setBusy(true);
    setMessage("");
    try {
      await call("/jobs", { method: "POST", body: JSON.stringify({ kind: "publish_articles", week: w }) }, token);
      setMessage(`Newsroom run for Week ${w} started.`);
      loadJobs();
      reload();
    } catch (e) {
      setMessage(`Not started: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (data) {
      if (!promptSeason) setPromptSeason(String(data.state.current_season));
      if (!promptWeek) setPromptWeek(String(data.state.current_week));
    }
  }, [data?.state.current_season, data?.state.current_week, promptSeason, promptWeek]);

  const handleAnalyzePrompt = async () => {
    if (!token || !promptText.trim()) return;
    setAnalyzing(true);
    setMessage("");
    try {
      const res = await call<NonNullable<typeof customPlan>>(
        "/classify_article",
        {
          method: "POST",
          body: JSON.stringify({
            prompt: promptText.trim(),
            season: Number(promptSeason) || curSeason,
            week: Number(promptWeek) || curWeek,
          }),
        },
        token
      );
      setCustomPlan(res);
      setFormDesk(res.classification.target_desk);
      setFormType(res.classification.article_type_id);
      setFormWriter(res.classification.writer_archetype);
      setFormTeam(res.classification.team_id !== null ? String(res.classification.team_id) : "");
      setFormDeskNote(res.classification.desk_note);
      setMessage("Prompt analyzed. Review and adjust parameters below, then commission.");
    } catch (e) {
      setMessage(`Analysis failed: ${(e as Error).message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleCommissionStory = async () => {
    if (!token || !customPlan) return;
    setCommissioning(true);
    setMessage("");
    try {
      const s = Number(promptSeason) || curSeason;
      const w = Number(promptWeek) || curWeek;
      await call(
        "/commission_article",
        {
          method: "POST",
          body: JSON.stringify({
            prompt: promptText.trim(),
            season: s,
            week: w,
            target_desk: formDesk,
            article_type_id: formType,
            writer_archetype: formWriter,
            team_id: formTeam ? Number(formTeam) : null,
            desk_note: formDeskNote.trim() || promptText.trim(),
          }),
        },
        token
      );
      setMessage(`Commissioned ${formDesk} article. Log streaming below.`);
      setCustomPlan(null);
      setPromptText("");
      loadJobs();
      reload();
    } catch (e) {
      setMessage(`Commission failed: ${(e as Error).message}`);
    } finally {
      setCommissioning(false);
    }
  };

  const handleOffseasonSeasonChange = (val: string) => {
    setOffseasonSeason(val);
    if (!offseasonManifestPath || offseasonManifestPath.startsWith("captures/offseason/")) {
      setOffseasonManifestPath(`captures/offseason/${val}/manifest.json`);
    }
  };

  const handleValidateOffseason = async () => {
    if (!token) return;
    setPreviewingOffseason(true);
    setMessage("");
    setOffseasonPreviewData(null);
    try {
      const s = Number(offseasonSeason) || curSeason;
      const res = await call<any>(
        "/offseason/preview",
        {
          method: "POST",
          body: JSON.stringify({
            season: s,
            manifest_path: offseasonManifestPath.trim(),
            dry_run: offseasonDryRun,
            only: offseasonOnly || null,
          }),
        },
        token
      );
      setOffseasonPreviewData(res);
      setMessage("Offseason manifest validated successfully.");
    } catch (e) {
      setMessage(`Manifest preview failed: ${(e as Error).message}`);
    } finally {
      setPreviewingOffseason(false);
    }
  };

  const handleRunOffseason = async () => {
    if (!token || !!running) return;
    const s = Number(offseasonSeason) || curSeason;
    const mode = offseasonDryRun ? "DRY-RUN " : "";
    const scope = offseasonOnly ? `(${offseasonOnly})` : "(Full Package)";
    if (!window.confirm(`Run ${mode}Offseason Editorial Swarm for Season ${s} ${scope}?\n\nManifest: ${offseasonManifestPath}`)) return;

    setRunningOffseason(true);
    setBusy(true);
    setMessage("");
    try {
      const res = await call<any>(
        "/offseason/run",
        {
          method: "POST",
          body: JSON.stringify({
            season: s,
            manifest_path: offseasonManifestPath.trim(),
            dry_run: offseasonDryRun,
            only: offseasonOnly || null,
          }),
        },
        token
      );
      setMessage(res?.message || `Offseason swarm run started for Season ${s}.`);
      loadJobs();
      reload();
    } catch (e) {
      setMessage(`Offseason run failed: ${(e as Error).message}`);
    } finally {
      setRunningOffseason(false);
      setBusy(false);
    }
  };

  const resumable = data?.resumable || [];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      <div className="grid gap-4 content-start">
        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">League state</h3>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <label><span className={label}>Season</span><input className={input} inputMode="numeric" value={season} onChange={e => setSeason(e.target.value)} /></label>
            <label><span className={label}>Current week</span><input className={input} inputMode="numeric" value={week} onChange={e => setWeek(e.target.value)} /></label>
          </div>
          <button className={btn} disabled={busy || !token} onClick={saveState}>Save</button>
          <p className="text-xs text-[var(--gray-500)] mt-2">Current week = Madden's next unplayed week (19–23 = postseason). The weekly load sets it; edit it only to fix it.</p>
        </section>

        <section className={card}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-black uppercase tracking-wide">Newsroom & Articles</h3>
            <span className="text-[10px] font-mono text-[var(--gray-500)]">
              {news ? `${news.total_articles} articles · ${news.total_tweets} posts` : "Loading..."}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 mb-3">
            <label>
              <span className={label}>Target</span>
              <select className={input} value={targetWeek} onChange={e => setTargetWeek(e.target.value)}>
                <option value="current">Current week (Wk {curWeek})</option>
                {Array.from({ length: 24 }, (_, i) => i + 1).map(w => (
                  <option key={w} value={String(w)}>
                    Week {w}{w === 24 ? " (Offseason)" : ""}{w === curWeek ? " (current)" : ""}
                  </option>
                ))}
                <option value="all_season">All weeks (Season {curSeason})</option>
                <option value="all_time">All seasons & weeks</option>
              </select>
            </label>
            <label>
              <span className={label}>Scope</span>
              <select className={input} value={newsScope} onChange={e => setNewsScope(e.target.value as "ARTICLE" | "ALL")}>
                <option value="ARTICLE">Articles only (keep tweets)</option>
                <option value="ALL">Articles & Agent Wire posts</option>
              </select>
            </label>
          </div>

          <div className="bg-[var(--paper)] p-2.5 mb-3 text-xs border border-[var(--gray-200)] flex items-center justify-between">
            <div>
              <span className="font-bold">{targetLabel}:</span>{" "}
              <span>{targetArticles} articles, {targetTweets} posts</span>
            </div>
            <StatusPill status={targetArticles > 0 ? "done" : "stopped"} />
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              className={btnRed}
              disabled={clearingNews || busy || !token || itemsToClear === 0}
              onClick={handleClearNews}
            >
              {clearingNews ? "Clearing..." : `Clear ${targetLabel} (${itemsToClear})`}
            </button>
            <button
              className={btn}
              disabled={busy || !token || !!running}
              onClick={handlePublishNews}
              title={`Run newsroom swarm for ${targetWeekNum ? `Week ${targetWeekNum}` : `Week ${curWeek}`}`}
            >
              Publish {targetWeekNum ? `Wk ${targetWeekNum}` : `Wk ${curWeek}`} Slate
            </button>
          </div>

          <p className="text-xs text-[var(--gray-500)] mt-2">
            Clearing articles removes article tags, power rankings, and agent memory takes, allowing the week to be rerun cleanly.
          </p>
        </section>

        <section className={card}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-black uppercase tracking-wide">Offseason Editorial Package</h3>
            <span className="text-[10px] font-mono text-[var(--gray-500)]">Week 24 Slate</span>
          </div>
          <p className="text-xs text-[var(--gray-600)] mb-3">
            Publish the 6-article offseason editorial package (Super Bowl recap &amp; retrospectives, coaching carousel audit, marquee signings, and first-round draft recap).
          </p>

          <div className="grid gap-3">
            <div className="grid grid-cols-2 gap-3">
              <label>
                <span className={label}>Season</span>
                <input
                  className={input}
                  inputMode="numeric"
                  value={offseasonSeason}
                  onChange={e => handleOffseasonSeasonChange(e.target.value)}
                  disabled={previewingOffseason || runningOffseason || busy}
                />
              </label>

              <label>
                <span className={label}>Sub-Event Scope (--only)</span>
                <select
                  className={input}
                  value={offseasonOnly}
                  onChange={e => setOffseasonOnly(e.target.value)}
                  disabled={previewingOffseason || runningOffseason || busy}
                >
                  <option value="">All (Full 6-Article Package)</option>
                  <option value="super_bowl">super_bowl (SB Recap, Champ, Runner-Up)</option>
                  <option value="coaching">coaching (Coaching Carousel Audit)</option>
                  <option value="signings">signings (Marquee Signing Grades)</option>
                  <option value="draft">draft (First-Round Draft Recap)</option>
                </select>
              </label>
            </div>

            <label>
              <span className={label}>Manifest Path</span>
              <input
                className={input}
                value={offseasonManifestPath}
                onChange={e => setOffseasonManifestPath(e.target.value)}
                placeholder="captures/offseason/2035/manifest.json"
                disabled={previewingOffseason || runningOffseason || busy}
              />
            </label>

            <div className="flex items-center gap-2 py-1">
              <input
                type="checkbox"
                id="offseason-dry-run"
                checked={offseasonDryRun}
                onChange={e => setOffseasonDryRun(e.target.checked)}
                disabled={previewingOffseason || runningOffseason || busy}
                className="w-4 h-4 text-[var(--red)] border-[var(--gray-300)] rounded cursor-pointer"
              />
              <label htmlFor="offseason-dry-run" className="text-xs font-bold uppercase tracking-wider text-[var(--ink)] cursor-pointer select-none">
                Dry Run (<code className="font-mono text-[11px] font-normal">--dry-run</code>)
              </label>
            </div>

            <div className="flex flex-wrap gap-2 pt-1">
              <button
                className={btn}
                disabled={previewingOffseason || runningOffseason || busy || !token || !offseasonManifestPath.trim()}
                onClick={handleValidateOffseason}
              >
                {previewingOffseason ? "Validating Manifest…" : "Validate / Preview Manifest"}
              </button>
              <button
                className={btnRed}
                disabled={previewingOffseason || runningOffseason || busy || !token || !!running || !offseasonManifestPath.trim()}
                onClick={handleRunOffseason}
              >
                {runningOffseason ? "Dispatching Swarm…" : "Run Offseason Swarm"}
              </button>
              {offseasonPreviewData && (
                <button
                  className="px-2 py-1 text-xs border border-[var(--gray-300)] text-[var(--gray-500)] hover:text-black"
                  onClick={() => setOffseasonPreviewData(null)}
                >
                  Dismiss Preview
                </button>
              )}
            </div>

            {offseasonPreviewData && (
              <div className="bg-[var(--paper)] p-3 text-xs border border-[var(--gray-200)] mt-2">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-bold uppercase text-[10px] tracking-wider text-[var(--ink)]">Manifest Preview Result</span>
                  <StatusPill status={offseasonPreviewData.valid !== false && !offseasonPreviewData.error ? "done" : "stopped"} />
                </div>
                {offseasonPreviewData.error && (
                  <div className="text-[var(--red)] font-bold mb-2">Error: {String(offseasonPreviewData.error)}</div>
                )}
                {offseasonPreviewData.summary && (
                  <div className="text-[var(--ink)] mb-2 font-medium">{String(offseasonPreviewData.summary)}</div>
                )}
                <pre className="max-h-60 overflow-y-auto text-[11px] font-mono bg-white p-2 border border-[var(--gray-200)] text-[var(--gray-700)]">
                  {JSON.stringify(offseasonPreviewData, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </section>

        <section className={card}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-black uppercase tracking-wide">Commission Custom Story</h3>
            <span className="text-[10px] font-mono text-[var(--gray-500)]">AI Editorial Desk</span>
          </div>
          <p className="text-xs text-[var(--gray-600)] mb-3">
            Give a story prompt or topic. Step 1 uses Gemini to classify the angle and assign an appropriate writer. Step 2 lets you review or adjust parameters before dispatching the ReAct swarm.
          </p>

          {!customPlan ? (
            <div className="grid gap-3">
              <label>
                <span className={label}>Story Prompt / Assignment</span>
                <textarea
                  className={`${input} min-h-[75px] resize-y font-sans`}
                  placeholder="e.g. Deep dive into Caleb Williams' performance under pressure vs man coverage, or who to target on fantasy waivers for the playoffs..."
                  value={promptText}
                  onChange={e => setPromptText(e.target.value)}
                  disabled={analyzing || busy}
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label>
                  <span className={label}>Season</span>
                  <input
                    className={input}
                    inputMode="numeric"
                    value={promptSeason}
                    onChange={e => setPromptSeason(e.target.value)}
                    disabled={analyzing || busy}
                  />
                </label>
                <label>
                  <span className={label}>Publication Week</span>
                  <input
                    className={input}
                    inputMode="numeric"
                    value={promptWeek}
                    onChange={e => setPromptWeek(e.target.value)}
                    disabled={analyzing || busy}
                  />
                </label>
              </div>
              <button
                className={btn}
                disabled={analyzing || busy || !token || !promptText.trim()}
                onClick={handleAnalyzePrompt}
              >
                {analyzing ? "Analyzing Prompt with Gemini…" : "Step 1: Analyze Prompt & Plan"}
              </button>
            </div>
          ) : (
            <div className="grid gap-3 border-t border-[var(--gray-200)] pt-3">
              <div className="bg-[var(--paper)] p-2.5 text-xs border border-[var(--gray-200)]">
                <div className="font-bold text-[10px] uppercase text-[var(--ink)] mb-1">Proposed Angle & Rationale</div>
                <p className="text-[var(--gray-700)] italic">{customPlan.classification.rationale}</p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label>
                  <span className={label}>Target Desk</span>
                  <select
                    className={input}
                    value={formDesk}
                    onChange={e => {
                      const newDesk = e.target.value as "NATIONAL" | "FANTASY";
                      setFormDesk(newDesk);
                      const available = newDesk === "FANTASY" ? customPlan.catalog.fantasy_types : customPlan.catalog.national_types;
                      if (available.length > 0) {
                        setFormType(available[0].id);
                        const writers = newDesk === "FANTASY" ? customPlan.catalog.fantasy_writers : customPlan.catalog.national_writers;
                        setFormWriter(writers[0]?.id || "");
                      }
                    }}
                  >
                    <option value="NATIONAL">National Newsroom (Main Slate)</option>
                    <option value="FANTASY">Mu Fantasy Desk</option>
                  </select>
                </label>

                <label>
                  <span className={label}>Target Team (Optional)</span>
                  <select
                    className={input}
                    value={formTeam}
                    onChange={e => setFormTeam(e.target.value)}
                  >
                    <option value="">League-Wide / None</option>
                    {customPlan.catalog.teams.map(t => (
                      <option key={t.id} value={String(t.id)}>
                        {t.city} {t.name} ({t.abbr})
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label>
                  <span className={label}>Article Type</span>
                  <select
                    className={input}
                    value={formType}
                    onChange={e => {
                      const newType = e.target.value;
                      setFormType(newType);
                      const currentTypes = formDesk === "FANTASY" ? customPlan.catalog.fantasy_types : customPlan.catalog.national_types;
                      const found = currentTypes.find(t => t.id === newType);
                      if (found && found.allowed_writers.length > 0 && !found.allowed_writers.includes(formWriter)) {
                        setFormWriter(found.allowed_writers[0]);
                      }
                    }}
                  >
                    {(formDesk === "FANTASY" ? customPlan.catalog.fantasy_types : customPlan.catalog.national_types).map(t => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  <span className={label}>Writer / Persona</span>
                  <select
                    className={input}
                    value={formWriter}
                    onChange={e => setFormWriter(e.target.value)}
                  >
                    {(formDesk === "FANTASY" ? customPlan.catalog.fantasy_writers : customPlan.catalog.national_writers).map(w => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label>
                <span className={label}>Mandatory Angle / Desk Note</span>
                <textarea
                  className={`${input} min-h-[55px] resize-y font-sans`}
                  value={formDeskNote}
                  onChange={e => setFormDeskNote(e.target.value)}
                />
              </label>

              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  className={btnRed}
                  disabled={commissioning || busy || !token}
                  onClick={handleCommissionStory}
                >
                  {commissioning ? "Dispatching Swarm…" : "Step 2: Commission Story"}
                </button>
                <button
                  className={btn}
                  disabled={commissioning || busy}
                  onClick={() => setCustomPlan(null)}
                >
                  Edit Prompt
                </button>
              </div>
            </div>
          )}
        </section>

        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">Runs</h3>
          <div className="grid gap-3">
            {RUNS.map(r => (
              <div key={r.kind} className="flex flex-col gap-1">
                <button className={btnRed} disabled={busy || !token || !!running} onClick={() => start(r.kind, r.title)}>{r.title}</button>
                <span className="text-xs text-[var(--gray-500)]">{r.note}</span>
              </div>
            ))}
          </div>
          <label className="block mt-4"><span className={label}>Resume a stopped run (optional)</span>
            <select className={input} value={resume} onChange={e => setResume(e.target.value)}>
              <option value="">— new run —</option>
              {resumable.map(c => <option key={c.folder} value={c.folder}>{c.folder} · {c.mode} · {c.stage}</option>)}
            </select>
          </label>
          {!token && <p className="text-xs text-[var(--red)] mt-2">Enter the debug token above to start runs or edit.</p>}
          {running && <p className="text-xs mt-2">A run is in progress; one at a time.</p>}
        </section>

        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">Recent captures</h3>
          <div className="grid gap-2 text-xs">
            {(data?.captures || []).map(c => (
              <div key={c.id} className="flex flex-wrap gap-2 items-center border-b border-[var(--gray-100)] pb-1">
                <StatusPill status={c.status === "loaded" ? "done" : c.status === "failed" ? "failed" : c.status} />
                <b className="font-mono">{c.folder}</b><span>{c.mode}</span>{c.week !== null && <span>wk {c.week}</span>}
                {c.error && <span className="w-full text-[var(--red)] break-words">{c.error}</span>}
              </div>
            ))}
          </div>
        </section>
        <TransactionsCard />
        <FrontOfficeCard token={token} />
        <TradeFinderCard />
        <NegotiateCard token={token} />
        <DraftRoomCard token={token} />
      </div>

      <div className="grid gap-4 content-start">
        <section className={card}>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <h3 className="font-black uppercase tracking-wide mr-auto">{latest ? `${latest.kind_label}` : "No runs yet"}</h3>
            {latest && <StatusPill status={latest.status} />}
            {latest && <span className="text-xs font-mono">{latest.started_at.replace("T", " ")}</span>}
            {running && <button className={btn} disabled={!token} onClick={() => stop(running.id)}>Stop</button>}
          </div>
          {latest?.status === "done" && latest.url_hash && (
            <div className="bg-emerald-50 border-2 border-emerald-500 p-3 mb-3 text-xs text-emerald-950 flex flex-wrap items-center justify-between gap-2">
              <div>
                <span className="font-black uppercase tracking-wider block text-[10px] text-emerald-800">
                  {latest.kind === "custom_commission" ? "Commissioned Story Published" : "Article Published"}
                </span>
                <span className="font-bold">{latest.headline || "Story ready"}</span>
              </div>
              <a
                href={latest.url_hash}
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1.5 bg-emerald-700 text-white font-bold uppercase text-[10px] tracking-wider hover:bg-emerald-800"
              >
                Read Story →
              </a>
            </div>
          )}
          <pre ref={logRef} className="bg-[#07080a] text-[#d8d8d8] text-[11px] leading-snug p-3 h-[420px] overflow-auto whitespace-pre-wrap break-words font-mono">{log || "Logs appear here when a run starts."}</pre>
        </section>
        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">Run history</h3>
          <div className="grid gap-1 text-xs">
            {jobs.map(j => (
              <button key={j.id} className="flex flex-wrap gap-2 items-center text-left hover:bg-[var(--paper)] py-1"
                onClick={() => call<{ log: string }>(`/jobs/${j.id}/log?lines=300`).then(r => setLog(r.log))}>
                <StatusPill status={j.status} /><span className="font-mono">{j.id}</span><span>{j.kind_label}</span>
                {j.resume && <span className="text-[var(--gray-500)]">resume {j.resume}</span>}
                {j.exit_code !== undefined && j.exit_code !== null && <span className="text-[var(--gray-500)]">exit {j.exit_code}</span>}
              </button>
            ))}
            {!jobs.length && <span className="text-[var(--gray-500)]">None yet.</span>}
          </div>
        </section>
        {message && <p className="text-sm font-bold">{message}</p>}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- tables ----
function Tables({ token }: { token: string }) {
  const [tables, setTables] = useState<{ name: string; kind: string; rows: number | null }[]>([]);
  const [table, setTable] = useState(() => { try { return localStorage.getItem("mu_debug_table") || "league_state"; } catch { return "league_state"; } });
  const [data, setData] = useState<TableRows | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sort, setSort] = useState<{ col: string; dir: "asc" | "desc" } | null>(null);
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<{ row: number; col: string; value: string } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { call<typeof tables>("/tables").then(setTables).catch(e => setError(String(e.message))); }, []);

  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (sort) { params.set("sort", sort.col); params.set("dir", sort.dir); }
  if (q) params.set("q", q);
  const activeFilters = Object.fromEntries(Object.entries(filters).filter(([, v]) => v.trim() !== ""));
  if (Object.keys(activeFilters).length) params.set("filters", JSON.stringify(activeFilters));
  const query = params.toString();

  const load = useCallback(() => {
    setLoading(true);
    call<TableRows>(`/tables/${encodeURIComponent(table)}?${query}`)
      .then(d => { setData(d); setError(""); })
      .catch(e => setError(String(e.message)))
      .finally(() => setLoading(false));
  }, [table, query]);

  useEffect(() => {
    const t = window.setTimeout(load, 250);           // debounce typing in the search and filter boxes
    return () => window.clearTimeout(t);
  }, [load]);

  const pick = (name: string) => {
    setTable(name); setPage(1); setSort(null); setQ(""); setFilters({}); setEditing(null); setData(null);
    try { localStorage.setItem("mu_debug_table", name); } catch { /* ignore */ }
  };

  const editable = (c: Column) => !!data && data.kind === "table" && data.pk.length > 0 && !c.generated && !c.pk && !!token;

  const save = async () => {
    if (!data || !editing) return;
    const row = data.rows[editing.row];
    const key = Object.fromEntries(data.pk.map(k => [k, row[k]]));
    const value = editing.value === "" ? null : editing.value;
    try {
      const r = await call<{ row: Record<string, unknown> }>(`/tables/${encodeURIComponent(data.table)}`,
        { method: "PATCH", body: JSON.stringify({ key, changes: { [editing.col]: value } }) }, token);
      setData({ ...data, rows: data.rows.map((x, i) => (i === editing.row ? r.row : x)) });
      setEditing(null); setError("");
    } catch (e) { setError(`Not saved: ${(e as Error).message}`); }
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-3 items-end">
        <label className="min-w-[220px]"><span className={label}>Table</span>
          <select className={input} value={table} onChange={e => pick(e.target.value)}>
            {tables.map(t => <option key={t.name} value={t.name}>{t.name}{t.kind === "view" ? " (view)" : ""}{t.rows !== null ? ` · ~${t.rows.toLocaleString()}` : ""}</option>)}
          </select>
        </label>
        <label className="flex-1 min-w-[180px]"><span className={label}>Search all columns</span>
          <input className={input} value={q} onChange={e => { setQ(e.target.value); setPage(1); }} placeholder="text…" />
        </label>
        <label><span className={label}>Rows</span>
          <select className={input} value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}>
            {[25, 50, 100, 200].map(n => <option key={n}>{n}</option>)}
          </select>
        </label>
        <div className="flex items-center gap-2">
          <button className={btn} disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</button>
          <span className="text-xs font-mono whitespace-nowrap">{page} / {pages}{data ? ` · ${data.total.toLocaleString()} rows` : ""}</span>
          <button className={btn} disabled={page >= pages} onClick={() => setPage(page + 1)}>›</button>
        </div>
      </div>
      <p className="text-[11px] text-[var(--gray-500)]">
        Click a header to sort. Filter boxes: text = contains; <code>=5</code>, <code>!=5</code>, <code>&gt;=300</code>, <code>&lt;10</code> compare; <code>null</code> / <code>!null</code>.
        {data && data.kind === "table" && data.pk.length > 0 ? (token ? " Tap a cell to edit (Enter saves, Esc cancels, empty = NULL)." : " Enter the debug token to edit cells.") : data ? " Views and tables without a primary key are read-only." : ""}
      </p>
      {error && <p className="text-sm text-[var(--red)] font-bold">{error}</p>}
      <div className={`overflow-auto max-h-[70vh] bg-white border-t-4 border-[var(--ink)] ${loading ? "opacity-60" : ""}`}>
        {data && (
          <table className="text-xs border-collapse min-w-full">
            <thead className="sticky top-0 bg-[var(--gray-100)] z-10">
              <tr>
                {data.columns.map(c => (
                  <th key={c.column} className="text-left px-2 py-1 font-bold whitespace-nowrap cursor-pointer select-none border-b border-[var(--gray-300)]"
                    title={`${c.type}${c.pk ? " · primary key" : ""}${c.generated ? " · generated" : ""}`}
                    onClick={() => { setSort(sort?.col === c.column ? { col: c.column, dir: sort.dir === "asc" ? "desc" : "asc" } : { col: c.column, dir: "asc" }); setPage(1); }}>
                    {c.column}{c.pk ? " 🔑" : ""}{sort?.col === c.column ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr>
              <tr>
                {data.columns.map(c => (
                  <th key={c.column} className="px-1 py-1 border-b border-[var(--gray-300)]">
                    <input className="w-full min-w-[64px] border border-[var(--gray-300)] px-1 py-0.5 text-[11px] font-normal bg-white"
                      value={filters[c.column] || ""} placeholder="filter"
                      onChange={e => { setFilters({ ...filters, [c.column]: e.target.value }); setPage(1); }} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i} className="odd:bg-[var(--paper)]">
                  {data.columns.map(c => {
                    const isEditing = editing?.row === i && editing.col === c.column;
                    const v = row[c.column];
                    return (
                      <td key={c.column} className={`px-2 py-1 align-top border-b border-[var(--gray-100)] ${editable(c) ? "cursor-text hover:bg-amber-50" : ""}`}
                        onClick={() => { if (editable(c) && !isEditing) setEditing({ row: i, col: c.column, value: show(v) }); }}>
                        {isEditing ? (
                          <input autoFocus className="border border-[var(--red)] px-1 py-0.5 text-xs w-full min-w-[120px]"
                            value={editing.value} onChange={e => setEditing({ ...editing, value: e.target.value })}
                            onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(null); }}
                            onBlur={() => setEditing(null)} />
                        ) : v === null || v === undefined ? (
                          <span className="text-[var(--gray-300)]">∅</span>
                        ) : (
                          <span className="block max-w-[360px] truncate" title={show(v)}>{show(v)}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------- endpoints ----
function Endpoints() {
  const [eps, setEps] = useState<{ path: string; methods: string[]; doc: string; path_params: string[]; query_params: string[] }[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { call<typeof eps>("/endpoints").then(setEps).catch(() => setEps([])); }, []);
  const shown = eps.filter(e => !q || e.path.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="grid gap-3">
      <input className={`${input} max-w-sm`} placeholder="Filter paths…" value={q} onChange={e => setQ(e.target.value)} />
      <div className="bg-white border-t-4 border-[var(--ink)]">
        {shown.map(e => (
          <div key={e.path + e.methods.join()} className="flex flex-wrap gap-x-3 gap-y-1 items-baseline px-3 py-2 border-b border-[var(--gray-100)] text-sm">
            {e.methods.map(m => <span key={m} className={`text-[10px] font-bold px-1.5 py-0.5 ${m === "GET" ? "bg-emerald-600" : m === "DELETE" ? "bg-[var(--red)]" : "bg-[var(--ink)]"} text-white`}>{m}</span>)}
            {e.methods.includes("GET") && !e.path_params.length
              ? <a className="font-mono underline" href={`${API_BASE}${e.path}`} target="_blank" rel="noreferrer">{e.path}</a>
              : <span className="font-mono">{e.path}</span>}
            {e.query_params.length > 0 && <span className="text-xs text-[var(--gray-500)]">?{e.query_params.join("&")}</span>}
            {e.doc && <span className="w-full text-xs text-[var(--gray-500)]">{e.doc}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- docs ----
function inline(text: string): ReactNode[] {
  // `code`, **bold**, *italic*; links show their text
  return text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) return <code key={i} className="font-mono text-[0.85em] bg-[var(--gray-100)] px-1">{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 3) return <b key={i}>{part.slice(2, -2)}</b>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <i key={i}>{part.slice(1, -1)}</i>;
    return part;
  });
}

function DocMarkdown({ content }: { content: string }) {
  const lines = content.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  const sizes = ["text-3xl", "text-2xl", "text-xl", "text-base", "text-sm", "text-sm"];
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(<pre key={out.length} className="bg-[#07080a] text-[#d8d8d8] text-[11px] leading-snug p-3 overflow-x-auto my-3 font-mono">{code.join("\n")}</pre>);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      out.push(<div key={out.length} className={`${sizes[h[1].length - 1]} font-black uppercase tracking-wide mt-6 mb-2 ${h[1].length <= 2 ? "border-b-2 border-[var(--ink)] pb-1" : ""}`}>{inline(h[2])}</div>);
      i++; continue;
    }
    if (line.trim().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, "|"));
        if (!cells.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      out.push(
        <div key={out.length} className="overflow-x-auto my-3">
          <table className="text-xs border-collapse">
            <tbody>{rows.map((r, ri) => <tr key={ri} className={ri === 0 ? "bg-[var(--gray-100)] font-bold" : "border-t border-[var(--gray-100)]"}>{r.map((c, ci) => <td key={ci} className="px-2 py-1 align-top">{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>);
      continue;
    }
    const li = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
    if (li) {
      out.push(<div key={out.length} className="text-sm leading-relaxed flex gap-2" style={{ paddingLeft: `${li[1].length * 8}px` }}><span className="text-[var(--gray-500)]">{/\d/.test(li[2]) ? li[2] : "•"}</span><span>{inline(li[3])}</span></div>);
      i++; continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^(#|```|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    out.push(<p key={out.length} className="text-sm leading-relaxed my-2">{inline(para.join(" "))}</p>);
  }
  return <>{out}</>;
}

const DOCS: { key: string; title: string }[] = [
  { key: "spec", title: "Project spec" },
  { key: "migration", title: "Pipeline migration" },
  { key: "frontend", title: "Frontend spec" },
];

function Docs() {
  const [doc, setDoc] = useState("spec");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    setText("");
    call<{ markdown: string }>(`/docs/${doc}`).then(r => { setText(r.markdown); setError(""); }).catch(e => setError(String(e.message)));
  }, [doc]);
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-2">
        {DOCS.map(d => <button key={d.key} className={`${btn} ${doc === d.key ? "!bg-[var(--ink)] !text-white" : ""}`} onClick={() => setDoc(d.key)}>{d.title}</button>)}
      </div>
      {error && <p className="text-sm text-[var(--red)]">{error}</p>}
      <article className="bg-white border-t-4 border-[var(--ink)] p-4 md:p-8 overflow-x-auto debug-doc">
        {text ? <DocMarkdown content={text} /> : !error && <p className="text-sm text-[var(--gray-500)]">Loading…</p>}
      </article>
    </div>
  );
}

// ---------------------------------------------------------------- page ----
const TABS = ["Control", "Tables", "Endpoints", "Docs"] as const;

export default function DebugPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>(() => {
    try { return (localStorage.getItem("mu_debug_tab") as (typeof TABS)[number]) || "Control"; } catch { return "Control"; }
  });
  const [token, setToken] = useState(readToken);
  const [data, setData] = useState<DebugState | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    call<DebugState>("/state").then(d => { setData(d); setError(""); }).catch(e => setError(String(e.message || e)));
  }, []);
  useEffect(() => { reload(); const t = window.setInterval(reload, 10000); return () => window.clearInterval(t); }, [reload]);

  const choose = (t: (typeof TABS)[number]) => { setTab(t); try { localStorage.setItem("mu_debug_tab", t); } catch { /* ignore */ } };
  const saveToken = (v: string) => { setToken(v); try { localStorage.setItem(TOKEN_KEY, v); } catch { /* ignore */ } };

  return (
    <main className="max-w-[1400px] mx-auto px-4 py-6 md:py-10">
      <div className="flex flex-wrap items-end gap-4 border-b-2 border-[var(--ink)] pb-4 mb-4">
        <div className="mr-auto">
          <p className="text-[10px] font-bold uppercase tracking-[.2em] text-[var(--red)]">Debug</p>
          <h1 className="text-3xl md:text-4xl font-black uppercase leading-none">League control</h1>
          {data && (
            <p className="text-xs font-mono mt-2">
              {data.database} · season {data.state.current_season} · current week {data.state.current_week}
              {data.running_job ? ` · running: ${data.running_job.kind}` : ""}
            </p>
          )}
          {error && <p className="text-xs text-[var(--red)] mt-2">API not reachable ({error}). Is it running on {API_BASE}?</p>}
        </div>
        <label className="w-full sm:w-64"><span className={label}>Debug token {token ? "✓" : "(needed for edits and runs)"}</span>
          <input className={input} type="password" value={token} onChange={e => saveToken(e.target.value)} placeholder="DEBUG_TOKEN from .env" />
        </label>
      </div>
      <nav className="flex gap-1 mb-4 overflow-x-auto">
        {TABS.map(t => (
          <button key={t} onClick={() => choose(t)}
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider border-b-4 ${tab === t ? "border-[var(--red)]" : "border-transparent text-[var(--gray-500)]"}`}>{t}</button>
        ))}
      </nav>
      {tab === "Control" && <Control token={token} data={data} reload={reload} />}
      {tab === "Tables" && <Tables token={token} />}
      {tab === "Endpoints" && <Endpoints />}
      {tab === "Docs" && <Docs />}
    </main>
  );
}
