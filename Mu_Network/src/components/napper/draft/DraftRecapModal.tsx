import React, { useMemo } from "react";
import type { NapperDraftState, NapperDraftPick } from "../../../types/napper";
import { getPlayerHeadshotUrl, handleImageError } from "../napperUtils";

interface DraftRecapModalProps {
  draftState: NapperDraftState;
  onClose: () => void;
  onRestartMock: () => void;
  onSaveAsLeague?: (leagueName: string) => void;
  isSaving?: boolean;
}

export const DraftRecapModal: React.FC<DraftRecapModalProps> = ({
  draftState,
  onClose,
  onRestartMock,
  onSaveAsLeague,
  isSaving = false,
}) => {

  const { teams, picks, user_slot } = draftState;

  // Grade calculation and analysis
  const analysis = useMemo(() => {
    // 1. Calculate projected total points per team
    const teamScores = teams.map((team) => {
      const totalPts = team.roster.reduce((sum, p) => sum + (p.proj_pts || 0), 0);
      return {
        team,
        totalPts,
      };
    });

    // Sort descending by points to assign grades
    teamScores.sort((a, b) => b.totalPts - a.totalPts);

    const grades = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D"];
    const gradedTeams = teamScores.map((item, idx) => {
      const grade = grades[Math.min(idx, grades.length - 1)];
      return {
        ...item,
        grade,
        rank: idx + 1,
      };
    });

    // 2. Find Best Value Pick (drafted latest relative to ADP)
    let bestValuePick: { pick: NapperDraftPick; valueDiff: number } | null = null;
    let biggestReachPick: { pick: NapperDraftPick; reachDiff: number } | null = null;

    for (const p of picks) {
      if (!p.player) continue;
      const diff = p.pick_overall - p.player.adp_rank; // Positive = drafted later than ADP = Value
      if (!bestValuePick || diff > bestValuePick.valueDiff) {
        bestValuePick = { pick: p, valueDiff: diff };
      }
      if (!biggestReachPick || diff < biggestReachPick.reachDiff) {
        biggestReachPick = { pick: p, reachDiff: diff };
      }
    }

    return {
      gradedTeams,
      bestValuePick,
      biggestReachPick,
    };
  }, [teams, picks]);

  const getGradeClass = (grade: string) => {
    if (grade.startsWith("A")) return "grade-a";
    if (grade.startsWith("B")) return "grade-b";
    if (grade.startsWith("C")) return "grade-c";
    return "grade-d";
  };

  const userTeamAnalysis = analysis.gradedTeams.find((t) => t.team.slot === user_slot);

  return (
    <div className="draft-modal-overlay">
      <div className="draft-recap-card">
        {/* Header */}
        <div className="draft-recap-header">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xl">🏆</span>
              <h2 className="text-xl font-black text-white tracking-wide">
                DRAFT SUMMARY & REPORT CARD
              </h2>
            </div>
            <p className="text-xs text-[#8a99ad] mt-1">
              All 15 rounds complete • Simulated with Sleeper standard scoring & consensus ADP
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-full bg-[#1c2940] hover:bg-[#283854] text-[#94a3b8] hover:text-white flex items-center justify-center transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* User Team Highlight Banner */}
          {userTeamAnalysis && (
            <div className="bg-gradient-to-r from-[#14263f] via-[#10304a] to-[#12283e] border border-[#00ceb8]/50 rounded-2xl p-5 flex items-center justify-between shadow-[0_0_24px_rgba(0,206,184,0.15)]">
              <div className="flex items-center gap-4">
                <div
                  className={`draft-grade-badge ${getGradeClass(userTeamAnalysis.grade)}`}
                >
                  {userTeamAnalysis.grade}
                </div>
                <div>
                  <div className="text-xs font-black text-[#00ceb8] tracking-wider uppercase">
                    Your Mock Draft Grade
                  </div>
                  <div className="text-lg font-black text-white">
                    {userTeamAnalysis.team.name}
                  </div>
                  <div className="text-xs text-[#8a99ad]">
                    Rank #{userTeamAnalysis.rank} of {teams.length} • Projected {userTeamAnalysis.totalPts.toFixed(1)} Total FPTS
                  </div>
                </div>
              </div>

              <div className="hidden sm:flex items-center gap-2">
                {userTeamAnalysis.team.roster.slice(0, 3).map((p) => (
                  <div key={p.player_id} className="flex flex-col items-center">
                    <img
                      src={getPlayerHeadshotUrl(p.player_id, p.headshot_url)}
                      alt={p.name}
                      className="w-10 h-10 rounded-full border border-[#233758] object-cover bg-[#090e17]"
                      onError={handleImageError}
                    />
                    <span className="text-[10px] font-bold text-white mt-1">
                      {p.name.split(" ").pop()}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Value Highlights: Best Steal & Biggest Reach */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Best Value */}
            {analysis.bestValuePick && analysis.bestValuePick.pick.player && (
              <div className="bg-[#121c2d] border border-[#10b981]/40 rounded-xl p-4 flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-[#10b981]/20 text-[#10b981] flex items-center justify-center font-black text-sm shrink-0">
                  💎
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[10px] font-black uppercase text-[#10b981] tracking-wider">
                    Best Value Pick (+{Math.max(0, analysis.bestValuePick.valueDiff)} slots)
                  </div>
                  <div className="text-xs font-extrabold text-white truncate">
                    {analysis.bestValuePick.pick.player.name} ({analysis.bestValuePick.pick.player.position})
                  </div>
                  <div className="text-[10px] text-[#8a99ad]">
                    Drafted at #{analysis.bestValuePick.pick.pick_overall} by {analysis.bestValuePick.pick.team_name} (ADP #{analysis.bestValuePick.pick.player.adp_rank})
                  </div>
                </div>
              </div>
            )}

            {/* Biggest Reach */}
            {analysis.biggestReachPick && analysis.biggestReachPick.pick.player && (
              <div className="bg-[#121c2d] border border-[#f43f5e]/40 rounded-xl p-4 flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-[#f43f5e]/20 text-[#f43f5e] flex items-center justify-center font-black text-sm shrink-0">
                  🎯
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[10px] font-black uppercase text-[#f43f5e] tracking-wider">
                    Biggest Reach ({analysis.biggestReachPick.reachDiff} slots)
                  </div>
                  <div className="text-xs font-extrabold text-white truncate">
                    {analysis.biggestReachPick.pick.player.name} ({analysis.biggestReachPick.pick.player.position})
                  </div>
                  <div className="text-[10px] text-[#8a99ad]">
                    Drafted at #{analysis.biggestReachPick.pick.pick_overall} by {analysis.biggestReachPick.pick.team_name} (ADP #{analysis.biggestReachPick.pick.player.adp_rank})
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* All Teams League Report Card Table */}
          <div className="bg-[#111928] border border-[#1f2d45] rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-[#1f2d45] flex items-center justify-between bg-[#0e1624]">
              <span className="text-xs font-black uppercase text-white tracking-wider">
                Full League Standings & Grades
              </span>
              <span className="text-[10px] text-[#64748b]">
                Ranked by Projected Fantasy Output
              </span>
            </div>

            <div className="divide-y divide-[#172338]">
              {analysis.gradedTeams.map((gt) => {
                const isUser = gt.team.slot === user_slot;

                return (
                  <div
                    key={gt.team.id}
                    className={`px-4 py-3 flex items-center justify-between transition-colors ${
                      isUser ? "bg-[#16253b]" : "hover:bg-[#141f33]"
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-xs font-mono font-bold text-[#64748b] w-5">
                        #{gt.rank}
                      </span>
                      <div
                        className={`w-8 h-8 rounded-lg flex items-center justify-center font-black text-xs ${getGradeClass(
                          gt.grade
                        )}`}
                      >
                        {gt.grade}
                      </div>
                      <div>
                        <div className="text-xs font-bold text-white flex items-center gap-1.5">
                          <span>{gt.team.name}</span>
                          {isUser && (
                            <span className="text-[9px] bg-[#00ceb8] text-[#090e17] px-1.5 py-0.2 rounded font-black">
                              YOU
                            </span>
                          )}
                        </div>
                        <div className="text-[10px] text-[#64748b]">
                          Slot {gt.team.slot} • {gt.team.manager_handle}
                        </div>
                      </div>
                    </div>

                    <div className="text-right">
                      <div className="text-xs font-mono font-black text-white">
                        {gt.totalPts.toFixed(1)} <span className="text-[9px] text-[#64748b]">FPTS</span>
                      </div>
                      <div className="text-[10px] text-[#8a99ad]">
                        {gt.team.roster[0] ? `R1: ${gt.team.roster[0].name}` : ""}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Modal Footer Actions */}
        <div className="px-6 py-4 bg-[#0a101a] border-t border-[#1f2d45] flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-[#141f33] hover:bg-[#1c2c47] text-[#cbd5e1] text-xs font-bold transition-colors"
          >
            Review Board
          </button>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onRestartMock}
              className="px-4 py-2 rounded-xl bg-[#1c2a42] hover:bg-[#253959] border border-[#2b3e60] text-white text-xs font-bold transition-colors"
            >
              Start New Mock Draft
            </button>

            {onSaveAsLeague && (
              <button
                type="button"
                disabled={isSaving}
                onClick={() => onSaveAsLeague(draftState.title)}
                className={`px-5 py-2 rounded-xl bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#090e17] text-xs font-black uppercase tracking-wider transition-all shadow-[0_0_12px_rgba(0,206,184,0.3)] flex items-center gap-2 ${
                  isSaving ? "opacity-60 cursor-not-allowed" : "cursor-pointer"
                }`}
              >
                {isSaving && (
                  <svg className="w-3.5 h-3.5 animate-spin text-[#090e17]" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                  </svg>
                )}
                <span>{isSaving ? "Saving League..." : "Save as League"}</span>
              </button>
            )}

          </div>
        </div>
      </div>
    </div>
  );
};

export default DraftRecapModal;
