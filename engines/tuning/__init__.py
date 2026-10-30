"""
Tuning against real NFL seasons (docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md §2.4).

  targets.py   real per-season targets from nflverse (2015-2025): leaders, percentiles, league and team totals,
               averaged across seasons with the min and max as the band -> data/tuning/targets_2015_2025.json
  compare.py   the same shapes from a pc_save_reader snapshot's season, scored against the targets' bands
"""
