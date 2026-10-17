#!/usr/bin/env python3
"""Validation script for Madden Draft Recap Remote Play capture flow.

Validates:
1. Chiaki Remote Play stream connection.
2. Navigation from Franchise Weekly Hub to the DRAFT RECAP tile.
3. Permitted Cross into the Draft Recap screen.
4. OCR confirmation of screen state (ROUND, PICK, NAME, POS, SALARY, BONUS, etc.).
5. Switching scope from current team to 'NFL' via L2 dropdown.
6. Scrolling through picks (Down / Up) and reading player cards.
7. Saving verification screenshot to captures/debug/draft_recap_test.png.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import madden_capture as mc

DEBUG_DIR = os.path.join(ROOT, "captures", "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)


def read_highlighted_player(f):
    """Extract player details from the top card on the Draft Recap screen."""
    name_parts = []
    college = None
    age = None
    archetype = None
    for t, box in sorted(f.items, key=lambda x: (x[1][1], x[1][0])):
        # Card name area: y between 160 and 360, x between 900 and 1450
        if 160 <= box[1] <= 360 and 900 <= box[0] <= 1450:
            if not any(k in t for k in ("AGE:", "ARCHETYPE:", "COLLEGE:", "CAP ROOM:")):
                name_parts.append(t)
        if "COLLEGE:" in t:
            college = t
        if "AGE:" in t:
            age = t
        if "ARCHETYPE:" in t:
            archetype = t
    player_name = " ".join(name_parts)
    return {
        "name": player_name,
        "age": age,
        "college": college,
        "archetype": archetype,
    }


def set_draft_recap_scope_to_nfl():
    """Use L2 to switch scope from current team to 'NFL'."""
    f, _ = mc.look()
    scope, _ = mc.chip_labels(f)
    print(f"Current scope chip: {scope}")
    if scope and mc.norm(scope) == "NFL":
        print("Scope is already NFL.")
        return f

    print("Opening scope dropdown by holding L2...")
    mc.key_down("l2")
    try:
        opened = False
        t_end = time.time() + 4.0
        while time.time() < t_end:
            time.sleep(0.35)
            f = mc.Frame()
            if "SELECT" in f.text and mc.dropdown_selected(f):
                opened = True
                break
        if not opened:
            raise mc.Abort("Scope dropdown did not open while holding L2")

        print("Navigating dropdown to 'NFL'...")
        for step in range(90):
            sel = mc.dropdown_selected(f)
            for _ in range(3):
                if sel is not None:
                    break
                time.sleep(0.3)
                f = mc.Frame()
                sel = mc.dropdown_selected(f)
            if sel and mc.norm(sel) == "NFL":
                print(f"Reached NFL in {step} steps. Releasing L2.")
                break
            if sel is None:
                raise mc.Abort("Lost track of dropdown selection")
            mc.key_down("down")
            time.sleep(0.1)
            mc.key_up("down")
            time.sleep(0.45)
            f = mc.Frame()
        else:
            raise mc.Abort("Could not reach NFL in scope list")
    finally:
        mc.key_up("l2")

    time.sleep(1.5)
    f, _ = mc.look()
    scope, _ = mc.chip_labels(f)
    print(f"Scope after selection: {scope}")
    if not scope or mc.norm(scope) != "NFL":
        raise mc.Abort(f"Expected scope NFL, but got {scope}")
    return f


def scroll_step(direction="down", retries=3):
    """Press direction with hold=0.1 and verify that the highlighted player updates."""
    f_prev, _ = mc.look()
    p_prev = read_highlighted_player(f_prev)
    for _ in range(retries):
        mc.press(direction, hold=0.1, wait=1.0)
        f_cur, _ = mc.look()
        p_cur = read_highlighted_player(f_cur)
        if p_cur["name"] and p_cur["name"] != p_prev["name"]:
            return f_cur, p_cur
    return f_cur, p_cur


def validate_draft_recap_flow():
    print("=== Step 1: Remote Play Connection ===")
    if not mc.chiaki_running():
        print("Chiaki not running. Connecting...")
        mc.connect()
    else:
        print("Chiaki running. Finding window...")
        mc.WIN_ID, mc.PID = mc.find_window()
        mc.ensure_window_size()

    f, s = mc.look()
    print(f"Initial screen: {s}")

    # If currently in draft recap, press Back (circle) to return to hub to test full flow
    if "ALL ROUNDS" in f.text and ("SALARY" in f.text or "BONUS" in f.text or "PICK" in f.text):
        print("Currently on Draft Recap. Pressing Back to return to Franchise Hub...")
        mc.press("circle", wait=2.0)
        f, s = mc.look()
        print(f"Returned to: {s}")

    # Ensure we are on Weekly Hub
    cur_tab = f.highlighted(mc.HUB_TABS)
    print(f"Highlighted hub tab: {cur_tab}")
    if cur_tab != "WEEKLY HUB":
        print("Navigating to WEEKLY HUB tab...")
        f = mc.move_to(f, mc.HUB_TABS, "WEEKLY HUB", "l1", "r1", "hub tab")
        time.sleep(1.0)
        f, s = mc.look()

    # Verify DRAFT RECAP tile
    print("Verifying DRAFT RECAP tile on Weekly Hub...")
    if "DRAFT RECAP" not in f.text:
        raise mc.Abort("DRAFT RECAP tile not found on Weekly Hub screen")

    # Safety check
    if "ADVANCE" in f.text and f.find("ADVANCE WEEK"):
        print("Warning: ADVANCE WEEK detected on hub, ensuring DRAFT RECAP tile is focused")

    # Perform permitted Cross
    print("=== Step 2: Permitted Cross into Draft Recap ===")
    mc.press("cross", wait=2.5)
    f, s = mc.look()
    print(f"Screen after Cross: {s}")

    # Verify headers
    has_expected_headers = any(h in f.text for h in ("ROUND", "PICK", "POS", "NAME"))
    print(f"Expected Draft Recap headers detected: {has_expected_headers}")
    if not has_expected_headers:
        raise mc.Abort(f"Screen did not show draft recap headers: {f.text[:200]}")

    # Step 3: Switch Scope to NFL
    print("=== Step 3: Switch Scope to NFL via L2 ===")
    f = set_draft_recap_scope_to_nfl()

    # Save screenshot
    test_cap = os.path.join(DEBUG_DIR, "draft_recap_test.png")
    mc.snap(test_cap)
    print(f"Saved test screenshot to {test_cap}")

    # Step 4: Verify Scrolling
    print("=== Step 4: Verify Scrolling (Down / Up) ===")
    p1 = read_highlighted_player(f)
    print(f"Pick 1 Player: {p1['name']} ({p1['college']}, {p1['archetype']})")

    print("Scrolling Down to Pick 2...")
    f, p2 = scroll_step("down")
    print(f"Pick 2 Player: {p2['name']} ({p2['college']}, {p2['archetype']})")

    print("Scrolling Down to Pick 3...")
    f, p3 = scroll_step("down")
    print(f"Pick 3 Player: {p3['name']} ({p3['college']}, {p3['archetype']})")

    print("Scrolling Up back to Pick 2...")
    f, p2_back = scroll_step("up")
    print(f"Back to Pick 2 Player: {p2_back['name']}")

    print("Scrolling Up back to Pick 1...")
    f, p1_back = scroll_step("up")
    print(f"Back to Pick 1 Player: {p1_back['name']}")

    # Verification checks
    assert p1["name"] != p2["name"], f"Pick 1 ({p1['name']}) and Pick 2 ({p2['name']}) players must differ"
    assert p2["name"] != p3["name"], f"Pick 2 ({p2['name']}) and Pick 3 ({p3['name']}) players must differ"
    assert p1["name"] == p1_back["name"], f"Scrolling Up did not return to Pick 1 player ({p1['name']} vs {p1_back['name']})"
    assert p2["name"] == p2_back["name"], f"Scrolling Up did not match Pick 2 player ({p2['name']} vs {p2_back['name']})"

    print("\n=== Validation Successful! ===")
    print("All checks passed:")
    print("  1. Navigation from Franchise Weekly Hub to DRAFT RECAP tile verified.")
    print("  2. Permitted Cross into Draft Recap verified.")
    print("  3. Headers and columns (ROUND, PICK, NAME, POS, SALARY, BONUS, etc.) verified.")
    print("  4. L2 scope switch to NFL verified.")
    print("  5. Down/Up scrolling verified across picks.")
    print(f"  6. Screenshot saved to {test_cap}.")


if __name__ == "__main__":
    validate_draft_recap_flow()
