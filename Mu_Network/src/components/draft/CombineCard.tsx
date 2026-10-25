import React from "react";

export interface CombineDrill {
  key: string;
  label: string;
  value: string;
  raw: number;
  percentile: number;
}

export interface CombineProfileData {
  participated: boolean;
  height?: string | null;
  height_in?: number | null;
  weight?: number | null;
  pro_day_result?: string | null;
  pro_day_blurb?: string | null;
  athletic_score?: number | null;
  interviews?: string | null;
  medical?: string | null;
  headline?: string | null;
  blurb?: string | null;
  drills?: CombineDrill[];
}

function percentileColor(pct: number): string {
  if (pct >= 85) return "#10b981";
  if (pct >= 65) return "#3b82f6";
  if (pct >= 40) return "#f59e0b";
  return "#ef4444";
}

export const CombineCard: React.FC<{ combine: CombineProfileData; position: string }> = ({ combine, position }) => {
  const drills = combine.drills || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
      {/* Top Physical & Combine Summary Strip */}
      <div className="draft-stat-boxes">
        <div className="draft-stat-box">
          <div className="draft-stat-box-label">Verified Height</div>
          <div className="draft-stat-box-val">{combine.height || "—"}</div>
        </div>
        <div className="draft-stat-box">
          <div className="draft-stat-box-label">Verified Weight</div>
          <div className="draft-stat-box-val">{combine.weight ? `${combine.weight} lbs` : "—"}</div>
        </div>
        <div className="draft-stat-box">
          <div className="draft-stat-box-label">Athletic Score (RAS)</div>
          <div className="draft-stat-box-val" style={{ color: combine.athletic_score && combine.athletic_score >= 8.0 ? "#15803d" : "#0f172a" }}>
            {combine.athletic_score != null ? `${combine.athletic_score.toFixed(2)} / 10` : "Pending"}
          </div>
        </div>
        <div className="draft-stat-box">
          <div className="draft-stat-box-label">Medical Check</div>
          <div className="draft-stat-box-val" style={{ fontSize: "15px", textTransform: "uppercase", color: "#15803d" }}>
            {combine.medical || "Cleared"}
          </div>
        </div>
        <div className="draft-stat-box">
          <div className="draft-stat-box-label">Team Interviews</div>
          <div className="draft-stat-box-val" style={{ fontSize: "15px", textTransform: "uppercase" }}>
            {combine.interviews || "Scheduled"}
          </div>
        </div>
      </div>

      {/* Combine Drills Percentile Breakdown */}
      {combine.participated && drills.length > 0 ? (
        <div className="draft-section-card" style={{ padding: "18px 22px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h4 style={{ margin: 0, fontSize: "13px", fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              NFL Scouting Combine Workout · vs. {position} Positional Benchmarks
            </h4>
            {combine.headline && (
              <span style={{ fontSize: "12px", fontWeight: 700, color: "#0f172a", background: "#fef3c7", padding: "3px 10px", borderRadius: "999px" }}>
                {combine.headline}
              </span>
            )}
          </div>
          {combine.blurb && (
            <p style={{ margin: "0 0 14px", fontSize: "13px", color: "#475569" }}>{combine.blurb}</p>
          )}
          <div>
            {drills.map((d) => (
              <div key={d.key} className="combine-drill-row">
                <div style={{ fontWeight: 700, fontSize: "13px", color: "#0f172a" }}>{d.label}</div>
                <div style={{ fontFamily: "monospace", fontWeight: 800, fontSize: "14px", color: "#1e293b" }}>{d.value}</div>
                <div className="combine-bar-track">
                  <div
                    className="combine-bar-fill"
                    style={{ width: `${d.percentile}%`, background: percentileColor(d.percentile) }}
                  />
                </div>
                <div style={{ textAlign: "right", fontWeight: 800, fontSize: "12px", color: percentileColor(d.percentile) }}>
                  {d.percentile}th %
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="draft-section-card" style={{ background: "#f8fafc", textAlign: "center", padding: "28px 20px" }}>
          <div style={{ fontSize: "14px", fontWeight: 800, color: "#0f172a", marginBottom: "6px" }}>
            NFL Scouting Combine Testing Pending (Offseason Stage 1 · Step 24)
          </div>
          <p style={{ margin: 0, fontSize: "13px", color: "#64748b" }}>
            Verified school measurables ({combine.height || "—"}, {combine.weight ? `${combine.weight} lbs` : "—"}) are on file.
            Official 40-yard dash, vertical, broad jump, 3-cone, and bench press percentiles populate once the Combine runs in Indianapolis.
          </p>
        </div>
      )}

      {/* Pro Day Note */}
      {combine.pro_day_blurb && (
        <div className="draft-section-card" style={{ borderLeft: "4px solid #f59e0b" }}>
          <div style={{ fontSize: "11px", fontWeight: 800, textTransform: "uppercase", color: "#b45309", marginBottom: "4px" }}>
            Campus Pro Day Report ({combine.pro_day_result?.toUpperCase()})
          </div>
          <div style={{ fontSize: "13px", color: "#1e293b" }}>{combine.pro_day_blurb}</div>
        </div>
      )}
    </div>
  );
};

export default CombineCard;
