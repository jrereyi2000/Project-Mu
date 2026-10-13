import cv2
import argparse
import sys
import streamlink
import threading
import queue
import time
from typing import Optional, List, Dict, Any
from pipeline.template_classifier import TemplateClassifier, TEMPLATE_DIR
from pipeline.ocr_extractor import BoxScoreExtractor
from db.db_handler import DatabaseHandler

def resolve_stream_url(url: str) -> str:
    print(f"Resolving live stream URL for: {url}")
    try:
        streams = streamlink.streams(url)
        if not streams: raise ValueError("No streams found. Is the channel offline?")
        stream = streams.get("1080p60") or streams.get("best")
        if not stream: raise ValueError("Could not find a suitable video stream quality.")
        print(f"Resolved raw stream URL successfully.")
        return stream.url
    except Exception as e:
        print(f"Streamlink Error: {e}")
        sys.exit(1)

class VideoCaptureThread(threading.Thread):
    def __init__(self, source: str):
        super().__init__()
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened(): raise ValueError(f"Failed to open source {source}")
        self.latest_frame = None
        self.ret = False
        self.running = True
        
    def run(self):
        while self.running:
            self.ret, frame = self.cap.read()
            if not self.ret:
                self.running = False
                break
            self.latest_frame = frame
                
    def stop(self):
        self.running = False
        self.cap.release()

class ApiWorkerPool:
    def __init__(
        self,
        extractor,
        db,
        active_game_id,
        all_extracted_stats: list,
        all_team_stats: list,
        game_meta_container: dict,
        num_workers: int = 3,
    ):
        self.extractor = extractor
        self.db = db
        self._active_game_id = active_game_id
        
        self.all_extracted_stats = all_extracted_stats
        self.all_team_stats = all_team_stats
        self.game_meta_container = game_meta_container
        self.num_workers = max(1, num_workers)
        
        self.job_queue = queue.Queue()
        self.running = True
        self._pending_game_generation = False
        self.lock = threading.Lock()
        self.workers: list[threading.Thread] = []

    @property
    def active_game_id(self):
        with self.lock:
            return self._active_game_id

    @active_game_id.setter
    def active_game_id(self, val):
        with self.lock:
            self._active_game_id = val

    @property
    def pending_game_generation(self):
        with self.lock:
            return self._pending_game_generation

    @pending_game_generation.setter
    def pending_game_generation(self, val):
        with self.lock:
            self._pending_game_generation = val

    def start(self):
        self.running = True
        self.workers = []
        for i in range(self.num_workers):
            t = threading.Thread(
                target=self._worker_loop,
                args=(i + 1,),
                daemon=True,
                name=f"ApiWorker-{i+1}"
            )
            self.workers.append(t)
            t.start()
        print(f"[API Worker Pool] Started {self.num_workers} parallel extraction workers.")

    def _worker_loop(self, worker_id: int):
        while self.running or not self.job_queue.empty():
            try:
                job = self.job_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                result = self.extractor.execute_job(job)
                
                if not result["success"]:
                    if job.get("type") == "single_player":
                        cat = job.get("category")
                        print(f"[API Worker #{worker_id}] Failed to parse {cat} (UI loading?). Unlocking tracker to retry...")
                        with self.lock:
                            if cat == "Passing": self.extractor.parsed_passing = False
                            if cat == "Rushing": self.extractor.parsed_rushing = False
                    continue
                    
                ext_type = result.get("type")
                ext_data = result.get("data")
                
                if ext_type == "player_stats" and ext_data and ext_data.player_stats:
                    print(f"\n[API Worker #{worker_id}] Successfully extracted {len(ext_data.player_stats)} player records!")
                    with self.lock:
                        self.all_extracted_stats.extend(ext_data.player_stats)
                        current_game_id = self._active_game_id
                    
                    # REAL-TIME DB INSERTION
                    if self.db and current_game_id:
                        self.db.upsert_player_stats(current_game_id, ext_data.player_stats)
                        print(f"[API Worker #{worker_id}] Instantly UPSERTED {len(ext_data.player_stats)} player stats to PostgreSQL!")
                    
                elif ext_type == "team_stats" and ext_data:
                    print(f"\n[API Worker #{worker_id}] Successfully extracted Game Meta and {len(ext_data.team_stats)} Team Stats!")
                    with self.lock:
                        self.game_meta_container["meta"] = ext_data.game_meta
                        self.all_team_stats.extend(ext_data.team_stats)
                        current_game_id = self._active_game_id
                    
                    # REAL-TIME DB INSERTION
                    if self.db and current_game_id:
                        # Merge the temporary game with the real scheduled game and get the true game_id
                        real_game_id = self.db.update_game_meta(current_game_id, ext_data.game_meta)
                        with self.lock:
                            self._active_game_id = real_game_id # Crucial: Sync the worker pool's active game id!
                        
                        self.db.upsert_team_stats(real_game_id, ext_data.team_stats)
                        print(f"[API Worker #{worker_id}] Instantly UPDATED Game Meta and Team Stats in PostgreSQL (Game ID: {real_game_id})!")
                        
                elif ext_type == "season_stats" and ext_data and ext_data.player_stats:
                    print(f"\n[API Worker #{worker_id}] Successfully extracted {len(ext_data.player_stats)} Cumulative Season Stats!")
                    if self.db:
                        self.db.upsert_season_stats(ext_data.player_stats)
                        with self.lock:
                            self._pending_game_generation = True
                        print(f"[API Worker #{worker_id}] Instantly UPSERTED {len(ext_data.player_stats)} cumulative records to PostgreSQL!")
                        
                elif ext_type == "schedule" and ext_data:
                    print(f"\n[API Worker #{worker_id}] Successfully extracted Schedule/Scores for Week {ext_data.week}!")
                    if self.db:
                        self.db.upsert_schedule(ext_data)
                        print(f"[API Worker #{worker_id}] Instantly UPDATED Game Scores in PostgreSQL!")
                        
                elif ext_type == "roster" and ext_data and ext_data.players:
                    print(f"\n[API Worker #{worker_id}] Successfully extracted {len(ext_data.players)} Roster Players!")
                    if self.db:
                        self.db.upsert_roster(ext_data.players)
                        print(f"[API Worker #{worker_id}] Instantly UPSERTED {len(ext_data.players)} roster players to PostgreSQL!")
            except Exception as e:
                print(f"[API Worker #{worker_id}] Error executing job: {e}")
            finally:
                self.job_queue.task_done()
                
    def stop(self):
        self.running = False

    def join(self, timeout: Optional[float] = None):
        for t in self.workers:
            t.join(timeout=timeout)

# Backwards compatibility alias
ApiWorkerThread = ApiWorkerPool

def run_pipeline(source: str, is_live: bool = False, use_db: bool = False, num_workers: int = 3):
    print("Initializing Pipeline Components...")
    
    classifier = TemplateClassifier(template_dir=TEMPLATE_DIR, threshold=0.9)
    try:
        extractor = BoxScoreExtractor()
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
        
    db = None
    active_game_id = None
    if use_db:
        try:
            db = DatabaseHandler()
            print("Connected to Database and Redis.")
            active_game_id = db.create_active_game()
            print(f"Created real-time game session in PostgreSQL (Game ID: {active_game_id})")
        except Exception as e:
            print(f"Failed to connect to Database/Redis: {e}")
            use_db = False
    
    all_extracted_stats = []
    all_team_stats = []
    game_meta_container = {"meta": None}
    
    api_worker = ApiWorkerPool(
        extractor,
        db,
        active_game_id,
        all_extracted_stats,
        all_team_stats,
        game_meta_container,
        num_workers=num_workers
    )
    api_worker.start()

    print(f"\nStarting End-to-End Ingestion at 1 FPS from: {'Live Stream' if is_live else source}")
    print("---------------------------------------------------------")
    
    if is_live:
        video_thread = VideoCaptureThread(source)
        video_thread.start()
        
        try:
            frame_idx = 0
            is_in_game = False
            while video_thread.running:
                time.sleep(1.0)
                frame_idx += 1
                
                frame = video_thread.latest_frame
                if frame is None: continue
                    
                raw_state, score = classifier.detect_ui_state(frame)
                
                # --- State Machine: GAMEPLAY Detection ---
                if raw_state == "GAMEPLAY":
                    if not is_in_game:
                        print("\n[Orchestrator] Detected GAMEPLAY! Setting is_in_game = True. Post-game parsing unlocked.")
                    is_in_game = True
                
                if raw_state == "MAIN_MENU":
                    if getattr(api_worker, "pending_game_generation", False):
                        print("\n[Orchestrator] Returned to MAIN MENU. Triggering pending Game Stat Generator...")
                        api_worker.pending_game_generation = False
                        def run_gen():
                            try:
                                from engines.game_stat_generator import generate_team_and_player_stats
                                generate_team_and_player_stats(season_year=2035)
                                print("[Orchestrator] Box Score Synchronization complete!")
                                # Publish the next week's newsroom slate (skips if that week is already published)
                                from newsroom.weekly_run import run_weekly_swarm
                                run_weekly_swarm(season=2035)
                            except Exception as e:
                                print(f"[Orchestrator] Stats sync / newsroom run failed: {e}")
                        import threading
                        threading.Thread(target=run_gen).start()
                
                # --- Real-Time Game Reset Logic (Continuous 24/7 Streaming) ---
                if raw_state == "MAIN_MENU" and active_game_id is not None:
                    is_in_game = False
                    # Only trigger the reset if we actually accumulated stats for the current session
                    if len(all_extracted_stats) > 0 or len(all_team_stats) > 0:
                        print(f"\n[Orchestrator] Detected MAIN_MENU! Finalizing Game {active_game_id}...")
                        
                        # Flush any dangling scrolling menus
                        final_job = extractor.flush()
                        if final_job: api_worker.job_queue.put(final_job)
                        
                        # Pause tracker to let the DB finish inserting the final stats
                        api_worker.job_queue.join()
                        
                        # Wake up the Swarm!
                        if use_db:
                            true_game_id = api_worker.active_game_id # The worker resolved the true scheduled game ID
                            db.emit_game_completed(true_game_id)
                            
                        # Wipe memory for the next game
                        all_extracted_stats.clear()
                        all_team_stats.clear()
                        game_meta_container["meta"] = None
                        
                        extractor.parsed_passing = False
                        extractor.parsed_rushing = False
                        extractor.is_receiving = False
                        extractor.is_team_stats = False
                        
                        # Instantly spin up a new Game ID
                        if use_db:
                            active_game_id = db.create_active_game()
                            api_worker.active_game_id = active_game_id
                            print(f"[Orchestrator] Generated new PostgreSQL session for NEXT game (Game ID: {active_game_id})\n")

                base_state = raw_state
                for known in [
                    "STATS_PASSING", "STATS_RUSHING", "STATS_RECEIVING", "TEAM_STATS",
                    "LEADERBOARD_PASSING", "LEADERBOARD_RUSHING", "LEADERBOARD_RECEIVING",
                    "ROSTER", "SCHEDULE"
                ]:
                    if raw_state.startswith(known):
                        base_state = known
                        break
                        
                # Strict State Machine: Route ambiguous stats based on the timeline!
                if not is_in_game:
                    # If we haven't played a game, any stats we see MUST be season stats from the franchise hub
                    if base_state == "STATS_PASSING": base_state = "LEADERBOARD_PASSING"
                    elif base_state == "STATS_RUSHING": base_state = "LEADERBOARD_RUSHING"
                    elif base_state == "STATS_RECEIVING": base_state = "LEADERBOARD_RECEIVING"
                    elif base_state == "TEAM_STATS": base_state = "UNKNOWN" # Ignore team stats if not in game
                        
                job = extractor.process_frame(frame, detected_state=base_state)
                if job: api_worker.job_queue.put(job)
                    
                print(f"[Tracker] Checked frame {frame_idx} (State: {raw_state}, InGame: {is_in_game})", end="\r")
        except KeyboardInterrupt:
            print("\nPipeline stopped by user.")
        finally:
            video_thread.stop()
            video_thread.join()
            
    else:
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            print(f"Failed to open {source}")
            api_worker.stop()
            return
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0 or fps > 120: fps = 30.0
        frames_to_skip = int(fps)
        frame_idx = 0
        is_in_game = False
        pending_game_generation = False
        
        try:
            while True:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret: break
                
                raw_state, score = classifier.detect_ui_state(frame)
                
                # --- State Machine: GAMEPLAY Detection ---
                if raw_state == "GAMEPLAY":
                    if not is_in_game:
                        print(f"\n[Orchestrator] Detected GAMEPLAY at frame {frame_idx}! Setting is_in_game = True. Post-game parsing unlocked.")
                    is_in_game = True
                    api_worker.pending_game_generation = False # Reset

                # --- Offline Game Reset Logic ---
                if raw_state == "MAIN_MENU":
                    # Check for pending generator trigger
                    if getattr(api_worker, "pending_game_generation", False):
                        print("\n[Orchestrator] Returned to MAIN MENU. Triggering pending Game Stat Generator...")
                        api_worker.pending_game_generation = False # Reset immediately
                        def run_gen():
                            try:
                                from engines.game_stat_generator import generate_team_and_player_stats
                                generate_team_and_player_stats(season_year=2035)
                                print("[Orchestrator] Box Score Synchronization complete!")
                                # Publish the next week's newsroom slate (skips if that week is already published)
                                from newsroom.weekly_run import run_weekly_swarm
                                run_weekly_swarm(season=2035)
                            except Exception as e:
                                print(f"[Orchestrator] Stats sync / newsroom run failed: {e}")
                        import threading
                        threading.Thread(target=run_gen).start()
                        
                if raw_state == "MAIN_MENU" and active_game_id is not None:
                    is_in_game = False
                    if len(all_extracted_stats) > 0 or len(all_team_stats) > 0:
                        print(f"\n[Orchestrator] Detected MAIN_MENU! Finalizing Game {active_game_id}...")
                        final_job = extractor.flush()
                        if final_job: api_worker.job_queue.put(final_job)
                        api_worker.job_queue.join()
                        if use_db:
                            true_game_id = api_worker.active_game_id
                            db.emit_game_completed(true_game_id)
                        
                        all_extracted_stats.clear()
                        all_team_stats.clear()
                        game_meta_container["meta"] = None
                        extractor.parsed_passing = False
                        extractor.parsed_rushing = False
                        extractor.is_receiving = False
                        extractor.is_team_stats = False
                        
                        if use_db:
                            active_game_id = db.create_active_game()
                            api_worker.active_game_id = active_game_id
                            print(f"[Orchestrator] Generated new PostgreSQL session for NEXT game (Game ID: {active_game_id})\n")
                
                base_state = raw_state
                for known in [
                    "STATS_PASSING", "STATS_RUSHING", "STATS_RECEIVING", "TEAM_STATS",
                    "LEADERBOARD_PASSING", "LEADERBOARD_RUSHING", "LEADERBOARD_RECEIVING",
                    "SCHEDULE"
                ]:
                    if raw_state.startswith(known):
                        base_state = known
                        break
                        
                # Strict State Machine: Route ambiguous stats based on the timeline!
                if not is_in_game:
                    if base_state == "STATS_PASSING": base_state = "LEADERBOARD_PASSING"
                    elif base_state == "STATS_RUSHING": base_state = "LEADERBOARD_RUSHING"
                    elif base_state == "STATS_RECEIVING": base_state = "LEADERBOARD_RECEIVING"
                    elif base_state == "TEAM_STATS": base_state = "UNKNOWN"
                        
                job = extractor.process_frame(frame, detected_state=base_state)
                if job: api_worker.job_queue.put(job)
                    
                frame_idx += frames_to_skip
                print(f"[Tracker] Checked frame {frame_idx} (State: {raw_state})", end="\r")
        except KeyboardInterrupt:
            print("\nPipeline stopped by user.")
        finally:
            cap.release()

    final_job = extractor.flush()
    if final_job:
        print("\n[Pipeline] Flushing dangling state to API Worker...")
        api_worker.job_queue.put(final_job)
        
    print("\n[Pipeline] Waiting for remaining API calls to finish...")
    api_worker.stop()
    api_worker.join()
        
    print("\n---------------------------------------------------------")
    print(f"Video End. Total Player Stats: {len(all_extracted_stats)} | Team Stats: {len(all_team_stats)}")
    
    if use_db and active_game_id:
        db.emit_game_completed(active_game_id)
        
    # Generator runs dynamically in the API Worker now, so no need to run at exit!
        
    elif not use_db:
        print("\nDry Run Results (Game Meta):")
        if game_meta_container["meta"]: print(game_meta_container["meta"].model_dump_json(indent=2))
        print("\nDry Run Results (Team Stats):")
        for ts in all_team_stats: print(ts.model_dump_json(indent=2))
        print("\nDry Run Results (Player Stats):")
        for stat in all_extracted_stats: print(stat.model_dump_json(indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="End-to-End Madden Pipeline (Phase 1 -> 3)")
    parser.add_argument("--file", type=str, help="Local video file to process")
    parser.add_argument("--stream", type=str, help="Live Twitch/YouTube URL to process (e.g. https://twitch.tv/username)")
    parser.add_argument("--db", action="store_true", help="Attempt to insert results into PostgreSQL")
    parser.add_argument("--workers", type=int, default=3, help="Number of concurrent API extraction workers (default: 3)")
    args = parser.parse_args()
    
    if args.stream:
        raw_url = resolve_stream_url(args.stream)
        run_pipeline(raw_url, is_live=True, use_db=args.db, num_workers=args.workers)
    elif args.file:
        run_pipeline(args.file, is_live=False, use_db=args.db, num_workers=args.workers)
    else:
        print("Error: You must provide either --file or --stream")
        sys.exit(1)
