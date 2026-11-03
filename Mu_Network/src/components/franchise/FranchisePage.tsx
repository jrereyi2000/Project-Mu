// #franchise: the Franchise Command Center (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2). A header with the team
// (GET /api/franchise/{abbr}/overview: record, user team or not), a team picker, and a tab bar for the eight modules
// in V2 order. Each module is standalone (`{ teamAbbr }`) and fetches its own data.
//
// Routes: `#franchise` (Game Center), `#franchise-<module>` (e.g. `#franchise-depth-chart`), each optionally with
// `?team=ABBR`. The team: `?team=` when given, else the last team picked here (localStorage), else CAR.
import { useEffect, type ComponentType } from "react";
import "../team/frontOffice.css";
import { teamLogo } from "../team/format";
import { TEAM_ABBRS } from "../../types/franchiseMarket";
import type { FranchiseOverview } from "../../types/franchiseGame";
import GameCenterModule from "./GameCenterModule";
import DepthChartModule from "./DepthChartModule";
import RosterCapModule, { useFranchiseData } from "./RosterCapModule";
import FreeAgencyModule from "./FreeAgencyModule";
import ContractRoomModule from "./ContractRoomModule";
import TradeMachineModule from "./TradeMachineModule";
import DraftWarRoomModule from "./DraftWarRoomModule";
import TrainingRoomModule from "./TrainingRoomModule";

type ModuleDef = { slug: string; label: string; Component: ComponentType<{ teamAbbr: string }> };

const MODULES: ModuleDef[] = [
  { slug: "game-center", label: "Game Center", Component: GameCenterModule },
  { slug: "depth-chart", label: "Depth Chart", Component: DepthChartModule },
  { slug: "roster-cap", label: "Roster & Cap", Component: RosterCapModule },
  { slug: "free-agency", label: "Free Agency", Component: FreeAgencyModule },
  { slug: "contract-room", label: "Contract Room", Component: ContractRoomModule },
  { slug: "trade-machine", label: "Trade Machine", Component: TradeMachineModule },
  { slug: "draft-war-room", label: "Draft War Room", Component: DraftWarRoomModule },
  { slug: "training-room", label: "Training Room", Component: TrainingRoomModule },
];

const DEFAULT_TEAM = "CAR";
const TEAM_KEY = "mu_franchise_team";
const ALIASES: Record<string, string> = { ARI: "AZ", WSH: "WAS" };

function storedTeam(): string | null {
  try { return localStorage.getItem(TEAM_KEY); } catch { return null; }
}

function storeTeam(abbr: string) {
  try { localStorage.setItem(TEAM_KEY, abbr); } catch { /* storage blocked */ }
}

function cleanTeam(raw: string | null | undefined): string | null {
  const t = (raw || "").toUpperCase().trim();
  const abbr = ALIASES[t] ?? t;
  return (TEAM_ABBRS as readonly string[]).includes(abbr) ? abbr : null;
}

/** "#franchise-depth-chart?team=IND" -> {slug: "depth-chart", team: "IND"}. Unknown modules fall back to Game Center. */
function parseFranchiseRoute(route: string): { slug: string; team: string | null } {
  const [path, query = ""] = route.replace(/^#/, "").split("?");
  const slug = path.startsWith("franchise-") ? path.slice("franchise-".length) : "game-center";
  return {
    slug: MODULES.some((m) => m.slug === slug) ? slug : "game-center",
    team: cleanTeam(new URLSearchParams(query).get("team")),
  };
}

const href = (slug: string, team: string | null) => `#franchise-${slug}${team ? `?team=${team}` : ""}`;

const PAGE_CSS = `
.fr-page { background: var(--paper, #f4f3f0); min-height: 70vh; }
.fr-head { max-width: 1240px; margin: 0 auto; padding: 30px 42px 0; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; }
.fr-head-team { display: flex; align-items: center; gap: 14px; }
.fr-head-team img { width: 54px; height: 54px; object-fit: contain; }
.fr-head-team span { color: var(--team-color, var(--red)); font: 700 8px var(--font-mono); letter-spacing: .12em; text-transform: uppercase; }
.fr-head-team h1 { margin: 4px 0 0; font: 700 28px var(--font-sans); letter-spacing: -.04em; text-transform: uppercase; }
.fr-head-team small { display: block; margin-top: 4px; color: #55575d; font: 600 10px var(--font-mono); }
.fr-picker { display: flex; align-items: center; gap: 8px; color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-picker select { height: 32px; padding: 0 10px; border: 1px solid var(--gray-100); border-radius: 16px; background: var(--white); color: var(--ink); font: 700 10px var(--font-mono); }
.fr-badge { padding: 3px 8px; border-radius: 3px; font: 700 8px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.fr-badge.yes { color: var(--ink); background: #e6efe4; }
.fr-badge.no { color: var(--white); background: var(--red); }
.fr-tabs { max-width: 1240px; margin: 18px auto 0; padding: 0 42px; display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid var(--gray-100); }
.fr-tabs a { flex: none; padding: 12px 14px; color: var(--gray-500); font: 700 9px var(--font-mono); letter-spacing: .06em; text-transform: uppercase; text-decoration: none; border-bottom: 3px solid transparent; white-space: nowrap; }
.fr-tabs a:hover { color: var(--ink); }
.fr-tabs a.active { color: var(--ink); border-bottom-color: var(--team-color, var(--red)); }
.fr-page .team-data-view.fo-view { max-width: 1240px; }
@media (max-width: 800px) { .fr-head { padding: 22px 16px 0; } .fr-tabs { padding: 0 16px; } }
`;

export default function FranchisePage({ route }: { route: string }) {
  const parsed = parseFranchiseRoute(route);
  const team = parsed.team ?? cleanTeam(storedTeam()) ?? DEFAULT_TEAM;
  const [overview] = useFranchiseData<FranchiseOverview>(team, "/overview");
  const o = overview.status === "ready" ? overview.data : null;
  const active = MODULES.find((m) => m.slug === parsed.slug) ?? MODULES[0];
  const Module = active.Component;

  useEffect(() => { if (parsed.team) storeTeam(parsed.team); }, [parsed.team]);
  useEffect(() => { document.title = `${team} ${active.label} · Franchise`; }, [team, active.label]);

  const pickTeam = (abbr: string) => {
    storeTeam(abbr);
    window.location.hash = href(active.slug, abbr);
  };

  return (
    <main className="fr-page">
      <style href="fr-page" precedence="default">{PAGE_CSS}</style>
      <header className="fr-head">
        <div className="fr-head-team">
          <img src={teamLogo(team)} alt="" />
          <div>
            <span>Franchise Command Center</span>
            <h1>{o ? `${o.team.abbr} ${o.team.name}` : team}</h1>
            <small>
              {o ? `${o.record.wins}-${o.record.losses}${o.record.ties ? `-${o.record.ties}` : ""} · Season ${o.season} · Week ${o.week}` : overview.status === "loading" ? "Loading…" : "Overview unavailable"}
              {o && o.pending_moves.length > 0 ? ` · ${o.pending_moves.length} pending move${o.pending_moves.length === 1 ? "" : "s"}` : ""}
            </small>
          </div>
        </div>
        <div className="fr-picker">
          {o && <span className={`fr-badge ${o.user_controlled ? "yes" : "no"}`}>{o.user_controlled ? "Your team" : "Not a user team"}</span>}
          <label htmlFor="fr-team">Team</label>
          <select id="fr-team" value={team} onChange={(e) => pickTeam(e.target.value)}>
            {TEAM_ABBRS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
      </header>
      <nav className="fr-tabs" aria-label="Franchise modules">
        {MODULES.map((m) => (
          <a key={m.slug} href={href(m.slug, parsed.team)} className={m.slug === active.slug ? "active" : ""} aria-current={m.slug === active.slug ? "page" : undefined}>
            {m.label}
          </a>
        ))}
      </nav>
      <Module key={`${active.slug}-${team}`} teamAbbr={team} />
    </main>
  );
}
