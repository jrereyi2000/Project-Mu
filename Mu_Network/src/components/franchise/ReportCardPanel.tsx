// #franchise NFLPA Report Card module (NFLPA_REPORT_CARD_PLAN §2.5, U3; PROJECT_SPEC §5.2): the eight category
// grades, the overall score/grade and league rank, CapEx renovation buttons (each tier's cost) and the annual OpEx
// policy toggles. It reads GET /api/team/{abbr}/report-card and posts to /facilities/upgrade and /facilities/policy
// (the team endpoints, not the franchise router), so it has its own tiny fetch helper. Shapes: types/reportCard.ts.
import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { money } from "../team/format";
import "../team/frontOffice.css";
import { SharedStyles } from "./RosterCapModule";
import type {
  CapExTier, FacilityUpgrade, PolicyKey, PolicyResponse, ReportCard, ReportCardResponse, UpgradeResponse,
} from "../../types/reportCard";

const CATEGORY_LABEL: Record<string, string> = {
  weight_room: "Weight Room",
  training_room: "Training Room",
  locker_room: "Locker Room",
  food_nutrition: "Food & Nutrition",
  team_travel: "Team Travel",
  family_treatment: "Family Treatment",
  head_coach: "Head Coach",
  ownership: "Ownership",
};

// The four physical (brick-and-mortar) categories an owner renovates with CapEx (plan §2.3a). The culture and
// OpEx categories are changed by policy/hires, not construction.
const CAPEX_CATEGORIES = ["weight_room", "training_room", "locker_room", "food_nutrition"];

const TIERS: { key: CapExTier; label: string; cost: number }[] = [
  { key: "MINOR", label: "Minor", cost: 5_000_000 },
  { key: "STANDARD", label: "Standard", cost: 12_000_000 },
  { key: "OVERHAUL", label: "Overhaul", cost: 25_000_000 },
];

const POLICIES: { key: PolicyKey; label: string; cost: number }[] = [
  { key: "luxury_travel", label: "Luxury travel", cost: 3_500_000 },
  { key: "family_daycare", label: "Family daycare", cost: 1_000_000 },
  { key: "private_chef", label: "Private chef", cost: 2_000_000 },
];

/** fetch against /api/team/{abbr}{path} (the report-card endpoints live on the team router, not the franchise one). */
async function teamFetch<T>(abbr: string, path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/team/${abbr}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") detail = j.detail;
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: ReportCardResponse };

const RC_CSS = `
.rc-overall { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; margin-bottom: 18px; padding: 18px 20px; background: var(--white); border-top: 3px solid var(--ink); }
.rc-overall .rc-badge { min-width: 64px; padding: 10px 14px; text-align: center; color: var(--white); background: var(--ink); }
.rc-overall .rc-badge strong { display: block; font: 700 30px/1 var(--font-sans); letter-spacing: -.04em; }
.rc-overall .rc-badge span { font: 700 8px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.rc-overall dl { margin: 0; display: flex; flex-wrap: wrap; gap: 22px; }
.rc-overall dt { color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.rc-overall dd { margin: 3px 0 0; font: 700 16px var(--font-mono); letter-spacing: -.03em; }
.rc-overall dd small { margin-left: 5px; color: var(--gray-500); font: 600 9px var(--font-mono); }
.rc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.rc-tile { padding: 12px 14px; background: var(--white); border-top: 3px solid var(--gray-100); display: flex; flex-direction: column; gap: 4px; }
.rc-tile span { color: var(--gray-500); font: 700 7px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.rc-tile strong { font: 700 26px var(--font-mono); letter-spacing: -.05em; }
.rc-tile.good { border-top-color: #2e7d32; }
.rc-tile.mid { border-top-color: var(--gray-100); }
.rc-tile.bad { border-top-color: var(--red); }
.rc-tile.good strong { color: #2e7d32; }
.rc-tile.bad strong { color: var(--red); }
.rc-upgrade { margin-bottom: 10px; padding: 14px 16px; background: var(--white); border-left: 3px solid var(--gray-100); }
.rc-upgrade header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.rc-upgrade header strong { font: 700 13px var(--font-sans); }
.rc-upgrade header .rc-grade { font: 700 15px var(--font-mono); }
.rc-upgrade header small { color: var(--gray-500); font: 600 9px var(--font-mono); text-transform: uppercase; }
.rc-policy { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 12px 14px; background: var(--white); border-top: 1px solid var(--gray-100); }
.rc-policy strong { min-width: 130px; font: 700 12px var(--font-sans); }
.rc-policy small { color: var(--gray-500); font: 600 9px var(--font-mono); }
.rc-policy .fr-btn { margin-left: auto; }
.rc-summary { margin: 0 0 14px; color: #55575d; font: 500 10px/1.6 var(--font-mono); }
.rc-sub { margin: 14px 0 8px; color: var(--gray-500); font: 700 9px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
`;

function gradeTone(grade: string): "good" | "mid" | "bad" {
  const g = grade[0];
  if (g === "A" || g === "B") return "good";
  if (g === "C") return "mid";
  return "bad";
}

function GradeGrid({ card }: { card: ReportCard }) {
  return (
    <div className="rc-grid" aria-label="Category grades">
      {Object.keys(CATEGORY_LABEL).map((cat) => (
        <article key={cat} className={`rc-tile ${gradeTone(card[cat as keyof ReportCard] as string)}`}>
          <span>{CATEGORY_LABEL[cat]}</span>
          <strong>{card[cat as keyof ReportCard] as string}</strong>
        </article>
      ))}
    </div>
  );
}

function UpgradeList({ card, busy, onOrder }: {
  card: ReportCard;
  busy: string | null;
  onOrder: (category: string, tier: CapExTier, label: string) => void;
}) {
  return (
    <>
      {CAPEX_CATEGORIES.map((cat) => {
        const grade = card[cat as keyof ReportCard] as string;
        return (
          <article className="rc-upgrade" key={cat}>
            <header>
              <strong>{CATEGORY_LABEL[cat]}</strong>
              <span className={`rc-grade ${gradeTone(grade)}`}>{grade}</span>
              <small>CapEx renovation · completes next season</small>
            </header>
            <div className="fr-actions">
              {TIERS.map((t) => (
                <button
                  key={t.key}
                  className="fr-btn"
                  disabled={busy !== null}
                  onClick={() => onOrder(cat, t.key, t.label)}
                >
                  {t.label} · {money(t.cost)}
                </button>
              ))}
            </div>
          </article>
        );
      })}
    </>
  );
}

function Upgrades({ upgrades }: { upgrades: FacilityUpgrade[] }) {
  return (
    <ul className="fr-reasons">
      {upgrades.map((u) => (
        <li key={u.id}>
          {CATEGORY_LABEL[u.category] ?? u.category}: {money(u.cost_dollars)} → {u.target_grade}, completes season {u.completion_season}.
        </li>
      ))}
    </ul>
  );
}

export default function ReportCardPanel({ teamAbbr }: { teamAbbr: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setState({ status: "loading" });
    teamFetch<ReportCardResponse>(teamAbbr, "/report-card")
      .then((data) => setState({ status: "ready", data }))
      .catch((e) => setState({ status: "error", message: e instanceof Error ? e.message : String(e) }));
  }, [teamAbbr]);

  useEffect(() => { load(); }, [load]);

  const data = state.status === "ready" ? state.data : null;
  const card = data?.current ?? null;

  const togglePolicy = async (policy: PolicyKey, enabled: boolean) => {
    setBusy(`policy:${policy}`);
    setError(null);
    setNotice(null);
    try {
      const res = await teamFetch<PolicyResponse>(teamAbbr, "/facilities/policy", "POST", { policy, enabled });
      setNotice(`${POLICIES.find((p) => p.key === policy)?.label ?? policy} ${enabled ? "on" : "off"} · ${money(res.annual_opex_cost)}/yr OpEx.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const orderUpgrade = async (category: string, tier: CapExTier, label: string) => {
    setBusy(`upgrade:${category}:${tier}`);
    setError(null);
    setNotice(null);
    try {
      const res = await teamFetch<UpgradeResponse>(teamAbbr, "/facilities/upgrade", "POST", { category, tier });
      setNotice(`Ordered a ${label.toLowerCase()} ${CATEGORY_LABEL[category].toLowerCase()} renovation — target grade ${res.order.target_grade}, completing season ${res.order.completion_season}.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <style href="fr-report-card" precedence="default">{RC_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title"><span>{teamAbbr} · NFLPA survey</span><h2>Report Card</h2></div>
        {state.status === "loading" && <p className="fo-state">Loading the report card…</p>}
        {state.status === "error" && <p className="fo-state">The report card couldn't load ({state.message}). Try again in a moment.</p>}
        {data && !data.cards && <p className="fo-state">No NFLPA report card is on file for this franchise yet.</p>}
        {error && <p className="fr-warn" role="alert">{error}</p>}
        {notice && !error && <p className="fr-ok" role="status">{notice}</p>}
        {data && card && (
          <>
            <article className="rc-overall">
              <div className="rc-badge"><strong>{card.overall_grade}</strong><span>Overall</span></div>
              <dl>
                <div>
                  <dt>Facility score</dt>
                  <dd>{card.overall_score.toFixed(3)}</dd>
                </div>
                <div>
                  <dt>League rank</dt>
                  <dd>
                    {data.rank ? `${data.rank.rank} of ${data.rank.teams}` : "—"}
                    {data.rank && <small>{data.rank.percentile.toFixed(1)}th percentile</small>}
                  </dd>
                </div>
                <div>
                  <dt>Season</dt>
                  <dd>{card.season}</dd>
                </div>
              </dl>
            </article>
            {card.summary_text && <p className="rc-summary">{card.summary_text}</p>}
            <GradeGrid card={card} />
          </>
        )}
      </section>

      {data && card && (
        <section className="fo-block">
          <div className="team-section-title"><span>Owner cash pool</span><h2>Facility Investments</h2></div>
          <p className="fo-basis">CapEx renovations draw from the owner cash pool and complete next season; OpEx policy toggles take effect immediately.</p>
          <UpgradeList card={card} busy={busy} onOrder={(category, tier, label) => void orderUpgrade(category, tier, label)} />
          {data.upgrades && data.upgrades.length > 0 && (
            <>
              <h3 className="rc-sub">In progress</h3>
              <Upgrades upgrades={data.upgrades} />
            </>
          )}
        </section>
      )}

      {data && (
        <section className="fo-block">
          <div className="team-section-title"><span>Annual operating policies</span><h2>OpEx Toggles</h2></div>
          {data.policies ? (
            <>
              {POLICIES.map((p) => {
                const on = data.policies![p.key];
                return (
                  <div className="rc-policy" key={p.key}>
                    <strong>{p.label}</strong>
                    <small>{money(p.cost)}/yr</small>
                    <button
                      className={`fr-btn${on ? " primary" : ""}`}
                      disabled={busy !== null}
                      onClick={() => void togglePolicy(p.key, !on)}
                    >
                      {on ? "On" : "Off"}
                    </button>
                  </div>
                );
              })}
              <p className="fo-basis">Total OpEx: {money(data.policies.annual_opex_cost)}/yr.</p>
            </>
          ) : (
            <p className="fo-state">No operating policies are recorded yet.</p>
          )}
        </section>
      )}
    </section>
  );
}
