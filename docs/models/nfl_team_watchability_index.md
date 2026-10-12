# Modeling the NFL Team Watchability Index (TWI)

This framework scales the single-game **Excitement Index ($E$)** across a full schedule to calculate a dynamic, rolling measure of how entertaining a specific team is to watch over the course of a season.

---

## 1. The Co-Dependency Problem

Taking a simple arithmetic mean of a team's game excitement scores ($\frac{1}{N} \sum E_i$) introduces a major methodological flaw: **every game is a joint event between two rosters**.

A slow, run-heavy squad that plays three consecutive shootouts against fast-paced, high-powered opponents will receive an artificially inflated score. A valid seasonal metric must isolate a team's **independent entertainment contribution** from opponent-driven context.

---

## 2. Mathematical Architecture

A team's **Team Watchability Index ($TWI$)** at week $T$ is computed through a three-stage adjustment pipeline:

```
Raw Game Scores (E) 
       │
       ▼
1. Attribution & Opponent Adjustment (E_adj)
       │
       ▼
2. Recency-Decayed Temporal Weighting (μ_adj)
       │
       ▼
3. Ceiling & Chaos Volatility Bonus (σ_top)
       │
       ▼
Final Team Watchability Index (TWI)
```

---

## 3. Core Adjustments

### 3.1 Game-Level Attribution ($E_{\text{adj}}$)
Instead of assigning the full game index $E$ to both participants, credit is distributed based on offensive agency and opponent strength.

For Team $A$ facing Team $B$ in week $t$:

$$E_{\text{adj}, A, t} = E_t \cdot \alpha_{A, t} + \delta_{\text{opp}, B}$$

#### Production Share Multiplier ($\alpha_{A, t}$)
Calibrated using Team $A$'s share of the offensive output that generated the game's production component:

$$\alpha_{A, t} = 0.5 \left( \frac{\text{Points}_A}{\text{Points}_A + \text{Points}_B} \right) + 0.5 \left( \frac{\text{Yards}_A}{\text{Yards}_A + \text{Yards}_B} \right)$$

* A team that loses $42–7$ in a high-scoring game receives minimal pace credit.
* A team driving a balanced $34–31$ thriller earns roughly $\alpha \approx 0.50$.

#### Opponent Baseline Correction ($\delta_{\text{opp}, B}$)
Adjusts for the baseline watchability of the opposing squad:

$$\delta_{\text{opp}, B} = \gamma \left( \overline{TWI}_{\text{League}} - TWI_{B, t-1} \right)$$

* Dragging a notoriously boring team ($\text{low } TWI_B$) into an exciting game yields a net positive bonus.
* Merely keeping pace with an already electric offense yields a slight negative adjustment.
* Recommended scale factor: $\gamma \approx 0.10$.

---

### 3.2 Recency Weighting via Exponential Decay
Rosters and play-calling identities change rapidly across an NFL schedule due to injuries, schematic adaptations, and quarterback changes. To reflect current watchability at week $T$, earlier games are decayed exponentially:

$$w_t = e^{-\lambda (T - t)}$$

Where:
* $t \in [1, T]$ is the game week.
* $\lambda$ is the decay parameter (calibrated at $\lambda = 0.15$).

The recency-adjusted mean watchability $\mu_{\text{adj}}$ is computed as:

$$\mu_{\text{adj}} = \frac{\sum_{t=1}^{T} w_t \cdot E_{\text{adj}, A, t}}{\sum_{t=1}^{T} w_t}$$

---

### 3.3 Ceiling & Chaos Volatility Bonus ($\sigma_{\text{top}}$)
Entertainment value is non-linear: fans prefer teams with explosive, unpredictable ceilings over teams that produce predictable, average games.

* A team consistently generating games around $60$ is predictable and methodical.
* A team alternating between $40$ (sluggish losses) and $95$ (wild shootouts) provides appointment viewing.

We isolate upside variance by taking the semi-deviation of games that scored above the median performance threshold ($M$):

$$\sigma_{\text{top}} = \sqrt{\frac{1}{K} \sum_{t \mid E_t > M} (E_t - M)^2}$$

---

## 4. Final Watchability Formula

The final index combines the weighted mean with the ceiling variance bonus:

$$TWI = \min\left(100, \mu_{\text{adj}} + \beta \cdot \sigma_{\text{top}}\right)$$

Where $\beta = 0.20$ governs the appetite for volatility over floor stability.

---

## 5. Team Classification Archetypes

| Archetype | Mean Score ($\mu_{\text{adj}}$) | Ceiling Bonus ($\sigma_{\text{top}}$) | TWI Band | Typical Profile |
| :--- | :--- | :--- | :--- | :--- |
| **Must-Watch Tier** | High ($\ge 75$) | High | **85 – 100** | High-tempo passing offenses, aggressive 4th-down tendencies, shaky coverage secondaries leading to continuous shootouts. |
| **Chaotic Underdogs** | Moderate ($55 - 70$) | Very High | **70 – 84** | High-turnover offenses with elite game-breaking playmakers; liable to get blown out or produce shocking comebacks. |
| **Methodical Contenders** | Moderate ($60 - 72$) | Low | **60 – 72** | High-efficiency teams that control time of possession, build steady leads, and salt away fourth quarters without drama. |
| **RedZone Fade** | Low ($< 50$) | Low | **$< 50$** | Run-heavy, clock-draining offenses paired with conservative bend-don't-break defenses; low turnover rates, low explosive play frequency. |