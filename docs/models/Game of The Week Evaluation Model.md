# Algorithmic Framework: Defining the NFL Game of the Week (GotW)

This framework defines an objective **Excitement Index ($E$)** for every NFL game in a given week using post-game final scores, box score statistics, and pre-game player overall ratings (OVRs).

---

## 1. Executive Summary

A memorable football game requires a balance of:
1. **Dramatic finishes** (competitiveness)
2. **High-leverage action** (offensive/scoring pace)
3. **Star power & signature performances** (OVRs and outlier stats)
4. **Context & stakes** (heavyweight bouts or dramatic upsets)

The composite score is normalized on a $0 - 100$ scale:

$$E = 0.35(C) + 0.25(P) + 0.20(S) + 0.20(U)$$

| Component | Weight | Key Metrics Evaluated |
| :--- | :--- | :--- |
| **$C$ (Competitiveness)** | 35% | Point differential, Overtime status |
| **$P$ (Production & Pace)** | 25% | Combined points scored, total offensive yardage |
| **$S$ (Star Impact & Outliers)** | 20% | Top-tier roster ratings, individual milestone stat lines |
| **$U$ (Upset & Matchup Narrative)** | 20% | Average roster OVR delta, underdog victories, heavyweight status |

---

## 2. Component Formulations

### 2.1 Competitiveness Score ($C$) — 35% Weight

A blowout significantly diminishes entertainment value regardless of star talent. Closeness and late-game stakes form the foundation of the model.

$$C = \min\left(100, \max\left(0, 100 - 4 \cdot |\text{Score}_A - \text{Score}_B|\right) + \text{OT Bonus}\right)$$

* **Score Differential Deductions:**
  * 1-point margin: Starts at $96$
  * 3-point margin: Starts at $88$
  * 7-point margin (one possession): Starts at $72$
  * $\ge 25$-point margin: Floors at $0$
* **Overtime Bonus ($\text{OT Bonus}$):** $+10$ points for games requiring extra time.

---

### 2.2 Offensive Pace & Production ($P$) — 25% Weight

While defensive slugfests can be tense, broad-appeal games typically feature sustained drives, explosive plays, and scoring volume.

$$P = \min\left(100, \left(\frac{\text{Total Points}}{70} \times 60\right) + \left(\frac{\text{Combined Total Yards}}{850} \times 40\right)\right)$$

* **Point Baseline:** Calibrated against a high-water mark of 70 combined points ($60\%$ contribution).
* **Yardage Baseline:** Calibrated against a benchmark of 850 combined yards ($40\%$ contribution).
* **Sample Scoring:**
  * Shootout ($38 - 35$, 900 total yards) $\to 100.0$
  * Balanced contest ($24 - 21$, 680 total yards) $\to 70.5$
  * Defensive grinder ($13 - 10$, 450 total yards) $\to 40.8$

---

### 2.3 Star Impact & Outlier Performances ($S$) — 20% Weight

Iconic games feature top-tier players executing at peak levels or individual athletes producing historic statistical outputs.

$$S = 0.5(S_{\text{stars}}) + 0.5(S_{\text{outliers}})$$

#### Sub-metric A: Star Power ($S_{\text{stars}}$)
Evaluates the aggregate quality of the top 3 rated players on each active roster:

$$S_{\text{stars}} = \frac{\overline{\text{OVR}}_{\text{Top 3, Team A}} + \overline{\text{OVR}}_{\text{Top 3, Team B}}}{2}$$

* A clash featuring elite signal-callers and edge rushers ($\text{OVR} \ge 92$) provides an elevated floor.

#### Sub-metric B: Outlier Stat Lines ($S_{\text{outliers}}$)
Points awarded for benchmark-clearing individual box scores across both rosters:

* **Passing:**
  * $400+$ yards: $+15$ pts
  * $4+$ passing touchdowns: $+15$ pts
* **Rushing / Receiving:**
  * $150+$ scrimmage yards: $+12$ pts
  * $2+$ total touchdowns: $+8$ pts
* **Defensive / Special Teams:**
  * $3+$ sacks or $2+$ takeaways: $+15$ pts
  * Return touchdown (Pick-6, scoop-and-score, kick return): $+10$ pts

*(Note: $S_{\text{outliers}}$ is capped at 100 before weighting).*

---

### 2.4 Upset & Narrative Leverage ($U$) — 20% Weight

Context matters: an evenly-matched battle of heavyweights or an underdog taking down a powerhouse creates high narrative tension.

Let $\overline{\text{OVR}}_{\text{Team}}$ represent the mean rating of each team's active 46-man roster, and define the expected differential as:

$$\Delta \text{OVR} = \overline{\text{OVR}}_{\text{Favorite}} - \overline{\text{OVR}}_{\text{Underdog}}$$

* **Heavyweight Clash:** If both rosters carry $\overline{\text{OVR}} \ge 84$, assign a baseline floor of $80$.
* **Underdog Victory:** If the lower-rated roster wins outright:
  $$U = \min(100, 50 + 5 \times |\Delta \text{OVR}|)$$
* **Chalk Victory:** If the favored team wins convincingly:
  $$U = \max(10, 50 - 3 \times |\Delta \text{OVR}|)$$

---

## 3. Evaluation Matrix Example

| Game Matchup | Final Score | $C$ (35%) | $P$ (25%) | $S$ (20%) | $U$ (20%) | **Final Index ($E$)** | Classification |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Team A @ Team B** | 34–31 (OT) | **98** | **91** | **88** | **85** | **91.8** | 🏆 **Game of the Week** |
| **Team C @ Team D** | 16–13 | **88** | **39** | **55** | **60** | **63.5** | Defensive Nailbiter |
| **Team E @ Team F** | 48–14 | **0** | **78** | **70** | **20** | **37.5** | High-Scoring Blowout |
| **Team G @ Team H** | 24–21 | **88** | **65** | **72** | **78** | **77.1** | Contender / Runner-Up |