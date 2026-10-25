import React, { useCallback, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { teamLogo } from "../team/format";
import { gradeTierClass } from "./ConsensusBigBoard";

const TOKEN_KEY = "mu_debug_token";

interface WarRoomProspect {
  prospect_key: string;
  prospect_id?: number;
  full_name: string;
  position: string;
  college: string;
  age?: number;
  consensus_rank: number;
  grade: number;
  round_label: string;
  team_grade?: number | null;
  team_rank?: number | null;
  stat_summary?: string;
  choice_source?: string;
  madden_match?: string | null;
  rationale?: string | null;
  pick_grade?: {
    letter: string;
    verdict: string;
    tone: string;
  };
}

interface WarRoomSlot {
  pick: number;
  team: string;
  original_team: string;
  user_controlled?: boolean;
  selection?: WarRoomProspect | null;
}

interface WarRoomOnClock {
  pick: number;
  owner_team_id: number;
  team: string;
  team_name: string;
  gm?: string | null;
  original_team: string;
  user_controlled?: boolean;
  recommendation?: WarRoomProspect | null;
  alternatives: WarRoomProspect[];
  rationale?: string | null;
  steps: string[];
}

interface WarRoomState {
  installed: boolean;
  draft_season: number;
  step?: number;
  locked?: boolean;
  error?: string;
  before?: string;
  after?: string;
  slots?: WarRoomSlot[];
  on_clock?: WarRoomOnClock | null;
  projected?: Array<{ pick: number; team: string; prospect_key: string; full_name: string; position: string; college: string; consensus_rank: number; grade: number }>;
  consensus?: WarRoomProspect[];
  team_board?: { team: string; rows: WarRoomProspect[] };
  trades?: Array<{ id: number; status: string; headline: string; pick?: number | null }>;
  all_teams?: Array<{ abbr: string; name: string }>;
}

function playDraftChime() {
  try {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const notes = [523.25, 659.25, 783.99, 1046.5]; // C5 - E5 - G5 - C6 NFL-style chime
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(freq, ctx.currentTime + i * 0.11);
      gain.gain.setValueAtTime(0.14, ctx.currentTime + i * 0.11);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.11 + 0.35);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.11);
      osc.stop(ctx.currentTime + i * 0.11 + 0.36);
    });
  } catch {
    // Audio disabled or blocked
  }
}

export const DraftWarRoom: React.FC<{
  onSelectProspect: (prospectKey: string) => void;
  onStateChange?: () => void;
}> = ({ onSelectProspect, onStateChange }) => {
  const [token, setToken] = useState<string>(() => localStorage.getItem(TOKEN_KEY) || "");
  const [wr, setWr] = useState<WarRoomState | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [busy, setBusy] = useState<boolean>(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [boardView, setBoardView] = useState<string>(""); // "" = consensus, else team abbr
  const [fixes, setFixes] = useState<string>("");
  const [offScriptSearch, setOffScriptSearch] = useState<string>("");
  const [soundOn, setSoundOn] = useState<boolean>(true);

  const saveToken = (val: string) => {
    setToken(val);
    localStorage.setItem(TOKEN_KEY, val);
  };

  const loadWarRoom = useCallback(() => {
    const q = boardView ? `?team=${encodeURIComponent(boardView)}` : "";
    fetch(`${API_BASE}/api/draft/war-room${q}`)
      .then((r) => r.json())
      .then((data) => {
        setWr(data);
        setLoading(false);
      })
      .catch((e) => {
        setMessage({ text: String(e.message || e), ok: false });
        setLoading(false);
      });
  }, [boardView]);

  useEffect(() => {
    loadWarRoom();
  }, [loadWarRoom]);

  const executeAction = async (path: string, body?: unknown, confirmMsg?: string, chime = false) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(`${API_BASE}/api/draft/war-room${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { "X-Debug-Token": token } : {}),
        },
        body: JSON.stringify(body || {}),
      });
      if (!res.ok) {
        const errText = await res.text();
        let detail = errText;
        try {
          detail = JSON.parse(errText).detail || errText;
        } catch {
          // keep raw text
        }
        throw new Error(detail);
      }
      const nextState = await res.json();
      setWr(nextState);
      if (chime && soundOn) playDraftChime();
      setMessage({ text: "Pick & War Room state updated.", ok: true });
      setOffScriptSearch("");
      loadWarRoom();
      onStateChange?.();
    } catch (e) {
      setMessage({ text: String((e as Error).message || e), ok: false });
    } finally {
      setBusy(false);
    }
  };

  const handleLockOrder = () => {
    const parsed: Record<string, string> = {};
    fixes
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean)
      .forEach((x) => {
        const [p, t] = x.split("=");
        if (p && t) parsed[p.trim()] = t.trim().toUpperCase();
      });
    executeAction(
      "/lock-order",
      { fixes: Object.keys(parsed).length ? parsed : undefined },
      "Lock the 224-pick draft order for all 7 rounds? Verify Round 1 owners against Madden's draft order screen first."
    );
  };

  if (loading && !wr) {
    return (
      <div className="draft-section-card" style={{ textAlign: "center", padding: "40px", color: "#64748b" }}>
        Initializing Broadcast Draft War Room…
      </div>
    );
  }

  const oc = wr?.on_clock;
  const recordedSlots = (wr?.slots || []).filter((s) => s.selection);
  const lastRecorded = recordedSlots.length ? recordedSlots[recordedSlots.length - 1] : null;
  const activePool = boardView && wr?.team_board ? wr.team_board.rows : wr?.consensus || [];
  const offScriptHits = offScriptSearch.trim()
    ? (wr?.consensus || [])
        .filter((p) => `${p.full_name} ${p.position} ${p.college}`.toLowerCase().includes(offScriptSearch.trim().toLowerCase()))
        .slice(0, 8)
    : [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {/* Top Operator Authorization & Controls Strip */}
      <div className="draft-filter-card" style={{ padding: "12px 18px" }}>
        <div className="draft-filter-row">
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "12px", fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.05em", color: "#0f172a" }}>
              🔴 Broadcast War Room Control
            </span>
            <span style={{ fontSize: "12px", color: "#64748b" }}>
              {recordedSlots.length} of 32 Round 1 Picks Recorded
            </span>
            {message && (
              <span
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  padding: "3px 10px",
                  borderRadius: "6px",
                  background: message.ok ? "#dcfce7" : "#fee2e2",
                  color: message.ok ? "#166534" : "#991b1b",
                }}
              >
                {message.text}
              </span>
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <button
              onClick={() => setSoundOn(!soundOn)}
              className="draft-filter-pill"
              title="Toggle NFL Draft chime audio on pick selection"
            >
              {soundOn ? "🔔 Chime On" : "🔕 Muted"}
            </button>
            <input
              type="password"
              className="draft-search-input"
              style={{ minWidth: "180px", padding: "6px 12px", fontSize: "12px" }}
              placeholder="Operator Debug Token…"
              value={token}
              onChange={(e) => saveToken(e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* Pre-Lock Order Setup Banner */}
      {wr?.installed && !wr.locked && (
        <div className="war-room-clock-banner">
          <div style={{ fontSize: "11px", fontWeight: 900, color: "#fbbf24", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: "6px" }}>
            Pre-Draft Stage · Order Not Yet Locked
          </div>
          <h3 style={{ margin: "0 0 8px", fontSize: "20px", fontWeight: 900 }}>
            Lock {wr.draft_season} Draft Order to Begin Live Round 1 War Room
          </h3>
          <p style={{ margin: "0 0 14px", fontSize: "13px", color: "#cbd5e1", lineHeight: 1.5 }}>
            {wr.before}
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <input
              type="text"
              value={fixes}
              onChange={(e) => setFixes(e.target.value)}
              placeholder="Optional R1 owner fixes (e.g. 5=DEN, 18=GB)"
              style={{
                padding: "9px 14px",
                borderRadius: "8px",
                border: "1px solid rgba(255,255,255,0.2)",
                background: "rgba(255,255,255,0.08)",
                color: "#ffffff",
                fontSize: "13px",
                minWidth: "280px",
              }}
            />
            <button
              disabled={!token || busy}
              onClick={handleLockOrder}
              style={{
                background: "#f59e0b",
                color: "#090e1a",
                border: "none",
                borderRadius: "8px",
                padding: "10px 18px",
                fontWeight: 900,
                fontSize: "13px",
                cursor: token && !busy ? "pointer" : "not-allowed",
                opacity: token && !busy ? 1 : 0.5,
              }}
            >
              🔒 Lock 7-Round Draft Order
            </button>
          </div>
        </div>
      )}

      {/* ON THE CLOCK BROADCAST CONSOLE */}
      {oc && (
        <div className="war-room-clock-banner">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px", marginBottom: "18px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
              <img
                src={teamLogo(oc.team)}
                alt={oc.team}
                style={{ width: "56px", height: "56px", objectFit: "contain", background: "rgba(255,255,255,0.08)", padding: "6px", borderRadius: "12px" }}
              />
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                  <span style={{ background: "#ef4444", color: "#ffffff", fontWeight: 900, fontSize: "11px", padding: "2px 8px", borderRadius: "4px", letterSpacing: "0.06em" }}>
                    ON THE CLOCK · PICK #{oc.pick}
                  </span>
                  {oc.original_team !== oc.team && (
                    <span style={{ background: "rgba(245, 158, 11, 0.2)", color: "#fbbf24", fontWeight: 800, fontSize: "11px", padding: "2px 8px", borderRadius: "4px" }}>
                      Acquired via {oc.original_team}
                    </span>
                  )}
                  {oc.user_controlled && (
                    <span style={{ background: "rgba(59, 130, 246, 0.25)", color: "#93c5fd", fontWeight: 800, fontSize: "11px", padding: "2px 8px", borderRadius: "4px" }}>
                      JR USER TEAM (ADVICE ONLY)
                    </span>
                  )}
                </div>
                <h2 style={{ margin: 0, fontSize: "24px", fontWeight: 900 }}>
                  {oc.team_name}
                </h2>
                <div style={{ fontSize: "12.5px", color: "#94a3b8", marginTop: "2px" }}>
                  General Manager: <strong style={{ color: "#e2e8f0" }}>{oc.gm || "Front Office"}</strong>
                </div>
              </div>
            </div>

            {lastRecorded && (
              <button
                disabled={!token || busy}
                onClick={() => executeAction(`/picks/${lastRecorded.pick}/undo`, {}, `Undo Pick #${lastRecorded.pick} (${lastRecorded.selection?.full_name})?`)}
                style={{
                  background: "rgba(255,255,255,0.1)",
                  color: "#e2e8f0",
                  border: "1px solid rgba(255,255,255,0.2)",
                  borderRadius: "8px",
                  padding: "8px 14px",
                  fontSize: "12px",
                  fontWeight: 700,
                  cursor: token && !busy ? "pointer" : "not-allowed",
                }}
              >
                ↩ Undo Pick #{lastRecorded.pick}
              </button>
            )}
          </div>

          {/* GM Recommendation Card */}
          {oc.recommendation && (
            <div
              style={{
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(245, 158, 11, 0.45)",
                borderRadius: "12px",
                padding: "16px 20px",
                marginBottom: "16px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "14px" }}>
                <div>
                  <div style={{ fontSize: "11px", fontWeight: 900, color: "#fbbf24", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "4px" }}>
                    GM War Room Primary Target
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                    <span
                      onClick={() => onSelectProspect(oc.recommendation!.prospect_key)}
                      style={{ fontSize: "20px", fontWeight: 900, color: "#ffffff", cursor: "pointer", textDecoration: "underline" }}
                    >
                      {oc.recommendation.full_name}
                    </span>
                    <span style={{ background: "#f59e0b", color: "#090e1a", fontWeight: 900, fontSize: "12px", padding: "2px 8px", borderRadius: "4px" }}>
                      {oc.recommendation.position}
                    </span>
                    <span style={{ fontSize: "14px", fontWeight: 700, color: "#cbd5e1" }}>
                      {oc.recommendation.college}
                    </span>
                    <span style={{ fontSize: "12px", color: "#94a3b8" }}>
                      Consensus #{oc.recommendation.consensus_rank} ({oc.recommendation.grade?.toFixed(1)})
                      {oc.recommendation.team_rank ? ` · ${oc.team} Board #${oc.recommendation.team_rank}` : ""}
                    </span>
                  </div>
                  {oc.recommendation.stat_summary && (
                    <div style={{ fontFamily: "monospace", fontSize: "12px", color: "#cbd5e1", marginTop: "6px" }}>
                      College Season: {oc.recommendation.stat_summary}
                    </div>
                  )}
                  {oc.rationale && (
                    <div style={{ fontSize: "13px", color: "#e2e8f0", fontStyle: "italic", marginTop: "8px" }}>
                      “{oc.rationale}”
                    </div>
                  )}
                </div>

                <button
                  disabled={!token || busy}
                  onClick={() =>
                    executeAction(
                      `/picks/${oc.pick}`,
                      { prospect_key: oc.recommendation!.prospect_key, source: "GM" },
                      undefined,
                      true
                    )
                  }
                  style={{
                    background: "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)",
                    color: "#090e1a",
                    border: "none",
                    borderRadius: "10px",
                    padding: "12px 22px",
                    fontSize: "14px",
                    fontWeight: 900,
                    cursor: token && !busy ? "pointer" : "not-allowed",
                    opacity: token && !busy ? 1 : 0.5,
                    boxShadow: "0 4px 14px rgba(245, 158, 11, 0.35)",
                  }}
                >
                  ✓ Make Selection: {oc.recommendation.full_name}
                </button>
              </div>
            </div>
          )}

          {/* Alternatives & Off-Script Search + Madden Steps */}
          <div className="draft-grid-2">
            <div>
              {oc.alternatives.length > 0 && (
                <div style={{ marginBottom: "12px" }}>
                  <div style={{ fontSize: "11px", fontWeight: 800, color: "#94a3b8", textTransform: "uppercase", marginBottom: "6px" }}>
                    Next Highest on {oc.team} Board:
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                    {oc.alternatives.map((alt) => (
                      <button
                        key={alt.prospect_key}
                        disabled={!token || busy}
                        onClick={() =>
                          executeAction(
                            `/picks/${oc.pick}`,
                            { prospect_key: alt.prospect_key, source: "OFF_SCRIPT" },
                            `Draft ${alt.full_name} (${alt.position}, ${alt.college}) at Pick #${oc.pick}?`,
                            true
                          )
                        }
                        style={{
                          background: "rgba(255,255,255,0.08)",
                          border: "1px solid rgba(255,255,255,0.18)",
                          color: "#f8fafc",
                          borderRadius: "6px",
                          padding: "6px 10px",
                          fontSize: "12px",
                          fontWeight: 700,
                          cursor: token && !busy ? "pointer" : "not-allowed",
                        }}
                      >
                        {alt.full_name} ({alt.position} · #{alt.consensus_rank})
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <div style={{ fontSize: "11px", fontWeight: 800, color: "#94a3b8", textTransform: "uppercase", marginBottom: "6px" }}>
                  Draft Different Player (Off-Script / User Selection):
                </div>
                <input
                  type="search"
                  value={offScriptSearch}
                  onChange={(e) => setOffScriptSearch(e.target.value)}
                  placeholder="Type player name, position, or school…"
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    borderRadius: "6px",
                    border: "1px solid rgba(255,255,255,0.2)",
                    background: "rgba(255,255,255,0.08)",
                    color: "#ffffff",
                    fontSize: "12.5px",
                  }}
                />
                {offScriptHits.length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px" }}>
                    {offScriptHits.map((p) => (
                      <button
                        key={p.prospect_key}
                        disabled={!token || busy}
                        onClick={() =>
                          executeAction(
                            `/picks/${oc.pick}`,
                            { prospect_key: p.prospect_key, source: "OFF_SCRIPT" },
                            `Record ${p.full_name} (${p.position}) at Pick #${oc.pick}?`,
                            true
                          )
                        }
                        style={{
                          background: "#3b82f6",
                          color: "#ffffff",
                          border: "none",
                          borderRadius: "6px",
                          padding: "5px 10px",
                          fontSize: "11.5px",
                          fontWeight: 700,
                          cursor: "pointer",
                        }}
                      >
                        Draft {p.full_name} · {p.position} · {p.college} (#{p.consensus_rank})
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Madden Execution Checklist */}
            <div style={{ background: "rgba(0,0,0,0.28)", borderRadius: "10px", padding: "12px 16px" }}>
              <div style={{ fontSize: "11px", fontWeight: 800, color: "#fbbf24", textTransform: "uppercase", marginBottom: "6px" }}>
                🎮 Madden Console Steps
              </div>
              <ol style={{ margin: 0, paddingLeft: "18px", fontSize: "12.5px", color: "#e2e8f0", lineHeight: 1.6 }}>
                {oc.steps.map((s, idx) => (
                  <li key={idx}>{s}</li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      )}

      {/* Post-Pick 32 Completion Banner */}
      {wr?.locked && !oc && (
        <div className="war-room-clock-banner" style={{ borderColor: "#10b981" }}>
          <div style={{ fontSize: "11px", fontWeight: 900, color: "#6ee7b7", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Round 1 Complete · All 32 Selections Recorded
          </div>
          <h3 style={{ margin: "4px 0 8px", fontSize: "20px", fontWeight: 900 }}>
            Ready to Sim Rounds 2–7 in Madden
          </h3>
          <p style={{ margin: 0, fontSize: "13px", color: "#cbd5e1" }}>{wr.after}</p>
        </div>
      )}

      {/* Draft-Day Trade Proposals Alert */}
      {(wr?.trades || []).length > 0 && (
        <div className="draft-section-card" style={{ borderLeft: "4px solid #f59e0b", background: "#fffbeb" }}>
          <div style={{ fontSize: "12px", fontWeight: 900, textTransform: "uppercase", color: "#92400e", marginBottom: "6px" }}>
            📞 Draft-Day Trade Desk Offers (Approve &amp; Confirm on Debug Front Office Card)
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {wr!.trades!.map((t) => (
              <div key={t.id} style={{ fontSize: "13px", color: "#0f172a" }}>
                <strong>Proposal #{t.id}</strong> (Pick #{t.pick ?? "?"}) ·{" "}
                <span style={{ fontWeight: 800, color: "#b45309" }}>{t.status}</span> — {t.headline}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Main 2-Column Split: Round 1 Selection Ledger & Switchable Team War Room Boards */}
      <div className="draft-grid-2">
        {/* Left: Round 1 Pick-by-Pick Board with Instant Draft Grades */}
        <div className="draft-section-card">
          <h3 style={{ margin: "0 0 14px", fontSize: "16px", fontWeight: 900, color: "#0f172a" }}>
            📋 Round 1 Selection Tracker &amp; Instant Draft Grades
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {(wr?.slots || []).map((s) => {
              const isClock = oc?.pick === s.pick;
              const sel = s.selection;
              return (
                <div
                  key={s.pick}
                  style={{
                    padding: "10px 12px",
                    borderRadius: "8px",
                    border: isClock ? "2px solid #f59e0b" : "1px solid #e2e8f0",
                    background: isClock ? "#fffbeb" : sel ? "#ffffff" : "#f8fafc",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <span style={{ fontFamily: "monospace", fontWeight: 900, fontSize: "14px", color: "#0f172a", minWidth: "26px" }}>
                        #{s.pick}
                      </span>
                      <img src={teamLogo(s.team)} alt={s.team} style={{ width: "24px", height: "24px", objectFit: "contain" }} />
                      <strong style={{ fontSize: "13.5px", color: "#0f172a" }}>{s.team}</strong>
                      {s.original_team !== s.team && (
                        <span style={{ fontSize: "11px", color: "#64748b" }}>(via {s.original_team})</span>
                      )}
                      {sel && (
                        <span
                          onClick={() => onSelectProspect(sel.prospect_key)}
                          style={{ fontWeight: 800, fontSize: "13.5px", color: "#1e40af", cursor: "pointer", textDecoration: "underline" }}
                        >
                          {sel.full_name} ({sel.position} · {sel.college})
                        </span>
                      )}
                    </div>

                    {sel?.pick_grade ? (
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <span className={`war-room-grade-badge ${sel.pick_grade.tone}`}>
                          {sel.pick_grade.letter} · {sel.pick_grade.verdict}
                        </span>
                        {sel.madden_match && (
                          <span
                            style={{
                              fontSize: "10px",
                              fontWeight: 800,
                              padding: "2px 6px",
                              borderRadius: "4px",
                              background: sel.madden_match === "MATCH" ? "#dcfce7" : "#fee2e2",
                              color: sel.madden_match === "MATCH" ? "#166534" : "#991b1b",
                            }}
                          >
                            {sel.madden_match}
                          </span>
                        )}
                      </div>
                    ) : isClock ? (
                      <span style={{ fontSize: "11px", fontWeight: 900, color: "#d97706" }}>ON THE CLOCK</span>
                    ) : (
                      <span style={{ fontSize: "11px", color: "#94a3b8" }}>Upcoming</span>
                    )}
                  </div>

                  {sel?.rationale && (
                    <div style={{ fontSize: "12px", color: "#475569", marginTop: "6px", paddingLeft: "36px" }}>
                      {sel.rationale}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Switchable Team GM Boards & Projected Rest of Round 1 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          <div className="draft-section-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", flexWrap: "wrap", gap: "10px" }}>
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 900, color: "#0f172a" }}>
                🎯 Best Available Board ({boardView ? `${boardView} Private GM Board` : "Public Consensus"})
              </h3>
              <select
                className="draft-select"
                value={boardView}
                onChange={(e) => setBoardView(e.target.value)}
                aria-label="Select team board"
              >
                <option value="">Public Consensus Board</option>
                {(wr?.all_teams || []).map((t) => (
                  <option key={t.abbr} value={t.abbr}>
                    {t.abbr} — {t.name} Board
                  </option>
                ))}
              </select>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "540px", overflowY: "auto" }}>
              {activePool.slice(0, 35).map((p) => (
                <div
                  key={p.prospect_key}
                  onClick={() => onSelectProspect(p.prospect_key)}
                  style={{
                    padding: "8px 12px",
                    borderRadius: "8px",
                    border: "1px solid #f1f5f9",
                    background: "#f8fafc",
                    cursor: "pointer",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: "10px",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ fontFamily: "monospace", fontWeight: 900, fontSize: "13px", color: "#0f172a" }}>
                        #{boardView && p.team_rank ? p.team_rank : p.consensus_rank}
                      </span>
                      <strong style={{ fontSize: "13.5px", color: "#0f172a" }}>{p.full_name}</strong>
                      <span className="draft-pos-badge">{p.position}</span>
                      <span style={{ fontSize: "12px", color: "#475569" }}>{p.college}</span>
                    </div>
                    {p.stat_summary && (
                      <div style={{ fontFamily: "monospace", fontSize: "11px", color: "#64748b", marginTop: "3px" }}>
                        {p.stat_summary}
                      </div>
                    )}
                  </div>

                  <div style={{ textAlign: "right", flexShrink: 0 }}>
                    <span className={`draft-grade-pill ${gradeTierClass(boardView && p.team_grade ? p.team_grade : p.grade)}`} style={{ fontSize: "11.5px", padding: "2px 8px" }}>
                      {(boardView && p.team_grade != null ? p.team_grade : p.grade)?.toFixed(1)}
                    </span>
                    {boardView && (
                      <div style={{ fontSize: "10px", color: "#64748b", marginTop: "2px" }}>
                        Cons #{p.consensus_rank}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Projected Rest of Round 1 */}
          {(wr?.projected || []).length > 0 && (
            <div className="draft-section-card">
              <h3 style={{ margin: "0 0 10px", fontSize: "15px", fontWeight: 900, color: "#0f172a" }}>
                🔮 Live Simulation: Projected Rest of Round 1
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "340px", overflowY: "auto" }}>
                {wr!.projected!.map((proj) => (
                  <div
                    key={proj.pick}
                    onClick={() => onSelectProspect(proj.prospect_key)}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "6px 10px",
                      borderBottom: "1px solid #f1f5f9",
                      fontSize: "12.5px",
                      cursor: "pointer",
                    }}
                  >
                    <div>
                      <span style={{ fontFamily: "monospace", fontWeight: 800, marginRight: "8px" }}>#{proj.pick}</span>
                      <strong>{proj.team}</strong>: {proj.full_name} ({proj.position} · {proj.college})
                    </div>
                    <span style={{ fontSize: "11px", color: "#64748b" }}>Cons #{proj.consensus_rank}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default DraftWarRoom;
