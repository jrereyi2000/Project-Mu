// The Record Book (#records, PROJECT_SPEC §5.1/§5.2; docs/RECORD_BOOK_AND_AWARDS_PLAN.md phase 1): every category
// card from GET /api/records (server/history_api.py), filtered by season type, scope, subject and era, or one
// franchise's records with ?team=ABBR. The filters live in the hash (#records?type=post&scope=GAME&era=mu&team=car)
// so a view can be linked. The regular-season view opens with Record Watch while a season is being played.
import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../api";
import FranchiseRecords from "./FranchiseRecords";
import RecordCard, { type RecordCategory } from "./RecordCard";
import RecordWatch from "./RecordWatch";
import { GROUP_LABELS, SUBJECT_LABELS } from "./format";
import "./history.css";

type RecordsData = {
  as_of: { season: number | null; week: number | null; in_progress: boolean };
  mu_marks: number;
  categories: RecordCategory[];
};

type Params = { type: "reg" | "post"; scope: string; subject: string; era: "all" | "mu"; team: string };

const GROUPS = { reg: ["SEASON", "GAME", "ROOKIE"], post: ["GAME", "SEASON", "SUPER_BOWL", "CAREER"] };
const POST_GROUP_LABELS: Record<string, string> = { SEASON: "Single postseason" };

function parseQuery(query: string): Params {
  const q = new URLSearchParams(query.replace(/^\?/, ""));
  return {
    type: q.get("type") === "post" ? "post" : "reg",
    scope: (q.get("scope") || "").toUpperCase(),
    subject: (q.get("subject") || "").toUpperCase(),
    era: q.get("era") === "mu" ? "mu" : "all",
    team: (q.get("team") || "").toLowerCase(),
  };
}

function toHash(p: Params): string {
  const q = new URLSearchParams();
  if (p.type === "post") q.set("type", "post");
  if (p.scope) q.set("scope", p.scope);
  if (p.subject) q.set("subject", p.subject);
  if (p.era === "mu") q.set("era", "mu");
  if (p.team) q.set("team", p.team);
  const s = q.toString();
  return s ? `#records?${s}` : "#records";
}

export default function RecordBookPage({ query, teams }: { query: string; teams: { name: string; abbr: string }[] }) {
  const params = useMemo(() => parseQuery(query), [query]);
  const [data, setData] = useState<RecordsData | null>(null);
  const [error, setError] = useState(false);
  const set = (patch: Partial<Params>) => { window.location.hash = toHash({ ...params, ...patch }); };

  useEffect(() => {
    if (params.team) return;
    setData(null);
    setError(false);
    const type = params.type === "post" ? "POSTSEASON" : "REGULAR";
    fetch(`${API_BASE}/api/records?season_type=${type}&era=${params.era}`)
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(setData)
      .catch(() => setError(true));
  }, [params.type, params.era, params.team]);

  const cards = (data?.categories || []).filter(c =>
    (!params.scope || c.group === params.scope) && (!params.subject || c.subject === params.subject));
  const subjects = Array.from(new Set((data?.categories || []).map(c => c.subject)));
  const week = data?.as_of.in_progress ? data.as_of.week : null;
  const groupLabel = (g: string) => (params.type === "post" && POST_GROUP_LABELS[g]) || GROUP_LABELS[g] || g;

  return (
    <main className="rb-page">
      <section className="rb-masthead">
        <div>
          <span>League history</span>
          <h1>Record Book</h1>
          <p>
            {data ? `${data.mu_marks} NFL record${data.mu_marks === 1 ? "" : "s"} set or tied in this league since 2026` : "NFL history and the marks set in this league"}
            {data?.as_of.in_progress && data.as_of.season ? ` · ${data.as_of.season} marks in progress through Week ${data.as_of.week}` : ""}
          </p>
        </div>
      </section>

      <section className="rb-controls">
        <div className="rb-segment" role="tablist">
          <button className={!params.team && params.type === "reg" ? "active" : ""} onClick={() => set({ type: "reg", scope: "", team: "" })}>Regular season</button>
          <button className={!params.team && params.type === "post" ? "active" : ""} onClick={() => set({ type: "post", scope: "", team: "" })}>Postseason</button>
        </div>
        <div className="rb-filters">
          {!params.team && (
            <>
              <div className="rb-chips">
                <button className={!params.scope ? "active" : ""} onClick={() => set({ scope: "" })}>All</button>
                {GROUPS[params.type].map(g => (
                  <button key={g} className={params.scope === g ? "active" : ""} onClick={() => set({ scope: g })}>{groupLabel(g)}</button>
                ))}
              </div>
              {subjects.length > 1 && (
                <select value={params.subject} onChange={e => set({ subject: e.target.value })} aria-label="Subject">
                  <option value="">Players, teams &amp; coaches</option>
                  {subjects.map(s => <option key={s} value={s}>{SUBJECT_LABELS[s] || s}</option>)}
                </select>
              )}
              <div className="rb-era">
                <button className={params.era === "all" ? "active" : ""} onClick={() => set({ era: "all" })}>All-time</button>
                <button className={params.era === "mu" ? "active" : ""} onClick={() => set({ era: "mu" })}>Mu Era</button>
              </div>
            </>
          )}
          <select value={params.team} onChange={e => set({ team: e.target.value, type: "reg", scope: "" })} aria-label="Franchise">
            <option value="">All franchises</option>
            {[...teams].sort((a, b) => a.name.localeCompare(b.name)).map(t => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
          </select>
        </div>
      </section>

      <section className="rb-body">
        {!params.team && params.type === "reg" && <RecordWatch />}
        {params.team ? (
          <FranchiseRecords abbr={params.team} />
        ) : error ? (
          <p className="rb-state">Couldn't load the record book.</p>
        ) : !data ? (
          <p className="rb-state">Loading the record book…</p>
        ) : cards.length === 0 ? (
          <p className="rb-state">{params.era === "mu" ? "No marks set in this league for this filter yet." : "No categories for this filter."}</p>
        ) : (
          <div className="rb-grid">
            {cards.map(c => <RecordCard key={c.category} cat={c} week={week} />)}
          </div>
        )}
      </section>
    </main>
  );
}
