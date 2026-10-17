import React, { useState, useEffect } from "react";
import {
  NapperManagerArchetype,
  ARCHETYPE_METAS,
  ArchetypeMeta,
} from "../../../types/napper";

export interface ArchetypeCompositionModalProps {
  isOpen: boolean;
  onClose: () => void;
  teamsCount: number;
  currentComposition: Record<string, number>;
  onApply: (newComposition: Record<string, number>, presetName?: string) => void;
  title?: string;
  subtitle?: string;
  applyButtonText?: string;
}

export const ARCHETYPE_PRESETS: Array<{
  name: string;
  description: string;
  getCounts: (aiSlots: number) => Record<string, number>;
}> = [
  {
    name: "Balanced Mix",
    description: "Realistic spread of all 8 archetypes: analytics minds, bellcow believers, trade fiends, and sleepers.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        ANALYTICS_NERD: 0,
        ZERO_RB_ZEALOT: 0,
        HERO_RB: 0,
        TRASH_TALKER: 0,
        WAIVER_HAWK: 0,
        DIE_HARD: 0,
        TRADE_FIEND: 0,
        UNDERDOG: 0,
      };
      const keys = Object.keys(counts);
      for (let i = 0; i < aiSlots; i++) {
        counts[keys[i % keys.length]] += 1;
      }
      return counts;
    },
  },
  {
    name: "Trader's Paradise",
    description: "Active market room dominated by aggressive Trade Fiends and VORP Analytics dealmakers.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        TRADE_FIEND: Math.max(1, Math.round(aiSlots * 0.45)),
        ANALYTICS_NERD: Math.max(1, Math.round(aiSlots * 0.25)),
        WAIVER_HAWK: 1,
        HERO_RB: 1,
        ZERO_RB_ZEALOT: 0,
        TRASH_TALKER: 0,
        DIE_HARD: 0,
        UNDERDOG: 0,
      };
      return balanceCounts(counts, aiSlots, "TRADE_FIEND");
    },
  },
  {
    name: "Sleeper Upside",
    description: "Young gun breakout room dominated by Underdogs and Zero-RB pass-catcher enthusiasts.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        UNDERDOG: Math.max(1, Math.round(aiSlots * 0.45)),
        ZERO_RB_ZEALOT: Math.max(1, Math.round(aiSlots * 0.25)),
        HERO_RB: 1,
        ANALYTICS_NERD: 1,
        TRADE_FIEND: 0,
        TRASH_TALKER: 0,
        WAIVER_HAWK: 0,
        DIE_HARD: 0,
      };
      return balanceCounts(counts, aiSlots, "UNDERDOG");
    },
  },
  {
    name: "Analytics Heavy",
    description: "Dominated by VORP optimizers hunting value drops and draft steals.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        ANALYTICS_NERD: Math.max(1, Math.round(aiSlots * 0.55)),
        ZERO_RB_ZEALOT: Math.max(1, Math.round(aiSlots * 0.15)),
        HERO_RB: Math.max(1, Math.round(aiSlots * 0.15)),
        TRADE_FIEND: 1,
        TRASH_TALKER: 0,
        WAIVER_HAWK: 0,
        DIE_HARD: 0,
        UNDERDOG: 0,
      };
      return balanceCounts(counts, aiSlots, "ANALYTICS_NERD");
    },
  },
  {
    name: "Zero-RB Gauntlet",
    description: "Stack of pass-catcher drafters ignoring early RBs, pushing WR values up the board.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        ZERO_RB_ZEALOT: Math.max(1, Math.round(aiSlots * 0.55)),
        ANALYTICS_NERD: Math.max(1, Math.round(aiSlots * 0.15)),
        UNDERDOG: 1,
        HERO_RB: 1,
        TRASH_TALKER: 0,
        WAIVER_HAWK: 0,
        DIE_HARD: 0,
        TRADE_FIEND: 0,
      };
      return balanceCounts(counts, aiSlots, "ZERO_RB_ZEALOT");
    },
  },
  {
    name: "Hero-RB Heavy",
    description: "Bellcow drafters rushing to lock tier-1 running backs in the first two rounds.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        HERO_RB: Math.max(1, Math.round(aiSlots * 0.55)),
        ZERO_RB_ZEALOT: 1,
        ANALYTICS_NERD: 1,
        TRADE_FIEND: 1,
        TRASH_TALKER: 0,
        WAIVER_HAWK: 0,
        DIE_HARD: 0,
        UNDERDOG: 0,
      };
      return balanceCounts(counts, aiSlots, "HERO_RB");
    },
  },
  {
    name: "Chaos & Reachers",
    description: "Volatile room dominated by boastful Trash Talkers and Die-Hard fan favorites.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        TRASH_TALKER: Math.max(1, Math.round(aiSlots * 0.45)),
        DIE_HARD: Math.max(1, Math.round(aiSlots * 0.35)),
        TRADE_FIEND: 1,
        UNDERDOG: 0,
        HERO_RB: 0,
        ANALYTICS_NERD: 0,
        ZERO_RB_ZEALOT: 0,
        WAIVER_HAWK: 0,
      };
      return balanceCounts(counts, aiSlots, "TRASH_TALKER");
    },
  },
  {
    name: "Fantasy League Sim Mix",
    description: "Aligns with fantasy_league_sim distribution: BPA 35%, Hero RB 15%, Zero RB 15%, Robust RB 15%, Early QB 10%, Early TE 10%.",
    getCounts: (aiSlots: number) => {
      const counts: Record<string, number> = {
        BPA: Math.max(1, Math.round(aiSlots * 0.35)),
        HERO_RB: Math.max(1, Math.round(aiSlots * 0.15)),
        ZERO_RB_ZEALOT: Math.max(1, Math.round(aiSlots * 0.15)),
        RB_HEAVY: Math.max(1, Math.round(aiSlots * 0.15)),
        EARLY_QB: Math.max(1, Math.round(aiSlots * 0.10)),
        EARLY_TE: Math.max(1, Math.round(aiSlots * 0.10)),
        ANALYTICS_NERD: 0,
        TRASH_TALKER: 0,
        WAIVER_HAWK: 0,
        DIE_HARD: 0,
        TRADE_FIEND: 0,
        UNDERDOG: 0,
      };
      return balanceCounts(counts, aiSlots, "BPA");
    },
  },
];

export function balanceCounts(counts: Record<string, number>, targetTotal: number, primaryKey: string): Record<string, number> {
  let currentTotal = Object.values(counts).reduce((a, b) => a + b, 0);
  if (currentTotal < targetTotal) {
    counts[primaryKey] += targetTotal - currentTotal;
  } else if (currentTotal > targetTotal) {
    let diff = currentTotal - targetTotal;
    for (const k of Object.keys(counts)) {
      if (counts[k] > 0 && diff > 0) {
        const sub = Math.min(counts[k], diff);
        counts[k] -= sub;
        diff -= sub;
      }
    }
  }
  return counts;
}

export const ArchetypeCompositionModal: React.FC<ArchetypeCompositionModalProps> = ({
  isOpen,
  onClose,
  teamsCount,
  currentComposition,
  onApply,
  title,
  subtitle,
  applyButtonText,
}) => {
  const aiSlots = Math.max(1, teamsCount - 1);
  const [counts, setCounts] = useState<Record<string, number>>(() => ({
    ANALYTICS_NERD: 0,
    ZERO_RB_ZEALOT: 0,
    HERO_RB: 0,
    TRASH_TALKER: 0,
    WAIVER_HAWK: 0,
    DIE_HARD: 0,
    TRADE_FIEND: 0,
    UNDERDOG: 0,
    RB_HEAVY: 0,
    EARLY_QB: 0,
    EARLY_TE: 0,
    BPA: 0,
    ...currentComposition,
  }));
  const [activePreset, setActivePreset] = useState<string>("Custom");

  useEffect(() => {
    if (isOpen) {
      setCounts({
        ANALYTICS_NERD: 0,
        ZERO_RB_ZEALOT: 0,
        HERO_RB: 0,
        TRASH_TALKER: 0,
        WAIVER_HAWK: 0,
        DIE_HARD: 0,
        TRADE_FIEND: 0,
        UNDERDOG: 0,
        RB_HEAVY: 0,
        EARLY_QB: 0,
        EARLY_TE: 0,
        BPA: 0,
        ...currentComposition,
      });
    }
  }, [isOpen, currentComposition]);

  if (!isOpen) return null;

  const totalAssigned = Object.values(counts).reduce((a, b) => a + b, 0);
  const isExact = totalAssigned === aiSlots;

  const handlePresetSelect = (presetName: string) => {
    const p = ARCHETYPE_PRESETS.find((x) => x.name === presetName);
    if (p) {
      setCounts(p.getCounts(aiSlots));
      setActivePreset(presetName);
    }
  };

  const handleIncrement = (key: string) => {
    setCounts((prev) => ({
      ...prev,
      [key]: (prev[key] || 0) + 1,
    }));
    setActivePreset("Custom");
  };

  const handleDecrement = (key: string) => {
    setCounts((prev) => ({
      ...prev,
      [key]: Math.max(0, (prev[key] || 0) - 1),
    }));
    setActivePreset("Custom");
  };

  const handleApply = () => {
    let finalCounts = { ...counts };
    if (totalAssigned !== aiSlots) {
      finalCounts = balanceCounts(finalCounts, aiSlots, "ANALYTICS_NERD");
    }
    onApply(finalCounts, activePreset);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in">
      <div className="bg-[#0b121e] border border-[#20314d] rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-[0_16px_48px_rgba(0,0,0,0.85)] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#20314d] bg-[#090e17]">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-lg">🤖</span>
              <h2 className="text-base font-black uppercase tracking-wider text-white">
                {title || "Mock Draft Archetype Composition"}
              </h2>
            </div>
            <p className="text-xs text-[#8a99ad] mt-0.5">
              {subtitle || `Customize the draft personas of the ${aiSlots} AI managers drafting against you`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#8a99ad] hover:text-white hover:bg-[#182333] transition-colors"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Preset Bar & Status Banner */}
        <div className="px-6 py-3 border-b border-[#20314d] bg-[#0d1626] flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
            <span className="text-[10px] font-bold text-[#64748b] uppercase tracking-wider mr-1">
              Presets:
            </span>
            {ARCHETYPE_PRESETS.map((p) => (
              <button
                key={p.name}
                type="button"
                onClick={() => handlePresetSelect(p.name)}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer whitespace-nowrap ${
                  activePreset === p.name
                    ? "bg-[#00ceb8]/20 border border-[#00ceb8] text-[#00ceb8]"
                    : "bg-[#141f33] border border-[#20314d] text-[#94a3b8] hover:text-white"
                }`}
              >
                {p.name}
              </button>
            ))}
          </div>

          <div
            className={`px-3 py-1 rounded-full text-xs font-bold flex items-center gap-1.5 border ${
              isExact
                ? "bg-[#00ceb8]/15 border-[#00ceb8]/40 text-[#00ceb8]"
                : totalAssigned > aiSlots
                ? "bg-rose-500/15 border-rose-500/40 text-rose-400"
                : "bg-amber-500/15 border-amber-500/40 text-amber-400"
            }`}
          >
            <span>
              {totalAssigned} / {aiSlots} AI Slots Allocated
            </span>
          </div>
        </div>

        {/* Archetypes List */}
        <div className="flex-1 overflow-y-auto p-6 space-y-3 scrollbar-thin">
          {(Object.keys(ARCHETYPE_METAS) as NapperManagerArchetype[]).map((key) => {
            const meta = ARCHETYPE_METAS[key];
            const count = counts[key] || 0;

            return (
              <div
                key={key}
                className="flex items-center justify-between p-3.5 rounded-xl border border-[#1e2c42] bg-[#111a2a] hover:border-[#2a3f60] transition-colors gap-3"
              >
                {/* Meta details */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className="text-[10px] font-black uppercase px-2 py-0.5 rounded tracking-wider border"
                      style={{
                        color: meta.color,
                        backgroundColor: meta.bgColor,
                        borderColor: meta.borderColor,
                      }}
                    >
                      {meta.shortLabel}
                    </span>
                    <span className="text-xs font-bold text-white truncate">{meta.label}</span>
                  </div>
                  <div className="text-[11px] font-medium text-[#00ceb8]/90 mb-0.5">
                    {meta.tagline}
                  </div>
                  <p className="text-[11px] text-[#8a99ad] line-clamp-2 leading-relaxed">
                    {meta.description}
                  </p>
                </div>

                {/* Stepper control */}
                <div className="flex items-center gap-2 shrink-0 bg-[#0b121e] border border-[#20314d] rounded-xl p-1">
                  <button
                    type="button"
                    onClick={() => handleDecrement(key)}
                    disabled={count <= 0}
                    className="w-7 h-7 rounded-lg bg-[#141f33] hover:bg-[#1f2f4c] text-white disabled:opacity-30 disabled:cursor-not-allowed font-black flex items-center justify-center transition-colors text-sm"
                  >
                    -
                  </button>
                  <span className="w-8 text-center text-sm font-black text-white">{count}</span>
                  <button
                    type="button"
                    onClick={() => handleIncrement(key)}
                    className="w-7 h-7 rounded-lg bg-[#141f33] hover:bg-[#1f2f4c] text-white font-black flex items-center justify-center transition-colors text-sm"
                  >
                    +
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-[#20314d] bg-[#090e17]">
          <div className="text-xs text-[#8a99ad]">
            {!isExact && (
              <span className="text-amber-400">
                Notice: Total will automatically auto-balance to {aiSlots} slots when applied.
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl border border-[#20314d] text-xs font-bold text-[#8a99ad] hover:text-white hover:bg-[#141f33] transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleApply}
              className="px-5 py-2 rounded-xl bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#090e17] text-xs font-black uppercase tracking-wider shadow-[0_0_12px_rgba(0,206,184,0.3)] transition-all cursor-pointer"
            >
              {applyButtonText || "Apply Archetypes"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ArchetypeCompositionModal;
