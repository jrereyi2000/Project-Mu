# Mathematical Specification: NFL Player Popularity Index ($Pop_{\text{player}}$)

This document formalizes the quantitative architecture for calculating an individual NFL athlete's **Player Popularity Index (**$Pop_{\text{player}} \in [0, 100]$**)**.

The model bridges in-game excitement, individual statistical production, roster pedigree, hardware equity, team success, and positional spotlight.

## 1. Mathematical Architecture

Player popularity is evaluated via six balanced structural components scaled through a positional attention multiplier:

$$
Pop_{\text{player}} = \min\left(100, \Big[ w_F F_{\text{platform}} + w_E E_{\text{exposure}} + w_P P_{\text{performance}} + w_R R_{\text{rating}} + w_A A_{\text{awards}} + w_T T_{\text{success}} \Big] \cdot M_{\text{pos}}\right)
$$

### Component Weights

| Symbol | Component | Weight | Conceptual Role | 
| ----- | ----- | ----- | ----- | 
| $F$ | **Franchise Platform** | **15%** | Market gravity, base franchise reach, national broadcast floor | 
| $E$ | **Game Excitability & Exposure** | **20%** | Involvement in high-drama shootouts and *Game of the Week* contests | 
| $P$ | **On-Field Performance & Spikes** | **20%** | Normalized box-score output, viral ceiling games, big-play volume | 
| $R$ | **Player Baseline Rating (OVR)** | **15%** | Pre-existing scouting pedigree, EA Madden OVR rating | 
| $A$ | **Awards & Accolade Equity** | **15%** | Trailing hardware (MVP, All-Pro, OROY/DROY) with temporal decay | 
| $T$ | **Team Success & Contender Clout** | **15%** | Current winning percentage, division standings, playoff equity | 
| $M_{\text{pos}}$ | **Positional Attention Multiplier** | *Scalar* | Cultural spotlight and commercial bias by position group | 

## 2. Component Sub-Models

### 2.1 Franchise Platform ($F_{\text{platform}}$) — 15% Weight

Leverages the franchise's macro popularity ($POP$) and base market gravity ($B$):

$$
F_{\text{platform}} = 0.65 \cdot POP_{\text{team}} + 0.35 \cdot B_{\text{gravity}}
$$

* Playing for high-gravity organizations (e.g., Cowboys, Steelers, Packers, Eagles) creates an immediate national floor for merchandise distribution and media exposure.

### 2.2 Game Excitability & High-Leverage Exposure ($E_{\text{exposure}}$) — 20% Weight

Integrates the single-game **Excitement Index (**$E_k$**)** and **Game of the Week (GotW)** status across the current season:

$$
E_{\text{exposure}} = \frac{\sum_{k=1}^{T} w_k \cdot \left[ E_k \cdot \text{SnapShare}_{i,k} \cdot \left(1 + 0.25 \cdot \mathbb{I}_{\text{GOTW}, k}\right) \right]}{\sum_{k=1}^{T} w_k}
$$

* $E_k$**:** Single-game Excitement Index ($0 - 100$) for week $k$.

* $\mathbb{I}_{\text{GOTW}, k}$**:** Binary indicator equal to $1$ if game $k$ was the official *Game of the Week*, $0$ otherwise ($+25\%$ broadcast viewership bonus).

* $\text{SnapShare}_{i,k}$**:** Fraction of total offensive or defensive snaps played by the athlete.

* $w_k = e^{-\lambda(T - k)}$**:** Recency decay parameter ($\lambda = 0.12$), giving greater weight to recent thrillers.

### 2.3 On-Field Performance & Spike Games ($P_{\text{performance}}$) — 20% Weight

Rewards sustained production while heavily weighting highlight-reel ceiling games:

$$
P_{\text{performance}} = \left[ 0.60 \cdot \overline{\text{Production}} + 0.40 \cdot \text{SpikeIndex} \right] \cdot \Omega_{\text{discount}}
$$

#### A. Production Score ($\overline{\text{Production}}$)

Percentile-ranked weekly statistical score normalized against positional benchmarks (passing/rushing/receiving EPA, total yards, scrimmage TDs, sacks, turnovers forced).

#### B. Spike Index ($\text{SpikeIndex}$)

Measures frequency of 90th-percentile weekly performances:

* Passing: $350+$ passing yds OR $4+$ total TDs

* Rushing / Receiving: $130+$ scrimmage yds OR $2+$ total TDs

* Defense: $2.5+$ sacks OR $2+$ takeaways OR defensive TD

$$
\text{SpikeIndex} = \min\left(100, \, 25 \cdot N_{\text{spikes}}\right)
$$

#### C. The "Empty Calories" Suppressor ($\Omega_{\text{discount}}$)

To penalize stats accumulated in non-competitive garbage time on losing teams:

$$
\Omega_{\text{discount}} = \begin{cases}  1.00 & \text{if } \text{Win\%}_{\text{team}} > 0.350 \\ 0.85 & \text{if } \text{Win\%}_{\text{team}} \le 0.350  \end{cases}
$$

### 2.4 Baseline Rating & Pedigree ($R_{\text{rating}}$) — 15% Weight

Anchored to pre-game overall ratings (OVR) and blue-chip draft pedigree:

$$
R_{\text{rating}} = \max\left(0, 100 \cdot \frac{\text{OVR} - 65}{99 - 65}\right) + \Delta_{\text{rookie}}
$$

* $\Delta_{\text{rookie}}$**:** Additive curiosity bonus for top-10 draft picks in Years 1–2:
  

  $$
  \Delta_{\text{rookie}} = 0.15 \cdot V_{\text{pick}}(k) \cdot w_{\text{year}}(Y)
  $$

  
  *(Ensures blue-chip rookies like a #1 overall pick carry immediate cultural intrigue before accumulating box-score volume).*

### 2.5 Awards & Accolade Equity ($A_{\text{awards}}$) — 15% Weight

Hardware provides an enduring prestige floor that insulates superstars from minor slump periods:

$$
A_{\text{awards}} = \min\left(100, \, \sum_{y=1}^{3} e^{-0.35(y-1)} A_{\text{points}}(y) + 0.25 \cdot A_{\text{career}}\right)
$$

#### Accolade Point Scale ($A_{\text{points}}$)

| Accolade | Points | Scope | 
| ----- | ----- | ----- | 
| **AP NFL MVP / Super Bowl MVP** | $50.0$ | Apex national status | 
| **AP Offensive/Defensive Player of the Year (OPOY/DPOY)** | $35.0$ | Premier tier accolade | 
| **AP 1st-Team All-Pro** | $30.0$ | True elite recognition | 
| **AP Rookie of the Year (OROY/DROY) / Comeback (CPOY)** | $18.0$ | High-pedigree breakthrough | 
| **AP 2nd-Team All-Pro** | $15.0$ | Near-elite recognition | 
| **Pro Bowl Games Selection** | $8.0$ | Broad consumer awareness floor | 

* $A_{\text{career}}$**:** Cumulative historical career accolade points (un-decayed), weighted at 25% to honor multi-time All-Pros and former MVPs.

### 2.6 Team Success & Contender Clout ($T_{\text{success}}$) — 15% Weight

Playing for a contender amplifies personal brand reach through playoff appearances and primetime television windows:

$$
T_{\text{success}} = 0.60 \cdot S_{\text{current}} + 0.40 \cdot S_{\text{postseason}}
$$

* **Current Regular Season Standing (**$S_{\text{current}}$**):**
  

  $$
  S_{\text{current}} = \min\left(100, \, 100 \cdot \text{Win\%}_{\text{team}} + \mathbb{I}_{\text{DivLeader}} \cdot 10\right)
  $$

* **Postseason Pedigree (**$S_{\text{postseason}}$**):**
  

  $$
  S_{\text{postseason}} = \min\left(100, \, 30 \cdot \text{SB}_{\text{starter}} + 15 \cdot \text{ConfTitles} + 5 \cdot \text{PlayoffWins}_{\text{L3Y}}\right)
  $$

## 3. Positional Attention Multipliers ($M_{\text{pos}}$)

Even with equivalent statistical percentiles and team gravity, skill positions command higher broadcast attention, social impressions, and commercial interest:

| Position Group | Multiplier ($M_{\text{pos}}$) | Primary Cultural Driver | 
| ----- | ----- | ----- | 
| **Tier 1: Apex Spotlight** (QB) | $1.15$ | Primary broadcast focal point; undisputed franchise face | 
| **Tier 2: Highlight Generators** (WR, Hybrid RB) | $1.05$ | Fantasy relevance, viral catches, high-frequency celebration highlights | 
| **Tier 3: Disruptive Playmakers** (EDGE, CB) | $0.90$ | Sacks, strip-sacks, pick-sixes, perimeter island matchups | 
| **Tier 4: Traditional Flex** (TE, Off-Ball LB, Safety) | $0.75$ | Dependent on scheme and specific highlight opportunities | 
| **Tier 5: Trenches & Specialists** (OT, IOL, DT, K/P) | $0.50$ | High purist respect; bounded mass-consumer mindshare | 

## 4. Empirical Archetypes & Calibrations

| Player Profile Archetype | $F$ | $E$ | $P$ | $R$ | $A$ | $T$ | $M_{\text{pos}}$ | Final $Pop_{\text{player}}$ | Structural Archetype Profile | 
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | 
| **Apex Superstar QB** | $92$ | $90$ | $94$ | $98$ | $95$ | $94$ | $1.15$ | **99.5** | Multi-time MVP quarterback leading a #1 seed in weekly shootouts. | 
| **Explosive Skill Star on Contender** | $84$ | $88$ | $92$ | $91$ | $60$ | $88$ | $1.05$ | **88.6** | Pro Bowl WR on an elite team delivering weekly highlight plays. | 
| **Aging All-Pro Veteran** | $80$ | $60$ | $65$ | $82$ | $85$ | $42$ | $1.15$ | **78.4** | Multi-time past MVP quarterback navigating a losing season. | 
| **Small-Market Defensive Monster** | $45$ | $68$ | $96$ | $97$ | $80$ | $50$ | $0.90$ | **64.7** | Reigning DPOY edge rusher on an average small-market roster. | 
| **Empty-Calorie Rookie / Rebuilder** | $38$ | $55$ | $78$ | $74$ | $0$ | $20$ | $1.05$ | **47.1** | High draft-pick WR on a 3–14 team facing the garbage-time penalty. | 
| **Elite All-Pro Tackle / Guard** | $75$ | $72$ | $92$ | $96$ | $75$ | $85$ | $0.50$ | **41.3** | Best offensive lineman in the NFL; capped by positional attention scalar. | 
