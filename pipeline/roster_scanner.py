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

class RosterPlayer(BaseModel):
    team_name: str = Field(description="Team name or abbreviation")
    player_name: str = Field(description="MUST be FirstInitial. LastName (e.g. P. Mahomes) to match box scores")
    full_name: Optional[str] = Field(description="The full name of the player if they are currently highlighted in the UI, else null")
    position: str = Field(description="The player's position (e.g. QB, HB, WR)")
    age: int = Field(description="The player's age")
    overall_rating: int = Field(description="The player's overall rating (OVR)", default=0)
    height: Optional[str] = Field(description="The player's height (e.g. 6'2\" or 6-2)")
    weight: Optional[int] = Field(description="The player's weight in lbs")
    years_of_experience: Optional[int] = Field(description="Years of experience (e.g. 0 for Rookie, 5 for veteran)")

class RosterExtraction(BaseModel):
    players: List[RosterPlayer]

def process_batch(chunk_data):
    i, chunk, api_key = chunk_data
    client = genai.Client(api_key=api_key)
    prompt = (
        "Analyze these sequential frames of a Madden NFL Roster screen. "
        "For EACH image, ONLY extract the information for the SINGLE player that is currently highlighted. "
        "The team name is located in the top left corner (e.g., PATRIOTS). "
        "The highlighted row on the left shows their abbreviated name, POS, AGE, and OVR. The large player card on the right shows their Height, Weight, and Experience. "
        "The large player card on the right shows their full name (e.g. COLE DALTON). "
        "Do NOT extract any of the un-highlighted players in the background list. "
        "You MUST return exactly ONE player per image provided. "
        "Format `player_name` strictly as 'FirstInitial. LastName' (e.g. 'C. Dalton'), and `full_name` as their complete name."
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[prompt] + chunk,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RosterExtraction,
                temperature=0.1
            )
        )
        ext_data = RosterExtraction.model_validate_json(response.text)
        return i, ext_data.players if ext_data else []
    except Exception as e:
        print(f"\nBatch {i+1} API Error: {e}")
        return i, []

def run_roster_scan(video_path: str, batch_size: int = 10, workers: int = 3):
    print(f"Starting Bulk Roster Scan on {video_path}...")
    
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
    
    chunks = [extracted_frames[i:i + batch_size] for i in range(0, len(extracted_frames), batch_size)]
    print(f"Divided into {len(chunks)} API batches. Executing with {workers} concurrent workers...")
    
    total_players_extracted = 0
    chunk_args = [(i, chunk, api_key) for i, chunk in enumerate(chunks)]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_batch, arg) for arg in chunk_args]
        
        for future in concurrent.futures.as_completed(futures):
            i, players = future.result()
            if players:
                count = len(players)
                total_players_extracted += count
                print(f"Batch {i+1} success: Extracted {count} players.")
                
                # Standardize team names to match INITCAP in the database
                for p in players:
                    if p.team_name:
                        p.team_name = p.team_name.capitalize()
                        
                # Instantly UPSERT to Database in main thread to avoid DB locking
                db.upsert_roster(players, update_only=True)
            else:
                print(f"Batch {i+1} returned empty data or failed.")
            
    print(f"\n=================================")
    print(f"Scan Complete! Total Players UPSERTED: {total_players_extracted}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Madden Roster Bulk Scanner")
    parser.add_argument("--file", type=str, required=True, help="Path to the MP4 file of you scrolling rosters")
    parser.add_argument("--batch", type=int, default=10, help="Number of images per API call (default: 10)")
    parser.add_argument("--workers", type=int, default=3, help="Number of concurrent API calls (default: 3)")
    args = parser.parse_args()
    
    run_roster_scan(args.file, args.batch, args.workers)
