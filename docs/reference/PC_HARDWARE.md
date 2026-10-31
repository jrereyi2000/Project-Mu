# PC Hardware & Small-Form-Factor Transition

Moved out of the PC migration plan on 2026-10-30: hardware notes and the return/rebuild timeline are not migration work.

**Current machine:** MSI Aegis prebuilt from Costco. AMD Ryzen 7 9700X, NVIDIA GeForce RTX 5070 Ti 16GB, 24GB–32GB DDR5, 1TB+ NVMe SSD.  
**Operating system:** Windows 11 for Madden, CFB and mods; WSL2 (Ubuntu) for Project Mu (setup: [`PC_MIGRATION_PLAN.md`](../done/PC_MIGRATION_PLAN.md) §3).

## Day 80 Costco Return / SFF Transition Roadmap (was PC migration §8)

To transition from the temporary Costco prebuilt to your permanent, whisper-quiet Small Form Factor living-room build:

1. **Days 1–75 (Active Operation):**
   * Keep the Costco PC intact in its designated spot on the hardwood floor.
   * Keep the original shipping box and all styrofoam inserts safe in a closet.
   * Run the fresh 2034 franchise's first seasons and refine the save reader scripts.
2. **Days 60–75 (Black Friday / Holiday Sales Monitoring):**
   * Watch for holiday discounts on the four SFF components:
     * **Case:** Fractal Design Terra (Jade or Silver, 10.4L) or Lian Li / DAN A4-H2O (11L)
     * **Motherboard:** ASRock B850I Lightning WiFi (Mini-ITX AM5)
     * **Power Supply:** Corsair SF850 (2024 Platinum SFX)
     * **Cooler:** Noctua NH-L12S (Low-profile) or Cooler Master 240 Atmos
3. **Day 80 (Data Export & System Wipe):**
   * Run `./db/backup_db.sh` to grab the latest database snapshot.
   * Copy `CAREER-*` franchise saves and your Git repo to a flash drive.
   * In Windows 11: Go to **Settings $\rightarrow$ System $\rightarrow$ Recovery $\rightarrow$ "Reset this PC" $\rightarrow$ "Remove everything"**.
4. **Day 82 (Return to Costco):**
   * Repack the MSI Aegis into its original box with included mouse, keyboard, and cables.
   * Walk into any Costco warehouse for a **100% full `$2,200` refund**.
5. **Day 85 (Final Living-Room Deployment):**
   * Assemble the custom SFF PC and place it on the open shelf behind the framed print (or on the media console where the candle sits).
   * Restore the database snapshot. The entire gridiron universe continues without missing a beat.
