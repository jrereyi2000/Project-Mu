import psycopg
import json
import argparse
import time
import os
import cv2
from PIL import Image
from google import genai
from google.genai import types

def get_missing_players(db_conn):
    with psycopg.connect(db_conn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT p.player_name
                FROM players p
                LEFT JOIN player_seasons ps ON p.id = ps.player_id
                WHERE p.height IS NULL OR p.weight IS NULL OR ps.years_of_experience IS NULL
            """)
            return [row[0] for row in cur.fetchall()]

def extract_frames(video_path, target_fps=4):
    print(f"Extracting frames locally using OpenCV at {target_fps} FPS...")
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0: fps = 30
    frame_interval = max(1, int(fps / target_fps))
    
    images = []
    count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        if count % frame_interval == 0:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_frame)
            # Resize slightly to save bandwidth on the upload payload
            pil_img.thumbnail((1280, 1280))
            images.append(pil_img)
        count += 1
    cap.release()
    print(f"Successfully extracted {len(images)} high-quality frames!")
    return images

def heal_roster(video_path, file_uri, db_conn):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set!")
        return
        
    client = genai.Client(api_key=api_key)

    missing_names = get_missing_players(db_conn)
    if not missing_names:
        print("No players are missing full names or teams!")
        return

    payload_contents = []

    if file_uri:
        print(f"Found {len(missing_names)} missing players. Reusing existing Gemini file: {file_uri}")
        video_file = client.files.get(name=file_uri)
        payload_contents.append(video_file)
        frames_to_process = [payload_contents] # Single batch
    elif video_path:
        print(f"Found {len(missing_names)} missing players.")
        frames = extract_frames(video_path, target_fps=3) # Reduced to 3fps
        
        # Batch frames into chunks of 250 to avoid 1GB payload limits
        batch_size = 250
        frames_to_process = []
        for i in range(0, len(frames), batch_size):
            frames_to_process.append(frames[i:i+batch_size])
    else:
        print("Error: You must provide either --video or --file-uri")
        return
        
    print(f"\nPayload ready! Processing in {len(frames_to_process)} batches using Gemini 3.8 Flash...")
    
    prompt = f"""
    Look at these sequential frames of a Madden NFL menu/roster.
    PROCESS THE FRAMES METICULOUSLY. The scrolling is fast, and you must check every single image.
    
    I have a specific list of players who are missing their full name or their team in my database.
    
    Here are the specific shorthand names of the players to look for:
    {missing_names}
    
    As you look at these frames, whenever you see one of these players, extract their information.
    Return a JSON array of objects. Use these exact keys:
    - "player_name": The exact shorthand name from the list above (e.g. "P. Mahomes")
    - "full_name": Their complete full name as shown on the screen
    - "team_name": The name of the team they play for (e.g. "Chiefs", "49ers"). Capitalize the first letter only.
    - "position": Their position (e.g. "QB", "WR")
    - "age": Their age as an integer
    - "rating": Their overall rating as an integer
    - "height": Their height as a string (e.g. "6'2\"")
    - "weight": Their weight as an integer
    - "years_of_experience": Their experience as an integer
    
    Ensure the JSON array is wrapped in ```json ... ``` blocks.
    Only extract information for the players in the list!
    """
    
    all_extracted_data = []
    
    for idx, batch in enumerate(frames_to_process):
        print(f"Unleashing Gemini on batch {idx+1}/{len(frames_to_process)}...")
        try:
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=batch + [prompt]
            )
            
            json_str = response.text
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]
                
            batch_data = json.loads(json_str.strip())
            print(f"  -> Extracted {len(batch_data)} players in this batch.")
            all_extracted_data.extend(batch_data)
        except Exception as e:
            print(f"  -> Failed on batch {idx+1}: {e}")

    print(f"\nTotal extraction complete! Gemini found {len(all_extracted_data)} total instances.")
    
    # Apply to DB
    try:
        with psycopg.connect(db_conn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_season FROM league_state LIMIT 1;")
                season = cur.fetchone()[0]
                
                updated_count = 0
                for item in all_extracted_data:
                    p_name = item.get("player_name")
                    f_name = item.get("full_name")
                    t_name = item.get("team_name")
                    pos = item.get("position")
                    age = item.get("age")
                    rating = item.get("rating")
                    
                    if not p_name: continue
                    
                    # USER OVERRIDE: ONLY UPDATE HEIGHT AND WEIGHT FOR MATCHED PLAYERS
                    if item.get("height"):
                        cur.execute("UPDATE players SET height = %s WHERE player_name = %s AND height IS NULL", (item.get("height"), p_name))
                        
                    if item.get("weight"):
                        cur.execute("UPDATE players SET weight = %s WHERE player_name = %s AND weight IS NULL", (item.get("weight"), p_name))
                        
                    if item.get("years_of_experience") is not None:
                        cur.execute("""
                            UPDATE player_seasons SET years_of_experience = %s 
                            FROM players p WHERE player_seasons.player_id = p.id AND p.player_name = %s AND player_seasons.season = %s AND player_seasons.years_of_experience IS NULL
                        """, (item.get("years_of_experience"), p_name, season))
                    updated_count += 1
            conn.commit()
            print(f"Successfully applied {updated_count} updates to the database!")
            
    except Exception as e:
        print(f"Failed to apply DB updates: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Heal Missing Players via Video")
    parser.add_argument("--video", type=str, help="Path to the roster video file")
    parser.add_argument("--file-uri", type=str, help="Reuse an existing Gemini file URI (e.g. files/a1gh0xwpqi51)")
    args = parser.parse_args()
    
    DB_CONN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    heal_roster(args.video, args.file_uri, DB_CONN)
