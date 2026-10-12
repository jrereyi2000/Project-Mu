# Estimating NFL Seasons Played in Madden (Backward-Looking Algorithm)

When calculating the number of seasons a player has *already* played in Madden Franchise mode, and the exact draft entry age is unknown (but falls within a 20-24 range), we can use a statistical approach. 

Instead of projecting forward, we calculate backward to estimate when they likely entered the league, using their current Overall Rating (OVR) as a proxy for their entry age.

## 1. Establish the Entry Age Distribution

Historically, the distribution of NFL draft entry ages generally follows this curve:
*   **Age 20:** 5% (Rare, early declarations)
*   **Age 21:** 25% (Standard early declarations/juniors)
*   **Age 22:** 45% (Seniors/standard entry)
*   **Age 23:** 20% (Redshirt seniors/COVID years)
*   **Age 24:** 5% (Older prospects/mission trips)

## 2. The OVR Modifier (Survivor Bias Effect)

In Madden, a player's OVR is heavily correlated with their draft pedigree. Elite prospects (1st-round picks) typically enter the league at ages 20-22 and develop into high-OVR players. Lower-OVR players or fringe roster guys are more likely to have been older prospects or undrafted free agents (ages 23-24).

We can adjust the expected entry age based on the player's current OVR tier:
*   **Elite (90+ OVR):** Distribution skews younger. Expected entry age leans toward 21.
*   **Starter (80-89 OVR):** Standard distribution. Expected entry age leans toward 22.
*   **Depth/Fringe (< 80 OVR):** Distribution skews older. Expected entry age leans toward 22.5 to 23.

By weighting these probabilities, we get the following **Weighted Average Entry Ages ($A_e$)**:
*   $A_e$ for $OVR \ge 90$ = $21.3$
*   $A_e$ for $80 \le OVR < 90$ = $21.9$
*   $A_e$ for $OVR < 80$ = $22.4$

## 3. The Formula

To calculate the **Estimated Seasons Played ($P_{est}$)**, simply subtract the weighted average entry age from their current age, and round to the nearest whole number (since partial seasons do not apply here).

$$P_{est} = \text{Round}(Current Age - A_e)$$

## Examples

### Scenario A: 28-year-old QB, 85 OVR
*   **Current Age:** $28$
*   **OVR Tier:** Starter ($80 \le OVR < 90$)
*   **Expected Entry Age ($A_e$):** $21.9$
*   **Calculation:** $28 - 21.9 = 6.1$
*   **Estimated Seasons Played:** **$6$ Seasons**

### Scenario B: 25-year-old QB, 95 OVR
*   **Current Age:** $25$
*   **OVR Tier:** Elite ($OVR \ge 90$)
*   **Expected Entry Age ($A_e$):** $21.3$
*   **Calculation:** $25 - 21.3 = 3.7$
*   **Estimated Seasons Played:** **$4$ Seasons**

*Note: This algorithm accounts for the reality that a 25-year-old 95 OVR superstar was almost certainly an early declare, giving them a slightly longer tenure for their age than an average depth player.*