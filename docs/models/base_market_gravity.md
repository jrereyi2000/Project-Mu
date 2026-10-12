# NFL Base Market Gravity ($B$) League Scorecard (2021–2026 Window)

Base Market Gravity ($B$) evaluates an NFL franchise's baseline audience floor on a normalized $0 \text{ to } 100$ scale:

$$
B_i = 0.35 \cdot L_{\text{norm}, i} + 0.30 \cdot D_{\text{norm}, i} + 0.35 \cdot N_{\text{norm}, i}
$$

* **$L_{\text{norm}}$**: Effective Local Market Scale (DMA households + secondary regional footprint, adjusted for market co-tenancy via $\gamma = 0.70$)
* **$D_{\text{norm}}$**: Local Market Density & Fervor (sub-.400 season local TV share floor, stadium capacity yield, waitlist elasticity)
* **$N_{\text{norm}}$**: National Reach & Diaspora (out-of-market retail merchandise, streaming package demand, extra-regional digital audience share)

---

## League-Wide Rankings

| Rank | Franchise | Archetype Classification | Local Scale ($L_{\text{norm}}$) | Local Fervor ($D_{\text{norm}}$) | National Reach ($N_{\text{norm}}$) | Base Gravity ($B$) |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: |
| **1** | Dallas Cowboys | National Juggernaut | 75.0 | 82.0 | 100.0 | **85.85** |
| **2** | Philadelphia Eagles | Solo Mega-Market Heavyweight | 64.0 | 78.0 | 72.0 | **71.00** |
| **3** | San Francisco 49ers | Wealth-Center Legacy Brand | 58.0 | 66.0 | 88.0 | **70.90** |
| **4** | Green Bay Packers | Legacy Heritage Diaspora | 24.0 | 98.0 | 94.0 | **70.70** |
| **5** | Pittsburgh Steelers | Industrial Rust-Belt Diaspora | 32.0 | 92.0 | 91.0 | **70.65** |
| **6** | Chicago Bears | Solo Historic Giant | 70.0 | 74.0 | 68.0 | **70.50** |
| **7** | New York Giants | Divided Urban Blueblood | 82.0 | 44.0 | 80.0 | **69.90** |
| **8** | Kansas City Chiefs | Modern Dynasty / Heartland Hub | 26.0 | 95.0 | 86.0 | **67.70** |
| **9** | New England Patriots | Multi-State Regional Blueblood | 62.0 | 60.0 | 76.0 | **66.30** |
| **10** | Denver Broncos | Mountain West Monolith | 44.0 | 84.0 | 64.0 | **63.00** |
| **11** | Washington Commanders | Latent Legacy Power | 66.0 | 46.0 | 65.0 | **59.65** |
| **12** | Buffalo Bills | Concentrated Regional Monolith | 18.0 | 100.0 | 58.0 | **56.60** |
| **13** | Las Vegas Raiders | Trans-Regional Brand Diaspora | 30.0 | 50.0 | 81.0 | **53.85** |
| **14** | Seattle Seahawks | Pacific Northwest Monolith | 48.0 | 72.0 | 44.0 | **53.80** |
| **15** | New York Jets | Co-Tenancy Urban Underdog | 82.0 | 35.0 | 41.0 | **53.55** |
| **16** | Minnesota Vikings | Upper-Midwest Regional Anchor | 42.0 | 75.0 | 46.0 | **53.30** |
| **17** | Detroit Lions | Resurgent Rust-Belt Civic Core | 45.0 | 74.0 | 42.0 | **52.65** |
| **18** | Cleveland Browns | Civic Devotion Stalwart | 35.0 | 86.0 | 38.0 | **51.35** |
| **19** | Baltimore Ravens | Mid-Atlantic Model Franchise | 38.0 | 70.0 | 48.0 | **51.10** |
| **20** | Miami Dolphins | South Florida Destination Brand | 52.0 | 38.0 | 52.0 | **47.80** |
| **21** | New Orleans Saints | Cultural Monolith (Gulf Coast) | 22.0 | 85.0 | 40.0 | **47.20** |
| **22** | Los Angeles Rams | Primary Tenant in Entertainment Mega-Hub | 65.0 | 28.0 | 38.0 | **44.45** |
| **23** | Atlanta Falcons | Large Sunbelt Urban Footprint | 56.0 | 36.0 | 38.0 | **43.70** |
| **24** | Houston Texans | Single-Tenant Industrial Sunbelt | 55.0 | 42.0 | 32.0 | **43.05** |
| **25** | Tampa Bay Buccaneers | Post-Dynasty Sunbelt Retraction | 44.0 | 40.0 | 34.0 | **39.30** |
| **26** | Indianapolis Colts | Central Midwest Regional Anchor | 30.0 | 60.0 | 30.0 | **39.00** |
| **27** | Cincinnati Bengals | Riverfront Contested Territory | 25.0 | 56.0 | 36.0 | **38.15** |
| **28** | Los Angeles Chargers | Diluted Secondary Tenant | 61.0 | 22.0 | 24.0 | **36.35** |
| **29** | Arizona Cardinals | Transplant-Saturated Metro | 46.0 | 26.0 | 26.0 | **33.00** |
| **30** | Tennessee Titans | Mid-South Secondary Hub | 28.0 | 42.0 | 28.0 | **32.20** |
| **31** | Carolina Panthers | Split-Carolinas Regional Network | 38.0 | 34.0 | 24.0 | **31.90** |
| **32** | Jacksonville Jaguars | Small-Market Emerging | 22.0 | 48.0 | 28.0 | **31.90** |

---

## Tier Archetypes

* **Tier 1: Sovereign Anchor ($B \ge 80.0$)**: Dallas Cowboys. Commands unmatched non-local merchandise consumption and baseline national broadcast viewership regardless of win-loss record.
* **Tier 2: Blueblood & National Diaspora Core ($65.0 \le B < 80.0$)**: Philadelphia, San Francisco, Green Bay, Pittsburgh, Chicago, NY Giants, Kansas City, New England. High single-market TV scale or generational national diaspora offsetting smaller geographic borders.
* **Tier 3: Regional Monoliths & Heritage Brands ($50.0 \le B < 65.0$)**: Denver, Washington, Buffalo, Las Vegas, Seattle, NY Jets, Minnesota, Detroit, Cleveland, Baltimore. Deep civic loyalty and stable multi-state broadcast footprints.
* **Tier 4: Contested Sunbelt & Regional Anchors ($40.0 \le B < 50.0$)**: Miami, New Orleans, LA Rams, Atlanta, Houston. Large urban centers facing either high local leisure competition or shared-market cannibalization.
* **Tier 5: Fragile Baseline Floors ($B < 40.0$)**: Tampa Bay, Indianapolis, Cincinnati, LA Chargers, Arizona, Tennessee, Carolina, Jacksonville. Minimal extra-regional pull; highly reliant on immediate on-field contention to drive viewership.