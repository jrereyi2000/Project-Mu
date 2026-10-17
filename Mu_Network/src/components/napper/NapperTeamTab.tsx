import React, { useState } from "react";
import { API_BASE } from "../../api";
import type { NapperStarterPlayer } from "../../types/napper";
import {
  getPlayerHeadshotUrl,
  getSlotBadgeClass,
  handleImageError,
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
}

const INITIAL_ROSTER: TeamRosterSlot[] = [
  // Starters
  {
    id: "s-qb1",
    slot: "QB",
    slot_label: "QB",
    player: {
      id: 101,
      player_id: 3918298,
      name: "Josh Allen",
      position: "QB",
      team: "BUF",
      opponent: "@ LV",
      kickoff: "Sun 4:25 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3918298.png",
      fpts: 0.0,
      proj_fpts: 24.95,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "s-rb1",
    slot: "RB",
    slot_label: "RB",
    player: {
      id: 102,
      player_id: 4036378,
      name: "Jordan Love",
      position: "RB",
      team: "ARI",
      opponent: "@ LAR",
      kickoff: "Sun 4:05 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4036378.png",
      fpts: 0.0,
      proj_fpts: 0.0,
      is_yet_to_play: true,
      injury_status: "O",
    },
  },
  {
    id: "s-rb2",
    slot: "RB",
    slot_label: "RB",
    player: {
      id: 103,
      player_id: 4259545,
      name: "D'Andre Swift",
      position: "RB",
      team: "CHI",
      opponent: "@ ATL",
      kickoff: "Sun 1:00 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4259545.png",
      fpts: 0.0,
      proj_fpts: 16.69,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "s-wr1",
    slot: "WR",
    slot_label: "WR",
    player: {
      id: 104,
      player_id: 4258169,
      name: "Nico Collins",
      position: "WR",
      team: "HOU",
      opponent: "@ JAX",
      kickoff: "Sun 9:30 AM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4258169.png",
      fpts: 0.0,
      proj_fpts: 18.34,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "s-wr2",
    slot: "WR",
    slot_label: "WR",
    player: {
      id: 105,
      player_id: 4426515,
      name: "Puka Nacua",
      position: "WR",
      team: "LAR",
      opponent: "@ SF",
      kickoff: "Sun 1:00 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4426515.png",
      fpts: 0.0,
      proj_fpts: 17.5,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "s-te1",
    slot: "TE",
    slot_label: "TE",
    player: {
      id: 106,
      player_id: 4432665,
      name: "Brock Bowers",
      position: "TE",
      team: "LV",
      opponent: "vs BUF",
      kickoff: "Sun 4:25 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432665.png",
      fpts: 0.0,
      proj_fpts: 14.2,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "s-flex1",
    slot: "FLEX",
    slot_label: "FLEX",
    player: {
      id: 107,
      player_id: 4427366,
      name: "Breece Hall",
      position: "RB",
      team: "NYJ",
      opponent: "vs NE",
      kickoff: "Sun 1:00 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4427366.png",
      fpts: 0.0,
      proj_fpts: 16.8,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "s-sflex1",
    slot: "SUPERFLEX",
    slot_label: "SFLEX",
    player: {
      id: 108,
      player_id: 3139477,
      name: "Patrick Mahomes",
      position: "QB",
      team: "KC",
      opponent: "vs LAC",
      kickoff: "Sun 4:25 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3139477.png",
      fpts: 0.0,
      proj_fpts: 21.4,
      is_yet_to_play: true,
      injury_status: null,
    },
  },

  // Bench
  {
    id: "b-1",
    slot: "BN",
    slot_label: "BN",
    player: {
      id: 109,
      player_id: 3042519,
      name: "Aaron Jones",
      position: "RB",
      team: "MIN",
      opponent: "vs GB",
      kickoff: "Sun 1:00 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3042519.png",
      fpts: 0.0,
      proj_fpts: 14.8,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "b-2",
    slot: "BN",
    slot_label: "BN",
    player: {
      id: 110,
      player_id: 4431452,
      name: "Drake Maye",
      position: "QB",
      team: "NE",
      opponent: "@ NYJ",
      kickoff: "Sun 1:00 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4431452.png",
      fpts: 0.0,
      proj_fpts: 15.6,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "b-3",
    slot: "BN",
    slot_label: "BN",
    player: {
      id: 111,
      player_id: 4429022,
      name: "Garrett Wilson",
      position: "WR",
      team: "NYJ",
      opponent: "vs NE",
      kickoff: "Sun 1:00 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4429022.png",
      fpts: 0.0,
      proj_fpts: 15.1,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "b-4",
    slot: "BN",
    slot_label: "BN",
    player: {
      id: 112,
      player_id: 4361528,
      name: "Trey McBride",
      position: "TE",
      team: "ARI",
      opponent: "@ LAR",
      kickoff: "Sun 4:05 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4361528.png",
      fpts: 0.0,
      proj_fpts: 13.9,
      is_yet_to_play: true,
      injury_status: null,
    },
  },
  {
    id: "b-5",
    slot: "BN",
    slot_label: "BN",
    player: {
      id: 113,
      player_id: 4372016,
      name: "Jaylen Waddle",
      position: "WR",
      team: "MIA",
      opponent: "@ IND",
      kickoff: "Sun 1:00 PM",
      game_status: "Yet to play",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4372016.png",
      fpts: 0.0,
      proj_fpts: 13.2,
      is_yet_to_play: true,
      injury_status: null,
    },
  },

  // IR
  {
    id: "ir-1",
    slot: "IR",
    slot_label: "IR",
    player: {
      id: 114,
      player_id: 3117251,
      name: "Christian McCaffrey",
      position: "RB",
      team: "SF",
      opponent: "vs LAR",
      kickoff: "Out (IR)",
      game_status: "Reserve/IR",
      headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3117251.png",
      fpts: 0.0,
      proj_fpts: 0.0,
      is_yet_to_play: false,
      injury_status: "IR",
    },
  },
];

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
}) => {
  const [roster, setRoster] = useState<TeamRosterSlot[]>(INITIAL_ROSTER);
  const [selectedSlotId, setSelectedSlotId] = useState<string | null>(null);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);

  const selectedSlot = roster.find((s) => s.id === selectedSlotId);
  const selectedPlayer = selectedSlot?.player;

  // Handle slot click (Select or Swap)
  const handleSlotClick = (targetSlot: TeamRosterSlot) => {
    // If no slot currently selected: select this slot
    if (!selectedSlotId) {
      if (targetSlot.player) {
        setSelectedSlotId(targetSlot.id);
      }
      return;
    }

    // If clicking same slot, deselect
    if (selectedSlotId === targetSlot.id) {
      setSelectedSlotId(null);
      return;
    }

    // Validate eligibility for both directions
    const srcPlayer = selectedSlot?.player;
    const tgtPlayer = targetSlot.player;

    const canSrcFitTarget = isSlotEligible(targetSlot.slot, srcPlayer?.position);
    const canTgtFitSrc = !tgtPlayer || isSlotEligible(selectedSlot!.slot, tgtPlayer.position);

    if (!canSrcFitTarget || !canTgtFitSrc) {
      setFeedbackMessage(
        `Incompatible swap: ${srcPlayer?.name} (${srcPlayer?.position}) cannot move to ${targetSlot.slot_label}`
      );
      setTimeout(() => setFeedbackMessage(null), 3000);
      return;
    }

    // Perform swap in roster state
    setRoster((prev) =>
      prev.map((item) => {
        if (item.id === selectedSlotId) {
          return { ...item, player: tgtPlayer };
        }
        if (item.id === targetSlot.id) {
          return { ...item, player: srcPlayer || null };
        }
        return item;
      })
    );

    setFeedbackMessage(`Swapped ${srcPlayer?.name} with ${tgtPlayer ? tgtPlayer.name : targetSlot.slot_label}!`);
    setTimeout(() => setFeedbackMessage(null), 3000);
    setSelectedSlotId(null);

    // Call backend API if connected
    try {
      fetch(`${API_BASE}/api/napper/league/${leagueId}/lineup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          league_id: leagueId,
          swaps: [
            { slot_from: selectedSlot?.slot, slot_to: targetSlot.slot },
          ],
        }),
      }).catch(() => {});
    } catch {}
  };

  // Lineup Optimizer: moves top projected bench players into starters
  const handleOptimizeLineup = () => {
    const starters = roster.filter((s) => s.slot !== "BN" && s.slot !== "IR");
    const benchAndStarters = roster.filter((s) => s.slot !== "IR");

    const allPlayers = benchAndStarters
      .map((s) => s.player)
      .filter((p): p is NapperStarterPlayer => Boolean(p))
      .sort((a, b) => b.proj_fpts - a.proj_fpts);

    // Greedily fill starters
    const assignedPlayers = new Set<number>();
    const newStarters: Record<string, NapperStarterPlayer | null> = {};

    for (const s of starters) {
      const best = allPlayers.find(
        (p) => !assignedPlayers.has(p.id) && isSlotEligible(s.slot, p.position)
      );
      if (best) {
        newStarters[s.id] = best;
        assignedPlayers.add(best.id);
      } else {
        newStarters[s.id] = null;
      }
    }

    const unassigned = allPlayers.filter((p) => !assignedPlayers.has(p.id));

    setRoster((prev) => {
      let bIdx = 0;
      return prev.map((s) => {
        if (s.slot === "IR") return s;
        if (s.slot !== "BN") {
          return { ...s, player: newStarters[s.id] ?? null };
        }
        const bPlayer = unassigned[bIdx] || null;
        bIdx++;
        return { ...s, player: bPlayer };
      });
    });

    setFeedbackMessage("Lineup optimized for maximum projected points!");
    setTimeout(() => setFeedbackMessage(null), 3500);
    setSelectedSlotId(null);
  };

  // Compute total starter projected points
  const starters = roster.filter((s) => s.slot !== "BN" && s.slot !== "IR");
  const bench = roster.filter((s) => s.slot === "BN");
  const ir = roster.filter((s) => s.slot === "IR");

  const totalStarterProj = starters.reduce(
    (sum, s) => sum + (s.player?.proj_fpts || 0),
    0
  );

  return (
    <div className="flex-1 px-5 py-4 max-w-6xl mx-auto w-full">
      {/* Roster Controls & Stats Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5 p-4 rounded-xl bg-[#1a2332] border border-[#283850]">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-extrabold text-white">The Straw Hat Pirates</h1>
            <span className="text-xs px-2 py-0.5 rounded bg-[#00ceb8]/15 text-[#00ceb8] font-bold">
              5-0
            </span>
          </div>
          <p className="text-xs text-[#8a99ad] mt-0.5">
            Click any player to initiate a lineup swap into eligible positions
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right px-3 py-1 bg-[#121926] rounded-lg border border-[#283850]">
            <div className="text-[10px] uppercase font-bold text-[#8a99ad] tracking-wider">
              Proj Points
            </div>
            <div className="text-base font-extrabold text-[#00ceb8] font-mono">
              {totalStarterProj.toFixed(2)}
            </div>
          </div>

          <button
            type="button"
            onClick={handleOptimizeLineup}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#0b141e] font-extrabold text-xs transition-colors cursor-pointer shadow-lg shadow-[#00ceb8]/20"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z" />
            </svg>
            <span>Optimize</span>
          </button>
        </div>
      </div>

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
              src={getPlayerHeadshotUrl(selectedPlayer.name, selectedPlayer.headshot_url)}
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

      {/* Section 1: Starters */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2.5 px-1">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-extrabold text-white tracking-wider uppercase">
              Starters
            </h2>
            <span className="text-xs text-[#8a99ad]">({starters.length})</span>
          </div>
          <span className="text-xs text-[#8a99ad]">Projected: {totalStarterProj.toFixed(2)}</span>
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
                        src={getPlayerHeadshotUrl(p.name, p.headshot_url)}
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
                          {p.injury_status && (
                            <span
                              className={`napper-injury-pill ${
                                p.injury_status === "O" ? "out" : "ques"
                              }`}
                            >
                              {p.injury_status}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-[#8a99ad] mt-0.5">
                          {p.kickoff} {p.opponent}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-[#5c6c80] italic">Empty Slot</div>
                  )}
                </div>

                {/* Right: Swap Button / Projections */}
                <div className="flex items-center gap-3 pl-3 shrink-0">
                  {isEligibleTarget && (
                    <span className="text-[11px] font-extrabold px-2.5 py-1 rounded bg-[#00ceb8] text-[#0b141e] animate-pulse">
                      SWAP HERE
                    </span>
                  )}
                  {p && (
                    <div className="text-right min-w-[50px]">
                      <div className="text-xs font-bold text-white font-mono">
                        {p.proj_fpts.toFixed(2)}
                      </div>
                      <div className="text-[9px] text-[#8a99ad] uppercase">Proj</div>
                    </div>
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
                        src={getPlayerHeadshotUrl(p.name, p.headshot_url)}
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
                          {p.injury_status && (
                            <span
                              className={`napper-injury-pill ${
                                p.injury_status === "O" ? "out" : "ques"
                              }`}
                            >
                              {p.injury_status}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-[#8a99ad] mt-0.5">
                          {p.kickoff} {p.opponent}
                        </div>
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
                  {p && (
                    <div className="text-right min-w-[50px]">
                      <div className="text-xs font-bold text-white font-mono">
                        {p.proj_fpts.toFixed(2)}
                      </div>
                      <div className="text-[9px] text-[#8a99ad] uppercase">Proj</div>
                    </div>
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
                        src={getPlayerHeadshotUrl(p.name, p.headshot_url)}
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
                          <span className="napper-injury-pill ir">IR</span>
                        </div>
                        <div className="text-[11px] text-[#ff6b87]/80 mt-0.5">
                          {p.kickoff}
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {p && (
                  <div className="text-right min-w-[50px] shrink-0">
                    <div className="text-xs font-bold text-[#8a99ad] font-mono">
                      {p.proj_fpts.toFixed(2)}
                    </div>
                    <div className="text-[9px] text-[#5c6c80] uppercase">Proj</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default NapperTeamTab;
