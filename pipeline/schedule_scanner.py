# @AUDIT-STATUS: PRUNE_FILE (v0)
# @REPLACED-BY: pipeline/save_reader/pc_save_reader.mjs
import cv2
import argparse
import os
import sys
import concurrent.futures
from PIL import Image
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Optional
from db.db_handler import DatabaseHandler
import psycopg

class ScheduledGame(BaseModel):
    week: int = Field(description="The week number (e.g. 1, 2, 3)")
    home_team: str = Field(description="The home team abbreviation/name")
    away_team: str = Field(description="The away team abbreviation/name")
    game_time: Optional[str] = Field(description="The scheduled time of the game (e.g. 'Sun 1:00 PM', 'Mon 8:15 PM')", default=None)
    home_score: Optional[int] = Field(description="The home team's score if the game is already completed, else 0", default=0)
    away_score: Optional[int] = Field(description="The away team's score if the game is already completed, else 0", default=0)
    is_completed: bool = Field(description="True if the UI shows a final score for this game, False if it has not been played yet", default=False)

class ScheduleExtraction(BaseModel):
    games: List[ScheduledGame]

def process_batch(chunk_data):
    i, chunk, api_key = chunk_data
    client = genai.Client(api_key=api_key)
    prompt = (
        "Analyze these sequential frames of a Madden NFL Season Schedule screen. "
        "A user is scrolling through the upcoming weeks. "
        "Extract every single game visible across ALL frames, deduplicating perfectly. "
        "Output the week number, home team, away team, and the scheduled `game_time` (e.g., 'Sun 1:00 PM'). "
        "If the game has already been played and a final score is visible, set `is_completed` to true and extract `home_score` and `away_score`."
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[prompt] + chunk,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ScheduleExtraction,
                temperature=0.1
            )
        )
        ext_data = ScheduleExtraction.model_validate_json(response.text)
        return i, ext_data.games if ext_data else []
    except Exception as e:
        print(f"\nBatch {i+1} API Error: {e}")
        return i, []

def run_schedule_scan(video_path: str, batch_size: int = 10, workers: int = 3):
    print(f"Starting Bulk Schedule Scan on {video_path}...")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video {video_path}")
        sys.exit(1)
        
    extracted_frames = []
    last_saved_gray = None
    frame_idx = 0
    
    print(f"Ripping video with Smart Deduplication (Threshold: 3.0)...")
    while True:
        ret, frame = cap.read()
        if not ret: break
        
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        is_unique = False
        
        if last_saved_gray is None:
            is_unique = True
        else:
            diff = cv2.absdiff(gray, last_saved_gray)
            mean_diff = cv2.mean(diff)[0]
            if mean_diff > 3.0:
                is_unique = True
                
        if is_unique:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            extracted_frames.append(Image.fromarray(rgb_frame))
            last_saved_gray = gray
            
        frame_idx += 1
        if frame_idx % 100 == 0:
            print(f"Processed {frame_idx} frames | Kept: {len(extracted_frames)} unique", end="\r")
        
    cap.release()
    print(f"\nTotal frames extracted: {len(extracted_frames)}")
    
    api_key = os.environ.get("GEMINI_API_KEY")
    db = DatabaseHandler()
    season, _ = db.get_current_league_state()
    
    chunks = [extracted_frames[i:i + batch_size] for i in range(0, len(extracted_frames), batch_size)]
    print(f"Divided into {len(chunks)} API batches. Executing with {workers} concurrent workers...")
    
    total_games_scheduled = 0
    chunk_args = [(i, chunk, api_key) for i, chunk in enumerate(chunks)]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_batch, arg) for arg in chunk_args]
        
        for future in concurrent.futures.as_completed(futures):
            i, games = future.result()
            if games:
                count = len(games)
                total_games_scheduled += count
                print(f"Batch {i+1} success: Extracted {count} games.")
                
                # Standardize team names before Postgres
                for g in games:
                    if g.home_team: g.home_team = g.home_team.capitalize()
                    if g.away_team: g.away_team = g.away_team.capitalize()
                
                with psycopg.connect(db.db_conn_info) as conn:
                    with conn.cursor() as cur:
                        for g in games:
                            home_id = db._get_or_create_team(cur, g.home_team)
                            away_id = db._get_or_create_team(cur, g.away_team)
                            
                            status = 'COMPLETED' if g.is_completed else 'SCHEDULED'
                            h_score = g.home_score if g.home_score is not None else 0
                            a_score = g.away_score if g.away_score is not None else 0
                            
                            cur.execute("""
                                INSERT INTO games (season, week, home_team_id, away_team_id, status, home_score, away_score, game_time)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (season, week, home_team_id, away_team_id) 
                                DO UPDATE SET
                                    status = EXCLUDED.status,
                                    home_score = EXCLUDED.home_score,
                                    away_score = EXCLUDED.away_score,
                                    game_time = COALESCE(EXCLUDED.game_time, games.game_time);
                            """, (season, g.week, home_id, away_id, status, h_score, a_score, g.game_time))
                            
            else:
                print(f"Batch {i+1} returned empty data or failed.")
                
    print(f"\n=================================")
    print(f"Schedule Scan Complete! Total Games Synchronized: {total_games_scheduled}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Madden Schedule Bulk Scanner")
    parser.add_argument("--file", type=str, required=True, help="Path to the MP4 file")
    parser.add_argument("--batch", type=int, default=10, help="Number of images per API call (default: 10)")
    parser.add_argument("--workers", type=int, default=3, help="Number of concurrent API calls (default: 3)")
    args = parser.parse_args()
    
    run_schedule_scan(args.file, args.batch, args.workers)
