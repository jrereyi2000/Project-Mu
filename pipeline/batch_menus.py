import argparse
import cv2
import numpy as np
import os
import shutil
from typing import List, Optional
from pydantic import BaseModel, Field
import psycopg
from pipeline.ocr_extractor import BoxScoreExtractor
from db.db_handler import DatabaseHandler
from PIL import Image

# --- SCHEMAS ---
class TeamRating(BaseModel):
    team_name: str = Field(description="The name of the NFL team (e.g., Panthers, Chiefs)")
    team_ovr: Optional[int] = Field(default=None)
    off_ovr: Optional[int] = Field(default=None)
    def_ovr: Optional[int] = Field(default=None)
    pass_off_rank: Optional[int] = Field(default=None)
    rush_off_rank: Optional[int] = Field(default=None)
    pass_rush_rank: Optional[int] = Field(default=None)
    coverage_rank: Optional[int] = Field(default=None)
    prev_wins: Optional[int] = Field(description="The number of wins the team had in the previous season (last year)", default=None)
    prev_losses: Optional[int] = Field(description="The number of losses the team had in the previous season (last year)", default=None)

class TeamRatingsExtraction(BaseModel):
    teams: List[TeamRating]

class AwardWinner(BaseModel):
    award_type: str = Field(description="The acronym of the award (MVP, COTY, OPOY, DPOY, OROY, DROY, SBMVP)")
    player_name: Optional[str] = Field(description="The full name of the player. If this is COTY, put the coach name here.", default=None)
    position: Optional[str] = Field(description="The position of the player (e.g. QB, WR, HB, MLB). Leave blank for COTY.", default=None)
    team_name: Optional[str] = Field(description="The name of the NFL team the winner plays for.", default=None)

class SuperBowlResult(BaseModel):
    winner_team: str = Field(description="The name of the team that won the Super Bowl")
    loser_team: str = Field(description="The name of the team that lost the Super Bowl")
    winner_score: int = Field(description="The final score of the winning team")
    loser_score: int = Field(description="The final score of the losing team")

class HistoricalYearRecap(BaseModel):
    season: int = Field(description="The NFL season year (e.g. 2024, 2033)")
    awards: List[AwardWinner]
    superbowl: Optional[SuperBowlResult] = Field(default=None)

class AwardsExtraction(BaseModel):
    years: List[HistoricalYearRecap]

# --- CORE LOGIC ---
def mse(imageA, imageB):
    err = np.sum((imageA.astype("float") - imageB.astype("float")) ** 2)
    err /= float(imageA.shape[0] * imageA.shape[1])
    return err

def extract_unique_frames(video_path: str, mse_threshold: float = 1000.0) -> list:
    print(f"Opening video: {video_path}")
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0: fps = 30
    
    frames_to_skip = 10
    
    unique_frames = []
    last_saved_frame = None
    
    frame_count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        if frame_count % frames_to_skip == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
            
            if last_saved_frame is None:
                unique_frames.append(frame)
                last_saved_frame = gray
                print(f"Captured initial frame at {frame_count/fps:.1f}s")
            else:
                error = mse(gray, last_saved_frame)
                if error > mse_threshold:
                    unique_frames.append(frame)
                    last_saved_frame = gray
                    print(f"Captured unique frame at {frame_count/fps:.1f}s (MSE: {error:.2f})")
                    
        frame_count += 1
        
    cap.release()
    print(f"Finished parsing video. Found {len(unique_frames)} unique frames.")
    return unique_frames

def process_ratings(db, ext: TeamRatingsExtraction):
    print(f"\nSuccessfully extracted {len(ext.teams)} teams! Saving to database...")
    with psycopg.connect(db.db_conn_info) as conn:
        with conn.cursor() as cur:
            for t in ext.teams:
                team_id = db._get_or_create_team(cur, t.team_name)
                if not team_id:
                    print(f"Could not resolve team: {t.team_name}")
                    continue
                    
                cur.execute("""
                    UPDATE team_seasons 
                    SET team_ovr = %s, off_ovr = %s, def_ovr = %s,
                        pass_off_rank = %s, rush_off_rank = %s, 
                        pass_rush_rank = %s, coverage_rank = %s
                    WHERE team_id = %s AND season = 2035
                """, (
                    t.team_ovr, t.off_ovr, t.def_ovr,
                    t.pass_off_rank, t.rush_off_rank, t.pass_rush_rank, t.coverage_rank,
                    team_id
                ))
                
                if t.prev_wins is not None and t.prev_losses is not None:
                    cur.execute("SELECT id FROM team_seasons WHERE team_id = %s AND season = 2034", (team_id,))
                    if cur.fetchone():
                        cur.execute("UPDATE team_seasons SET wins = %s, losses = %s WHERE team_id = %s AND season = 2034", (t.prev_wins, t.prev_losses, team_id))
                    else:
                        cur.execute("INSERT INTO team_seasons (team_id, season, wins, losses) VALUES (%s, 2034, %s, %s)", (team_id, t.prev_wins, t.prev_losses))
                        
                print(f"Updated {t.team_name} (including 2034 record)")
        conn.commit()
    print("Database updated!")

def process_awards(db, ext: AwardsExtraction):
    print(f"\nSuccessfully extracted {len(ext.years)} historical years! Saving to database...")
    with psycopg.connect(db.db_conn_info) as conn:
        with conn.cursor() as cur:
            for y in ext.years:
                season = y.season
                print(f"--- Processing Season {season} ---")
                
                # Process Super Bowl into games table!
                if y.superbowl:
                    sb = y.superbowl
                    win_id = db._get_or_create_team(cur, sb.winner_team)
                    lose_id = db._get_or_create_team(cur, sb.loser_team)
                    
                    if win_id and lose_id:
                        # Check if this exact Super Bowl already exists
                        cur.execute("SELECT id FROM games WHERE season = %s AND game_type = 'SUPER_BOWL'", (season,))
                        row = cur.fetchone()
                        
                        if row:
                            # Update existing Super Bowl
                            cur.execute("""
                                UPDATE games 
                                SET home_team_id = %s, away_team_id = %s, 
                                    home_score = %s, away_score = %s, status = 'COMPLETED'
                                WHERE id = %s
                            """, (win_id, lose_id, sb.winner_score, sb.loser_score, row[0]))
                        else:
                            # Insert new Super Bowl (Arbitrarily mapping Winner to Home and Loser to Away)
                            # We set week to 22 which is standard Madden Super Bowl week
                            cur.execute("""
                                INSERT INTO games (season, week, home_team_id, away_team_id, home_score, away_score, status, game_type)
                                VALUES (%s, 22, %s, %s, %s, %s, 'COMPLETED', 'SUPER_BOWL')
                            """, (season, win_id, lose_id, sb.winner_score, sb.loser_score))
                            
                        print(f"Saved SUPER_BOWL to games: {sb.winner_team} ({sb.winner_score}) def {sb.loser_team} ({sb.loser_score})")
                        
                for a in y.awards:
                    team_id = db._get_or_create_team(cur, a.team_name) if a.team_name else None
                    
                    if a.award_type.upper() == "COTY":
                        cur.execute("""
                            INSERT INTO awards (season, award_type, coach_name, team_id)
                            VALUES (%s, 'COTY', %s, %s)
                            ON CONFLICT (season, award_type) DO UPDATE SET
                                coach_name = EXCLUDED.coach_name,
                                team_id = EXCLUDED.team_id;
                        """, (season, a.player_name, team_id))
                        print(f"Saved COTY: {a.player_name} ({a.team_name})")
                    else:
                        player_id = None
                        if a.player_name:
                            cur.execute("SELECT id FROM players WHERE full_name = %s OR player_name = %s LIMIT 1", (a.player_name, a.player_name))
                            row = cur.fetchone()
                            if row: player_id = row[0]
                            
                        cur.execute("""
                            INSERT INTO awards (season, award_type, player_name, player_id, team_id, position)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (season, award_type) DO UPDATE SET
                                player_name = EXCLUDED.player_name,
                                player_id = EXCLUDED.player_id,
                                team_id = EXCLUDED.team_id,
                                position = EXCLUDED.position;
                        """, (season, a.award_type.upper(), a.player_name, player_id, team_id, a.position))
                        print(f"Saved {a.award_type.upper()}: {a.player_name} ({a.position}, {a.team_name})")
        conn.commit()
    print("Database updated!")

def main():
    parser = argparse.ArgumentParser(description="Extract data from Madden menus.")
    parser.add_argument("--video", required=True, help="Path to the video file")
    parser.add_argument("--mode", choices=["ratings", "awards"], required=True, help="What data to extract")
    parser.add_argument("--local", action="store_true", help="Save unique frames locally instead of sending to Gemini")
    parser.add_argument("--threshold", type=float, default=500.0, help="MSE Threshold for detecting unique frames")
    args = parser.parse_args()
    
    frames = extract_unique_frames(args.video, args.threshold)
    
    if args.local:
        output_dir = "data/frames/unique_frames"
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir)
        for i, frame in enumerate(frames):
            cv2.imwrite(os.path.join(output_dir, f"frame_{i:03d}.jpg"), frame)
        print(f"\n[LOCAL MODE] Saved {len(frames)} frames to ./{output_dir}/")
        return
        
    print(f"\n[GEMINI MODE] Packaging frames for LLM Extraction ({args.mode})...")
    extractor = BoxScoreExtractor()
    images = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]
    
    db = DatabaseHandler()
    
    if args.mode == "ratings":
        prompt = (
            "Analyze these screenshots of Madden NFL team screens. Extract the Team OVR, OFF OVR, DEF OVR, "
            "their league-wide rankings for Pass Offense, Rush Offense, Pass Rush, and Coverage, and their 2034 (last year's) win-loss record. "
            "Return the data strictly in the JSON schema provided."
        )
        ext = extractor._call_gemini(prompt, images, TeamRatingsExtraction)
        if ext: process_ratings(db, ext)
        
    elif args.mode == "awards":
        prompt = (
            "Analyze these screenshots of Madden NFL historical season recaps. For each year shown, extract the Season Year, "
            "the Super Bowl winner, loser, and final score. Also extract the winners for MVP, COTY, OPOY, DPOY, OROY, DROY, and SBMVP (Super Bowl MVP). "
            "Extract the full name, position, and team for each award winner (for COTY, put the coach name in player_name and leave position blank). "
            "Return the data strictly in the JSON schema provided."
        )
        ext = extractor._call_gemini(prompt, images, AwardsExtraction)
        if ext: process_awards(db, ext)

if __name__ == "__main__":
    main()
