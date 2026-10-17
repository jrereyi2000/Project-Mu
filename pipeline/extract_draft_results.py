"""Draft Recap extraction for Madden completed draft results screenshots.

Extracts draft picks across all 7 rounds from paged screenshots (draft_results/page_*.png)
or row-by-row frames (draft_results/pick_*.png) into draft_results.csv and draft_results.json.

Uses local Apple Vision OCR on macOS for speed and accuracy, with Gemini Vision fallback
when Apple Vision is not available. Overlapping rows between pages are stitched with
character-level OCR tolerance (_close) to form a contiguous 1-to-N draft order (224 picks
in a standard 7-round NFL draft). Draft picks are enriched with:
- overall_pick, round, round_pick
- team and team_id (from draft order engine)
- full_name, college, age, archetype (matched from draft_prospects board)
"""

import csv
import glob
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import Quartz
    import Vision
    from Foundation import NSURL
    from PIL import Image
    HAS_APPLE_VISION = True
except ImportError:
    HAS_APPLE_VISION = False

from pipeline.capture_matching import name_key

HOMOGLYPHS = str.maketrans(
    "АВСЕНІЈКМОРЅТХУаеорсухіјΑΒΕΗΙΚΜΝΟΡΤΧΥΖ",
    "ABCEHIJKMOPSTXYaeopcyxijABEHIKMNOPTXYZ"
)

OUT_COLS = [
    "overall_pick",
    "round",
    "round_pick",
    "team",
    "player_name",
    "position",
    "college",
    "age",
    "archetype",
]


def _close(a: str, b: str) -> bool:
    """Names one OCR slip apart: same length and at most one different letter,
    or the same once dots and spaces are dropped."""
    a, b = a.upper(), b.upper()
    if a.replace(".", "").replace(" ", "") == b.replace(".", "").replace(" ", ""):
        return True
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 1


def _ocr_apple_vision(path: str) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """Run Apple Vision OCR on an image and return list of (text, (x0, y0, x1, y1))."""
    if not HAS_APPLE_VISION:
        raise RuntimeError("Apple Vision framework not available.")
    url = NSURL.fileURLWithPath_(path)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    success, err = handler.performRequests_error_([req], None)
    if not success or err:
        raise RuntimeError(f"Vision OCR request failed: {err}")

    img = Image.open(path)
    W, H = img.size
    tokens = []
    for obs in req.results() or []:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        text = str(candidates[0].string()).translate(HOMOGLYPHS).upper()
        bb = obs.boundingBox()
        x0 = bb.origin.x * W
        y0 = (1.0 - bb.origin.y - bb.size.height) * H
        x1 = x0 + bb.size.width * W
        y1 = y0 + bb.size.height * H
        tokens.append((text, (x0, y0, x1, y1)))
    return tokens


def find_draft_recap_frames(root: str) -> List[str]:
    """Find draft recap page frames in draft_results/."""
    rdir = os.path.join(root, "draft_results")
    if not os.path.isdir(rdir):
        return []
    paged = sorted(glob.glob(os.path.join(rdir, "page_*.png")))
    if paged:
        return paged
    return sorted(glob.glob(os.path.join(rdir, "pick_*.png")))


def parse_page_rows_ocr(path: str) -> List[Dict[str, Any]]:
    """Parse table rows from a draft recap paged screenshot using Apple Vision OCR."""
    items = _ocr_apple_vision(path)
    # The table names sit at x in [100, 450], y in [740, 1680]
    names = [it for it in items if 100 <= it[1][0] <= 450 and 740 <= it[1][1] <= 1680]
    names.sort(key=lambda x: x[1][1])
    rows = []
    for n_text, (nx0, ny0, nx1, ny1) in names:
        row_items = [it for it in items if abs(it[1][1] - ny0) < 32]
        pos = next((it[0] for it in row_items if 580 <= it[1][0] <= 720), "")
        rnd = next((it[0] for it in row_items if 820 <= it[1][0] <= 940), "")
        pick = next((it[0] for it in row_items if 1000 <= it[1][0] <= 1120), "")
        ovr = next((it[0] for it in row_items if 1800 <= it[1][0] <= 1920), "")
        rows.append({
            "name": n_text.strip(),
            "position": pos.strip(),
            "round": rnd.strip(),
            "pick": pick.strip(),
            "ovr": ovr.strip(),
            "y": ny0,
        })
    return rows


def stitch_pages(pages_rows: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Stitch overlapping pages of draft recap rows into a contiguous draft sequence."""
    if not pages_rows:
        return []
    combined = list(pages_rows[0])

    def close_keys(k1: str, k2: str) -> bool:
        return k1 == k2 or _close(k1, k2)

    for idx in range(1, len(pages_rows)):
        curr = pages_rows[idx]
        matched_k = None
        for k in range(min(len(combined), len(curr)), 0, -1):
            tail = [name_key(r["name"]) for r in combined[-k:]]
            head = [name_key(r["name"]) for r in curr[:k]]
            if all(close_keys(t, h) for t, h in zip(tail, head)):
                matched_k = k
                break
        if matched_k is not None:
            combined.extend(curr[matched_k:])
        else:
            # Fallback: check match with distance tolerance or append
            best_overlap = 0
            best_score = 0
            for k in range(min(len(combined), len(curr)), 0, -1):
                tail = [name_key(r["name"]) for r in combined[-k:]]
                head = [name_key(r["name"]) for r in curr[:k]]
                score = sum(1 for t, h in zip(tail, head) if close_keys(t, h))
                if score / k >= 0.7 and score > best_score:
                    best_score = score
                    best_overlap = k
            if best_overlap > 0:
                combined.extend(curr[best_overlap:])
            else:
                combined.extend(curr)
    return combined


def load_db_context() -> Tuple[int, Dict[int, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Load current draft season, round 1 draft order teams, and draft prospects from DB."""
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    try:
        import psycopg
        from engines.draft_order import compute_draft_order, fetch_games, fetch_teams

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_season FROM league_state WHERE id = 1")
                row = cur.fetchone()
                season = row[0] if row else 2035

                teams = fetch_teams(cur)
                games = fetch_games(cur, season)
                order_res = compute_draft_order(teams, games)
                team_by_pick = {item["pick"]: item for item in order_res.get("round_1_order", [])}

                # Prospects could be under season or season + 1 (upcoming class)
                cur.execute(
                    "SELECT id, full_name, position, college, age, ovr, archetype "
                    "FROM draft_prospects WHERE draft_season = %s",
                    (season,),
                )
                prospects = cur.fetchall()
                if not prospects:
                    cur.execute(
                        "SELECT id, full_name, position, college, age, ovr, archetype "
                        "FROM draft_prospects WHERE draft_season = %s",
                        (season + 1,),
                    )
                    prospects = cur.fetchall()

        prospects_by_namekey: Dict[str, List[Dict[str, Any]]] = {}
        for p in prospects:
            pid, full_name, pos, college, age, ovr, archetype = p
            k = name_key(full_name)
            prospects_by_namekey.setdefault(k, []).append({
                "id": pid,
                "full_name": full_name,
                "position": pos,
                "college": college,
                "age": age,
                "ovr": ovr,
                "archetype": archetype,
            })

        return season, team_by_pick, prospects_by_namekey
    except Exception as e:
        print(f"  Note: could not load database draft context ({e}); proceeding with screen data only")
        return 2035, {}, {}


def extract_draft_results(root: str) -> Optional[str]:
    """Extract draft results from captures/<run>/draft_results/ into draft_results.csv & json."""
    csv_path = os.path.join(root, "draft_results.csv")
    json_path = os.path.join(root, "draft_results.json")
    if os.path.exists(csv_path) and os.path.exists(json_path):
        return csv_path

    frames = find_draft_recap_frames(root)
    if not frames:
        return None

    is_paged = any(os.path.basename(f).startswith("page_") for f in frames)
    season, team_by_pick, prospects_by_namekey = load_db_context()

    records: List[Dict[str, Any]] = []

    if is_paged:
        print(f"Extracting draft results from {len(frames)} paged screenshot(s)...")
        pages_rows = []
        for f in frames:
            try:
                rows = parse_page_rows_ocr(f)
                pages_rows.append(rows)
            except Exception as e:
                print(f"  Warning: OCR failed on {os.path.basename(f)}: {e}")
                pages_rows.append([])

        stitched = stitch_pages(pages_rows)
        print(f"Stitched {len(stitched)} draft pick rows.")

        for idx, r in enumerate(stitched, start=1):
            overall_pick = idx
            curr_round = ((overall_pick - 1) // 32) + 1
            curr_round_pick = ((overall_pick - 1) % 32) + 1
            tinfo = team_by_pick.get(curr_round_pick, {})
            team_str = tinfo.get("abbreviation") or tinfo.get("team") or ""

            raw_name = r["name"]
            nk = name_key(raw_name)
            p_cands = prospects_by_namekey.get(nk)
            if not p_cands:
                close_cands = [
                    c for k, c_list in prospects_by_namekey.items()
                    if _close(nk, k) for c in c_list
                ]
                if close_cands:
                    p_cands = close_cands

            p_info = p_cands[0] if p_cands else {}

            records.append({
                "overall_pick": overall_pick,
                "round": curr_round,
                "round_pick": curr_round_pick,
                "team": team_str,
                "team_id": tinfo.get("team_id"),
                "player_name": p_info.get("full_name") or raw_name,
                "position": p_info.get("position") or r.get("position") or "",
                "college": p_info.get("college"),
                "age": p_info.get("age"),
                "archetype": p_info.get("archetype"),
            })
    else:
        # Row-by-row mode frames (pick_001.png, etc.)
        from madden_capture import Frame, read_draft_recap_card
        print(f"Extracting draft results from {len(frames)} row-by-row screenshot(s)...")
        for idx, fpath in enumerate(frames, start=1):
            f_card = Frame(fpath)
            card_info = read_draft_recap_card(f_card)
            curr_round = ((idx - 1) // 32) + 1
            curr_round_pick = ((idx - 1) % 32) + 1
            tinfo = team_by_pick.get(curr_round_pick, {})
            team_str = tinfo.get("abbreviation") or tinfo.get("team") or ""
            records.append({
                "overall_pick": idx,
                "round": curr_round,
                "round_pick": curr_round_pick,
                "team": team_str,
                "team_id": tinfo.get("team_id"),
                "player_name": card_info.get("name", ""),
                "position": card_info.get("position", ""),
                "college": card_info.get("college"),
                "age": card_info.get("age"),
                "archetype": card_info.get("archetype"),
            })

    # Write draft_results.csv and draft_results.json
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)

    print(f"Extracted {len(records)} draft results to {csv_path} and {json_path}.")
    return csv_path
