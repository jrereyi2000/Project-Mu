import React, { useCallback, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import type { NapperStarterPlayer } from "../../types/napper";
import {
  getPlayerHeadshotUrl,
  getSlotBadgeClass,
  handleImageError,
  InjuryPill,
  PostGameLine,
} from "./napperUtils";

interface TeamRosterSlot {
  id: string;
  slot: string; // "QB" | "RB" | "WR" | "TE" | "FLEX" | "SUPERFLEX" | "BN" | "IR"
  slot_label: string;
  player: NapperStarterPlayer | null;
}

interface NapperTeamTabProps {
  leagueId: number;
  onSelectPlayer?: (player: NapperStarterPlayer) => void;
  onTeamNameChanged?: (teamName: string) => void;
}

/** Check slot eligibility for swaps */
function isSlotEligible(slotType: string, playerPosition?: string): boolean {
  if (!playerPosition) return true;
  const pos = playerPosition.toUpperCase();
  const slot = slotType.toUpperCase();

  if (slot === "BN") return true;
  if (slot === "IR") return true;
  if (slot === pos) return true;
  if (slot === "SUPERFLEX") return ["QB", "RB", "WR", "TE"].includes(pos);
  if (slot === "FLEX") return ["RB", "WR", "TE"].includes(pos);
  return false;
}

export const NapperTeamTab: React.FC<NapperTeamTabProps> = ({
  leagueId,
  onSelectPlayer,
  onTeamNameChanged,
}) => {
  const [roster, setRoster] = useState<TeamRosterSlot[]>([]);
  const [teamName, setTeamName] = useState<string>("My Team");
  const [teamId, setTeamId] = useState<number | null>(null);
  const [week, setWeek] = useState<number>(1);
  const [record, setRecord] = useState<string>("0-0");
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [selectedSlotId, setSelectedSlotId] = useState<string | null>(null);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [nameDialogOpen, setNameDialogOpen] = useState<boolean>(false);
  const [editedTeamName, setEditedTeamName] = useState<string>("");
  const [savingTeamName, setSavingTeamName] = useState<boolean>(false);

  const loadRoster = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/api/napper/league/${leagueId}/team`);
      if (!response.ok) throw new Error("Could not load your league roster.");
      const data = await response.json();
      setRoster(data.roster || []);
      setTeamName(data.team?.team_name || "My Team");
      onTeamNameChanged?.(data.team?.team_name || "My Team");
      setTeamId(data.team?.id ?? null);
      setWeek(data.week || 1);
      setRecord(data.team?.record || "0-0");
    } catch (error) {
      setRoster([]);
      setFeedbackMessage(error instanceof Error ? error.message : "Could not load your league roster.");
    } finally {
      setLoading(false);
    }
  }, [leagueId, onTeamNameChanged]);

  useEffect(() => {
    void loadRoster();
  }, [loadRoster]);

  const selectedSlot = roster.find((s) => s.id === selectedSlotId);
  const selectedPlayer = selectedSlot?.player;

  const saveSlots = async (swaps: Array<{ player_id: number; new_slot: string }>) => {
    if (!teamId || !swaps.length) return;
    setSaving(true);
    try {
      const response = await fetch(`${API_BASE}/api/napper/league/${leagueId}/lineup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_id: teamId, week, swaps }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not save lineup changes.");
      await loadRoster();
      setFeedbackMessage("Lineup saved.");
    } catch (error) {
      await loadRoster();
      setFeedbackMessage(error instanceof Error ? error.message : "Could not save lineup changes.");
    } finally {
      setSaving(false);
      setSelectedSlotId(null);
      setTimeout(() => setFeedbackMessage(null), 3000);
    }
  };

  // Handle slot click (Select or Swap)
  const handleSlotClick = (targetSlot: TeamRosterSlot) => {
    if (saving) return;
    if (!selectedSlotId) {
      if (targetSlot.player) setSelectedSlotId(targetSlot.id);
      return;
    }
    if (selectedSlotId === targetSlot.id) {
      setSelectedSlotId(null);
      return;
    }

    const source = selectedSlot;
    const sourcePlayer = source?.player;
    const targetPlayer = targetSlot.player;
    if (!source || !sourcePlayer) return;

    const canSourceMove = isSlotEligible(targetSlot.slot, sourcePlayer.position);
    const canTargetMove = !targetPlayer || isSlotEligible(source.slot, targetPlayer.position);
    if (!canSourceMove || !canTargetMove) {
      setFeedbackMessage(`Incompatible move: ${sourcePlayer.name} cannot move to ${targetSlot.slot_label}.`);
      setTimeout(() => setFeedbackMessage(null), 3000);
      return;
    }

    const swaps = [{ player_id: sourcePlayer.player_id, new_slot: targetSlot.slot }];
    if (targetPlayer) swaps.push({ player_id: targetPlayer.player_id, new_slot: source.slot });
    void saveSlots(swaps);
  };

  const starters = roster.filter((s) => s.slot !== "BN" && s.slot !== "IR");
  const bench = roster.filter((s) => s.slot === "BN");
  const ir = roster.filter((s) => s.slot === "IR");
  // Starters who can't score this week: injured (OUT/IR) or on a bye. Your lineup is never changed for you.
  const inactiveStarters = starters
    .map((s) => s.player)
    .filter((p): p is NapperStarterPlayer => !!p && (!!p.injury_status || p.game_status === "Bye week"));

  const saveTeamName = async () => {
    const nextName = editedTeamName.trim();
    if (!nextName || nextName.length > 64) {
      setFeedbackMessage("Team name must be between 1 and 64 characters.");
      return;
    }
    setSavingTeamName(true);
    try {
      const response = await fetch(`${API_BASE}/api/napper/league/${leagueId}/team/name`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_name: nextName }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not update team name.");
      setTeamName(data.team.team_name);
      onTeamNameChanged?.(data.team.team_name);
      setNameDialogOpen(false);
      setFeedbackMessage("Team name updated.");
    } catch (error) {
      setFeedbackMessage(error instanceof Error ? error.message : "Could not update team name.");
    } finally {
      setSavingTeamName(false);
      setTimeout(() => setFeedbackMessage(null), 3000);
    }
  };

  return (
    <div className="flex-1 px-5 py-4 max-w-6xl mx-auto w-full">
      {/* Roster Controls & Stats Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5 p-4 rounded-xl bg-[#1a2332] border border-[#283850]">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-extrabold text-white truncate">{teamName}</h1>
            <button
              type="button"
              aria-label="Edit team name"
              title="Edit team name"
              onClick={() => { setEditedTeamName(teamName); setNameDialogOpen(true); }}
              className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-[#283850] bg-[#121926] text-[#8a99ad] hover:border-[#00ceb8] hover:text-[#00ceb8] transition-colors"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <circle cx="12" cy="12" r="3" />
                <path d="m19.4 15 .1.1a1.7 1.7 0 0 1-2.4 2.4l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.3a1.7 1.7 0 0 1-3.4 0v-.2a1.7 1.7 0 0 0-2.9-1.2l-.1.1a1.7 1.7 0 1 1-2.4-2.4l.1-.1a1.7 1.7 0 0 0-1.2-2.9h-.3a1.7 1.7 0 0 1 0-3.4h.2a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a1.7 1.7 0 1 1 2.4-2.4l.1.1a1.7 1.7 0 0 0 2.9-1.2v-.3a1.7 1.7 0 0 1 3.4 0v.2a1.7 1.7 0 0 0 2.9 1.2l.1-.1a1.7 1.7 0 1 1 2.4 2.4l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.3a1.7 1.7 0 0 1 0 3.4h-.2a1.7 1.7 0 0 0-1.2 2.9Z" />
              </svg>
            </button>
            <span className="text-xs px-2 py-0.5 rounded bg-[#00ceb8]/15 text-[#00ceb8] font-bold">
              {record}
            </span>
          </div>
          <p className="text-xs text-[#8a99ad] mt-0.5">
            Click any player to initiate a lineup swap into eligible positions
          </p>
        </div>

      </div>

      {nameDialogOpen && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/65 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setNameDialogOpen(false); }}>
          <div role="dialog" aria-modal="true" aria-labelledby="napper-team-name-title" className="w-full max-w-sm rounded-xl border border-[#283850] bg-[#1a2332] p-5 shadow-2xl">
            <h2 id="napper-team-name-title" className="text-base font-extrabold text-white">Edit team name</h2>
            <input
              autoFocus
              maxLength={64}
              value={editedTeamName}
              onChange={(event) => setEditedTeamName(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") void saveTeamName(); }}
              aria-label="Team name"
              className="mt-4 w-full rounded-lg border border-[#283850] bg-[#121926] px-3 py-2 text-sm text-white outline-none focus:border-[#00ceb8]"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setNameDialogOpen(false)} disabled={savingTeamName} className="rounded-lg border border-[#283850] px-3 py-2 text-xs font-bold text-[#8a99ad] hover:text-white disabled:opacity-50">Cancel</button>
              <button type="button" onClick={() => void saveTeamName()} disabled={savingTeamName} className="rounded-lg bg-[#00ceb8] px-3 py-2 text-xs font-extrabold text-[#0b141e] hover:bg-[#2bd9c6] disabled:opacity-50">{savingTeamName ? "Saving…" : "Save name"}</button>
            </div>
          </div>
        </div>
      )}

      {/* Dynamic Feedback Banner */}
      {feedbackMessage && (
        <div className="mb-4 px-4 py-2.5 rounded-lg bg-[#00ceb8]/15 border border-[#00ceb8] text-xs font-bold text-[#00ceb8] flex items-center justify-between">
          <span>{feedbackMessage}</span>
          <button
            type="button"
            onClick={() => setFeedbackMessage(null)}
            className="text-white hover:text-[#00ceb8] cursor-pointer ml-2"
          >
            ✕
          </button>
        </div>
      )}

      {/* Selected Player Swap Indicator bar */}
      {selectedPlayer && (
        <div className="mb-4 px-4 py-3 rounded-xl bg-[#202d40] border-2 border-[#00ceb8] flex items-center justify-between shadow-xl animate-pulse">
          <div className="flex items-center gap-3">
            <img
              src={getPlayerHeadshotUrl(selectedPlayer.player_id, selectedPlayer.headshot_url, selectedPlayer.espn_id)}
              alt={selectedPlayer.name}
              onError={handleImageError}
              className="w-9 h-9 rounded-full object-cover border border-[#00ceb8]"
            />
            <div>
              <div className="text-xs font-bold text-white">
                Swapping: <span className="text-[#00ceb8]">{selectedPlayer.name}</span> ({selectedPlayer.position} - {selectedPlayer.team})
              </div>
              <div className="text-[11px] text-[#8a99ad]">
                Click any highlighted target slot below to complete swap
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setSelectedSlotId(null)}
            className="px-3 py-1.5 rounded-md bg-[#161f2e] border border-[#283850] text-xs font-bold text-[#8a99ad] hover:text-white cursor-pointer"
          >
            Cancel
          </button>
        </div>
      )}

      {loading ? (
        <div className="py-12 text-center text-sm text-[#8a99ad]">Loading your roster…</div>
      ) : roster.length === 0 ? (
        <div className="py-12 text-center text-sm text-[#8a99ad]">No roster has been drafted for this league yet.</div>
      ) : <>
      {inactiveStarters.length > 0 && (
        <div role="status" className="napper-lineup-warning">
          ⚠ {inactiveStarters.length === 1 ? "1 starter" : `${inactiveStarters.length} starters`} won't score in week {week}:{" "}
          {inactiveStarters
            .map((p) => `${p.name} (${p.injury_status === "IR" ? "IR" : p.injury_status ? "Out" : "Bye"})`)
            .join(", ")}
          . Swap in a bench player{inactiveStarters.some((p) => p.injury_status) ? " or move the injured player to IR" : ""}.
        </div>
      )}

      {/* Section 1: Starters */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2.5 px-1">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-extrabold text-white tracking-wider uppercase">
              Starters
            </h2>
            <span className="text-xs text-[#8a99ad]">({starters.length})</span>
          </div>
        </div>

        <div className="space-y-2">
          {starters.map((slotItem) => {
            const p = slotItem.player;
            const isSelected = selectedSlotId === slotItem.id;
            const isEligibleTarget =
              selectedSlotId &&
              selectedSlotId !== slotItem.id &&
              isSlotEligible(slotItem.slot, selectedPlayer?.position);
            const slotClass = getSlotBadgeClass(slotItem.slot);

            return (
              <div
                key={slotItem.id}
                onClick={() => handleSlotClick(slotItem)}
                className={`flex items-center justify-between p-2.5 rounded-xl border transition-all cursor-pointer ${
                  isSelected
                    ? "bg-[#25374f] border-[#ff4a6e] shadow-lg shadow-[#ff4a6e]/20"
                    : isEligibleTarget
                    ? "bg-[#162939] border-[#00ceb8] hover:bg-[#1a3348] ring-1 ring-[#00ceb8]"
                    : "bg-[#1a2332] border-[#283850] hover:bg-[#202d40]"
                }`}
              >
                {/* Left: Slot Badge & Player Details */}
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className={`w-14 shrink-0 napper-slot-badge ${slotClass}`}>
                    {slotItem.slot_label}
                  </div>

                  {p ? (
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      <img
                        src={getPlayerHeadshotUrl(p.player_id, p.headshot_url, p.espn_id)}
                        alt={p.name}
                        onError={handleImageError}
                        className="napper-headshot-crop"
                      />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectPlayer?.(p);
                            }}
                            className="text-xs font-bold text-white hover:text-[#00ceb8] transition-colors truncate"
                          >
                            {p.name}
                          </span>
                          <span className="text-[10px] text-[#8a99ad] font-semibold">
                            {p.position} - {p.team}
                          </span>
                          <InjuryPill player={p} />
                        </div>
                        {p.game_result ? (
                          <PostGameLine player={p} />
                        ) : (
                          <div className="text-[11px] text-[#8a99ad] mt-0.5">
                            {p.kickoff} {p.opponent}
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-[#5c6c80] italic">Empty Slot</div>
                  )}
                </div>

                {/* Right: swap target indicator */}
                <div className="flex items-center gap-3 pl-3 shrink-0">
                  {isEligibleTarget && (
                    <span className="text-[11px] font-extrabold px-2.5 py-1 rounded bg-[#00ceb8] text-[#0b141e] animate-pulse">
                      SWAP HERE
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Section 2: Bench */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2.5 px-1">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-extrabold text-[#8a99ad] tracking-wider uppercase">
              Bench
            </h2>
            <span className="text-xs text-[#8a99ad]">({bench.length})</span>
          </div>
        </div>

        <div className="space-y-2">
          {bench.map((slotItem) => {
            const p = slotItem.player;
            const isSelected = selectedSlotId === slotItem.id;
            const isEligibleTarget =
              selectedSlotId &&
              selectedSlotId !== slotItem.id &&
              isSlotEligible("BN", selectedPlayer?.position);

            return (
              <div
                key={slotItem.id}
                onClick={() => handleSlotClick(slotItem)}
                className={`flex items-center justify-between p-2.5 rounded-xl border transition-all cursor-pointer ${
                  isSelected
                    ? "bg-[#25374f] border-[#ff4a6e] shadow-lg shadow-[#ff4a6e]/20"
                    : isEligibleTarget
                    ? "bg-[#162939] border-[#00ceb8] hover:bg-[#1a3348] ring-1 ring-[#00ceb8]"
                    : "bg-[#18212e] border-[#243347] hover:bg-[#1f2b3c]"
                }`}
              >
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className="w-14 shrink-0 napper-slot-badge slot-bn">BN</div>

                  {p ? (
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      <img
                        src={getPlayerHeadshotUrl(p.player_id, p.headshot_url, p.espn_id)}
                        alt={p.name}
                        onError={handleImageError}
                        className="napper-headshot-crop grayscale-[25%]"
                      />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectPlayer?.(p);
                            }}
                            className="text-xs font-bold text-white hover:text-[#00ceb8] transition-colors truncate"
                          >
                            {p.name}
                          </span>
                          <span className="text-[10px] text-[#8a99ad] font-semibold">
                            {p.position} - {p.team}
                          </span>
                          <InjuryPill player={p} />
                        </div>
                        {p.game_result ? (
                          <PostGameLine player={p} />
                        ) : (
                          <div className="text-[11px] text-[#8a99ad] mt-0.5">
                            {p.kickoff} {p.opponent}
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-[#5c6c80] italic">Empty Slot</div>
                  )}
                </div>

                <div className="flex items-center gap-3 pl-3 shrink-0">
                  {isEligibleTarget && (
                    <span className="text-[11px] font-extrabold px-2.5 py-1 rounded bg-[#00ceb8] text-[#0b141e] animate-pulse">
                      MOVE TO BENCH
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Section 3: Injured Reserve (IR) */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-2.5 px-1">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-extrabold text-[#ff4a6e] tracking-wider uppercase">
              Injured Reserve (IR)
            </h2>
            <span className="text-xs text-[#8a99ad]">({ir.length})</span>
          </div>
        </div>

        <div className="space-y-2">
          {ir.map((slotItem) => {
            const p = slotItem.player;
            const isSelected = selectedSlotId === slotItem.id;

            return (
              <div
                key={slotItem.id}
                onClick={() => handleSlotClick(slotItem)}
                className={`flex items-center justify-between p-2.5 rounded-xl border border-[#382129] bg-[#1a1824] transition-all cursor-pointer ${
                  isSelected ? "border-[#ff4a6e] ring-1 ring-[#ff4a6e]" : ""
                }`}
              >
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className="w-14 shrink-0 napper-slot-badge border-[#ff4a6e]/40 text-[#ff4a6e] bg-[#ff4a6e]/10">
                    IR
                  </div>

                  {p && (
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      <img
                        src={getPlayerHeadshotUrl(p.player_id, p.headshot_url, p.espn_id)}
                        alt={p.name}
                        onError={handleImageError}
                        className="napper-headshot-crop grayscale"
                      />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-bold text-white truncate">{p.name}</span>
                          <span className="text-[10px] text-[#ff6b87] font-semibold">
                            {p.position} - {p.team}
                          </span>
                          {p.injury_status ? <InjuryPill player={p} /> : (
                            <span className="text-[10px] text-[#8a99ad]" title="No longer injured: move to your bench to free the IR slot">Healthy</span>
                          )}
                        </div>
                        <div className="text-[11px] text-[#ff6b87]/80 mt-0.5">
                          {p.kickoff}
                        </div>
                      </div>
                    </div>
                  )}
                </div>

              </div>
            );
          })}
        </div>
      </div>
      </>}
    </div>
  );
};

export default NapperTeamTab;
