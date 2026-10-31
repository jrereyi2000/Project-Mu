"""verify-mods check: is the first-down line the colour the installed mod says?

This is the example check (plan section 2.3), for JR's `Mu_YellowFirstDown` mod
(docs/reference/MOD_BUILDING.md). With the mod in the pack, the line should be yellow.

What it needs before it can pass or fail:
1. A catalogued screen where the line shows: Play Now or practice, pre-snap, with the broadcast
   camera. No such screen is in tools/madden_pilot/catalog.json yet. Reaching one also needs
   MAIN_MENU, which is still todo.
2. A route to it in the catalog's exits (main menu > Play Now > team picks > kickoff > pre-snap)
   that confirms only on catalogued labels.
3. A measurement on the captured PNG. In the field band, take the share of line-shaped pixels
   that are yellow (hue about 50-65 degrees, high saturation) and compare it with the default
   colour. Pass when yellow clearly wins, fail when the default wins, unknown otherwise.

Until 1 and 2 exist, it presses nothing and returns status "unknown".
"""

NEEDS = ["a catalogued pre-snap screen (Play Now or practice) where the first-down line shows",
         "a catalog route from MAIN_MENU to it",
         "MAIN_MENU itself catalogued (it is todo)"]


def run(pilot):
    return {"status": "unknown",
            "why": "the screen where the first-down line shows isn't catalogued yet; nothing was pressed",
            "needs": NEEDS}
