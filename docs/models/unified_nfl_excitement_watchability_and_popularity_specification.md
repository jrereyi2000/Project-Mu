# Unified Quantitative Framework: NFL Game Excitement, Team Watchability, and Franchise Popularity

This document outlines the end-to-end mathematical model for measuring and ranking consumer entertainment value across the National Football League. The framework translates low-level post-game primitives (box scores, final scores, and player overall ratings) into three hierarchical analytics tiers:

```
Tier 1: Single-Game Excitement Index (E) ──► Game of the Week
                   │
                   ▼
Tier 2: Team Watchability Index (TWI) ──► In-Season Entertainment & Trajectory
                   │
                   ▼
Tier 3: Franchise Popularity Index (POP) ──► Macro Market Relevance & Network Draw
```

---

## 1. Tier 1: Single-Game Excitement Index ($E$)

The **Game Excitement Index ($E$)** scores any played contest on a normalized $0 \text{ to } 100$ scale. The weekly maximum represents the **Game of the Week (GotW)**:

$$\text{GotW} = \arg\max_{g \in \text{Week } t} E_g$$

### 1.1 Master Game Formulation
$$E = 0.35(C) + 0.25(P) + 0.20(S) + 0.20(U)$$

| Component | Weight | Concept Measured |
| :--- | :---: | :--- |
| **$C$ (Competitiveness)** | 35% | Final margin leverage, one-possession status, and overtime drama |
| **$P$ (Production & Pace)** | 25% | Cumulative offensive yardage and scoring volume |
| **$S$ (Star Impact & Outliers)** | 20% | Top-end talent showdown + singular milestone box scores |
| **$U$ (Upset & Matchup Narrative)** | 20% | Pre-game talent parity, heavyweight status, or underdog shocks |

---

### 1.2 Mathematical Sub-Models

#### A. Competitiveness ($C$)
Measures late-game tension and final point margin:
$$C = \min\left(100, \max\left(0, 100 - 4 \cdot |\text{Score}_A - \text{Score}_B|\right) + \text{OT Bonus}\right)$$
* Margin penalties scale linearly up to 25 points ($C \to 0$).
* $\text{OT Bonus} = +10$ points for games decided in overtime.

#### B. Offensive Pace & Production ($P$)
Reflects high-tempo offense, big plays, and score volume:
$$P = \min\left(100, \left(\frac{\text{Points}_A + \text{Points}_B}{70} \times 60\right) + \left(\frac{\text{Yards}_A + \text{Yards}_B}{850} \times 40\right)\right)$$

#### C. Star Impact & Outlier Performances ($S$)
Blends baseline top-tier talent with standout individual box scores:
$$S = 0.5(S_{\text{stars}}) + 0.5(S_{\text{outliers}})$$

* **Star Matchup Baseline ($S_{\text{stars}}$):**
  $$S_{\text{stars}} = \frac{\overline{\text{OVR}}_{\text{Top 3, Team A}} + \overline{\text{OVR}}_{\text{Top 3, Team B}}}{2}$$
* **Outlier Box Score Bonuses ($S_{\text{outliers}}$):** Summed across both teams and capped at 100:
  * Passing: $\ge 400$ yds ($+15$), $\ge 4$ pass TDs ($+15$)
  * Scrimmage: $\ge 150$ yds ($+12$), $\ge 2$ total TDs ($+8$)
  * Defensive/ST: $\ge 3$ sacks ($+15$), $\ge 2$ turnovers forced ($+15$), defensive/ST return TD ($+10$)

#### D. Upset & Narrative Leverage ($U$)
Let $\overline{\text{OVR}}_{\text{Team}}$ denote the average Madden/scouting overall rating of the active 46-man gameday roster, and:
$$\Delta \text{OVR} = \overline{\text{OVR}}_{\text{Favorite}} - \overline{\text{OVR}}_{\text{Underdog}}$$

* **Heavyweight Clash:** If both rosters carry $\overline{\text{OVR}} \ge 84$, $U$ has a baseline floor of $80$.
* **Underdog Victory:** If the lower-rated roster wins outright:
  $$U = \min(100, 50 + 5 \times |\Delta \text{OVR}|)$$
* **Favored Victory (Chalk):**
  $$U = \max(10, 50 - 3 \times |\Delta \text{OVR}|)$$

---

## 2. Tier 2: Team Watchability Index ($TWI$)

The **Team Watchability Index ($TWI$)** isolates how fun a specific team is to watch over time, filtering out opponent-driven noise and factoring in roster age, upside volatility, and draft pedigree.

### 2.1 Master Watchability Formulation
$$TWI = \min\left(100, 0.40(\mu_{\text{adj}}) + 0.20(\sigma_{\text{top}}) + 0.25(P_{\text{draft}}) + 0.15(Y_{\text{prod}}) - \text{Penalty}_{\text{purgatory}}\right)$$

---

### 2.2 Game Attribution & Recency Weighting

#### A. Production-Share Attribution ($\alpha_{A, t}$)
To prevent a boring team from getting credit for a high-scoring game driven entirely by their opponent:
$$\alpha_{A, t} = 0.5 \left( \frac{\text{Points}_A}{\text{Points}_A + \text{Points}_B} \right) + 0.5 \left( \frac{\text{Yards}_A}{\text{Yards}_A + \text{Yards}_B} \right)$$

#### B. Opponent Baseline Correction ($\delta_{\text{opp}, B}$)
Dragging a low-entertainment team into a shootout earns a bonus:
$$\delta_{\text{opp}, B} = 0.10 \left( \overline{TWI}_{\text{League}} - TWI_{B, t-1} \right)$$
$$E_{\text{adj}, A, t} = E_t \cdot \alpha_{A, t} + \delta_{\text{opp}, B}$$

#### C. Exponential Recency Decay ($\mu_{\text{adj}}$)
With decay parameter $\lambda = 0.15$ and current week $T$:
$$w_t = e^{-\lambda(T - t)} \implies \mu_{\text{adj}} = \frac{\sum_{t=1}^T w_t \cdot E_{\text{adj}, A, t}}{\sum_{t=1}^T w_t}$$

---

### 2.3 Ceiling & Chaos Volatility Bonus ($\sigma_{\text{top}}$)
Measures a team's propensity to produce explosive, non-linear entertainment highs:
$$\sigma_{\text{top}} = \sqrt{\frac{1}{K} \sum_{t \mid E_t > M} (E_t - M)^2}$$
Where $M$ is the league median game excitement score.

---

### 2.4 Youth Trajectory & Blue-Chip Draft Pedigree

#### A. Blue-Chip Pedigree Index ($P_{\text{draft}}$)
Quantifies "anticipation equity" (viewers tuning in to watch high-ceiling, unproven talent develop):
$$P_{\text{draft}} = \min\left(100, \sum_{i \in \text{Roster}} \left[ V_{\text{pick}}(k_i) \cdot w_{\text{year}}(Y_i) \cdot M_{\text{pos}}(i) \cdot \text{SnapShare}_i \right]\right)$$

1. **Draft Capital Decay Curve ($V_{\text{pick}}$) by overall pick $k$:**
   $$V_{\text{pick}}(k) = \max\left(0, 100 \cdot e^{-0.045(k - 1)}\right)$$
   *(Pick 1 = 100, Pick 5 ≈ 83, Pick 10 ≈ 67, Pick 32 ≈ 25)*

2. **Window of Curiosity Horizon ($w_{\text{year}}$) by NFL career season $Y$:**
   $$w_{\text{year}}(Y) = \begin{cases} 
   1.00 & Y = 1 \text{ (Rookie curiosity)} \\
   0.75 & Y = 2 \text{ (Sophomore leap)} \\
   0.40 & Y = 3 \text{ (Final evaluation window)} \\
   0.00 & Y \ge 4 \text{ (Absorbed into proven OVR)}
   \end{cases}$$

3. **Positional Entertainment Multipliers ($M_{\text{pos}}$):**
   * **$1.50\times$:** Quarterback (Highest broadcast leverage)
   * **$1.20\times$:** Wide Receiver, Dynamic Edge Rusher
   * **$1.00\times$:** Running Back, Tight End, Cornerback, Offensive Tackle
   * **$0.50\times$:** Interior Offensive Line, Off-Ball Linebacker, Safety

#### B. Under-25 Production Share ($Y_{\text{prod}}$)
Normalized to a 0–100 scale:
$$Y_{\text{prod}} = 100 \times \left( \frac{\text{Scrimmage Yards by Skill Players Aged } \le 24}{\text{Total Scrimmage Yards}} \right)$$

#### C. The "Purgatory Penalty" ($\text{Penalty}_{\text{purgatory}}$)
Severely penalizes old, low-ceiling teams that lack developmental intrigue:
$$\text{Penalty}_{\text{purgatory}} = \max\left(0, (\text{Age}_{\text{snap-weighted}} - 27.5) \times (0.500 - \text{Win\%}) \times 40\right)$$
* Young and losing $\to \text{Penalty} = 0$ (High developmental interest)
* Old and winning $\to \text{Penalty} = 0$ (Title contender chasing rings)
* Old and losing $\to \text{Severe deduction}$ (Aesthetic and narrative television poison)

---

## 3. Tier 3: Franchise Popularity Index ($POP$)

The **Franchise Popularity Index ($POP$)** combines long-term cultural gravity with in-season momentum and on-field watchability to determine total consumer drawing power.

### 3.1 Master Popularity Formulation
$$POP = 0.35(B) + 0.30(H) + 0.35(TWI)$$

| Component | Weight | Definition & Core Drivers |
| :--- | :---: | :--- |
| **$B$ (Base Market Gravity)** | 35% | TV market size (DMA), out-of-market social footprint, jersey sales share |
| **$H$ (Historical Equity)** | 30% | Dual-horizon championship legacy with heavy recent decay |
| **$TWI$ (Real-Time Watchability)** | 35% | Dynamic Tier 2 watchability index capturing casual/neutral fandom |

---

### 3.2 Historical Equity Model ($H$)
Preserves blueblood prestige while reflecting the rapid generational turnover of sports fans:
$$H = 0.70 \left( \sum_{y=1}^5 e^{-0.25(y-1)} S_y \right) + 0.30 \left( S_{\text{all-time}} \right)$$

* **Recent Success ($S_y$):** Weighted points for Super Bowl titles ($100$), Conference titles ($50$), Playoff appearances ($25$), and regular season win percentage over the trailing 5-year window.
* **Legacy Floor ($S_{\text{all-time}}$):** All-time franchise win percentage, total Super Bowl championships, and Hall of Fame alumni share.

---

## 4. End-to-End Classification Taxonomy

| Profile Class | Fanbase ($B$) | Historical ($H$) | Watchability ($TWI$) | Popularity ($POP$) | Strategic Television Strategy |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Apex National Draw** | High | High | High | **85 – 100** | Mandatory A-crew national broadcast / Primetime lock. |
| **Heritage Anchor** | Very High | High | Low | **70 – 84** | High structural ratings floor; network relies on market size. |
| **Bandwagon Breakout** | Moderate | Low | Very High | **70 – 84** | Top flex-scheduling priority; heavy RedZone highlight focus. |
| **Ascending Sandbox** | Low–Mod | Low | High | **55 – 69** | Cult following; heavy social engagement around young stars. |
| **Purgatory Franchise** | Moderate | Low–Mod | Very Low | **35 – 54** | Avoid national windows; unwatchable checkdown football. |
| **Regional Invisible** | Low | Low | Low | **< 35** | Regional 1:00 PM ET coverage only; zero neutral appeal. |