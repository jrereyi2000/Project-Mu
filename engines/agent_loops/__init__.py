"""Coach and player agent loops (docs/done/COACH_PLAYER_AGENT_LOOPS_PLAN.md).

  base.py    options, the seeded personality policy, model choice with fallback, the marquee budget, recording
  coach.py   CPU coach close-call charts, promises to unhappy players and promise reviews
  player.py  CPU contract responses (choice among clearing counters), capped leaks, inbox replies

Every decision is a seeded personality choice; the model is asked (to choose among the legal options and give the
words) only where JR or the press will see it. Nothing here changes acceptance, valuation, legality or a chart
algorithm: the loops only choose among options their owning engines already allow.
"""
