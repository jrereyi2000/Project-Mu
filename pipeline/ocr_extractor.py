import cv2
import os
import json
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from google import genai
from google.genai import types
from PIL import Image

# ---------------------------------------------------------
# Define the Pydantic models for structured Output (Phase 2)
# ---------------------------------------------------------

class PlayerStat(BaseModel):
    player_name: str = Field(description="The name of the player. MUST be standardized exactly to 'FirstInitial. LastName' (e.g., 'G. Crawford' or 'C. Stroud').")
    team: str = Field(description="The player's team name or abbreviation")
    position: str = Field(description="The player's position (e.g., QB, HB, WR). If not visible, guess based on stats or leave blank.", default="")
    completions: int = Field(description="Total passes completed", default=0)
    attempts: int = Field(description="Total passes attempted", default=0)
    passing_yards: int = Field(description="Total passing yards", default=0)
    passing_touchdowns: int = Field(description="Total passing touchdowns", default=0)
    interceptions: int = Field(description="Total interceptions thrown", default=0)
    carries: int = Field(description="Total rushing attempts (carries)", default=0)
    rushing_yards: int = Field(description="Total rushing yards", default=0)
    rushing_touchdowns: int = Field(description="Total rushing touchdowns", default=0)
    fumbles: int = Field(description="Total fumbles lost or kept", default=0)
    receptions: int = Field(description="Total receptions", default=0)
    receiving_yards: int = Field(description="Total receiving yards", default=0)
    receiving_tds: int = Field(description="Total receiving touchdowns", default=0)

class ScheduleGame(BaseModel):
    away_team: str = Field(description="Away team name")
    home_team: str = Field(description="Home team name")
    away_score: int = Field(description="Away team score. 0 if unplayed.")
    home_score: int = Field(description="Home team score. 0 if unplayed.")
    status: str = Field(description="Must be 'COMPLETED' if there are scores or 'Final' is visible. Otherwise 'SCHEDULED'.", default="SCHEDULED")

class ScheduleExtraction(BaseModel):
    season: int = Field(description="The NFL season year (e.g. 2035)", default=2035)
    week: int = Field(description="The week of the NFL schedule shown (e.g. 1-18)")
    games: List[ScheduleGame]

class BoxScoreExtraction(BaseModel):
    player_stats: List[PlayerStat]

class GameMeta(BaseModel):
    home_team: str = Field(description="Home team name or abbreviation")
    home_score: int = Field(description="Home team's final score")
    away_team: str = Field(description="Away team name or abbreviation")
    away_score: int = Field(description="Away team's final score")

class TeamStat(BaseModel):
    team_name: str = Field(description="Team name or abbreviation")
    score: int = Field(description="Total score")
    first_downs: int = Field(default=0)
    total_offense: int = Field(default=0)
    total_plays: int = Field(default=0)
    rushing_attempts: int = Field(default=0)
    rushing_yards: int = Field(default=0)
    rushing_tds: int = Field(default=0)
    passing_completions: int = Field(default=0)
    passing_attempts: int = Field(default=0)
    passing_yards: int = Field(default=0)
    passing_tds: int = Field(default=0)
    third_down_attempts: int = Field(default=0)
    third_down_conversions: int = Field(default=0)
    fourth_down_attempts: int = Field(default=0)
    fourth_down_conversions: int = Field(default=0)
    turnovers: int = Field(default=0)

class TeamStatsExtraction(BaseModel):
    game_meta: GameMeta
    team_stats: List[TeamStat]

class RosterPlayer(BaseModel):
    team_name: str = Field(description="Team name or abbreviation")
    player_full_name: str = Field(description="The full name of the player")
    position: str = Field(description="The player's position (e.g. QB, HB, WR)")
    age: int = Field(description="The player's age")
    overall_rating: int = Field(description="The player's overall rating (OVR)", default=0)

class RosterExtraction(BaseModel):
    players: List[RosterPlayer]


# ---------------------------------------------------------
# Extractor Engine
# ---------------------------------------------------------
class BoxScoreExtractor:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key must be provided or set in GEMINI_API_KEY environment variable.")
        self.client = genai.Client(api_key=self.api_key)
        
        # Single-Frame Trackers
        self.parsed_passing = False
        self.parsed_rushing = False
        
        # Generalized Scrolling State Tracker (Dynamic Batching)
        self.active_scroll_state = None
        self.frames_in_scroll = 0
        self.scroll_frames = []
        self.last_seen_frame = None

    def _call_gemini(self, prompt: str, images: list, schema: type) -> Optional[BaseModel]:
        try:
            response = self.client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[prompt] + images,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1
                )
            )
            return schema.model_validate_json(response.text)
        except Exception as e:
            print(f"[API Worker] Error during VLM extraction: {e}")
            return None

    # --- Job Execution Methods (Called by Thread C) ---
    def execute_job(self, job: Dict[str, Any]) -> dict:
        jtype = job["type"]
        frames = job["frames"]
        
        if jtype == "single_player":
            category = job["category"]
            print(f"[API Worker] Executing {category} extraction...")
            img = Image.fromarray(cv2.cvtColor(frames[0], cv2.COLOR_BGR2RGB))
            prompt = (
                f"Analyze the provided image of a Madden NFL player stats screen for {category}. "
                "Extract the player statistics. Output the results strictly adhering to the provided JSON schema. "
                "If a specific stat isn't visible, infer 0 for numbers or 'N/A' for strings."
            )
            ext = self._call_gemini(prompt, [img], BoxScoreExtraction)
            
            if ext and ext.player_stats and len(ext.player_stats) > 0 and ext.player_stats[0].player_name not in ["", "N/A"]:
                return {"type": "player_stats", "data": ext, "category": category, "success": True}
            else:
                return {"type": "player_stats", "data": None, "category": category, "success": False}
                
        elif jtype.startswith("batch_"):
            scroll_type = jtype.replace("batch_", "")
            print(f"[API Worker] Executing dynamic batch extraction for {scroll_type.upper()} ({len(frames)} images)...")
            
            # Convert all dynamic frames into PIL images
            images = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]
            
            if scroll_type == "team_stats":
                prompt = (
                    "Analyze the provided images of a Madden NFL Team Stats screen. "
                    "The images show progressive screenshots of a scrolling list. "
                    "Extract the game metadata (teams and total scores) and the detailed team statistics for both teams. "
                    "Combine the stats from ALL images into a single complete list. "
                    "Output the results strictly adhering to the provided JSON schema."
                )
                ext = self._call_gemini(prompt, images, TeamStatsExtraction)
                return {"type": "team_stats", "data": ext, "success": True}
                
            elif scroll_type.startswith("leaderboard"):
                category = scroll_type.split("_")[1].capitalize()
                prompt = (
                    f"Analyze the provided images of a Madden NFL Season Stats Leaderboard for {category}. "
                    "The images show progressive screenshots of a scrolling list. "
                    "Combine the players from ALL images and extract their cumulative season statistics without duplicating any players. "
                    "Make absolutely sure to explicitly extract the player's Position (e.g. QB, WR, TE) from the screen. "
                    "Output the results strictly adhering to the provided JSON schema. "
                    "If a specific stat isn't visible, infer 0."
                )
                ext = self._call_gemini(prompt, images, BoxScoreExtraction)
                return {"type": "season_stats", "data": ext, "success": True}
                
            elif scroll_type == "stats_receiving":
                prompt = (
                    "Analyze the provided images of a Madden NFL player stats screen for Receiving. "
                    "The images show progressive screenshots of a scrolling list. "
                    "Combine the players from ALL images and extract their statistics without duplicating any players. "
                    "Output the results strictly adhering to the provided JSON schema."
                )
                ext = self._call_gemini(prompt, images, BoxScoreExtraction)
                return {"type": "player_stats", "data": ext, "success": True}
                
            elif scroll_type == "roster":
                prompt = (
                    "Analyze the provided images of a Madden NFL Team Roster screen. "
                    "The images show progressive screenshots of a scrolling list. "
                    "Combine the players from ALL images and extract their roster information without duplicating any players. "
                    "Output the results strictly adhering to the provided JSON schema. "
                    "If overall_rating is not visible, infer 0."
                )
                ext = self._call_gemini(prompt, images, RosterExtraction)
                return {"type": "roster", "data": ext, "success": True}
            elif scroll_type == "schedule":
                prompt = (
                    "Analyze the provided images of a Madden NFL weekly schedule screen. "
                    "The images show progressive screenshots of a scrolling list. "
                    "Combine the games from ALL images and extract their teams and scores without duplicating any games. "
                    "Explicitly note the season and week shown on the screen. "
                    "If a game has no scores or hasn't been played, set scores to 0 and status to 'SCHEDULED'. "
                    "If it has scores, set status to 'COMPLETED'. "
                    "Output the results strictly adhering to the provided JSON schema."
                )
                ext = self._call_gemini(prompt, images, ScheduleExtraction)
                return {"type": "schedule", "data": ext, "success": True}
            return {"success": False, "error": "Unknown batch type"}

            
        return {"type": None, "data": None, "success": False}

    # --- State Tracking Methods (Called by Thread B at 1 FPS) ---
    def process_frame(self, frame, detected_state: str) -> Optional[Dict[str, Any]]:
        job_to_queue = None
        
        scrollable_states = [
            "STATS_RECEIVING", "TEAM_STATS", 
            "LEADERBOARD_PASSING", "LEADERBOARD_RUSHING", "LEADERBOARD_RECEIVING",
            "ROSTER", "SCHEDULE"
        ]

        # 1. Handle Exits (Flush active scroll state if we left it)
        if self.active_scroll_state and detected_state != self.active_scroll_state:
            print(f"[Tracker] Exited {self.active_scroll_state}. Queuing dynamic batch job...")
            if len(self.scroll_frames) > 0:
                # Always append the very last frame seen before exiting to catch the bottom!
                if self.last_seen_frame is not None:
                    self.scroll_frames.append(self.last_seen_frame)
                    
                job_to_queue = {
                    "type": f"batch_{self.active_scroll_state.lower()}", 
                    "frames": self.scroll_frames
                }
            self.active_scroll_state = None
            self.scroll_frames = []
            self.last_seen_frame = None

        if job_to_queue:
            return job_to_queue

        # 2. Handle Entries/Continues
        if detected_state == "STATS_PASSING":
            if not self.parsed_passing:
                print("[Tracker] Detected PASSING tab. Queuing single job...")
                self.parsed_passing = True
                return {"type": "single_player", "category": "Passing", "frames": [frame.copy()]}

        elif detected_state == "STATS_RUSHING":
            if not self.parsed_rushing:
                print("[Tracker] Detected RUSHING tab. Queuing single job...")
                self.parsed_rushing = True
                return {"type": "single_player", "category": "Rushing", "frames": [frame.copy()]}

        elif detected_state in scrollable_states:
            if self.active_scroll_state != detected_state:
                print(f"[Tracker] Entered {detected_state}. Waiting to stabilize...")
                self.active_scroll_state = detected_state
                self.frames_in_scroll = 0
                self.scroll_frames = []
                self.last_seen_frame = None
                
            self.frames_in_scroll += 1
            
            # Debounce: start capturing on the 2nd second to avoid UI animation
            if self.frames_in_scroll >= 2:
                # Capture every 3rd second (e.g. 2, 5, 8, 11...)
                if (self.frames_in_scroll - 2) % 3 == 0:
                    print(f"[Tracker] Captured {detected_state} frame chunk (Scroll duration: {self.frames_in_scroll}s)")
                    self.scroll_frames.append(frame.copy())
                self.last_seen_frame = frame.copy()
                
        return None

    def flush(self) -> Optional[Dict[str, Any]]:
        if self.active_scroll_state:
            print(f"[Tracker] Forced Flush of {self.active_scroll_state}. Queuing dynamic batch job...")
            if len(self.scroll_frames) > 0:
                if self.last_seen_frame is not None:
                    self.scroll_frames.append(self.last_seen_frame)
                job = {
                    "type": f"batch_{self.active_scroll_state.lower()}", 
                    "frames": self.scroll_frames
                }
                self.active_scroll_state = None
                self.scroll_frames = []
                self.last_seen_frame = None
                return job
        return None
