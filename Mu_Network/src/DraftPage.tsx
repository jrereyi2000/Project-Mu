import React, { useCallback, useEffect, useState } from "react";
import { API_BASE } from "./api";
import ConsensusBigBoard from "./components/draft/ConsensusBigBoard";
import CollegeAwardsView from "./components/draft/CollegeAwardsView";
import TeamNeedsMatrix from "./components/draft/TeamNeedsMatrix";
import MockDraftView from "./components/draft/MockDraftView";
import DraftWarRoom from "./components/draft/DraftWarRoom";
import ProspectModal from "./components/draft/ProspectModal";
import "./components/draft/draft.css";

type DraftTab = "board" | "honors" | "needs" | "mock" | "war-room";
const DRAFT_TABS: DraftTab[] = ["board", "honors", "needs", "mock", "war-room"];

// Deep links: #draft?tab=needs&team=CAR opens a tab (and, on Team Needs, one franchise).
function readDraftHash(): { tab: DraftTab; team: string | null } {
  const query = window.location.hash.split("?")[1] || "";
  const params = new URLSearchParams(query);
  const tab = params.get("tab") as DraftTab | null;
  return {
    tab: tab && DRAFT_TABS.includes(tab) ? tab : "board",
    team: params.get("team")?.toUpperCase() || null,
  };
}

interface DraftStateResponse {
  season: number;
  week: number;
  draft_season: number;
  installed: boolean;
  current_step: number | null;
  step_label?: string | null;
  phase?: string | null;
  phase_label?: string | null;
  stored_steps: Array<{
    step: number;
    season: number;
    week?: number | null;
    stage?: number | null;
    phase: string;
    phase_label: string;
    label: string;
  }>;
  total_prospects: number;
  war_room_locked: boolean;
  picks_recorded: number;
  recent_headlines: Array<{
    prospect_key: string;
    full_name: string;
    position: string;
    college: string;
    rank: number;
    grade?: number | null;
    kind: string;
    headline: string;
    blurb?: string | null;
    step: number;
  }>;
}

export const DraftPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<DraftTab>(() => readDraftHash().tab);
  const [teamFocus, setTeamFocus] = useState<string | null>(() => readDraftHash().team);
  const [state, setState] = useState<DraftStateResponse | null>(null);
  const [selectedStep, setSelectedStep] = useState<number | null>(null);
  const [selectedProspectKey, setSelectedProspectKey] = useState<string | null>(null);

  const loadState = useCallback(() => {
    fetch(`${API_BASE}/api/draft/state`)
      .then((r) => r.json())
      .then((data: DraftStateResponse) => {
        setState(data);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadState();
  }, [loadState]);

  useEffect(() => {
    const onHash = () => {
      const { tab, team } = readDraftHash();
      setActiveTab(tab);
      setTeamFocus(team);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const draftSeason = state?.draft_season || 2037;

  return (
    <div className="draft-central-shell">
      {/* Broadcast Masthead */}
      <header className="draft-masthead">
        <div className="draft-masthead-top">
          <div className="draft-brand-group">
            <div className="draft-badge-shield">DC</div>
            <div className="draft-title-block">
              <h1>
                Gridiron Draft Central
                <span className="draft-class-pill">{draftSeason} DRAFT CLASS</span>
              </h1>
              <div className="draft-subtitle">
                <span>
                  {state?.phase_label || "College Scouting & Evaluation"} ·{" "}
                  <strong style={{ color: "#e2e8f0" }}>
                    {state?.step_label || `Season ${state?.season || 2036}`}
                  </strong>
                </span>
                <span>·</span>
                <span>{state?.total_prospects || 388} Scouted Prospects</span>
                {state?.war_room_locked && (
                  <>
                    <span>·</span>
                    <span style={{ color: "#fbbf24", fontWeight: 700 }}>
                      War Room Active ({state.picks_recorded}/32 R1 Picks)
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="draft-nav-actions">
            <a href="#debug" className="draft-back-btn">
              ← Return to Debug
            </a>
            <a href="#" className="draft-back-btn">
              Gridiron Home
            </a>
          </div>
        </div>

        {/* Live College Scouting Headline Ticker */}
        {state?.recent_headlines && state.recent_headlines.length > 0 && (
          <div className="draft-ticker-bar">
            <div className="draft-ticker-inner">
              <span className="draft-ticker-label">Scouting Wire</span>
              {state.recent_headlines.map((h, idx) => (
                <button
                  key={`${h.prospect_key}-${idx}`}
                  className="draft-ticker-item"
                  onClick={() => setSelectedProspectKey(h.prospect_key)}
                  title={h.blurb || h.headline}
                >
                  <span className="draft-ticker-rank">#{h.rank}</span>
                  <strong>{h.headline}</strong>
                  <span style={{ color: "#64748b", fontSize: "11px" }}>({h.position} · {h.college})</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Primary Tab Bar */}
        <nav className="draft-tabs-bar" aria-label="Draft Central sections">
          <button
            className={`draft-tab-btn ${activeTab === "board" ? "active" : ""}`}
            onClick={() => setActiveTab("board")}
          >
            📊 Consensus Big Board
          </button>
          <button
            className={`draft-tab-btn ${activeTab === "honors" ? "active" : ""}`}
            onClick={() => setActiveTab("honors")}
          >
            🏆 Honors, Heisman &amp; Movers
          </button>
          <button
            className={`draft-tab-btn ${activeTab === "needs" ? "active" : ""}`}
            onClick={() => setActiveTab("needs")}
          >
            📋 Team Needs &amp; Capital
          </button>
          <button
            className={`draft-tab-btn ${activeTab === "mock" ? "active" : ""}`}
            onClick={() => setActiveTab("mock")}
          >
            🔮 Media Mock Draft
          </button>
          <button
            className={`draft-tab-btn ${activeTab === "war-room" ? "active" : ""}`}
            onClick={() => setActiveTab("war-room")}
          >
            <span className="draft-tab-live-dot" />
            Live Draft War Room
            {state?.war_room_locked ? ` (${state.picks_recorded}/32)` : ""}
          </button>
        </nav>
      </header>

      {/* Active Tab Content */}
      <main className="draft-main-container">
        {activeTab === "board" && (
          <ConsensusBigBoard
            draftSeason={state?.draft_season}
            selectedStep={selectedStep}
            storedSteps={(state?.stored_steps || []).map((s) => ({ step: s.step, label: s.label }))}
            onSelectStep={setSelectedStep}
            onSelectProspect={setSelectedProspectKey}
          />
        )}

        {activeTab === "honors" && (
          <CollegeAwardsView
            draftSeason={state?.draft_season}
            selectedStep={selectedStep}
            onSelectProspect={setSelectedProspectKey}
          />
        )}

        {activeTab === "needs" && <TeamNeedsMatrix team={teamFocus} onClearTeam={() => setTeamFocus(null)} />}

        {activeTab === "mock" && <MockDraftView onSelectProspect={setSelectedProspectKey} />}

        {activeTab === "war-room" && (
          <DraftWarRoom
            onSelectProspect={setSelectedProspectKey}
            onStateChange={loadState}
          />
        )}
      </main>

      {/* Global Prospect Dossier Modal */}
      {selectedProspectKey && (
        <ProspectModal
          prospectKey={selectedProspectKey}
          step={selectedStep}
          onClose={() => setSelectedProspectKey(null)}
        />
      )}
    </div>
  );
};

export default DraftPage;
