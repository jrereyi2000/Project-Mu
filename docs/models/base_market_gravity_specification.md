# Mathematical Specification: NFL Franchise Base Market Gravity ($B$)

This document outlines the formal quantitative specification for measuring an NFL franchise's **Base Market Gravity ($B$)** on a standardized $0 \text{ to } 100$ scale. 

Base Market Gravity quantifies an organization's structural audience floor: the baseline television viewership, commercial pull, and national brand attention a franchise commands independent of current-season win-loss performance.

---

## 1. Mathematical Architecture

Base Market Gravity balances geographic market scale, home-market penetration/loyalty, and extra-regional brand dispersion:

$$
B = 0.35 \cdot L + 0.30 \cdot D + 0.35 \cdot N
$$

Where:
* $L \in [0, 100]$: **Effective Local Market Scale** (DMA population adjusted for market co-tenancy)
* $D \in [0, 100]$: **Local Market Density & Fervor** (Concentration of attention within the home footprint)
* $N \in [0, 100]$: **National Reach & Diaspora** (Out-of-market consumer demand and cultural footprint)

```
                     Base Market Gravity (B)
                      [0.35L + 0.30D + 0.35N]
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
Effective Scale (L)     Local Fervor (D)       National Reach (N)
• DMA TV Households     • Losing-Season Share   • Out-of-Market Merch
• Cannibalization Pen.  • Capacity Utilization • OOM Streaming Demand
• Regional Footprint    • Waitlist Elasticity  • National Social Share
```

---

## 2. Component Sub-Models

### 2.1 Effective Local Market Scale ($L$)

Raw Designated Market Area (DMA) household numbers overestimate pull in shared markets and underestimate franchises that command entire states or multi-state regions.

$$
L_i = 100 \cdot \left[ \frac{\text{HH}_i + \sum_{k} \omega_k \cdot \text{AdjHH}_{i,k}}{\text{DMA}_{\max}} \right] \cdot \frac{1}{(C_i)^\gamma}
$$

#### Variables & Parameters:
* $\text{HH}_i$: Primary home market television households (Nielsen DMA).
* $\text{AdjHH}_{i,k}$: Households in secondary adjacent non-home DMAs where the franchise holds exclusive regional broadcast priority (e.g., Green Bay commanding the Milwaukee DMA).
* $\omega_k \in (0, 1]$: Secondary market proximity weight (default $\omega_k = 0.75$).
* $\text{DMA}_{\max}$: The highest media market baseline in the league (New York DMA $\approx 7.5\text{M}$ households).
* $C_i$: Market co-tenancy count:
  * $C_i = 1$ for single-tenant markets (e.g., Philadelphia, Chicago, Denver).
  * $C_i = 2$ for shared markets (NY Giants/Jets, LA Rams/Chargers).
* $\gamma$: Non-linear market cannibalization exponent ($\gamma = 0.70$). 
  * *Note:* A shared market is not a pure 50% split ($C=2, \gamma=1.0$), because shared multi-team mega-markets provide cross-promotional infrastructure and larger secondary retail footprints. With $\gamma = 0.70$, dividing by $2^{0.70} \approx 1.624$ penalizes dilution without over-penalizing the gross scale of top-2 markets.

---

### 2.2 Local Market Density & Fervor ($D$)

Captures the degree of civic capture and relative importance of the franchise within its own borders. This metric isolates structural loyalty from "fair-weather" performance by sampling multi-year losing seasons.

$$
D_i = 100 \cdot \left[ 0.60 \cdot \left(\frac{\overline{\text{Share}}_{\text{floor}, i}}{\text{Share}_{\max}}\right) + 0.40 \cdot \left(0.70 \cdot \frac{\text{Attn}_i}{\text{Cap}_i} + 0.30 \cdot W_i\right) \right]
$$

#### Variables & Parameters:
* $\overline{\text{Share}}_{\text{floor}, i}$: The franchise's average local Nielsen television share (percentage of active televisions in use tuned to the broadcast) computed strictly across seasons where the franchise finished with a winning percentage $\le 0.400$ over the trailing 10-year window.
* $\text{Share}_{\max}$: Upper-bound local share benchmark ($\text{Share}_{\max} = 55.0$). Small, single-focus markets (Buffalo, Green Bay, Kansas City) approach this threshold, while fractured, entertainment-saturated mega-markets (New York, Los Angeles, Miami) floor out significantly lower.
* $\frac{\text{Attn}_i}{\text{Cap}_i}$: Paid attendance percentage relative to certified stadium capacity across home dates.
* $W_i$: Scaled Season-Ticket Waitlist Elasticity Index ($W_i \in [0, 1]$), reflecting ticket scarcity and generational demand independent of on-field record.

---

### 2.3 National Reach & Diaspora ($N$)

Measures the geographic dispersion of the fanbase outside the primary and secondary broadcast radiuses ($> 150$ miles from the team's home venue).

$$
N_i = 100 \cdot \left[ 0.40 \cdot S_{\text{merch}, i} + 0.35 \cdot S_{\text{stream}, i} + 0.25 \cdot S_{\text{social}, i} \right]
$$

#### Sub-Metrics:
* $S_{\text{merch}, i}$: Team share of total non-local licensed retail merchandise volume:
  $$
  S_{\text{merch}, i} = \frac{\text{Retail Units Sold Outside Home State}}{\text{League Total Non-Local Units}}
  $$
* $S_{\text{stream}, i}$: Out-of-market direct-to-consumer streaming subscription preference (e.g., share of primary out-of-market out-of-region package selections like NFL Sunday Ticket/YouTube TV primary team selections).
* $S_{\text{social}, i}$: Percentage of active digital and social engagement (video views, impressions, interaction volume) originating from users residing outside the local territory.

---

## 3. Standardization and Normalization Pipeline

To eliminate unit disparities and preserve relational distances, raw inputs are transformed via min-max scaling before computing $B$:

$$
X_{\text{norm}, i} = \frac{X_i - \min_{j \in [1, 32]}(X_j)}{\max_{j \in [1, 32]}(X_j) - \min_{j \in [1, 32]}(X_j)} \times 100
$$

Where $X \in \{L, D, N\}$. The final output $B_i$ is clamped:

$$
B_i = \max\left(0, \min\left(100, 0.35 L_{\text{norm}, i} + 0.30 D_{\text{norm}, i} + 0.35 N_{\text{norm}, i}\right)\right)
$$

---

## 4. Empirical Benchmarks & Archetype Distribution

The table below illustrates the model's behavior across representative franchises covering every structural archetype in the NFL ecosystem:

| Franchise | Archetype Classification | Local Scale ($L$) | Local Fervor ($D$) | National Reach ($N$) | **Final Gravity ($B$)** | Structural Driver |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **Dallas Cowboys** | National Juggernaut | $75$ | $82$ | $100$ | **$85.9$** | Unrivaled extra-regional footprint & merchandise share |
| **Philadelphia Eagles** | Solo Mega-Market Heavyweight | $64$ | $78$ | $72$ | **$71.0$** | High single-occupant DMA combined with deep civic fervor |
| **Green Bay Packers** | Legacy Heritage Diaspora | $24$ | $98$ | $94$ | **$70.7$** | Small DMA ceiling offset by saturated share & global diaspora |
| **Pittsburgh Steelers** | Industrial Rust-Belt Diaspora | $32$ | $92$ | $91$ | **$70.7$** | Decades of demographic migration creating nationwide fan pockets |
| **New York Giants** | Divided Urban Blueblood | $82$ | $44$ | $80$ | **$69.9$** | Massive baseline DMA pool diluted by market co-tenancy |
| **Buffalo Bills** | Concentrated Regional Monolith | $18$ | $100$ | $58$ | **$56.6$** | Maximum local rating share; constrained national scale |
| **Los Angeles Chargers** | Diluted Secondary Tenant | $61$ | $22$ | $24$ | **$36.4$** | Large DMA penalized by co-tenancy, low share, and zero diaspora |
| **Jacksonville Jaguars** | Small-Market Emerging | $22$ | $48$ | $28$ | **$31.9$** | Small municipal footprint, low out-of-market demand |

---

## 5. Architectural Properties & Temporal Stability

1. **Invariance to Short-Term Variance:** Because inputs like stadium waitlists, multi-year losing-season ratings floors, and non-local merchandise consumption shift over decades rather than weeks, $B$ behaves as a low-frequency anchor metric.
2. **Decoupling Floor from Ceiling:** A team with $B \ge 80$ (e.g., Dallas) will generate high broadcast ratings even during non-competitive periods, whereas a team with $B \le 35$ requires high in-season **Watchability ($TWI$)** and playoff contention to capture neutral television audiences.