# @AUDIT-STATUS: PRUNE_FILE (v0)
# @REPLACED-BY: pipeline/load_save.py
import cv2
import time
import argparse
from pipeline.template_classifier import TemplateClassifier
from pipeline.ocr_extractor import BoxScoreExtractor
from db.db_handler import DatabaseHandler
import psycopg

def get_panthers_schedule(db: DatabaseHandler, season: int | None = None) -> list:
    if season is None:
        season = db.get_current_league_state()[0]
    with psycopg.connect(db.db_conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM teams WHERE name ILIKE '%Panthers%' LIMIT 1")
            panthers_id = cur.fetchone()[0]
            
            cur.execute("""
                SELECT week, id FROM games 
                WHERE (home_team_id = %s OR away_team_id = %s) 
                AND season = %s
                ORDER BY week ASC
            """, (panthers_id, panthers_id, season))
            return cur.fetchall()

def run_historical_batch(video_path: str):
    classifier = TemplateClassifier()
    extractor = BoxScoreExtractor()
    db = DatabaseHandler()
    season = db.get_current_league_state()[0]
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("Failed to open video")
        return
        
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0: fps = 30.0
    
    # We'll sample 1 frame per second to speed up processing
    frames_to_skip = max(1, int(fps))
    
    matchup_counts = {}
    current_state = None
    week_counter = 1
    
    week_frames = {
        "SCHEDULE": [],
        "STATS_PASSING": [],
        "STATS_RUSHING": [],
        "STATS_RECEIVING": []
    }
    
    print("Starting Historical Batch Parsing with Smart Week Detection...")
    
    frame_idx = 0
    while True:
        # Skip frames
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret: break
        
        raw_state, _ = classifier.detect_ui_state(frame)
        
        # If the state changed, log it
        if raw_state != current_state:
            if current_state in week_frames and len(week_frames[current_state]) > 0:
                print(f"[Block {week_counter}] Finished capturing {current_state} ({len(week_frames[current_state])} frames)")
            
            # If we just left STATS_RECEIVING, it means this week's data is fully captured!
            if current_state == "STATS_RECEIVING" and raw_state != "STATS_RECEIVING":
                print(f"\n[Block {week_counter}] Week complete! Sending frames to Gemini...")
                
                all_player_stats = []
                
                for stat_type in ["STATS_PASSING", "STATS_RUSHING", "STATS_RECEIVING"]:
                    frames = week_frames[stat_type]
                    if not frames: continue
                    
                    print(f"Extracting {stat_type}...")
                    from PIL import Image
                    images = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]
                    
                    prompt = (
                        f"Analyze the provided images of Madden NFL player stats for {stat_type.split('_')[1]}. "
                        f"Extract the player statistics without duplicating any players. "
                        f"Output strictly adhering to the JSON schema."
                    )
                    
                    from pipeline.ocr_extractor import BoxScoreExtraction
                    ext = extractor._call_gemini(prompt, images, BoxScoreExtraction)
                    
                    if ext and ext.player_stats:
                        all_player_stats.extend(ext.player_stats)
                        
                if all_player_stats:
                    try:
                        with psycopg.connect(db.db_conn_info) as conn:
                            with conn.cursor() as cur:
                                cur.execute("SELECT id, name FROM teams WHERE name ILIKE '%Panthers%' LIMIT 1")
                                panthers_row = cur.fetchone()
                                panthers_id = panthers_row[0]
                                
                                distinct_team_ids = set()
                                for p in all_player_stats:
                                    if p.team and p.team.strip() != "":
                                        tid = db._get_or_create_team(cur, p.team)
                                        if tid: distinct_team_ids.add(tid)
                                
                                opponent_id = None
                                for tid in distinct_team_ids:
                                    if tid != panthers_id:
                                        opponent_id = tid
                                        break
                                
                                if opponent_id is None:
                                    print("Could not detect opponent from extracted stats! Dropping block.")
                                else:
                                    cur.execute("SELECT name FROM teams WHERE id = %s", (opponent_id,))
                                    opp_name = cur.fetchone()[0]
                                    print(f"Matchup detected! Panthers vs {opp_name}")
                                    
                                    matchup_key = tuple(sorted([panthers_id, opponent_id]))
                                    offset = matchup_counts.get(matchup_key, 0)
                                    
                                    cur.execute("""
                                        SELECT id, week FROM games 
                                        WHERE ((home_team_id = %s AND away_team_id = %s) OR (home_team_id = %s AND away_team_id = %s))
                                        AND season = %s
                                        ORDER BY week ASC
                                        LIMIT 1 OFFSET %s
                                    """, (panthers_id, opponent_id, opponent_id, panthers_id, season, offset))
                                    
                                    res = cur.fetchone()
                                    if res:
                                        game_id, week = res
                                        print(f"Mapped to chronological matchup #{offset + 1} -> Game ID {game_id} (Week {week}). Upserting {len(all_player_stats)} total records...")
                                        db.upsert_player_stats(game_id, all_player_stats)
                                        matchup_counts[matchup_key] = offset + 1
                                    else:
                                        print(f"Error: Could not find Scheduled Game for Panthers vs {opp_name} (chronological occurrence #{offset + 1})")
                    except Exception as e:
                        print(f"DB Error: {e}")
                
                week_frames = {k: [] for k in week_frames}
                week_counter += 1
                print(f"--- Advancing to Block {week_counter} ---\n")
                
            current_state = raw_state
            
        # Collect frames if we are in one of the target states
        if current_state in week_frames:
            week_frames[current_state].append(frame)
            
        frame_idx += frames_to_skip
        
    print("Video processing complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to video file")
    args = parser.parse_args()
    run_historical_batch(args.file)
